from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from camoufox_reverse_mcp.tools import trace


async def test_snapshot_states_its_own_page_not_event_window(monkeypatch,tmp_path):
    page=SimpleNamespace(url='https://main.example.test/',evaluate=AsyncMock(return_value={'window_innerWidth':1440}))
    monkeypatch.setattr(trace.browser_manager,'get_active_page',AsyncMock(return_value=page))
    result=await trace._collect_property_values([{'path':'window.innerWidth','count':1}],tmp_path)
    assert result['values']['window.innerWidth']==1440
    assert result['context']=={'url':'https://main.example.test/','world':'isolated','scope':'active_page_main_frame','event_window_attribution':False}
