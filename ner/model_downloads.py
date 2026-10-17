"""Verified official model downloads shared by the CLI and web background jobs."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import quote

import requests

from .io_utils import atomic_output
from .file_locks import exclusive_lock

CATALOG = {
    'gguf-q4': {'label': 'GGUF Q4_K_M（軽量・llama.cpp用）', 'repository': 'IndexTeam/Index-Translate-2B-GGUF',
                'revision': '3a0eca6ce931d88d00614d427ab3ed8ff0743902',
                'filename': 'Index-Translate-2B.Q4_K_M.gguf', 'size_bytes': 1312164352},
    'gguf-q8': {'label': 'GGUF Q8_0（llama.cpp用）', 'repository': 'IndexTeam/Index-Translate-2B-GGUF',
                'revision': '3a0eca6ce931d88d00614d427ab3ed8ff0743902',
                'filename': 'Index-Translate-2B.Q8_0.gguf', 'size_bytes': 2076674560},
    'hf': {'label': '標準モデル一式（Safetensors・vLLM等用）', 'repository': 'IndexTeam/Index-Translate-2B',
           'revision': '9bc08445315723eb9b77d1b93505915eb03c3f01', 'size_bytes': 4571141940},
}


class DownloadCancelled(RuntimeError):
    pass


class DownloadBusy(RuntimeError):
    pass


def check_cancel(event):
    if event is not None and event.is_set():
        raise DownloadCancelled('Download cancelled.')


def plan_index(session, variant):
    if variant not in CATALOG:
        raise ValueError('Unknown Index model format.')
    release = CATALOG[variant]
    repo, revision = release['repository'], release['revision']
    with session.get(f'https://huggingface.co/api/models/{repo}/revision/{revision}',
                     params={'blobs': 'true'}, timeout=(10, 15)) as response:
        response.raise_for_status()
        metadata = response.json()
    if metadata.get('sha') != revision:
        raise ValueError('Model revision mismatch.')
    files = []
    for item in metadata['siblings']:
        name = item['rfilename']
        if name == '.gitattributes':
            continue
        if variant != 'hf' and name not in (release['filename'], 'README.md', 'SHA256SUMS.txt'):
            continue
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or not path.parts:
            raise ValueError('Invalid model file path.')
        if variant == 'hf' and path.suffix not in ('.json', '.txt', '.md', '.safetensors', '.model'):
            continue
        size = item['size']
        if type(size) is not int or not 0 <= size <= 16 * 1024 ** 3:
            raise ValueError('Invalid model file size.')
        lfs = item.get('lfs')
        algorithm, digest = ('sha256', lfs['sha256']) if lfs else ('git-sha1', item['blobId'])
        if not re.fullmatch(r'[0-9a-f]{64}' if lfs else r'[0-9a-f]{40}', digest):
            raise ValueError('Invalid model checksum.')
        files.append({'name': name, 'size': size, 'algorithm': algorithm, 'digest': digest,
                      'url': f'https://huggingface.co/{repo}/resolve/{revision}/{quote(name, safe="/")}'})
    expected = release.get('filename', 'model.safetensors')
    if not any(item['name'] == expected for item in files):
        raise ValueError('Model weights are missing from the official release.')
    if variant == 'hf' and not {'config.json', 'tokenizer.json', 'tokenizer_config.json'} <= {item['name'] for item in files}:
        raise ValueError('Model configuration or tokenizer is missing.')
    return release, files


def verified(path, resource, event=None):
    if not path.is_file() or path.stat().st_size != resource['size']:
        return False
    digest = hashlib.sha256() if resource['algorithm'] == 'sha256' else hashlib.sha1()
    if resource['algorithm'] == 'git-sha1':
        digest.update(f"blob {resource['size']}\0".encode())
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            check_cancel(event)
            digest.update(chunk)
    return digest.hexdigest() == resource['digest']


def download_resource(session, resource, destination, progress, event=None):
    path = destination / resource['name']
    if not path.resolve().is_relative_to(destination.resolve()):
        raise ValueError('Model file is outside the destination.')
    path.parent.mkdir(parents=True, exist_ok=True)
    progress(0, 'verifying')
    if verified(path, resource, event):
        progress(resource['size'], 'verifying')
        return path
    partial = path.with_name('.' + path.name + '.' + resource['digest'][:12] + '.part')
    if partial.is_symlink():
        raise ValueError('Invalid partial model file.')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > resource['size']:
        partial.unlink(missing_ok=True)
        offset = 0
    if offset == resource['size'] and verified(partial, resource, event):
        partial.chmod(0o644)
        partial.replace(path)
        progress(resource['size'], 'verifying')
        return path
    if offset == resource['size']:
        partial.unlink(missing_ok=True)
        offset = 0
    if shutil.disk_usage(destination).free < resource['size'] - offset + 16 * 1024 * 1024:
        raise OSError('Insufficient disk space for model download.')
    check_cancel(event)
    headers = {'Accept-Encoding': 'identity'}
    if offset:
        headers['Range'] = f'bytes={offset}-'
    with session.get(resource['url'], headers=headers, stream=True, timeout=(10, 15)) as response:
        response.raise_for_status()
        if response.headers.get('Content-Encoding', 'identity') != 'identity':
            raise ValueError('Unexpected model transfer encoding.')
        if response.status_code == 206:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
            if not match or int(match[1]) != offset or int(match[3]) != resource['size'] or int(match[2]) != resource['size'] - 1:
                raise ValueError('Invalid model resume response.')
        elif response.status_code == 200:
            offset = 0  # The server ignored Range; start over instead of appending.
        else:
            raise ValueError('Unexpected model download response.')
        with partial.open('ab' if offset else 'wb') as handle:
            progress(offset, 'downloading')
            for chunk in response.iter_content(1024 * 1024):
                check_cancel(event)
                if not chunk:
                    continue
                if offset + len(chunk) > resource['size']:
                    raise ValueError('Model download exceeded its expected size.')
                handle.write(chunk)
                offset += len(chunk)
                progress(offset, 'downloading')
    progress(offset, 'verifying')
    if not verified(partial, resource, event):
        # A corrupt complete file cannot usefully be resumed.
        if offset == resource['size']:
            partial.unlink(missing_ok=True)
        raise ValueError('Model size or checksum verification failed.')
    partial.chmod(0o644)
    partial.replace(path)
    return path


@contextmanager
def _model_lock(destination):
    try:
        with exclusive_lock(destination / '.download.lock', blocking=False):
            yield
    except BlockingIOError as exc:
        raise DownloadBusy('This model destination is already being downloaded.') from exc


def download_index(destination, variant='gguf-q4', *, on_progress=None, cancel_event=None):
    destination = Path(destination).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with _model_lock(destination):
        check_cancel(cancel_event)
        with requests.Session() as session:
            if on_progress:
                on_progress({'stage': 'metadata', 'downloaded_bytes': 0, 'total_bytes': 0, 'file': None})
            release, resources = plan_index(session, variant)
            total, done = sum(item['size'] for item in resources), 0
            for index, resource in enumerate(resources):
                check_cancel(cancel_event)
                def progress(value, stage):
                    if on_progress:
                        on_progress({'stage': stage, 'downloaded_bytes': done + value,
                                     'total_bytes': total, 'file': resource['name'],
                                     'completed_files': index, 'total_files': len(resources)})
                download_resource(session, resource, destination, progress, cancel_event)
                done += resource['size']
            manifest = {'schema': 1, 'backend': 'INDEX', 'variant': variant,
                        'repository': release['repository'], 'revision': release['revision'],
                        'files': [{key: item[key] for key in ('name', 'size', 'algorithm', 'digest')} for item in resources]}
            check_cancel(cancel_event)
            with atomic_output(destination / 'ner-model.json') as path:
                path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            if on_progress:
                on_progress({'stage': 'done', 'downloaded_bytes': total, 'total_bytes': total,
                             'completed_files': len(resources), 'total_files': len(resources), 'file': None})
            return {'directory': str(destination), 'model_path': str(destination / release['filename'])
                    if 'filename' in release else str(destination), 'variant': variant,
                    'repository': release['repository'], 'revision': release['revision']}
