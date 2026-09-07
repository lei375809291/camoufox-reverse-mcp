"""General capture/storage/verification contracts; no external sites required."""
import asyncio
import json
import re
import shutil
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import network, storage, verification
from camoufox_reverse_mcp.utils.domains import domain_matches


class Request:
    def __init__(self, body='A', url='https://example.test/api'):
        self.url, self.post_data = url, body
        self.method, self.resource_type = 'POST', 'fetch'
        self.headers = {'content-type': 'text/plain'}
        self.all_headers = AsyncMock(return_value={**self.headers, 'cookie': 'demo=secret'})
        self.failure = 'connection reset'


class Response:
    def __init__(self, request, body=b'payload', status=200):
        self.request, self.url, self.status = request, request.url, status
        self.headers = {'content-type': 'text/plain'}
        self.all_headers = AsyncMock(return_value={**self.headers, 'set-cookie': 'demo=secret'})
        self.body = AsyncMock(return_value=body)


@pytest.fixture
def manager(monkeypatch):
    manager = BrowserManager()
    monkeypatch.setattr(network, 'browser_manager', manager)
    return manager


async def settle(manager):
    if manager._capture_tasks:
        await asyncio.gather(*manager._capture_tasks.values())
    await asyncio.sleep(0)


@pytest.mark.parametrize('order', [(0, 1), (1, 0)])
async def test_same_url_concurrency_and_complete_headers(manager, order):
    await network.network_capture('start', capture_body=True)
    requests = [Request('A'), Request('B')]
    for request in requests:
        manager._on_request(request)
    for i in order:
        manager._on_response_async(Response(requests[i], f'response-{i}'.encode(), 201+i))
    await settle(manager)
    entries = list(manager._network_requests)
    assert [r['status'] for r in entries] == [201, 202]
    assert [r['response_body'] for r in entries] == ['response-0', 'response-1']
    assert all(r['headers_complete'] and r['response_headers']['set-cookie'] == 'demo=secret' for r in entries)
    assert entries[0]['request_headers']['cookie'] == 'demo=secret'
    assert not manager._request_entries and not manager._capture_tasks


async def test_stop_finishes_existing_and_ignores_new_requests(manager):
    await network.network_capture('start', capture_body=True)
    request = Request()
    manager._on_request(request)
    async def finish():
        await asyncio.sleep(.01)
        manager._on_request(Request('ignored'))
        manager._on_response_async(Response(request))
    task = asyncio.create_task(finish())
    result = await network.network_capture('stop', wait_timeout_ms=1000)
    await task
    assert result['total_requests'] == 1
    assert result['pending_requests'] == result['pending_responses'] == 0
    assert manager._network_requests[0]['response_body'] == 'payload'


async def test_failure_clear_and_monotonic_cursor(manager):
    await network.network_capture('start')
    request = Request()
    manager._on_request(request)
    manager._on_request_failed(request)
    assert manager._network_requests[0]['failure'] == 'connection reset'
    assert not manager._request_entries
    await network.network_capture('clear')
    manager._on_request(Request('new'))
    assert (await network.list_network_requests(after_id=1))[0]['id'] == 2
    # Late response to the cleared request must not change the new entry.
    manager._on_response_async(Response(request))
    assert manager._network_requests[0]['status'] is None


async def test_clear_cancels_pending_response_work(manager):
    await network.network_capture('start', capture_body=True)
    request = Request()
    manager._on_request(request)
    response = Response(request)
    event = asyncio.Event()
    response.body = event.wait
    manager._on_response_async(response)
    tasks = list(manager._capture_tasks.values())
    await asyncio.sleep(0)
    await network.network_capture('clear')
    await asyncio.gather(*tasks, return_exceptions=True)
    assert tasks[0].cancelled()
    assert not manager._capture_tasks and not manager._network_requests


async def test_eviction_and_response_capacity_are_explicit(manager, monkeypatch):
    import camoufox_reverse_mcp.browser as browser
    await network.network_capture('start', capture_body=True)
    manager._network_requests = deque(maxlen=2)
    first = Request()
    for request in (first, Request('B'), Request('C')):
        manager._on_request(request)
    assert manager.capture_status()['dropped_requests'] == 1
    assert len(manager._request_entries) == 2
    manager._on_response_async(Response(first))
    monkeypatch.setattr(browser, 'MAX_CAPTURE_TASKS', 0)
    manager._on_response_async(Response(request))
    assert manager._network_requests[-1]['body_state'] == 'skipped_capacity'


