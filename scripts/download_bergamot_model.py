"""Download a Firefox translation model once, for subsequent offline use."""

import argparse
import gzip
import hashlib
from pathlib import Path
import shutil
import tempfile

import requests
import yaml


REGISTRY_URL = (
    'https://storage.googleapis.com/moz-fx-translations-data--303e-prod-translations-data/db/models.json'
)


def download_file(session, base_url, resource, destination):
    filename = Path(resource['path']).name
    output = destination / filename.removesuffix('.gz')
    compressed = None
    unpacked = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as handle:
            compressed = Path(handle.name)
            with session.get(base_url.rstrip('/') + '/' + resource['path'],
                             stream=True, timeout=120) as response:
                response.raise_for_status()
                for chunk in response.iter_content(1024 * 1024):
                    handle.write(chunk)
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as handle:
            unpacked = Path(handle.name)
            opener = gzip.open if filename.endswith('.gz') else open
            with opener(compressed, 'rb') as source:
                shutil.copyfileobj(source, handle)
        if resource.get('uncompressedSize') is not None:
            if unpacked.stat().st_size != resource['uncompressedSize']:
                raise ValueError(f'Unexpected model size: {filename}')
        if resource.get('uncompressedHash'):
            with unpacked.open('rb') as handle:
                digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            if digest != resource['uncompressedHash']:
                raise ValueError(f'Model checksum mismatch: {filename}')
        unpacked.replace(output)
        return output.name
    finally:
        for temporary in (compressed, unpacked):
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def download_model(destination, source='en', target='ja', model_index=None):
    destination = Path(destination).expanduser().resolve()
    with requests.Session() as session:
        response = session.get(REGISTRY_URL, timeout=60)
        response.raise_for_status()
        registry = response.json()
        candidates = registry['models'].get(f'{source}-{target}', [])
        if model_index is None:
            model = next((m for m in candidates if m.get('releaseStatus') == 'Release'), None)
            if model is None:
                raise ValueError(f'No released Firefox model for {source}-{target}.')
        else:
            if not 0 <= model_index < len(candidates):
                raise ValueError(f'Invalid model index for {source}-{target}: {model_index}')
            model = candidates[model_index]
        destination.mkdir(parents=True, exist_ok=True)
        files = model['files']
        downloaded = {}
        for key in ('model', 'vocab', 'srcVocab', 'trgVocab', 'lexicalShortlist'):
            if key in files:
                print(f'Downloading {key} for {source}-{target}...')
                downloaded[key] = download_file(session, registry['baseUrl'], files[key], destination)
    vocabs = ([downloaded['vocab']] * 2 if 'vocab' in downloaded
              else [downloaded['srcVocab'], downloaded['trgVocab']])
    config = {
        'relative-paths': True,
        'models': [downloaded['model']],
        'vocabs': vocabs,
        'beam-size': 1,
        'normalize': 1.0,
        'word-penalty': 0,
        'max-length-break': 128,
        'mini-batch-words': 1024,
        'workspace': 128,
        'max-length-factor': 2.0,
        'skip-cost': True,
        'cpu-threads': 1,
        'quiet': True,
        'quiet-translation': True,
        'ssplit-mode': 'paragraph',
        'ssplit-prefix-file': '',
        'gemm-precision': ('int8shiftAlphaAll' if 'alphas' in downloaded['model'] else 'int8shiftAll'),
    }
    if 'lexicalShortlist' in downloaded:
        config['shortlist'] = [downloaded['lexicalShortlist'], False]
    config_path = destination / 'config.yml'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding='utf-8')
    print(f'Model configuration: {config_path}')
    return config_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='models/bergamot/en-ja')
    parser.add_argument('--source', default='en')
    parser.add_argument('--target', default='ja')
    parser.add_argument('--model-index', type=int,
                        help='Explicit model candidate index; defaults to a released model.')
    args = parser.parse_args()
    download_model(args.output, args.source, args.target, args.model_index)


if __name__ == '__main__':
    main()
