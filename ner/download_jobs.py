"""Persistent model acquisition jobs, independent of the PDF worker queue."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid

import requests

from .io_utils import atomic_output
from .model_downloads import CATALOG, DownloadBusy, DownloadCancelled, download_index

ACTIVE = {'queued', 'running', 'cancelling'}


def public_download_error(exc):
    if isinstance(exc, DownloadBusy):
        return {'code': 'DOWNLOAD_BUSY', 'message': '同じ保存先で別の取得処理が実行中です。完了後に再試行してください。'}
    if isinstance(exc, OSError) and (str(exc).startswith('Insufficient disk space') or getattr(exc, 'errno', None) == 28):
        return {'code': 'DISK_FULL', 'message': 'モデル保存先の空き容量が不足しています。空き容量を確保して再試行してください。'}
    if isinstance(exc, PermissionError):
        return {'code': 'DOWNLOAD_PERMISSION_DENIED', 'message': 'モデル保存先へ書き込めません。サーバーの保存先の権限を確認してください。'}
    if isinstance(exc, requests.Timeout):
        return {'code': 'DOWNLOAD_TIMEOUT', 'message': 'モデル配布元への接続がタイムアウトしました。再試行すると取得可能な続きから再開します。'}
    if isinstance(exc, requests.ConnectionError):
        return {'code': 'DOWNLOAD_CONNECTION_FAILED', 'message': 'モデル配布元へ接続できません。サーバーのネットワーク・プロキシ設定を確認してください。'}
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return {'code': 'DOWNLOAD_SOURCE_REJECTED', 'message': f'モデル配布元からHTTP {exc.response.status_code}が返されました。接続制限・配布元の状態を確認してください。'}
    if isinstance(exc, ValueError):
        return {'code': 'MODEL_VERIFICATION_FAILED', 'message': 'モデルのサイズ・チェックサム・配布情報を確認できませんでした。再試行してください。'}
    return {'code': 'MODEL_DOWNLOAD_FAILED', 'message': 'モデルを取得できませんでした。ネットワーク・保存先の権限・空き容量を確認して再試行してください。'}


class DownloadManager:
    def __init__(self, root, *, downloader=None):
        self.root = Path(root)
        self.jobs_root = self.root / 'model-downloads'
        self.models_root = self.root / 'models'
        self.downloader = downloader or download_index
        self.jobs = {}
        self.tasks = {}
        self.events = {}
        self.lock = threading.RLock()
        self.closing = False

    def _save(self, job):
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        with atomic_output(self.jobs_root / (job['id'] + '.json')) as path:
            path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')

    def start(self):
        for path in self.jobs_root.glob('*.json'):
            try:
                job = json.loads(path.read_text())
                if job['id'] != path.stem or job['variant'] not in CATALOG:
                    continue
                if job['status'] in ACTIVE:
                    job.update(status='failed', error={'code': 'SERVER_INTERRUPTED',
                        'message': 'サーバー停止で取得が中断されました。再試行すると途中のファイルを再利用します。'})
                    self._save(job)
                self.jobs[job['id']] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def list(self):
        with self.lock:
            return deepcopy(sorted(self.jobs.values(), key=lambda item: item['created_at'], reverse=True)[:20])

    def get(self, job_id):
        with self.lock:
            return deepcopy(self.jobs[job_id])

    def submit(self, variant):
        if variant not in CATALOG:
            raise ValueError('Unknown Index model format.')
        with self.lock:
            if self.closing:
                raise DownloadBusy('Server is stopping.')
            for job in self.jobs.values():
                if job['status'] in ACTIVE:
                    if job['variant'] == variant:
                        return deepcopy(job)
                    raise DownloadBusy('Another model download is running.')
            job_id = uuid.uuid4().hex
            job = {'id': job_id, 'backend': 'INDEX', 'variant': variant, 'status': 'queued',
                   'created_at': time.time(), 'updated_at': time.time(), 'stage': 'queued',
                   'downloaded_bytes': 0, 'total_bytes': 0, 'file': None, 'error': None, 'result': None}
            self._save(job)
            self.jobs[job_id] = job
            self.events[job_id] = threading.Event()
            task = asyncio.create_task(self._run(job_id))
            self.tasks[job_id] = task
            task.add_done_callback(lambda task: self.tasks.pop(job_id, None))
            return deepcopy(job)

    async def _run(self, job_id):
        event = self.events[job_id]
        last_saved = 0
        def progress(value):
            nonlocal last_saved
            with self.lock:
                job = self.jobs[job_id]
                job.update(value, updated_at=time.time())
                if time.monotonic() - last_saved > .25:
                    self._save(job)
                    last_saved = time.monotonic()
        try:
            with self.lock:
                job = self.jobs[job_id]
                job.update(status='running', updated_at=time.time())
                self._save(job)
                variant = job['variant']
            result = await asyncio.to_thread(self.downloader, self.models_root / 'index' / variant,
                                            variant, on_progress=progress, cancel_event=event)
            with self.lock:
                self.jobs[job_id].update(status='cancelled' if event.is_set() else 'succeeded', result=result)
        except DownloadCancelled:
            with self.lock:
                self.jobs[job_id]['status'] = 'cancelled'
        except Exception as exc:
            with self.lock:
                self.jobs[job_id].update(status='cancelled' if event.is_set() else 'failed',
                                          error=None if event.is_set() else public_download_error(exc))
        finally:
            with self.lock:
                self.jobs[job_id]['updated_at'] = time.time()
                try:
                    self._save(self.jobs[job_id])
                except OSError:
                    self.jobs[job_id].update(status='failed', error={'code': 'DOWNLOAD_STORAGE_FAILED',
                        'message': '取得状況を保存できません。保存先の書き込み権限と空き容量を確認してください。'})
                finally:
                    self.events.pop(job_id, None)

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job['status'] in ACTIVE:
                self.events[job_id].set()
                job.update(status='cancelling', updated_at=time.time())
                self._save(job)
            return deepcopy(job)

    async def close(self):
        self.closing = True
        with self.lock:
            for event in self.events.values():
                event.set()
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)