@pytest.mark.parametrize('read_limit,returned_size', [(-1, 5), (100, 5), (2, 2), (0, 0)])
async def test_capture_and_return_truncation(manager, read_limit, returned_size):
    await network.network_capture('start', capture_body=True, max_body_size=5)
    request = Request()
    manager._on_request(request)
    manager._on_response_async(Response(request, '中文abcdef'.encode()))
    await settle(manager)
    result = await network.get_network_request(1, include_body=True, max_body_size=read_limit)
    assert result['response_body_truncated'] is True
    assert result['response_body_capture_truncated'] is True
    assert result['response_body_original_size'] == 8
    assert result['response_body_stored_size'] == 5
    assert result['response_body_size_returned'] == returned_size
    assert result['response_body_total_bytes'] == 12


async def test_pagination_domain_boundary_and_export(manager, tmp_path):
    await network.network_capture('start')
    for url in ['https://example.test/a', 'https://sub.example.test/b', 'https://other.test/?next=example.test']:
        manager._on_request(Request(url=url))
    rows = await network.list_network_requests(url_contains_domain='example.test', limit=1, after_id=1)
    assert [r['id'] for r in rows] == [2]
    assert 'error' in (await network.list_network_requests(limit=0))[0]
    entry = manager._network_requests[0]
    entry.update(url='https://user:secret@example.test/a?token=secret#secret',
                 request_headers={'authorization':'secret'}, response_body='secret',
                 failure='https://example.test/?secret')
    path = tmp_path/'capture.json'
    result = await network.export_network_capture(str(path))
    assert result['count'] == 3 and result['redacted']
    exported = path.read_text()
    assert 'secret' not in exported
    assert json.loads(exported)['schema_version'] == 1
    assert 'error' in await network.export_network_capture(str(path))
    assert 'error' in await network.export_network_capture(str(tmp_path/'bad.json'), include_body=True)
    private = tmp_path/'private.json'
    await network.export_network_capture(str(private), include_body=True, include_sensitive=True)
    assert json.loads(private.read_text())['requests'][0]['response_body'] == 'secret'


@pytest.mark.parametrize('actual,requested,expected', [
    ('.example.test', 'example.test', True), ('a.example.test', '.example.test', True),
    ('notexample.test', 'example.test', False), ('example.test.evil', 'example.test', False),
    ('EXAMPLE.TEST.', '.example.test', True), ('example.test', '.', False)])
def test_domain_boundary(actual, requested, expected):
    assert domain_matches(actual, requested) is expected


class CookieContext:
    def __init__(self):
        self.jar = [dict(name=name, value='demo', domain=domain, path=path)
                    for name, domain, path in [('sid','.a.test','/'),('sid','.b.test','/'),
                                              ('other','.a.test','/'),('sid','.nota.test','/')]]
        self.calls = []
    async def cookies(self):
        return list(self.jar)
    async def clear_cookies(self, **filters):
        self.calls.append(filters)
        self.jar = [c for c in self.jar if not all(value.search(c[key]) for key,value in filters.items())]


async def test_cookie_combined_filters_leave_other_cookies(monkeypatch):
    ctx = CookieContext()
    monkeypatch.setattr(storage, 'browser_manager', SimpleNamespace(get_active_page=AsyncMock(return_value=SimpleNamespace(context=ctx))))
    result = await storage.cookies('delete', name='sid', domain='.a.test')
    assert result['count'] == 1
    assert len(ctx.jar) == 3
    assert all(set(call) == {'name','domain','path'} for call in ctx.calls)
    assert len(await storage.cookies('get', domain='a.test')) == 1
    assert (await storage.cookies('delete'))['count'] == 3
    assert ctx.jar == []


@pytest.mark.parametrize('samples,focus', [([],None),([{}],None),([{'expected':{}}],None),
    ([{'expected':{'sign':'x'}}],[]),([{'expected':{'sign':'x'}}],['typo']),
    ([{'input':[], 'expected':{'sign':'x'}}],None)])
