"""Opt-in real Camoufox contract test, using only a loopback HTTP server.

CAMOUFOX_COLLECTION_INTEGRATION=1 pytest -q tests/test_collection_browser.py
Requires an installed browser; never downloads one as part of this test.
"""
import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import network, storage

pytestmark = pytest.mark.skipif(os.environ.get('CAMOUFOX_COLLECTION_INTEGRATION') != '1',
                                reason='opt-in installed Camoufox integration')


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'<html><body>Local collection fixture</body></html>'
        self.send_response(200)
        self.send_header('Content-Type','text/html')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def do_POST(self):
        data=self.rfile.read(int(self.headers['Content-Length'])).decode()
        if data=='A': time.sleep(.1)
        body=json.dumps({'label':data,'padding':'x'*100}).encode()
        self.send_response(201 if data=='A' else 202)
        self.send_header('Content-Type','application/json')
        self.send_header('Set-Cookie','fixture_'+data+'=demo; Path=/')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass


async def test_actual_browser_concurrent_capture_and_filtered_cookie_delete(monkeypatch):
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    manager=BrowserManager()
    monkeypatch.setattr(network,'browser_manager',manager)
    monkeypatch.setattr(storage,'browser_manager',manager)
    try:
        await manager.launch({'headless':True})
        page=await manager.get_active_page()
        await page.goto(f'http://127.0.0.1:{server.server_port}/')
        await network.network_capture('start',capture_body=True,max_body_size=20)
        await page.evaluate("async () => await Promise.all(['A','B'].map(body=>fetch('/api',{method:'POST',body}).then(r=>r.text())))")
        result=await network.network_capture('stop',wait_timeout_ms=5000)
        assert result['pending_responses']==0
        rows=list(manager._network_requests)
        assert [r['request_post_data'] for r in rows]==['A','B']
        assert [r['status'] for r in rows]==[201,202]
        assert all(r['headers_complete'] and 'set-cookie' in r['response_headers'] for r in rows)
        assert all(r['response_body_capture_truncated'] for r in rows)
        result=await storage.cookies('delete',name='fixture_A',domain='127.0.0.1')
        assert result['count']==1
        jar=await page.context.cookies()
        assert 'fixture_B' in {c['name'] for c in jar}
        assert 'fixture_A' not in {c['name'] for c in jar}
        # Exercise the pre-1.43 expiry path against real browser storage.
        await page.context.add_cookies([{'name':'keep','value':'demo','domain':'127.0.0.1','path':'/'}])
        class LegacyContext:
            async def cookies(self): return await page.context.cookies()
            async def add_cookies(self, values): await page.context.add_cookies(values)
            async def clear_cookies(self): raise AssertionError('must not clear jar')
        monkeypatch.setattr(storage,'browser_manager',SimpleNamespace(get_active_page=AsyncMock(return_value=SimpleNamespace(context=LegacyContext()))))
        assert (await storage.cookies('delete',name='fixture_B',domain='127.0.0.1'))['count']==1
        assert {c['name'] for c in await page.context.cookies()} == {'keep'}
    finally:
        await manager.close()
        server.shutdown();server.server_close();thread.join(timeout=2)
