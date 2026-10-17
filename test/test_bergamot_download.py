import gzip
import hashlib
import stat

import pytest
import yaml

from scripts.download_bergamot_model import download_file, download_model


class Response:
    def __init__(self, data=None, content=b''):
        self.data = data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def iter_content(self, chunk_size):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Session:
    def __init__(self, registry=None, content=b''):
        self.registry = registry
        self.content = content

    def get(self, url, **kwargs):
        return Response(data=self.registry, content=self.content)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_download_decompresses_and_verifies_published_model(tmp_path):
    model = b'local translation model'
    resource = {
        'path': 'models/en-ja/model.bin.gz',
        'uncompressedSize': len(model),
        'uncompressedHash': hashlib.sha256(model).hexdigest(),
    }
    name = download_file(Session(content=gzip.compress(model)), 'https://example.com', resource, tmp_path)
    assert (tmp_path / name).read_bytes() == model
    assert (tmp_path / name).stat().st_mode & stat.S_IROTH
    assert list(tmp_path.iterdir()) == [tmp_path / name]


def test_corrupt_download_preserves_previous_model(tmp_path):
    previous = tmp_path / 'model.bin'
    previous.write_bytes(b'previous good model')
    resource = {'path': 'model.bin.gz', 'uncompressedHash': '0' * 64}
    with pytest.raises(ValueError, match='checksum'):
        download_file(Session(content=gzip.compress(b'corrupt')), 'https://example.com', resource, tmp_path)
    assert previous.read_bytes() == b'previous good model'
    assert list(tmp_path.iterdir()) == [previous]


@pytest.mark.parametrize('split_vocab', [True, False])
def test_registry_model_with_split_or_shared_vocabulary(tmp_path, monkeypatch, split_vocab):
    files = {'model': {'path': 'model.alphas.bin.gz'}}
    if split_vocab:
        files.update({'srcVocab': {'path': 'source.spm.gz'}, 'trgVocab': {'path': 'target.spm.gz'}})
    else:
        files['vocab'] = {'path': 'shared.spm.gz'}
    registry = {'baseUrl': 'https://example.com', 'models': {'en-ja': [
        {'releaseStatus': None, 'files': {}},
        {'releaseStatus': 'Release', 'files': files},
    ]}}
    monkeypatch.setattr('scripts.download_bergamot_model.requests.Session',
                        lambda: Session(registry, gzip.compress(b'model data')))
    config_path = download_model(tmp_path)
    config = yaml.safe_load(config_path.read_text())
    assert config['vocabs'] == (['source.spm', 'target.spm'] if split_vocab else ['shared.spm'] * 2)
    assert config['relative-paths'] is True
    for filename in config['models'] + config['vocabs']:
        assert (config_path.parent / filename).is_file()


def test_missing_direction_does_not_create_model_directory(tmp_path, monkeypatch):
    registry = {'baseUrl': 'https://example.com', 'models': {}}
    monkeypatch.setattr('scripts.download_bergamot_model.requests.Session', lambda: Session(registry))
    destination = tmp_path / 'missing'
    with pytest.raises(ValueError, match='No released Firefox model'):
        download_model(destination)
    assert not destination.exists()
