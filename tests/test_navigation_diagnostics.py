import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools import navigation


@pytest.mark.parametrize('message',[
    'Page.goto: NS_ERROR_CONNECTION_REFUSED\nCall log: navigating, waiting until "load"',
    'Page.goto: net::ERR_NAME_NOT_RESOLVED\nwaiting until load',
])
async def test_goto_non_timeout_keeps_original_error(monkeypatch,message):
    page=SimpleNamespace(url='https://example.test/',goto=AsyncMock(side_effect=RuntimeError(message)),evaluate=AsyncMock())
    manager=BrowserManager();manager.get_active_page=AsyncMock(return_value=page)
    monkeypatch.setattr(navigation,'browser_manager',manager)
    result=await navigation.navigate('https://example.test/')
    assert result['error']==message and result['phase']=='goto'
    page.evaluate.assert_not_called()
    assert page.goto.await_count==1


async def test_snapshot_timeout_does_not_replay_or_close_browser(monkeypatch):
    gate=asyncio.Event()
    page=SimpleNamespace(accessibility=SimpleNamespace(snapshot=gate.wait))
    manager=BrowserManager();manager.get_active_page=AsyncMock(return_value=page)
    manager.close=AsyncMock()
    monkeypatch.setattr(navigation,'browser_manager',manager)
    result=await navigation.take_snapshot(timeout_ms=10)
    assert result['automatic_retry'] is False and 'timed out' in result['error']
    manager.close.assert_not_called()


async def test_snapshot_result_is_bounded_and_explicit(monkeypatch):
    tree={'role':'document','name':'x'*4000,'children':[{'role':'button','name':str(n)} for n in range(30)]}
    page=SimpleNamespace(accessibility=SimpleNamespace(snapshot=AsyncMock(return_value=tree)))
    manager=BrowserManager();manager.get_active_page=AsyncMock(return_value=page)
    monkeypatch.setattr(navigation,'browser_manager',manager)
    result=await navigation.take_snapshot(max_nodes=5)
    assert result['method']=='accessibility' and result['truncated']
    assert len(result['snapshot']['name'])==2000
    assert len(result['snapshot']['children'])<=3


async def test_dom_snapshot_fallback_is_labelled(monkeypatch):
    page=SimpleNamespace(evaluate=AsyncMock(return_value={'role':'body','text':'fixture'}))
    manager=BrowserManager();manager.get_active_page=AsyncMock(return_value=page)
    monkeypatch.setattr(navigation,'browser_manager',manager)
    result=await navigation.take_snapshot()
    assert result['method']=='dom_fallback' and not result['truncated']
    assert result['snapshot']['text']=='fixture'


def test_initialize_reports_application_version():
    from camoufox_reverse_mcp import __version__
    from camoufox_reverse_mcp.server import mcp
    assert mcp._mcp_server.create_initialization_options().server_version==__version__
