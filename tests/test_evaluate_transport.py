import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from camoufox_reverse_mcp.tools import debugging


@pytest.mark.skipif(shutil.which('node') is None,reason='Node unavailable')
def test_ascii_json_preserves_utf16_strings_and_executes_once():
    expression='(() => {globalThis.calls++;return {text:"  \\ufeff雪\\u2028"+String.fromCharCode(0xd800), tagged:{$number:"-0"}};})()'
    script=debugging._build_evaluate_script(expression,True,'main','json_ascii')
    code='globalThis.calls=0;('+script+')().then(r=>console.log(JSON.stringify({r,calls})));'
    run=subprocess.run(['node','-e',code],capture_output=True,text=True,check=True)
    result=json.loads(run.stdout)
    assert result['calls']==1
    encoded=result['r']['result'];assert encoded.isascii()
    value=json.loads(encoded)
    assert value['text']=='  \ufeff雪\u2028\ud800'
    assert value['tagged']=={'$number':'-0'}


async def test_isolated_failure_is_not_replayed_with_handle(monkeypatch):
    target=SimpleNamespace(evaluate_handle=AsyncMock())
    monkeypatch.setattr(debugging.browser_manager,'get_active_page',AsyncMock(return_value=object()))
    monkeypatch.setattr(debugging,'resolve_frame',lambda *a,**k:(target,{'index':0}))
    execution=AsyncMock(side_effect=RuntimeError('serialization failed after side effect'))
    monkeypatch.setattr(debugging,'evaluate_in_world',execution)
    result=await debugging.evaluate_js('sideEffect()')
    assert result['type']=='error' and 'not replayed' in result['error']
    execution.assert_awaited_once();target.evaluate_handle.assert_not_called()


async def test_ascii_result_bypasses_legacy_smart_parsing(monkeypatch):
    monkeypatch.setattr(debugging.browser_manager,'get_active_page',AsyncMock(return_value=object()))
    monkeypatch.setattr(debugging,'resolve_frame',lambda *a,**k:(object(),{'index':0}))
    monkeypatch.setattr(debugging,'evaluate_in_world',AsyncMock(return_value=({'result':'"  value  "','type':'string','json_ascii':True},'camoufox_native',None)))
    result=await debugging.evaluate_js('"  value  "',world='main',result_format='json_ascii')
    assert result['type']=='json_ascii' and result['value']=='"  value  "'