async def test_invalid_verification_never_launches_browser(monkeypatch, samples, focus):
    page = AsyncMock()
    monkeypatch.setattr(verification, 'browser_manager', SimpleNamespace(get_active_page=page))
    assert 'error' in await verification.verify_signer_offline('() => ({})', samples, focus)
    page.assert_not_called()


async def test_browser_verification_errors_missing_and_first_difference(monkeypatch):
    page = SimpleNamespace(evaluate=AsyncMock(return_value=[{'computed':{}},{'computed':{'sign':'abZ'}},{'error':'boom'}]))
    monkeypatch.setattr(verification, 'browser_manager', SimpleNamespace(get_active_page=AsyncMock(return_value=page)))
    result = await verification.verify_signer_offline('() => ({})', [{'expected':{'sign':None}}, {'expected':{'sign':'abc'}},{'expected':{'sign':'x'}}])
    assert result['passed'] == 0 and result['failed'] == 3
    assert result['first_divergence']['diffs'][0]['actual_missing']
    assert result['details'][1]['diffs'][0]['first_diff_char'] == 2
    assert 'window.__mcp_signer_fn =' not in page.evaluate.call_args.args[0]


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js unavailable')
async def test_node_signer_crypto_async_and_bad_returns(monkeypatch):
    browser = AsyncMock()
    monkeypatch.setattr(verification, 'browser_manager', SimpleNamespace(get_active_page=browser))
    result = await verification.verify_signer_offline("async ({text}) => ({sign: require('node:crypto').createHash('md5').update(text).digest('hex')})", [{'input':{'text':'abc'},'expected':{'sign':'900150983cd24fb0d6963f7d28e17f72'}}], runtime='node')
    assert result['pass_rate'] == 1
    for code in ['() => null', '() => {throw new Error("bad")}', '() => ({sign: 1n})']:
        result = await verification.verify_signer_offline(code, [{'expected':{'sign':'x'}}], runtime='node')
        assert result['failed'] == 1 and result['first_divergence']
    browser.assert_not_called()


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js unavailable')
async def test_node_timeout_and_recovery():
    result = await verification.verify_signer_offline('() => {while(true){}}', [{'expected':{'sign':'x'}}], runtime='node', timeout_ms=200)
    assert 'error' in result or result['failed'] == 1
    result = await verification.verify_signer_offline('() => ({sign:"x"})', [{'expected':{'sign':'x'}}], runtime='node')
    assert result['passed'] == 1


async def test_cookie_delete_legacy_runtime_expires_only_selected(monkeypatch):
    class LegacyContext(CookieContext):
        async def clear_cookies(self):
            raise AssertionError('filtered delete must not clear the entire jar')
        async def add_cookies(self, cookies):
            for cookie in cookies:
                assert cookie['expires'] == 1
                self.jar = [old for old in self.jar if any(old[key] != cookie[key] for key in ('name','domain','path'))]
    ctx = LegacyContext()
    monkeypatch.setattr(storage, 'browser_manager', SimpleNamespace(get_active_page=AsyncMock(return_value=SimpleNamespace(context=ctx))))
    result = await storage.cookies('delete', name='sid', domain='a.test')
    assert result['count'] == 1 and len(ctx.jar) == 3


def test_nested_boolean_is_not_numeric_equality():
    assert verification._compare_params({'sign':{'parts':[True]}}, {'sign':{'parts':[1]}}, None)


async def test_close_disables_capture_before_browser_teardown(manager):
    await network.network_capture('start', capture_body=True)
    async def teardown(*args):
        manager._on_request(Request('late'))
        assert not manager._capturing
    manager._cm = SimpleNamespace(__aexit__=teardown)
    await manager.close()
    assert not manager._network_requests and not manager._request_entries


async def test_imported_context_installs_hooks_before_first_page(monkeypatch):
    manager = BrowserManager()
    manager._persistent_scripts = [{'name':'fixture', 'content':'window.fixture = true;'}]
    order = []
    async def init_script(**kwargs):
        order.append('init')
    async def new_page():
        order.append('page')
        return SimpleNamespace(on=lambda *args: None)
    context = SimpleNamespace(add_init_script=init_script, new_page=new_page)
    manager.browser = SimpleNamespace(new_context=AsyncMock(return_value=context))
    monkeypatch.setattr(storage, 'browser_manager', manager)
    result = await storage.import_state('fixture-state.json')
    assert result['status'] == 'imported' and order == ['init','page']
