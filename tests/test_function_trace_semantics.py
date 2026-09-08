"""Run the generated function hooks in Node realms, not a rewritten test wrapper."""

from __future__ import annotations

import json
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from camoufox_reverse_mcp.browser import BrowserManager
from camoufox_reverse_mcp.tools.hooking import (
    _build_installer_core,
    get_trace_data,
    hook_function,
)


_NODE_RUNNER = r"""
const vm = require('node:vm');
const source = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const messages = [];
const context = vm.createContext({
    assert: require('node:assert/strict'), setTimeout, clearTimeout,
    console: {log(...args) { messages.push(args); }}
});
vm.runInContext(`
    globalThis.window = globalThis;
    window.top = window; window.name = '';
    globalThis.location = {href: 'https://trace.test/'};
`, context);
(async () => {
    const value = await vm.runInContext('(async () => {' + source + '})()', context,
        {timeout: 3000});
    process.stdout.write(JSON.stringify({value, messages}));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


def _run_js(source: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for executable trace semantics tests")
    completed = subprocess.run(
        [node, "-e", _NODE_RUNNER], input=json.dumps(source),
        capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    result["events"] = [
        json.loads(message[0].removeprefix("__MCP_TRACE__:"))
        for message in result["messages"]
        if message and isinstance(message[0], str)
        and message[0].startswith("__MCP_TRACE__:")
    ]
    return result


def _core(path="window.fn", **options) -> str:
    settings = dict(
        function_path=path, mode="trace", hook_code="", position="before",
        non_overridable=False, log_args=True, log_return=True, log_stack=False,
        max_captures=50, wait_timeout_ms=0, poll_interval_ms=10,
        frame_selector=None, frame_metadata=None, world="isolated",
        install_id="test:" + path, watch_assignments=False,
    )
    settings.update(options)
    return _build_installer_core(**settings)


def _install(path="window.fn", **options) -> str:
    return f"assert.equal((await {_core(path, **options)}).ok, true);"


@pytest.mark.parametrize("serialization", ["json", "preview"])
def test_sync_throws_are_logged_and_rethrown_with_identical_values(serialization):
    actual = _run_js("""
        let calls = 0;
        window.fn = function(value) { calls++; throw value; };
        const error = new Error('CFF failure');
        const symbol = Symbol('sentinel');
    """ + _install(serialization=serialization, log_return=False) + """
        for (const value of ['KProtect string throw', error, undefined, null, symbol]) {
            let caught = false;
            try { window.fn(value); }
            catch (actual) { caught = true; assert.equal(actual, value); }
            assert.equal(caught, true);
        }
        assert.equal(calls, 5);
        return window.__mcp_traces['window.fn'];
    """)
    entries = actual["value"]
    assert len(entries) == len(actual["events"]) == 5
    assert entries[0]["thrownValue"] == '"KProtect string throw"'
    assert [entry["callIndex"] for entry in entries] == [1, 2, 3, 4, 5]
    for entry in entries:
        assert entry["outcome"] == "throw"
        assert entry["completion"] == "sync"
        assert entry["serialization"] == serialization
        assert "returnValue" not in entry
        assert "thrownValue" in entry
    assert entries == [
        {key: value for key, value in entry.items() if key != "__path__"}
        for entry in actual["events"]
    ]


@pytest.mark.parametrize("serialization", ["json", "preview"])
def test_exact_receivers_arguments_and_return_identity_even_after_capture_cap(serialization):
    actual = _run_js("""
        const apply = Reflect.apply;
        const token = {};
        let calls = 0, receiver, values;
        window.fn = function() {
            'use strict';
            calls++; receiver = this; values = arguments;
            return token;
        };
        Object.defineProperty(window.fn, 'apply', {
            get() { throw new Error('must not read original.apply'); }
        });
    """ + _install(serialization=serialization, max_captures=2) + """
        Function.prototype.apply = () => { throw new Error('overwritten apply'); };
        Reflect.apply = () => { throw new Error('overwritten Reflect.apply'); };
        for (const expected of [undefined, null, 0, false, 'receiver', token]) {
            assert.equal(apply(window.fn, expected, [token, undefined, -0]), token);
            assert.equal(receiver, expected);
            assert.equal(values.length, 3);
            assert.equal(values[0], token);
            assert.equal(values[1], undefined);
            assert.equal(Object.is(values[2], -0), true);
        }
        assert.equal(calls, 6);
        return window.__mcp_traces['window.fn'];
    """)
    assert len(actual["value"]) == len(actual["events"]) == 2


@pytest.mark.parametrize("path", ["window.fn", "Math.random"])
def test_trace_ids_do_not_consume_business_randomness(path):
    actual = _run_js("""
        let calls = 0;
        Math.random = () => ++calls / 10;
        window.fn = () => Math.random();
    """ + _install(path, max_captures=3) + f"""
        assert.equal(calls, 0);
        for (let i = 1; i <= 5; i++) assert.equal({path}(), i / 10);
        assert.equal(calls, 5);
        return window.__mcp_traces[{json.dumps(path)}];
    """)
    entries = actual["value"]
    assert len(entries) == 3
    assert len({entry["traceId"] for entry in entries}) == 3


@pytest.mark.parametrize("order", [
    ["JSON.stringify", "Date.now", "console.log", "Reflect.apply", "window.fn"],
    ["Date.now", "JSON.stringify", "window.fn", "Reflect.apply", "console.log"],
])
def test_intrinsic_hooks_share_originals_and_do_not_recursively_log(order):
    actual = _run_js("""
        window.fn = value => value + 1;
    """ + "\n".join(_install(path) for path in order) + """
        assert.equal(JSON.stringify({a: 1}), '{"a":1}');
        assert.equal(typeof Date.now(), 'number');
        console.log('business log');
        assert.equal(Reflect.apply(value => value + 1, null, [2]), 3);
        assert.equal(window.fn(41), 42);
        return window.__mcp_traces;
    """)
    assert set(actual["value"]) == set(order)
    assert len(actual["events"]) == len(order)
    for entries in actual["value"].values():
        assert len(entries) == 1
        assert entries[0]["callIndex"] == 1
        assert entries[0]["outcome"] == "return"
    assert actual["value"]["JSON.stringify"][0]["args"] == '[{"a":1}]'


def test_later_replacement_of_logger_globals_does_not_redirect_logging():
    actual = _run_js("window.fn = value => value;\n" + _install() + """
        const poison = () => { throw new Error('redirected intrinsic'); };
        String.prototype.substring = poison;
        JSON.stringify = Date.now = console.log = String = poison;
        Array.isArray = poison;
        assert.equal(window.fn(42), 42);
        return window.__mcp_traces['window.fn'];
    """)
    assert len(actual["events"]) == 1
    assert actual["value"][0]["args"] == "[42]"
    assert actual["value"][0]["returnValue"] == "42"
    assert isinstance(actual["value"][0]["timestamp"], int)


def test_preview_never_inspects_or_coerces_objects_including_proxies():
    actual = _run_js("""
        let effects = 0, calls = 0;
        const poison = () => { effects++; throw new Error('object inspected'); };
        const object = {
            get value() { return poison(); }, get toJSON() { return poison(); },
            get then() { return poison(); }, get [Symbol.toStringTag]() { return poison(); },
            toString: poison, valueOf: poison, [Symbol.toPrimitive]: poison
        };
        const handler = {get: poison, ownKeys: poison, getPrototypeOf: poison,
            getOwnPropertyDescriptor: poison};
        const proxy = new Proxy({}, handler);
        const callable = new Proxy(function() {}, handler);
        const revoked = Proxy.revocable({}, handler); revoked.revoke();
        const circular = {}; circular.self = circular;
        window.fn = function(value, throws) { calls++; if (throws) throw value; return value; };
    """ + _install(serialization="preview") + """
        // Inherited hooks must not be invoked on logger-owned containers either.
        Object.prototype.toJSON = poison;
        Array.prototype.toJSON = poison;
        for (const value of [object, proxy, callable, revoked.proxy, circular,
                             new Date(), new Number(5)]) {
            assert.equal(window.fn(value, false), value);
            let caught = false;
            try { window.fn(value, true); }
            catch (actual) { caught = true; assert.equal(actual, value); }
            assert.equal(caught, true);
        }
        assert.equal(calls, 14);
        assert.equal(effects, 0);
        delete Object.prototype.toJSON;
        delete Array.prototype.toJSON;
        return window.__mcp_traces['window.fn'];
    """)
    assert len(actual["value"]) == len(actual["events"]) == 14
    for entry in actual["value"]:
        values = json.loads(entry["args"])
        assert values[0] in {"[object]", "[function]"}
        field = "thrownValue" if entry["outcome"] == "throw" else "returnValue"
        assert json.loads(entry[field]) == values[0]


def test_preview_preserves_primitive_values_and_tags_special_numbers():
    actual = _run_js("window.fn = value => value;\n" + _install(serialization="preview") + """
        for (const value of [null, true, 'text', 42, undefined, NaN, Infinity,
                             -Infinity, -0, 9007199254740993n, Symbol('hidden')]) {
            assert.equal(Object.is(window.fn(value), value), true);
        }
        return window.__mcp_traces['window.fn'];
    """)
    expected = [None, True, "text", 42, "[undefined]", "[NaN]", "[Infinity]",
                "[-Infinity]", "[-0]", "[bigint:9007199254740993]", "[symbol]"]
    assert [json.loads(entry["returnValue"]) for entry in actual["value"]] == expected
    assert [json.loads(entry["args"]) for entry in actual["value"]] == [[x] for x in expected]


def test_default_json_retains_serialization_and_coercion_side_effects():
    actual = _run_js("""
        let getters = 0, toJSON = 0, coercions = 0, calls = 0;
        const object = {get value() { getters++; return 42; }};
        const custom = {toJSON() { toJSON++; return {encoded: true}; }};
        const fallback = {toJSON() { throw 'cannot encode'; },
            [Symbol.toPrimitive]() { coercions++; return 'fallback'; }};
        window.fn = value => { calls++; return value; };
    """ + _install() + """
        for (const value of [object, custom, fallback]) assert.equal(window.fn(value), value);
        assert.equal(calls, 3);
        assert.equal(getters, 2);
        assert.equal(toJSON, 2);
        assert.equal(coercions, 2);
        return window.__mcp_traces['window.fn'];
    """)
    assert [entry["returnValue"] for entry in actual["value"]] == [
        '{"value":42}', '{"encoded":true}', "fallback",
    ]
    assert all(entry["serialization"] == "json" for entry in actual["value"])


@pytest.mark.parametrize("serialization", ["json", "preview"])
def test_promises_and_thenables_are_only_synchronous_returns(serialization):
    actual = _run_js("""
        let resolve, thenReads = 0, handled = 0;
        const pending = new Promise(done => { resolve = done; });
        const error = new Error('async rejection');
        const rejected = Promise.reject(error);
        const handling = rejected.catch(value => { assert.equal(value, error); handled++; });
        const thenable = {};
        for (const value of [pending, rejected, thenable]) {
            Object.defineProperty(value, 'then', {get() { thenReads++; throw 'then read'; }});
        }
        window.fn = value => value;
    """ + _install(serialization=serialization) + """
        for (const value of [pending, rejected, thenable]) assert.equal(window.fn(value), value);
        assert.equal(window.__mcp_traces['window.fn'].length, 3);
        resolve(42);
        await handling;
        await Promise.resolve();
        assert.equal(handled, 1);
        assert.equal(thenReads, 0);
        return window.__mcp_traces['window.fn'];
    """)
    assert len(actual["value"]) == len(actual["events"]) == 3
    for entry in actual["value"]:
        assert entry["completion"] == "sync"
        assert entry["outcome"] == "return"
        assert "settled" not in entry and "thrownValue" not in entry


def test_business_recursion_is_recorded_with_entry_order_call_indices():
    actual = _run_js("""
        let calls = 0;
        window.fn = n => { calls++; return n ? window.fn(n - 1) + 1 : 0; };
    """ + _install() + """
        assert.equal(window.fn(3), 3);
        assert.equal(calls, 4);
        return window.__mcp_traces['window.fn'];
    """)
    assert len(actual["events"]) == 4
    assert [entry["callIndex"] for entry in actual["value"]] == [4, 3, 2, 1]


def test_serialization_reentry_is_suppressed_without_disabling_later_traces():
    actual = _run_js("""
        let calls = 0;
        window.fn = value => { calls++; return value; };
        const object = {toJSON() { window.fn('from toJSON'); return 7; }};
    """ + _install(log_return=False) + """
        assert.equal(window.fn(object), object);
        assert.equal(calls, 2); // One explicit call and the documented toJSON side effect.
        assert.equal(window.fn(42), 42);
        assert.equal(calls, 3);
        return window.__mcp_traces['window.fn'];
    """)
    assert [entry["args"] for entry in actual["value"]] == ["[7]", "[42]"]
    assert len(actual["events"]) == 2


@pytest.mark.parametrize("failed_sink", ["page", "console", "metadata", "serialization"])
def test_logging_failures_do_not_replace_returns_or_exceptions(failed_sink):
    setup = {
        "page": "Object.defineProperty(window, '__mcp_traces', {get: poison});",
        "console": "console.log = (...args) => { if (broken) poison(); savedLog(...args); };",
        "metadata": "Date.now = () => { if (broken) poison(); return 123; };",
        "serialization": "JSON.stringify = value => { if (broken) poison(); return savedJSON(value); };",
    }[failed_sink]
    actual = _run_js("""
        let broken = false, calls = 0;
        const poison = () => { throw 'logger failed'; };
        const savedLog = console.log, savedJSON = JSON.stringify;
        const error = new Error('original');
        const token = {toString: poison, toJSON: poison, [Symbol.toPrimitive]: poison};
        window.fn = throws => { calls++; if (throws) throw error; return token; };
    """ + setup + _install() + """
        broken = true;
        assert.equal(window.fn(false), token);
        let caught = false;
        try { window.fn(true); }
        catch (actual) { caught = true; assert.equal(actual, error); }
        assert.equal(caught, true);
        assert.equal(calls, 2);
        if (Object.getOwnPropertyDescriptor(window, '__mcp_traces').get) return null;
        return window.__mcp_traces['window.fn'];
    """)
    if failed_sink != "page":
        assert [entry["outcome"] for entry in actual["value"]] == ["return", "throw"]
    if failed_sink not in {"console", "serialization"}:
        assert len(actual["events"]) == 2


@pytest.mark.parametrize("clear", ["delete window.__mcp_traces['window.fn'];",
                                  "window.__mcp_traces = {};"])
def test_clearing_data_preserves_counter_capture_limit_and_wrapper(clear):
    actual = _run_js("""
        let calls = 0;
        window.fn = value => { calls++; return value; };
    """ + _install(max_captures=2) + """
        const wrapper = window.fn;
        assert.equal(window.fn(1), 1);
        const first = window.__mcp_traces['window.fn'][0];
    """ + clear + """
        assert.equal(window.fn(2), 2);
        assert.equal(window.fn(3), 3);
        assert.equal(window.fn, wrapper);
        assert.equal(calls, 3);
        const later = window.__mcp_traces['window.fn'];
        assert.equal(later.length, 1);
        assert.equal(later[0].callIndex, 2);
        assert.notEqual(later[0].traceId, first.traceId);
        return later;
    """)
    assert len(actual["events"]) == 2
    assert actual["value"][0]["args"] == "[2]"


def test_reinstallation_in_same_realm_has_distinct_ids_without_clock_or_random():
    actual = _run_js("""
        Date.now = () => 123;
        Math.random = () => { throw 'random consumed'; };
        window.fn = value => value;
    """ + _install() + """
        window.fn(1);
        window.__mcp_function_uninstall();
    """ + _install() + """
        window.fn(1);
        return window.__mcp_traces['window.fn'];
    """)
    assert len({entry["traceId"] for entry in actual["value"]}) == 2
    assert [entry["timestamp"] for entry in actual["value"]] == [123, 123]


@pytest.fixture(scope="module")
def identical_realm_events():
    source = """
        window.top = {}; window.name = 'same';
        Date.now = () => 123;
        Math.random = () => { throw 'random consumed'; };
        window.fn = value => value;
    """ + _install() + "window.fn(1); return window.__mcp_traces['window.fn'];"
    # Fresh realms model equal-name/URL frames and reloads of the same init script.
    results = [_run_js(source) for _ in range(4)]
    assert all(result["value"] == results[0]["value"] for result in results)
    return results


@pytest.mark.asyncio
@pytest.mark.parametrize(("live_count", "cached_count"), [(2, 4), (0, 4), (2, 2), (2, 1)])
async def test_multiset_merge_keeps_identical_frames_and_reload_events(
    identical_realm_events, live_count, cached_count,
):
    manager = BrowserManager()
    main = SimpleNamespace(url="https://host.test/", name="", parent_frame=None)
    children = [SimpleNamespace(url="https://trace.test/", name="same", parent_frame=main)
                for _ in range(2)]
    page = SimpleNamespace(frames=[main, *children], main_frame=main, url=main.url)
    manager.get_active_page = AsyncMock(return_value=page)
    for result in identical_realm_events[:cached_count]:
        for message in result["messages"]:
            manager._on_console(SimpleNamespace(text=message[0], type="log", location={}))

    async def read(target, *_args, **_kwargs):
        for index, child in enumerate(children):
            if target is child and index < live_count:
                return {"window.fn": identical_realm_events[index]["value"]}, "isolated", None
        return {}, "isolated", None

    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch("camoufox_reverse_mcp.tools.hooking.evaluate_in_world", side_effect=read),
    ):
        result = await get_trace_data("window.fn")
        repeated = await get_trace_data("window.fn")
        live_only = await get_trace_data("window.fn", include_persistent=False)
    assert len(result["window.fn"]) == max(live_count, cached_count)
    assert repeated == result
    assert len(live_only.get("window.fn", [])) == live_count


@pytest.mark.asyncio
async def test_equal_ids_do_not_hide_different_payloads_or_timestamps(identical_realm_events):
    entry = identical_realm_events[0]["value"][0]
    entries = [entry, {**entry, "timestamp": 124}, {**entry, "args": "[2]"},
               {**entry, "outcome": "throw", "thrownValue": '"failure"'}]
    manager = BrowserManager()
    page = SimpleNamespace(frames=[], url="https://host.test/")
    manager.get_active_page = AsyncMock(return_value=page)
    for value in entries:
        manager._on_console(SimpleNamespace(
            text="__MCP_TRACE__:" + json.dumps({**value, "__path__": "window.fn"}),
            type="log", location={},
        ))
    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch("camoufox_reverse_mcp.tools.hooking.evaluate_in_world",
              new=AsyncMock(return_value=({"window.fn": [entry]}, "isolated", None))),
    ):
        actual = await get_trace_data()
    assert actual["window.fn"] == entries


@pytest.mark.asyncio
async def test_serialization_validation_and_persistent_public_api_generation():
    page = SimpleNamespace(frames=[], url="https://trace.test/")
    manager = SimpleNamespace(get_active_page=AsyncMock(return_value=page),
                              _persistent_scripts=[], add_persistent_script=AsyncMock())
    generated = []

    async def install(_target, source, _world, **_kwargs):
        generated.append(source)
        return {"ok": True}, "isolated", None

    with (
        patch("camoufox_reverse_mcp.tools.hooking.browser_manager", manager),
        patch("camoufox_reverse_mcp.tools.hooking.evaluate_in_world", side_effect=install),
    ):
        invalid = await hook_function("window.fn", mode="trace", serialization="invalid")
        assert "serialization" in invalid["error"]
        manager.get_active_page.assert_not_awaited()
        for serialization in ["json", "preview"]:
            result = await hook_function("window.fn", mode="trace", persistent=True,
                                         serialization=serialization)
            assert result["status"] == "tracing"
            assert result["serialization"] == serialization
    registrations = manager.add_persistent_script.await_args_list
    assert registrations[0].args[0] != registrations[1].args[0]
    for serialization, immediate, registration in zip(["json", "preview"], generated, registrations):
        for source in [immediate, registration.args[1]]:
            actual = _run_js("window.fn = value => value;\n" + f"await {source};\n" + """
                window.fn({value: 1}); return window.__mcp_traces['window.fn'];
            """)
            assert actual["value"][0]["serialization"] == serialization
            expected = '{"value":1}' if serialization == "json" else '"[object]"'
            assert actual["value"][0]["returnValue"] == expected


@pytest.mark.parametrize(("position", "hook_code", "expected"), [
    ("before", "events.push(['before', __this === receiver, arguments[0]]);", 42),
    ("after", "events.push(['after', __this === receiver, __result]);", 42),
    ("replace", "events.push(['replace', __this === receiver, arguments[0]]); return 99;", 99),
])
def test_custom_intercept_hook_code_semantics_are_unchanged(position, hook_code, expected):
    actual = _run_js("""
        window.events = []; window.receiver = {};
        window.fn = function(value) { events.push(['original', this === receiver, value]); return value + 1; };
    """ + _install(mode="intercept", position=position, hook_code=hook_code,
                    serialization="ignored-for-intercept") + f"""
        assert.equal(window.fn.call(receiver, 41), {expected});
        assert.equal(window.__mcp_traces, undefined);
        return events;
    """)
    expected_events = {
        "before": [["before", True, 41], ["original", True, 41]],
        "after": [["original", True, 41], ["after", True, 42]],
        "replace": [["replace", True, 41]],
    }
    assert actual["value"] == expected_events[position]
    assert not actual["events"]
