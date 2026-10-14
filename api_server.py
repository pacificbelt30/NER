"""Stream uploads into isolated workspaces and process PDFs outside the event loop."""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
import logging
import json
import multiprocessing
import os
from pathlib import Path, PureWindowsPath
import shutil
import tempfile
import time
from typing import Annotated, Literal

import aiofiles
from fastapi import FastAPI, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
import uvicorn

from ner import CombinePDF, PaperExtract, TextraConnection, TranslationService, config_load
from ner.api_models import Backend, JobView, Output
from ner.errors import ConfigurationError, PDFError, TranslationCancelled, TranslationError
from ner.io_utils import atomic_output
from ner.jobs import JobExpired, JobManager, QueueFull
from ner.pdf_warnings import PDFWarning


logger = logging.getLogger(__name__)
base = os.environ.get('NER_STORAGE_PATH', 'pdfstore/')
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
CONFIG_PATH = os.environ.get('NER_CONFIG_PATH', 'config.yml')
WEB_ROOT = Path(__file__).resolve().parent / 'web'
RESULT_RETENTION = 3600


def new_pool():
    # PyMuPDF does not support concurrent use from Python threads.
    return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context('spawn'))


@asynccontextmanager
async def lifespan(app):
    app.state.pdf_pool = new_pool()
    app.state.jobs = JobManager(Path(base).resolve() / 'jobs', run_translation_job,
                                retention=RESULT_RETENTION)
    app.state.jobs.start()
    async def cleanup():
        while True:
            await asyncio.sleep(60)
            app.state.jobs.expire()
    cleanup_task = asyncio.create_task(cleanup())
    try:
        yield
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await app.state.jobs.close()
        await run_in_threadpool(app.state.pdf_pool.shutdown, wait=True, cancel_futures=True)


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=['*'], allow_credentials=False,
    allow_methods=['GET', 'POST', 'OPTIONS'], allow_headers=['*'],
    expose_headers=['Location', 'Retry-After', 'Content-Disposition'],
)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc):
    if not request.url.path.startswith('/api/'):
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code, headers=exc.headers)
    detail = exc.detail if isinstance(exc.detail, dict) else {
        'code': 'REQUEST_FAILED', 'message': str(exc.detail), 'retryable': exc.status_code >= 500,
    }
    return JSONResponse({'error': detail}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc):
    if request.url.path.startswith('/api/'):
        fields = {str(error['loc'][-1]) for error in exc.errors() if error.get('loc')}
        message = ('ページ番号は1以上の整数で指定してください。' if 'page' in fields else
                   '対応する翻訳方式と出力形式を選んでください。' if fields & {'backend', 'output'} else
                   'PDFファイルを指定してください。' if 'file' in fields else
                   'リクエストの内容を確認してください。')
        return JSONResponse({'error': {'code': 'INVALID_REQUEST',
            'message': message, 'retryable': False}}, status_code=422)
    return JSONResponse({'detail': 'Invalid request.'}, status_code=422)


def api_error(status, code, message, retryable=False):
    return HTTPException(status, {'code': code, 'message': message, 'retryable': retryable},
                         headers={'Retry-After': '5'} if status == 429 else None)


@app.get('/', include_in_schema=False)
async def web_ui():
    return FileResponse(WEB_ROOT / 'index.html', headers={'Cache-Control': 'no-cache'})


def selected_config(backend=None):
    try:
        config = config_load(CONFIG_PATH)
        chosen = backend or config.get('PRIORITY', 'GOOGLE')
        config = {**config, 'PRIORITY': chosen}
        with TranslationService(config):
            pass
        return config
    except (ConfigurationError, FileNotFoundError) as exc:
        logger.error('Translation configuration is unavailable: %s', type(exc).__name__)
        raise api_error(503, 'BACKEND_NOT_CONFIGURED', '翻訳方式の準備が完了していません。サーバーの設定を確認してください。', True) from exc


