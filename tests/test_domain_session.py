"""Tests for domain_session.py core storage."""
import tempfile
from pathlib import Path

import pytest

from camoufox_reverse_mcp.domain_session import (
    DomainSessionStore, normalize_domain, new_run_id
)


def test_normalize_domain_basic():
    assert normalize_domain("https://www.example.com/search") == "example.com"
    assert normalize_domain("m.example.com") == "example.com"
    assert normalize_domain("api.example.co.uk") == "example.co.uk"


def test_normalize_domain_empty():
    with pytest.raises(ValueError):
        normalize_domain("")


def test_new_run_id_format():
    rid = new_run_id()
    assert rid.startswith("run_")
    assert len(rid) > 20


def test_start_stop_run():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        info, run = store.start_run("example.com", "https://example.com/", note="test")
        assert info.domain == "example.com"
        assert run.status == "active"
        assert store.active is not None

        store.stop_run("closed")
        assert store.active is None


def test_multi_run_same_domain():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        info1, run1 = store.start_run("example.com", "https://example.com/")
        store.record("tool_calls", {"tool": "test"})
        assert run1.counters["tool_calls"] == 1
        store.stop_run()

        info2, run2 = store.start_run("example.com", "https://example.com/page2")
        assert len(info2.runs) == 2
        assert run2.counters["tool_calls"] == 0
        assert info2.domain_counters["tool_calls_total"] == 1
        store.stop_run()


def test_record_events():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        store.start_run("example.com", "https://example.com/")
        assert store.record("tool_calls", {"tool": "navigate"})
        assert store.record("network_events", {"type": "request", "id": 1})
        assert not store.record("console", {"text": "hi"})  # console off by default
        store.stop_run()

        # Verify jsonl files exist
        assert (Path(tmp) / "example.com" / "tool_calls.jsonl").exists()
        assert (Path(tmp) / "example.com" / "network_events.jsonl").exists()


def test_assertion_events():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        store.start_run("example.com", "https://example.com/")
        store.record_assertion_event({"action": "add", "id": "test_assert"})
        store.active.active_assertions.append("test_assert")
        store.flush_manifest()
        store.stop_run()

        # Re-attach and verify assertion persists
        info = store.attach_domain("example.com")
        assert "test_assert" in info.active_assertions


def test_list_domains():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        store.start_run("a.com", "https://a.com/")
        store.stop_run()
        store.start_run("b.com", "https://b.com/")
        store.stop_run()
        lst = store.list_domains()
        domains = {d["domain"] for d in lst}
        assert "a.com" in domains
        assert "b.com" in domains


def test_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        store.start_run("example.com", "https://example.com/")
        store.record("tool_calls", {"tool": "test1"})
        store.record("tool_calls", {"tool": "test2"})
        snap = store.snapshot(sections=["tool_calls"])
        assert snap["tool_calls"]["count_returned"] == 2
        store.stop_run()


def test_export_import():
    with tempfile.TemporaryDirectory() as tmp:
        store = DomainSessionStore(root=Path(tmp))
        store.start_run("example.com", "https://example.com/")
        store.record("tool_calls", {"tool": "test"})
        store.active.active_assertions.append("assert_1")
        store.flush_manifest()

        zip_path = store.export_domain()
        assert zip_path.exists()
        store.stop_run()

        # Import into a fresh store
        store2 = DomainSessionStore(root=Path(tmp) / "store2")
        info = store2.import_domain(zip_path, merge_strategy="replace")
        assert info.domain == "example.com"
        assert "assert_1" in info.active_assertions


def test_new_tools_count():
    """v0.8.0 should have 78 tools total (65 + 13 new)."""
    from camoufox_reverse_mcp.server import mcp
    tools = mcp._tool_manager.list_tools()
    assert len(tools) == 78


def test_version():
    from camoufox_reverse_mcp import __version__
    assert __version__ == "0.8.0"
