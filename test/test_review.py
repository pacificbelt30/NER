import json
import shutil
import time

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

import api_server
from ner.auth import AuthStore
from ner.errors import ProtectedContentError
from ner.review import (DRAFT, MANIFEST, load_manifest, load_review, render_review,
                        save_manifest, validate_decisions, write_json)
from ner.textra_api import TextraConnection
from ner.text_blocks import TranslationBlock
from ner.translation import BACKENDS, TranslationService


class Backend:
    def __init__(self, config):
        self.unknown = config.get('unknown', False)

    def translate_chunk(self, text, **kwargs):
        if 'X0' in text:
            return '確認先は X99 です。' if self.unknown else '確認先は X0/broken です。'
        return '正常に翻訳できた別の段落です。'

    def close(self):
        pass


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setitem(BACKENDS, 'INDEX', Backend)


def source_pdf(path, *, skew=False):
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=500)
        kwargs = {'morph': (fitz.Point(40, 80), fitz.Matrix(25))} if skew else {}
        page.insert_text((40, 80), 'Please read https://example.org/guide for more information.', **kwargs)
        page.insert_text((40, 260), 'Another paragraph should remain translated throughout the review.')
        doc.save(path)


def seed(workspace, *, skew=False):
    workspace.mkdir(exist_ok=True)
    source_pdf(workspace / 'input.pdf', skew=skew)
    with TextraConnection(workspace / 'input.pdf', config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        job.post_page()
        job.insert()
        save_manifest(job, workspace)
        return job.result_summary, [warning.view() for warning in job.warnings]


def child(parent, destination, action, text=None):
    destination.mkdir()
    for name in ('input.pdf', MANIFEST):
        shutil.copyfile(parent / name, destination / name)
    item = load_review(parent)['items'][0]
    write_json(destination / DRAFT, {'revision': 0, 'decisions': [
        {'id': item['id'], 'action': action, 'text': text},
    ]})
    return destination


def test_rejected_candidate_saved_privately_and_unknown_tokens_do_not_crash(tmp_path, backend):
    parent = tmp_path / 'parent'
    seed(parent)
    item = load_review(parent)['items'][0]
    assert item['candidate'] == '確認先は https://example.org/guide/broken です。'
    assert item['candidate_complete'] and item['can_adopt']
    assert not item['candidate_validated']
    assert item['literals'][0]['href'] == 'https://example.org/guide'
    with fitz.open(parent / 'input_new.pdf') as doc:
        assert '/broken' not in ''.join(page.get_text() for page in doc)
        assert '/broken' not in str([doc.embfile_get(name) for name in doc.embfile_names()])
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {'unknown': True}}) as service:
        with pytest.raises(ProtectedContentError) as exc:
            service.translate_block(TranslationBlock('block', 'See https://example.org now.'))
        assert '[[NER_99]]' in exc.value.candidate
        assert exc.value.complete
        assert not service.cache


def test_incomplete_candidate_cannot_be_adopted(tmp_path, backend):
    with TranslationService({'PRIORITY': 'INDEX', 'INDEX': {'CHUNK_CHARS': 50}}) as service:
        with pytest.raises(ProtectedContentError) as exc:
            service.translate_block(TranslationBlock('block', 'Read https://example.org now. ' + 'More prose. ' * 30))
        assert not exc.value.complete
    parent = tmp_path / 'parent'
    seed(parent)
    data = load_manifest(parent)
    key = load_review(parent)['items'][0]['id']
    data['candidates'][key]['complete'] = False
    with pytest.raises(ValueError, match='不完全'):
        validate_decisions(data, [{'id': key, 'action': 'adopt', 'text': None}])


