"""Run CLI, real HTTP jobs, and a real Chromium UI against local Bergamot.

Requires playwright (and its Chromium runtime). All config, uploads, and browser
profiles are isolated in a temporary directory; the local server is stopped on exit.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import pymupdf as fitz
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_pdf_layout import fixture


def wait_for_job(session, base, job_id, statuses=('succeeded', 'failed', 'cancelled'), timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = session.get(f'{base}/api/jobs/{job_id}', timeout=10)
        response.raise_for_status()
        value = response.json()
        if value['status'] in statuses:
            return value
        time.sleep(0.05)
    raise AssertionError(f'Job did not reach {statuses}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    config = {'PRIORITY': 'BERGAMOT', 'BERGAMOT': {
        'WORKER_BINARY': str(args.worker.resolve()), 'MODEL_CONFIG': str(args.model.resolve())}}
    with tempfile.TemporaryDirectory(prefix='ner-e2e-') as directory:
        workspace = Path(directory)
        source = workspace / 'sample.pdf'
        fixture(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        cli = [sys.executable, str(ROOT / 'launcher.py')]
        info = subprocess.run([*cli, 'info', str(source)], cwd=workspace, capture_output=True, text=True, timeout=30)
        assert info.returncode == 0 and 'Pages: 2' in info.stdout
        (workspace / 'config.yml').write_text(yaml.safe_dump(config))
        for command in ('translate', 'combine'):
            result = subprocess.run([*cli, command, str(source)], cwd=workspace,
                                    capture_output=True, text=True, timeout=90)
            assert result.returncode == 0, result.stderr
        with fitz.open(workspace / 'sample_new.pdf') as translated:
            assert translated.page_count == 2
            text = ''.join(page.get_text() for page in translated)
            assert 'translate_document_without_network' in text
            assert 'https://docs.example.org/translation/local-models?lang=en' in text
        with fitz.open(workspace / 'sample_all.pdf') as combined:
            assert len(combined) == 4
            assert 'Local translation' in combined[0].get_text()
        bad = subprocess.run([*cli, 'translate', str(workspace / 'absent.pdf')], cwd=workspace,
                             capture_output=True, text=True, timeout=30)
        assert bad.returncode != 0
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
        report['cli'] = {'info_without_config': True, 'native_translation': True,
                         'alternating_comparison': True, 'missing_file_nonzero': True, 'source_unchanged': True}

        # Arbitrary skew is intentionally unsupported by the placement engine.
        # Real Bergamot can translate it, and its full validated output must be
        # retained in the appendix while another block still translates normally.
        recovery_source = workspace / 'recovery.pdf'
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((60, 160), 'This rotated paragraph cannot be placed safely.',
                             morph=(fitz.Point(60, 160), fitz.Matrix(25)))
            page.insert_text((60, 260), 'This paragraph can be translated normally.')
            document.save(recovery_source)
        recovery_digest = hashlib.sha256(recovery_source.read_bytes()).hexdigest()
        recovery_cli = subprocess.run([*cli, 'translate', str(recovery_source)], cwd=workspace,
                                      capture_output=True, text=True, timeout=90)
        assert recovery_cli.returncode == 0 and 'Warning:' in recovery_cli.stderr, recovery_cli.stderr
        with fitz.open(workspace / 'recovery_new.pdf') as document:
            assert len(document) == 2
            assert 'This rotated paragraph' in document[0].get_text()
            assert '翻訳エラーレポート' in ''.join(document[1].get_text().split())
        assert hashlib.sha256(recovery_source.read_bytes()).hexdigest() == recovery_digest
        report['cli']['partial_failure_still_outputs_pdf'] = True

        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = dict(os.environ, NER_CONFIG_PATH=str(workspace / 'config.yml'),
                   NER_STORAGE_PATH=str(workspace / 'storage'))
        with (workspace / 'server.log').open('w+') as log:
            server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api_server:app',
                                       '--host', '127.0.0.1', '--port', str(port)],
                                      cwd=ROOT, env=env, stdout=log, stderr=log)
            session = requests.Session()
            session.trust_env = False
            try:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        response = session.get(base + '/api/capabilities', timeout=1)
                        if response.ok:
                            break
                    except requests.RequestException:
                        pass
                    assert server.poll() is None and time.monotonic() < deadline
                    time.sleep(0.05)
                payload = source.read_bytes()
                def submit(content=payload, filename='sample.pdf', key=None):
                    response = session.post(base + '/api/jobs', files={'file': (filename, content, 'application/pdf')},
                        data={'backend': 'BERGAMOT', 'output': 'comparison'},
                        headers={'Idempotency-Key': key} if key else {}, timeout=30)
                    assert response.status_code == 202, response.text
                    return response.json()
                receipt = submit(key='e2e-receipt')
                assert submit(key='e2e-receipt')['id'] == receipt['id']
                result = wait_for_job(session, base, receipt['id'])
                assert result['status'] == 'succeeded', result
                assert result['progress']['completed_blocks'] == result['progress']['total_blocks']
                assert 'total_chunks' in result['progress']
                for kind, relative in result['downloads'].items():
                    response = session.get(base + relative, timeout=30)
                    assert response.status_code == 200
                    with fitz.open(stream=response.content, filetype='pdf') as document:
                        assert len(document) == (4 if kind == 'comparison' else 2)
                    (output / f'http-{kind}.pdf').write_bytes(response.content)
                png = session.get(base + f'/api/jobs/{receipt["id"]}/preview/translated?page=2', timeout=30)
                assert png.ok and png.content.startswith(b'\x89PNG')
                invalid = session.post(base + '/api/jobs', files={'file': ('invalid.pdf', b'not PDF')}, timeout=10)
                assert invalid.status_code == 202
                invalid_job = wait_for_job(session, base, invalid.json()['id'])
                assert invalid_job['status'] == 'failed' and invalid_job['error']['code'] == 'INVALID_PDF'

                # A unique long document gives time to cancel a genuinely running job.
                long_source = workspace / 'cancel.pdf'
                with fitz.open() as document:
                    for index in range(20):
                        page = document.new_page()
                        text = (f'The document number is {index}. '
                            'Offline translation keeps private documents on this computer. ' * 20)
                        assert page.insert_textbox((40, 40, 550, 790), text, fontsize=10) >= 0
                    document.save(long_source)
                cancellable = submit(long_source.read_bytes(), 'cancel.pdf')
                running = wait_for_job(session, base, cancellable['id'], statuses=('running',))
                cancel = session.post(base + f'/api/jobs/{running["id"]}/cancel', timeout=10)
                assert cancel.ok
                stopped = wait_for_job(session, base, running['id'])
                assert stopped['status'] == 'cancelled' and stopped['downloads'] is None
                retry = session.post(base + f'/api/jobs/{running["id"]}/retry', timeout=10)
                assert retry.status_code == 202
                retried = wait_for_job(session, base, retry.json()['id'])
                assert retried['status'] == 'succeeded', retried
                report['http'] = {'native_job_and_downloads': True, 'duplicate_receipt': True,
                    'page_preview': True, 'invalid_pdf_job_failed': True, 'running_cancel': True, 'retry': True}

                recovered = wait_for_job(session, base,
                    submit(recovery_source.read_bytes(), 'recovery.pdf')['id'])
                assert recovered['status'] == 'succeeded', recovered
                assert recovered['result_summary']['outcome'] == 'partial', recovered
                assert recovered['result_summary']['translated_blocks'] == 1
                assert recovered['warnings'][0]['code'] == 'LAYOUT_FAILED'
                for kind, pages in (('translated', 2), ('comparison', 3)):
                    response = session.get(base + recovered['downloads'][kind], timeout=30)
                    assert response.ok
                    with fitz.open(stream=response.content, filetype='pdf') as document:
                        assert len(document) == pages
                        assert '翻訳エラーレポート' in ''.join(document[-1].get_text().split())
                    (output / f'recovery-{kind}.pdf').write_bytes(response.content)
                report['http']['partial_failure_download_and_appendix'] = True

                from playwright.sync_api import sync_playwright
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
                    try:
                        page = browser.new_page(viewport={'width': 1280, 'height': 960})
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(base)
                        page.wait_for_function('!document.querySelector("#backend").disabled')
                        page.locator('#pdf-file').set_input_files(str(source))
                        page.locator('#start-button').click()
                        page.locator('#results').wait_for(state='visible', timeout=90000)
                        def check_preview():
                            page.wait_for_function('''["original", "translated"].every(kind => {
                              const image = document.querySelector(`#${kind}-frame`);
                              return image.complete && image.naturalWidth > 0;
                            })''')
                            page.locator('#translated-frame').scroll_into_view_if_needed()
                            counts = page.evaluate('''async () => {
                              const counts = [];
                              for (const kind of ["original", "translated"]) {
                                const image = document.querySelector(`#${kind}-frame`);
                                await image.decode();
                                const canvas = document.createElement("canvas");
                                canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
                                const context = canvas.getContext("2d");
                                context.drawImage(image, 0, 0);
                                const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
                                let content = 0;
                                for (let i = 0; i < pixels.length; i += 4)
                                  if (Math.min(pixels[i], pixels[i+1], pixels[i+2]) < 240) content++;
                                counts.push(content);
                              }
                              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                              return counts;
                            }''')
                            assert all(count > 1000 for count in counts), counts
                        check_preview()
                        assert '翻訳が完了しました' in page.locator('#status-title').inner_text()
                        page.screenshot(path=str(output / 'desktop.png'), full_page=True)
                        page.reload()
                        page.locator('#results').wait_for(state='visible', timeout=30000)
                        page.set_viewport_size({'width': 390, 'height': 844})
                        check_preview()
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        page.screenshot(path=str(output / 'mobile.png'), full_page=True)
                        assert not errors, errors
                        report['browser'] = {'actual_upload_and_translation': True, 'png_preview': True,
                                             'decoded_previews_have_content': True,
                                             'reload_recovery': True, 'mobile_no_overflow': True, 'javascript_errors': errors}
                        page.locator('#new-file-button').click()
                        page.locator('#pdf-file').set_input_files(str(recovery_source))
                        page.locator('#start-button').click()
                        page.locator('#results').wait_for(state='visible', timeout=90000)
                        assert '注意付きPDFを作成しました' in page.locator('#status-title').inner_text()
                        assert '1 / 2 ブロック' in page.locator('#progress-detail').inner_text()
                        check_preview()
                        with page.expect_download() as download:
                            page.locator('#download-translated').click()
                        download.value.save_as(str(output / 'browser-recovery.pdf'))
                        with fitz.open(output / 'browser-recovery.pdf') as document:
                            assert len(document) == 2
                        page.screenshot(path=str(output / 'recovery-mobile.png'), full_page=True)
                        page.reload()
                        page.locator('#results').wait_for(state='visible', timeout=30000)
                        assert '注意付きPDFを作成しました' in page.locator('#status-title').inner_text()
                        assert not errors, errors
                        report['browser']['partial_failure_download_and_reload'] = True
                    finally:
                        browser.close()
            finally:
                session.close()
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
                log.flush()
                (output / 'server.log').write_text((workspace / 'server.log').read_text())
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