@app.get('/api/capabilities')
async def capabilities():
    try:
        config = config_load(CONFIG_PATH)
    except ConfigurationError:
        config = {}
    labels = {'INDEX': 'Index 2B', 'BERGAMOT': 'Firefox / Bergamot', 'GOOGLE': 'Google翻訳', 'TEXTRA': 'TexTra'}
    backends = []
    for name, label in labels.items():
        configured = name in config or config.get('PRIORITY') == name
        if configured:
            try:
                with TranslationService({**config, 'PRIORITY': name}):
                    pass
            except (ConfigurationError, FileNotFoundError):
                configured = False
        backends.append({'id': name, 'label': label, 'local': name in ('INDEX', 'BERGAMOT'),
                         'configured': configured})
    return {'source_language': 'en', 'target_language': 'ja', 'max_upload_bytes': MAX_UPLOAD_BYTES,
            'result_retention_seconds': RESULT_RETENTION, 'backends': backends,
            'default_backend': config.get('PRIORITY')}


def translation_job_worker(workspace, config, output):
    workspace = Path(workspace)
    state = {'stage': 'extracting', 'completed_blocks': 0, 'total_blocks': 0, 'page': 0, 'pages': 0,
             'block_id': None, 'completed_chunks': 0, 'total_chunks': 0}
    last_update = 0
    def check_cancel():
        if (workspace / 'cancel').exists():
            raise TranslationCancelled('Translation cancelled.')
    def report(stage, force=True):
        nonlocal last_update
        check_cancel()
        if force or time.monotonic() - last_update >= 0.15:
            state['stage'] = stage
            with atomic_output(workspace / 'progress.json') as path:
                path.write_text(json.dumps(state), encoding='utf-8')
            last_update = time.monotonic()
    def progress(completed, total, page, pages):
        if completed > state['completed_blocks']:
            state.update(block_id=None, completed_chunks=0, total_chunks=0)
        state.update(completed_blocks=completed, total_blocks=total, page=page, pages=pages)
        report('translating', completed == 0 or completed == total)
    def chunk_progress(block_id, completed, total):
        state.update(block_id=block_id, completed_chunks=completed, total_chunks=total)
        report('translating', completed == 0 or completed == total)
    report('extracting')
    with TextraConnection(workspace / 'input.pdf', config=config) as job:
        job.get_pdf_info()
        job.post_page(on_progress=progress, on_chunk_progress=chunk_progress)
        report('saving')
        translated = job.insert(check_cancel=check_cancel)
        result = {'result_summary': job.result_summary,
                  'warnings': [warning.view() for warning in job.warnings]}
    if output == 'comparison':
        report('combining')
        try:
            CombinePDF(workspace / 'input.pdf', translated, workspace / 'comparison.pdf').combine()
        except (PDFError, OSError, RuntimeError, ValueError):
            logger.warning('Comparison PDF could not be created; translated PDF remains available.')
            result['warnings'].append(PDFWarning('COMPARISON_FAILED', 'combining').view())
            result['result_summary']['warning_count'] += 1
            if result['result_summary']['outcome'] == 'complete':
                result['result_summary']['outcome'] = 'complete_with_warnings'
    report('previewing')
    try:
        preview_worker(str(workspace), 'original', 1)
        preview_worker(str(workspace), 'translated', 1)
    except (OSError, RuntimeError, ValueError):
        logger.warning('Initial page previews could not be rendered.')
    report('done')
    return result


def preview_worker(workspace, kind, page_number):
    import pymupdf
    workspace = Path(workspace)
    source = workspace / ('input.pdf' if kind == 'original' else 'input_new.pdf')
    with PaperExtract(source) as document:
        if page_number > document.page_count:
            raise PDFError('Page does not exist.')
        page = document.doc[page_number - 1]
        scale = min(1.5, 1100 / page.rect.width, 1800 / page.rect.height)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        destination = workspace / f'preview-{kind}-{page_number}.png'
        with atomic_output(destination) as path:
            pixmap.save(path)
    return str(destination)