@pytest.mark.parametrize('action', ['original', 'adopt', 'edit'])
def test_offline_review_preserves_source_successful_blocks_and_links(tmp_path, backend, monkeypatch, action):
    parent = tmp_path / 'parent'
    seed(parent)
    before = {name: (parent / name).read_bytes() for name in ('input.pdf', 'input_new.pdf', MANIFEST)}
    workspace = child(parent, tmp_path / 'child', action,
                      '修正した確認先は https://example.net/fixed です。' if action == 'edit' else None)
    class NeverTranslate:
        def __init__(self, *args):
            pytest.fail('A human review must never initialize a translation backend.')
    monkeypatch.setitem(BACKENDS, 'GOOGLE', NeverTranslate)
    output, result = render_review(workspace)
    assert result['warnings'] == []
    assert result['result_summary']['retained_blocks'] == (1 if action == 'original' else 0)
    assert load_review(workspace)['items'][0]['applied'] is True
    with fitz.open(output) as doc:
        assert doc.page_count == 1
        text = ''.join(doc[0].get_text().split())
        assert '正常に翻訳できた別の段落です。' in text
        assert json.loads(doc.embfile_get('ner-human-review.json'))['decisions'][0]['action'] == action
        links = [link.get('uri') for link in doc[0].get_links()]
        if action == 'original':
            assert 'Pleasereadhttps://example.org/guide' in text
        elif action == 'adopt':
            assert 'https://example.org/guide/broken' in text
            assert 'https://example.org/guide/broken' in links
        else:
            assert 'https://example.net/fixed' in text
            assert 'https://example.net/fixed' in links
            assert 'https://example.org/guide' not in text
            assert 'https://example.org/guide' not in links
    assert {name: (parent / name).read_bytes() for name in before} == before


def test_failed_manual_placement_stays_unresolved_and_can_be_reviewed_again(tmp_path, backend):
    parent = tmp_path / 'parent'
    seed(parent, skew=True)
    workspace = child(parent, tmp_path / 'child', 'edit', '手修正しても斜めの本文は安全に配置できません。')
    output, result = render_review(workspace)
    assert any(warning['code'] == 'LAYOUT_FAILED' for warning in result['warnings'])
    item = load_review(workspace)['items'][0]
    assert item['applied'] is False
    assert item['decision']['text'].startswith('手修正')
    with fitz.open(output) as doc:
        assert 'Pleaseread' in ''.join(doc[0].get_text().split())
        assert '手修正' in ''.join(''.join(page.get_text().split()) for page in list(doc)[1:])
    next_workspace = child(workspace, tmp_path / 'next', 'original')
    _, next_result = render_review(next_workspace)
    assert not next_result['warnings']
    assert load_review(next_workspace)['items'][0]['applied'] is True


def test_review_rejects_changed_source_pdf(tmp_path, backend):
    parent = tmp_path / 'parent'
    seed(parent)
    workspace = child(parent, tmp_path / 'child', 'original')
    with fitz.open(workspace / 'input.pdf') as doc:
        doc[0].insert_text((40, 400), 'Different source PDF')
        doc.save(workspace / 'changed.pdf')
    (workspace / 'changed.pdf').replace(workspace / 'input.pdf')
    with pytest.raises(ValueError, match='does not match'):
        render_review(workspace)


@pytest.fixture
def api(monkeypatch, tmp_path, backend):
    root = tmp_path / 'server'
    monkeypatch.setattr(api_server, 'base', str(root))
    monkeypatch.setattr(api_server, 'selected_config', lambda backend=None: {'PRIORITY': backend or 'INDEX'})
    AuthStore.provision(root, 'admin', 'review-test-password')
    async def processor(workspace, config, output):
        return api_server.translation_job_worker(workspace, config, output)
    with TestClient(api_server.app) as client:
        client.post('/api/auth/login', data={'username': 'admin', 'password': 'review-test-password'})
        api_server.app.state.jobs.processor = processor
        path = tmp_path / 'upload.pdf'
        source_pdf(path)
        response = client.post('/api/jobs', files={'file': ('review.pdf', path.read_bytes())}, data={'backend': 'INDEX'})
        assert response.status_code == 202
        job_id = response.json()['id']
        wait_finished(client, job_id)
        yield client, job_id


def wait_finished(client, job_id):
    for _ in range(100):
        job = client.get(f'/api/jobs/{job_id}').json()
        if job['status'] in ('succeeded', 'failed', 'cancelled'):
            assert job['status'] == 'succeeded', job
            return job
        time.sleep(.01)
    pytest.fail('Review job did not finish')


