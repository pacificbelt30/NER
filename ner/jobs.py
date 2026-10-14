"""Bounded translation queue with retained results and restart-safe metadata."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import time
import uuid

from .errors import ConfigurationError, PDFError, TranslationCancelled, TranslationError
from .io_utils import atomic_output


ACTIVE = {'queued', 'running', 'cancelling'}
TERMINAL = {'succeeded', 'failed', 'cancelled'}


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class JobExpired(LookupError):
    pass


class QueueFull(RuntimeError):
    pass


def public_error(exc):
    if isinstance(exc, PDFError):
        if any(word in str(exc) for word in ('does not fit', 'overflow', 'rectangle', 'font size', 'block count')):
            return {'code': 'LAYOUT_FAILED', 'message': '訳文が元のページ枠に収まりませんでした。別の翻訳方式で再試行してください。', 'retryable': True}
        message = ('翻訳できる本文が見つかりません。スキャンPDFは文字認識（OCR）を行ってからお試しください。'
                   if 'No translatable text' in str(exc) else
                   'PDFを読み込めないか、訳文を配置できませんでした。パスワードを解除したPDF、または別のPDFでお試しください。')
        return {'code': 'INVALID_PDF', 'message': message, 'retryable': False}
    if isinstance(exc, (ConfigurationError, FileNotFoundError)):
        return {'code': 'BACKEND_NOT_CONFIGURED', 'message': '翻訳方式の準備が完了していません。設定を確認してから再試行してください。', 'retryable': True}
    if isinstance(exc, TranslationError):
        if 'Protected token mismatch' in str(exc):
            return {'code': 'PROTECTED_CONTENT_INVALID', 'message': 'URLやコードを変更する訳文が返されたため停止しました。別の翻訳方式で再試行してください。', 'retryable': True}
        return {'code': 'TRANSLATION_FAILED', 'message': '翻訳を完了できませんでした。翻訳エンジンの状態を確認し、もう一度お試しください。', 'retryable': True}
    return {'code': 'PROCESSING_FAILED', 'message': 'PDFの処理に失敗しました。時間を置いて再試行してください。', 'retryable': True}


class JobManager:
    def __init__(self, root, processor, *, retention=3600, capacity=16):
        self.root = Path(root)
        self.processor = processor
        self.retention = retention
        self.capacity = capacity
        self.jobs = {}
        self.tasks = {}
        self.expired = set()
        self.semaphore = asyncio.Semaphore(1)
        self.closing = False

    def start(self):
        # Unaccepted uploads cannot be resumed after a process restart.
        for workspace in self.root.glob('*'):
            if (workspace.is_dir() and not workspace.is_symlink() and len(workspace.name) == 32
                    and all(char in '0123456789abcdef' for char in workspace.name)
                    and not (workspace / 'job.json').exists()):
                shutil.rmtree(workspace, ignore_errors=True)
        for path in self.root.glob('*/job.json'):
            try:
                job = json.loads(path.read_text(encoding='utf-8'))
                if job['id'] != path.parent.name or len(job['id']) != 32:
                    continue
                if job['status'] in ACTIVE:
                    job.update(status='failed', error={
                        'code': 'SERVER_INTERRUPTED', 'message': 'サーバーの再起動で処理が中断されました。同じPDFで再試行できます。', 'retryable': True,
                    }, updated_at=time.time(), expires_at=time.time() + self.retention)
                    self._save(job)
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.expire()

    def _save(self, job):
        with atomic_output(self.root / job['id'] / 'job.json') as path:
            path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')

    def expire(self):
        now = time.time()
        for job_id, job in list(self.jobs.items()):
            if job['status'] in TERMINAL and job.get('expires_at', now + 1) <= now:
                self.expired.add(job_id)
                shutil.rmtree(self.root / job_id, ignore_errors=True)
                del self.jobs[job_id]

    def check_capacity(self):
        self.expire()
        if self.closing or sum(job['status'] in ACTIVE for job in self.jobs.values()) >= self.capacity:
            raise QueueFull('Translation queue is full.')

    def submit(self, workspace, filename, backend, output, config, submission_key=None):
        self.check_capacity()
        job_id = Path(workspace).name
        now = time.time()
        job = {
            'id': job_id, 'filename': filename, 'backend': backend, 'output': output,
            'status': 'queued', 'created_at': now, 'updated_at': now,
            'expires_at': None, 'error': None,
            'warnings': [], 'result_summary': None,
            'submission_key': submission_key,
        }
        self._save(job)
        self.jobs[job_id] = job
        self.tasks[job_id] = asyncio.create_task(self._run(job, config))
        self.tasks[job_id].add_done_callback(lambda task: self.tasks.pop(job_id, None))
        return self.describe(job_id)

    async def _run(self, job, config):
        workspace = self.root / job['id']
        try:
            async with self.semaphore:
                if job['status'] == 'cancelled':
                    return
                job.update(status='running', updated_at=time.time())
                self._save(job)
                result = await self.processor(workspace, config, job['output'])
                if result is not None:
                    job.update(warnings=result.get('warnings', []), result_summary=result.get('result_summary'))
                job['status'] = 'cancelled' if job['status'] == 'cancelling' else 'succeeded'
        except TranslationCancelled:
            job['status'] = 'cancelled'
        except asyncio.CancelledError:
            if job['status'] not in ('cancelled', 'cancelling'):
                job.update(status='failed', error={
                    'code': 'SERVER_INTERRUPTED', 'message': 'サーバーの停止で処理が中断されました。再試行してください。', 'retryable': True,
                })
            else:
                job['status'] = 'cancelled'
        except Exception as exc:
            if job['status'] in ('cancelled', 'cancelling'):
                job.update(status='cancelled', error=None)
            else:
                logging.getLogger(__name__).exception('Translation job %s failed.', job['id'])
                job.update(status='failed', error=public_error(exc))
        finally:
            job.update(updated_at=time.time(), expires_at=time.time() + self.retention)
            self._save(job)
            self.tasks.pop(job['id'], None)

    def get(self, job_id):
        self.expire()
        if job_id in self.expired:
            raise JobExpired(job_id)
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return self.jobs[job_id]

    def by_key(self, key):
        self.expire()
        return next((job for job in self.jobs.values() if job.get('submission_key') == key), None)

    def describe(self, job_id):
        job = self.get(job_id)
        progress = {'stage': job['status'], 'completed_blocks': 0, 'total_blocks': 0,
                    'page': 0, 'pages': 0}
        if job['status'] != 'queued':
            try:
                progress.update(json.loads((self.root / job_id / 'progress.json').read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass
        if job['status'] == 'succeeded':
            progress['stage'] = 'done'
        elif job['status'] in ('failed', 'cancelled', 'cancelling'):
            progress['stage'] = job['status']
        queued = [j['id'] for j in self.jobs.values() if j['status'] == 'queued']
        downloads = None
        if job['status'] == 'succeeded':
            downloads = {kind: f'/api/jobs/{job_id}/files/{kind}' for kind in ('original', 'translated')}
            if job['output'] == 'comparison' and (self.root / job_id / 'comparison.pdf').is_file():
                downloads['comparison'] = f'/api/jobs/{job_id}/files/comparison'
        return {
            **{k: job[k] for k in ('id', 'filename', 'backend', 'output', 'status', 'error')},
            'created_at': timestamp(job['created_at']), 'updated_at': timestamp(job['updated_at']),
            'expires_at': timestamp(job['expires_at']) if job['expires_at'] else None,
            'queue_position': queued.index(job_id) + 1 if job_id in queued else None,
            'progress': progress, 'downloads': downloads,
            'warnings': job.get('warnings', []), 'result_summary': job.get('result_summary'),
        }

    def cancel(self, job_id):
        job = self.get(job_id)
        if job['status'] == 'queued':
            job.update(status='cancelled', updated_at=time.time(), expires_at=time.time() + self.retention)
            self._save(job)
            if job_id in self.tasks:
                self.tasks[job_id].cancel()
        elif job['status'] == 'running':
            (self.root / job_id / 'cancel').touch()
            job.update(status='cancelling', updated_at=time.time())
            self._save(job)
        return self.describe(job_id)

    async def close(self):
        self.closing = True
        tasks = list(self.tasks.values())
        for job_id, task in list(self.tasks.items()):
            workspace = self.root / job_id
            if workspace.exists():
                (workspace / 'cancel').touch()
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def new_workspace(self):
        self.check_capacity()
        workspace = self.root / uuid.uuid4().hex
        workspace.mkdir(parents=True)
        return workspace