async def run_translation_job(workspace, config, output):
    pool = app.state.pdf_pool
    future = asyncio.get_running_loop().run_in_executor(pool, translation_job_worker,
                                                       str(workspace), config, output)
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(future)
        except Exception:
            pass
        raise
    except BrokenProcessPool:
        if app.state.pdf_pool is pool:
            app.state.pdf_pool = new_pool()
            await run_in_threadpool(pool.shutdown, wait=False, cancel_futures=True)
        raise


async def save_job_upload(file, workspace):
    filename = PureWindowsPath(file.filename or '').name
    if not filename or Path(filename).suffix.lower() != '.pdf':
        raise api_error(415, 'UNSUPPORTED_FILE', 'PDFファイルを選んでください。')
    size = 0
    async with aiofiles.open(workspace / 'input.pdf', 'wb') as handle:
        while chunk := await file.read(CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise api_error(413, 'FILE_TOO_LARGE', 'PDFは50 MiB以内にしてください。')
            await handle.write(chunk)
    if not size:
        raise api_error(400, 'EMPTY_FILE', 'PDFが空です。別のファイルを選んでください。')
    return filename


@app.post('/api/jobs', status_code=202, response_model=JobView)
async def create_job(file: UploadFile, backend: Annotated[Backend | None, Form()] = None,
                     output: Annotated[Output, Form()] = 'comparison',
                     submission_key: Annotated[str | None, Header(alias='Idempotency-Key', max_length=64,
                                                                  pattern=r'^[A-Za-z0-9_-]+$')] = None):
    workspace = None
    accepted = False
    try:
        manager = app.state.jobs
        existing = manager.by_key(submission_key) if submission_key else None
        if existing:
            if existing['filename'] != PureWindowsPath(file.filename or '').name or existing['output'] != output or (backend and existing['backend'] != backend):
                raise api_error(409, 'IDEMPOTENCY_CONFLICT', 'この受付キーは別の翻訳に使われています。')
            return JSONResponse(manager.describe(existing['id']), status_code=202,
                                headers={'Location': f"/api/jobs/{existing['id']}", 'Retry-After': '1'})
        config = selected_config(backend)
        workspace = manager.new_workspace()
        filename = await save_job_upload(file, workspace)
        # Two simultaneous retries of an upload must create only one job.
        existing = manager.by_key(submission_key) if submission_key else None
        if existing:
            if existing['filename'] != filename or existing['backend'] != config['PRIORITY'] or existing['output'] != output:
                raise api_error(409, 'IDEMPOTENCY_CONFLICT', 'この受付キーは別の翻訳に使われています。')
            result = manager.describe(existing['id'])
        else:
            result = manager.submit(workspace, filename, config['PRIORITY'], output, config, submission_key)
            accepted = True
        return JSONResponse(result, status_code=202,
                            headers={'Location': f"/api/jobs/{result['id']}", 'Retry-After': '1'})
    except QueueFull as exc:
        raise api_error(429, 'QUEUE_FULL', '翻訳の受付が混み合っています。少し待って再送してください。', True) from exc
    except OSError as exc:
        raise api_error(500, 'UPLOAD_FAILED', 'PDFを保存できませんでした。もう一度お試しください。', True) from exc
    finally:
        await file.close()
        if workspace and not accepted:
            await run_in_threadpool(shutil.rmtree, workspace, ignore_errors=True)


def get_job(job_id):
    try:
        return app.state.jobs.get(job_id)
    except JobExpired as exc:
        raise api_error(410, 'JOB_EXPIRED', '保存期限が過ぎました。PDFをもう一度アップロードしてください。') from exc
    except KeyError as exc:
        raise api_error(404, 'JOB_NOT_FOUND', '翻訳結果が見つかりません。PDFをもう一度アップロードしてください。') from exc


@app.get('/api/jobs/by-key/{submission_key}', response_model=JobView)
async def job_by_key(submission_key: str):
    job = app.state.jobs.by_key(submission_key)
    if not job:
        raise api_error(404, 'JOB_NOT_FOUND', '翻訳の受付を確認できませんでした。もう一度送信してください。')
    return app.state.jobs.describe(job['id'])


@app.get('/api/jobs/{job_id}', response_model=JobView)
async def job_status(job_id: str):
    get_job(job_id)
    return JSONResponse(app.state.jobs.describe(job_id), headers={'Cache-Control': 'no-store', 'Retry-After': '1'})


@app.post('/api/jobs/{job_id}/cancel', response_model=JobView)
async def cancel_job(job_id: str):
    get_job(job_id)
    return app.state.jobs.cancel(job_id)


@app.post('/api/jobs/{job_id}/retry', status_code=202, response_model=JobView)
async def retry_job(job_id: str, backend: Annotated[Backend | None, Form()] = None,
                    output: Annotated[Output | None, Form()] = None):
    job = get_job(job_id)
    if job['status'] not in ('failed', 'cancelled'):
        raise api_error(409, 'JOB_NOT_RETRYABLE', '再試行できるのは失敗または停止した翻訳です。')
    config = selected_config(backend or job['backend'])
    workspace = None
    accepted = False
    try:
        workspace = app.state.jobs.new_workspace()
        await run_in_threadpool(shutil.copyfile, app.state.jobs.root / job_id / 'input.pdf', workspace / 'input.pdf')
        result = app.state.jobs.submit(workspace, job['filename'], config['PRIORITY'], output or job['output'], config)
        accepted = True
        return JSONResponse(result, status_code=202, headers={'Location': f"/api/jobs/{result['id']}"})
    except QueueFull as exc:
        raise api_error(429, 'QUEUE_FULL', '翻訳の受付が混み合っています。少し待って再試行してください。', True) from exc
    finally:
        if workspace and not accepted:
            await run_in_threadpool(shutil.rmtree, workspace, ignore_errors=True)


@app.get('/api/jobs/{job_id}/files/{kind}')
async def job_file(job_id: str, kind: Literal['original', 'translated', 'comparison'],
                   inline: bool = Query(False)):
    job = get_job(job_id)
    if job['status'] != 'succeeded':
        raise api_error(409, 'RESULT_NOT_READY', '翻訳結果はまだダウンロードできません。')
    if kind == 'comparison' and job['output'] != 'comparison':
        raise api_error(404, 'FILE_NOT_FOUND', 'この翻訳では比較用PDFを作成していません。')
    paths = {'original': 'input.pdf', 'translated': 'input_new.pdf', 'comparison': 'comparison.pdf'}
    suffixes = {'original': '', 'translated': '_new', 'comparison': '_all'}
    filename = Path(job['filename']).stem + suffixes[kind] + '.pdf'
    path = app.state.jobs.root / job_id / paths[kind]
    if not path.is_file():
        raise api_error(410, 'FILE_EXPIRED', 'PDFが見つかりません。もう一度アップロードしてください。')
    return FileResponse(path, filename=filename, media_type='application/pdf',
                        content_disposition_type='inline' if inline else 'attachment',
                        headers={'Cache-Control': 'no-store'})


@app.get('/api/jobs/{job_id}/preview/{kind}')
async def job_preview(job_id: str, kind: Literal['original', 'translated'],
                       page: int = Query(1, ge=1)):
    job = get_job(job_id)
    if job['status'] != 'succeeded':
        raise api_error(409, 'RESULT_NOT_READY', '翻訳結果はまだ表示できません。')
    workspace = app.state.jobs.root / job_id
    path = workspace / f'preview-{kind}-{page}.png'
    if not path.is_file():
        future = asyncio.get_running_loop().run_in_executor(app.state.pdf_pool, preview_worker,
                                                            str(workspace), kind, page)
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(future)
            except Exception:
                pass
            raise
        except PDFError as exc:
            raise api_error(404, 'PAGE_NOT_FOUND', '指定されたページはありません。') from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise api_error(503, 'PREVIEW_UNAVAILABLE', 'プレビューを作成できませんでした。PDFを別タブで開くか、ダウンロードしてください。', True) from exc
    return FileResponse(path, media_type='image/png', headers={'Cache-Control': 'no-store'})


def translate(filename):
    with TextraConnection(filename) as connection:
        connection.get_pdf_info()
        connection.post_page()
        return connection.insert()


def process_pdf(filename, should_translate):
    if should_translate:
        return translate(filename)
    with PaperExtract(filename):
        return filename


async def run_pdf_job(filename, should_translate):
    pool = app.state.pdf_pool
    future = asyncio.get_running_loop().run_in_executor(
        pool, process_pdf, filename, should_translate,
    )
    try:
        return await asyncio.shield(future)
    except asyncio.CancelledError:
        # Wait for the worker to release the request's files before cleanup.
        try:
            await asyncio.shield(future)
        except Exception:
            logger.exception('Cancelled PDF job failed.')
        raise
    except BrokenProcessPool as exc:
        if app.state.pdf_pool is pool:
            app.state.pdf_pool = new_pool()
            await run_in_threadpool(pool.shutdown, wait=False, cancel_futures=True)
        raise HTTPException(500, 'PDF worker failed; retry the request.') from exc


class TemporaryFileResponse(FileResponse):
    def __init__(self, path, workspace, **kwargs):
        super().__init__(path, **kwargs)
        self.workspace = workspace

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await run_in_threadpool(shutil.rmtree, self.workspace, ignore_errors=True)


async def handle_upload(file, should_translate):
    workspace = None
    response_owns_workspace = False
    try:
        filename = PureWindowsPath(file.filename or '').name
        if not filename or Path(filename).suffix.lower() != '.pdf':
            raise HTTPException(415, 'Upload a PDF file.')
        storage = Path(base)
        storage.mkdir(parents=True, exist_ok=True)
        workspace = Path(tempfile.mkdtemp(prefix='request-', dir=storage))
        path = workspace / filename
        size = 0
        async with aiofiles.open(path, 'wb') as output:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, 'PDF exceeds the 50 MiB upload limit.')
                await output.write(chunk)
        if size == 0:
            raise HTTPException(400, 'The uploaded PDF is empty.')
        result = await run_pdf_job(str(path), should_translate)
        if not Path(result).is_file():
            raise OSError('PDF worker did not produce its output file.')
        response = TemporaryFileResponse(
            result, workspace, filename=filename, media_type='application/pdf',
            headers={'Access-Control-Expose-Headers': 'Content-Disposition'},
        )
        response_owns_workspace = True
        return response
    except PDFError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TranslationError as exc:
        logger.warning('PDF translation failed: %s', exc)
        raise HTTPException(502, str(exc)) from exc
    except (ConfigurationError, FileNotFoundError) as exc:
        logger.error('Translation setup failed: %s', exc)
        raise HTTPException(500, 'Translation backend is not configured correctly.') from exc
    except OSError as exc:
        logger.exception('PDF file operation failed.')
        raise HTTPException(500, 'Could not process the uploaded PDF.') from exc
    finally:
        await file.close()
        if workspace is not None and not response_owns_workspace:
            await run_in_threadpool(shutil.rmtree, workspace, ignore_errors=True)


@app.post('/return_translated_file')
async def return_translated_file(file: UploadFile):
    return await handle_upload(file, True)


@app.post('/return_translated_file_jaen')
async def return_translated_file_jaen(file: UploadFile):
    await file.close()
    raise HTTPException(501, 'Japanese-to-English translation is not implemented.')


@app.post('/return_origin_file')
async def return_origin_file(file: UploadFile):
    return await handle_upload(file, False)


app.mount('/assets', StaticFiles(directory=WEB_ROOT, check_dir=False), name='assets')


if __name__ == '__main__':
    uvicorn.run('api_server:app', port=8000, reload=True, host='0.0.0.0')
