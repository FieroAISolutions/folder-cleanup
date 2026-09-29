"""Optional browser checks: pip install playwright (uses installed Microsoft Edge)."""
import threading

import pytest

import jobs
import webui


def test_browser_keeper_selection_apply_and_undo(tmp_path, monkeypatch):
    playwright = pytest.importorskip('playwright.sync_api')
    monkeypatch.setattr(jobs, 'LOG_PATH', tmp_path / 'activity.jsonl')
    monkeypatch.setattr(jobs, 'choose_folder', lambda: {'folder': str(tmp_path / 'files')})
    root = tmp_path / 'files'
    root.mkdir()
    for name in ['a.txt', 'b.txt', 'c.txt']:
        (root / name).write_text('same content')
    server = webui.ThreadingHTTPServer(('127.0.0.1', 0), webui.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel='msedge', headless=True)
            except playwright.Error:
                pytest.skip('Microsoft Edge browser is not installed')
            page = browser.new_page(viewport={'width': 1280, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.goto(f'http://127.0.0.1:{server.server_port}')
            page.get_by_role('button', name='Browse for folder').click()
            playwright.expect(page.locator('#folder')).to_have_value(str(root))
            page.locator('#duplicates').check()
            page.locator('#prev').click()
            playwright.expect(page.locator('#groups fieldset')).to_have_count(1)
            page.get_by_label('Keep c.txt', exact=False).check()
            playwright.expect(page.locator('#apply')).to_be_disabled()
            page.locator('#revise').click()
            playwright.expect(page.locator('#actions tr')).to_have_count(2)
            playwright.expect(page.get_by_label('Keep c.txt', exact=False)).to_be_checked()
            page.get_by_label('Apply recover to b.txt', exact=True).uncheck()
            playwright.expect(page.locator('#summary')).to_contain_text('1 duplicate copies')
            page.screenshot(path=str(tmp_path / 'preview.png'), full_page=True)
            page.locator('#apply').click()
            playwright.expect(page.locator('#undo')).to_be_enabled()
            assert not (root / 'a.txt').exists()
            assert (root / 'b.txt').exists() and (root / 'c.txt').exists()
            page.locator('#undo').click()
            playwright.expect(page.locator('#status')).to_have_text('Cleanup restored.')
            assert (root / 'a.txt').read_text() == 'same content'
            page.reload()
            playwright.expect(page.locator('#historyList')).to_contain_text('apply · complete')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            assert not errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
