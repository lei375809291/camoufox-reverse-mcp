import shutil
import subprocess
import json
import pytest
from camoufox_reverse_mcp.utils.js_parser import parse_source
from camoufox_reverse_mcp.utils.ast_rewriter import ast_rewrite


def test_es2017_remains_python_only(monkeypatch):
    monkeypatch.setattr(shutil,'which',lambda name:None)
    tree,backend=parse_source('var x = obj.name;')
    assert tree.type=='Program' and backend=='esprima'
    with pytest.raises(ValueError,match='needs Node.js'):
        parse_source('const x = obj?.name ?? 0;')


@pytest.mark.skipif(shutil.which('node') is None,reason='Node.js unavailable')
def test_modern_parser_offsets_and_optional_semantics():
    source='"use strict"; const prefix="😀"; const a=null; let c=0; const x=a?.[c++]; const y={ok:7}.ok; globalThis.result={x:x===undefined,c,y};'
    tree,backend=parse_source(source,locations=True)
    assert backend=='acorn-8.15.0'
    rewritten,stats=ast_rewrite(source,include_source_site=True)
    assert rewritten is not None and stats['parser_backend']=='acorn-8.15.0'
    for site in stats['source_sites']:
        assert 0<=site['start']<site['end']<=len(source)
    def run(code):
        script='globalThis.window=globalThis;'+code+';process.stdout.write(JSON.stringify(globalThis.result));'
        p=subprocess.run(['node','-e',script],capture_output=True,text=True,check=True)
        return json.loads(p.stdout)
    assert run(rewritten)==run(source)=={'x':True,'c':0,'y':7}
    assert any(source[x['start']:x['end']]=='{ok:7}.ok' for x in stats['source_sites'])


@pytest.mark.skipif(shutil.which('node') is None,reason='Node.js unavailable')
def test_parse_never_executes_source(tmp_path):
    target=tmp_path/'must-not-exist'
    source='require("fs").writeFileSync('+json.dumps(str(target))+', "x"); obj?.value;'
    tree,backend=parse_source(source)
    assert backend=='acorn-8.15.0' and not target.exists()


@pytest.mark.skipif(shutil.which('node') is None,reason='Node.js unavailable')
def test_unicode_source_lines_and_hashbang_survive():
    source='#!/usr/bin/env node\n"use strict"; const emoji="😀";\u2028const ignored=null?.x; const y=({k:2}).k;'
    rewritten,stats=ast_rewrite(source,include_source_site=True)
    assert rewritten.startswith('#!/usr/bin/env node\n"use strict";')
    site=next(x for x in stats['source_sites'] if source[x['start']:x['end']]=='{k:2}).k' or source[x['start']:x['end']]=='({k:2}).k')
    assert site['line']==3
    completed=subprocess.run(['node','--check'],input=rewritten,capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr


def test_static_paths_and_method_filters_limit_actual_calls():
    source='function run(){this.bytecode[0]; this.executeOpcode(1); this.other(2); helper();}'
    _,stats=ast_rewrite(source,filter_property_names=['executeOpcode'],include_source_site=True)
    sites=stats['source_sites']
    assert len(sites)==1 and source[sites[0]['start']:sites[0]['end']]=='this.executeOpcode(1)'
    _,stats=ast_rewrite(source,filter_object_names=['this.bytecode'],rewrite_calls=False,include_source_site=True)
    assert len(stats['source_sites'])==1
    site=stats['source_sites'][0]
    assert source[site['start']:site['end']]=='this.bytecode[0]'