def test_api_draft_conflicts_generation_idempotency_and_restart(api):
    client, job_id = api
    endpoint = f'/api/jobs/{job_id}/review'
    response = client.get(endpoint)
    assert response.headers['cache-control'] == 'no-store'
    data = response.json()
    assert data['revision'] == 0
    decision = {'id': data['items'][0]['id'], 'action': 'edit', 'text': '確認先 https://example.net/fixed です。'}
    draft = {'revision': 0, 'decisions': [decision]}
    saved = client.put(endpoint, json=draft)
    assert saved.status_code == 200
    assert saved.json()['revision'] == 1
    assert client.get(endpoint).json()['items'][0]['decision'] == decision
    assert client.put(endpoint, json=draft).status_code == 409
    assert client.post(endpoint, json={'revision': 0, 'submission_key': 'old-revision'}).status_code == 409
    submission = {'revision': 1, 'submission_key': 'review-key-123'}
    response = client.post(endpoint, json=submission)
    assert response.status_code == 202, response.text
    child_job = wait_finished(client, response.json()['id'])
    assert child_job['kind'] == 'review' and child_job['parent_id'] == job_id
    assert child_job['warnings'] == []
    assert child_job['downloads']['comparison']
    assert client.post(endpoint, json=submission).json()['id'] == child_job['id']
    manager = api_server.app.state.jobs
    restarted = api_server.JobManager(manager.root, manager.processor)
    restarted.start()
    api_server.app.state.jobs = restarted
    assert client.get(endpoint).json()['items'][0]['decision'] == decision
    assert client.get(f"/api/jobs/{child_job['id']}/review").json()['items'][0]['applied'] is True
    assert client.get(f"/api/jobs/{child_job['id']}").json()['parent_id'] == job_id
    assert client.get(child_job['downloads']['translated']).status_code == 200
    assert client.get(f'/api/jobs/{job_id}').json()['warnings']


@pytest.mark.parametrize('decision', [
    {'id': 'unknown', 'action': 'original'},
    {'action': 'edit', 'text': '   '},
    {'action': 'edit', 'text': '[[NER_0]]'},
    {'action': 'edit', 'text': 'bad\x00text'},
    {'action': 'adopt', 'text': 'unexpected override'},
    {'action': 'arbitrary'},
    {'action': 'edit', 'text': 'a' * 20001},
    {'action': 'original', 'extra': True},
])
def test_api_rejects_invalid_decisions_without_losing_draft(api, decision):
    client, job_id = api
    endpoint = f'/api/jobs/{job_id}/review'
    key = client.get(endpoint).json()['items'][0]['id']
    response = client.put(endpoint, json={'revision': 0, 'decisions': [{'id': key, **decision}]})
    assert response.status_code == 422, response.text
    assert client.get(endpoint).json()['revision'] == 0


def test_api_rejects_duplicates_empty_generation_and_unavailable_jobs(api):
    client, job_id = api
    endpoint = f'/api/jobs/{job_id}/review'
    key = client.get(endpoint).json()['items'][0]['id']
    assert client.put(endpoint, json={'revision': 0, 'decisions': [
        {'id': key, 'action': 'original'}, {'id': key, 'action': 'original'},
    ]}).status_code == 422
    assert client.post(endpoint, json={'revision': 0, 'submission_key': 'empty'}).status_code == 422
    assert client.get('/api/jobs/does-not-exist/review').status_code == 404
    assert client.put(endpoint, json={'revision': 0, 'decisions': []},
                      headers={'Origin': 'https://untrusted.example'}).status_code == 403
    client.post('/api/auth/logout')
    assert client.get(endpoint).status_code == 401


def test_legacy_history_can_prepare_editable_new_translation(api):
    client, job_id = api
    path = api_server.app.state.jobs.root / job_id / MANIFEST
    path.unlink()
    endpoint = f'/api/jobs/{job_id}/review'
    assert client.get(endpoint).json()['available'] is False
    response = client.post(endpoint + '/prepare')
    assert response.status_code == 202
    child_job = wait_finished(client, response.json()['id'])
    assert child_job['parent_id'] == job_id
    assert client.get(f"/api/jobs/{child_job['id']}/review").json()['available'] is True
    assert client.post(f"/api/jobs/{child_job['id']}/review/prepare").status_code == 409
    assert client.get(f'/api/jobs/{job_id}/files/translated').status_code == 200


def test_unchanged_pdf_link_label_keeps_its_original_destination(tmp_path, backend):
    from ner.review import edited_result
    from ner.text_blocks import restore_text
    block = {'id': 'label', 'txt': 'Read the project site for details.', 'protected_ranges': [
        {'start': 9, 'end': 21, 'kind': 'link', 'href': 'https://example.org/destination'}]}
    result = edited_result(block, '詳細は project site を参照してください。')
    assert result.spans[0].href == 'https://example.org/destination'
    assert restore_text(result.canonical_text, result.spans) == result.text


