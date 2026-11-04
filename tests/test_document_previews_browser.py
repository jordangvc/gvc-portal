"""Offline browser regression for all document previews.

Run explicitly: python -m pytest tests/test_document_previews_browser.py -q
Requires Playwright + Chromium. Every network request is intercepted; no portal,
Google, Stripe, Monday, or customer data is accessed.
"""
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

pw = pytest.importorskip('playwright.sync_api')
WEB = Path(__file__).resolve().parents[1] / 'web'

@pytest.fixture(scope='module')
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()

@pytest.mark.parametrize('width', [1440, 1024, 375])
@pytest.mark.parametrize('kind', ['estimate', 'invoice', 'change-order', 'coi'])
def test_preview_review_repeat_and_recovery(browser, width, kind):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    runs, errors = [], []
    state = {'status': 200, 'signed': kind == 'coi'}
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route_request(route):
        request = route.request
        path = urlsplit(request.url).path
        if path == f'/ui/api/{kind}/run':
            body = request.post_data_json
            runs.append(body)
            assert body['mode'] == 'dry-run', 'Preview must never finalize'
            wb = {'identifier': '2026-1104-001', 'filename': 'TEST-PREVIEW.pdf'}
            if state['signed']:
                wb['preview_pdf_url'] = 'https://preview.invalid/test.pdf?signature=unchanged'
            result = {'writeback': wb} if state['status'] == 200 else {'detail': {'code': 'TEST_ERROR', 'detail': 'Test failure. Retry preview.'}}
            route.fulfill(status=state['status'], json=result)
        elif path == '/ui/api/preview' or request.url.startswith('https://preview.invalid/'):
            # HTML stand-in avoids headless PDF viewer differences. This test
            # verifies the UI's URL, visibility, and requests, not PDF rendering.
            route.fulfill(content_type='text/html', body='<p>Synthetic PDF response</p>')
        elif path.startswith('/ui/api/'):
            route.fulfill(json={'ok': True, 'drafts': [], 'items': [], 'results': []})
        elif path.startswith('/ui/'):
            rel = path.removeprefix('/ui/')
            file = WEB / (rel if '.' in Path(rel).name else rel + '.html')
            if file.is_file():
                if file.suffix == '.html':
                    html = file.read_text(encoding='utf-8').replace('{{EMAIL_JSON}}', json.dumps('preview-test@example.invalid')).replace('{{EMAIL}}', 'preview-test@example.invalid')
                    assert not re.search(r'\{\{[A-Z_]+\}\}', html)
                    route.fulfill(content_type='text/html', body=html)
                else:
                    route.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            else:
                route.fulfill(status=404)
        else:
            route.fulfill(status=404)

    page.route('**/*', route_request)
    page.goto(f'https://portal.test/ui/{kind}')
    page.evaluate('''() => {
      const values = {client_name:'Test Contractor', client_email:'preview@example.invalid',
        customer_name:'Test Contractor', customer_email:'preview@example.invalid',
        job_name:'Synthetic preview job', base_number:'2026-1104-001', title:'Test additional work',
        holder_name:'Synthetic holder', holder_address:'100 Test Street', contact_name:'Test Contact',
        contact_email:'preview@example.invalid'};
      for (const [name,value] of Object.entries(values)) {
        const el = document.querySelector('[name="'+name+'"]'); if(el) el.value = value;
      }
      for (const el of document.querySelectorAll('.li-label, .li-desc')) el.value = 'Synthetic work';
      for (const el of document.querySelectorAll('.li-amount, .li-unit-price')) el.value = '125';
    }''')
    if kind == 'coi':
        page.locator('#btn-generate').click()
    else:
        # Actual stage control: entering Review must trigger the dry-run hook.
        page.locator('.gvc-stage[data-step="4"]').click()
    pw.expect(page.locator('#preview-frame')).to_be_visible()
    assert len(runs) == 1
    first = page.locator('#preview-frame').get_attribute('src')
    assert page.locator('#preview-frame').bounding_box()['width'] > 250
    if kind != 'coi':
        pw.expect(page.get_by_role('link', name='Open the preview PDF')).to_be_visible()
    field = 'holder_name' if kind == 'coi' else 'job_name'
    page.evaluate("field => document.querySelector('[name=\"'+field+'\"]').value = 'Updated synthetic job'", field)
    page.locator('#btn-generate').click()
    pw.expect(page.locator('#preview-frame')).to_be_visible()
    assert len(runs) == 2
    data_key = 'holder' if kind == 'coi' else 'job'
    assert runs[-1]['data'][data_key]['name'] == 'Updated synthetic job'
    if kind != 'coi':
        assert page.locator('#preview-frame').get_attribute('src') != first
        # Signed URLs must be passed through verbatim (adding a query breaks signing).
        state['signed'] = True
        page.locator('#btn-generate').click()
        pw.expect(page.locator('#preview-frame')).to_have_attribute('src', 'https://preview.invalid/test.pdf?signature=unchanged')
        state['status'] = 403
        page.locator('#btn-generate').click()
        pw.expect(page.locator('#status')).to_contain_text('TEST_ERROR')
        pw.expect(page.locator('#preview-frame')).not_to_be_visible()
        state['status'] = 200
        page.locator('#btn-generate').click()
        pw.expect(page.locator('#preview-frame')).to_be_visible()
        state['status'] = 401
        page.locator('#btn-generate').click()
        pw.expect(page.locator('#status')).to_contain_text('Session expired')
        pw.expect(page.locator('#preview-frame')).not_to_be_visible()
    assert not errors, errors
    page.close()