def test_saved_new_draft_is_not_claimed_as_already_applied(tmp_path, backend):
    parent = tmp_path / 'parent'
    seed(parent)
    workspace = child(parent, tmp_path / 'child', 'edit', '修正した確認先は https://example.net/fixed です。')
    render_review(workspace)
    item = load_review(workspace)['items'][0]
    assert item['applied'] is True
    write_json(workspace / DRAFT, {'revision': 1, 'decisions': [
        {'id': item['id'], 'action': 'edit', 'text': 'まだPDFに反映していない下書きです。'}]})
    assert load_review(workspace)['items'][0]['applied'] is None


def test_review_retry_uses_saved_edits_without_a_translation_backend(api, monkeypatch):
    client, job_id = api
    endpoint = f'/api/jobs/{job_id}/review'
    key = client.get(endpoint).json()['items'][0]['id']
    client.put(endpoint, json={'revision': 0, 'decisions': [{'id': key, 'action': 'original'}]})
    response = client.post(endpoint, json={'revision': 1, 'submission_key': 'retry-review'})
    child_id = response.json()['id']
    wait_finished(client, child_id)
    manager = api_server.app.state.jobs
    manager.jobs[child_id]['status'] = 'failed'
    manager._save(manager.jobs[child_id])
    monkeypatch.setattr(api_server, 'selected_config', lambda *args: pytest.fail('Review retry must work offline'))
    response = client.post(f'/api/jobs/{child_id}/retry')
    assert response.status_code == 202
    retried = wait_finished(client, response.json()['id'])
    assert retried['kind'] == 'review' and retried['parent_id'] == child_id
    assert not retried['warnings']


def test_manual_html_is_literal_text_not_injected_links(tmp_path, backend):
    parent = tmp_path / 'parent'
    seed(parent)
    workspace = child(parent, tmp_path / 'child', 'edit', '確認済み <a href="https://malicious.example">リンク</a> の説明です。')
    output, result = render_review(workspace)
    assert not result['warnings']
    with fitz.open(output) as doc:
        assert '<a' in doc[0].get_text()
        # The literal URL in the text may be clickable, but HTML itself never
        # changes the displayed label or creates an injected anchor element.
        assert 'href=' in doc[0].get_text()
        assert 'リンク</a>' in ''.join(doc[0].get_text().split())


def test_table_cell_edit_preserves_grid_and_contains_clickable_url(tmp_path, backend):
    parent = tmp_path / 'table'
    parent.mkdir()
    with fitz.open() as doc:
        page = doc.new_page(width=600, height=500)
        for x in (40, 300, 560):
            page.draw_line((x, 50), (x, 210))
        for y in (50, 100, 210):
            page.draw_line((40, y), (560, y))
        page.insert_text((50, 80), 'Reference information')
        page.insert_text((310, 80), 'Additional description')
        page.insert_textbox((50, 120, 290, 200), 'Please read https://example.org/guide for more information.')
        page.insert_textbox((310, 120, 550, 200), 'Another paragraph should remain translated throughout the review.')
        # Uploaded PDFs may already have a previous review attachment.
        doc.embfile_add('ner-human-review.json', b'previous document audit')
        doc.save(parent / 'input.pdf')
    with TextraConnection(parent / 'input.pdf', config={'PRIORITY': 'INDEX'}) as job:
        job.get_pdf_info()
        assert job.pe.table_regions[0]
        job.post_page()
        job.insert()
        save_manifest(job, parent)
    item = next(item for item in load_review(parent)['items'] if item['code'] == 'PROTECTED_CONTENT_INVALID')
    workspace = child(parent, tmp_path / 'edited', 'edit', '詳細は https://example.net/fixed を確認してください。')
    assert load_review(parent)['items'][0]['id'] == item['id']
    output, result = render_review(workspace)
    assert not result['warnings']
    with fitz.open(parent / 'input.pdf') as original, fitz.open(output) as doc:
        drawings = doc[0].get_drawings()
        assert all(drawing in drawings for drawing in original[0].get_drawings())
        assert doc.embfile_get('ner-human-review.json') == b'previous document audit'
        assert 'ner-human-review-2.json' in doc.embfile_names()
        cell = fitz.Rect(40, 100, 300, 210)
        links = [link for link in doc[0].get_links() if link.get('uri') == 'https://example.net/fixed']
        assert links and all(cell.contains(link['from']) for link in links)
        assert 'https://example.net/fixed' in ''.join(doc[0].get_textbox(cell).split())
