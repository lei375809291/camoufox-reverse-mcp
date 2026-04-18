"""Domain-level coarse-grained assertions.

An assertion = executable JS + expected rule. Assertions persist at the
domain level (not per-run), so they survive across analysis sessions and
can be batch-reverified after site updates.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Optional

from ..server import mcp, browser_manager
from ..domain_session import get_store


def _check_expected(actual: Any, expected: dict) -> tuple[bool, str]:
    et = expected.get("type")
    if et == "equals":
        ok = actual == expected.get("value")
        return ok, (f"expected == {expected.get('value')!r}, got {actual!r}" if not ok else "equals")
    if et == "regex":
        if not isinstance(actual, str):
            return False, f"expected string for regex, got {type(actual).__name__}"
        ok = bool(re.search(expected.get("pattern", ""), actual))
        return ok, f"regex /{expected.get('pattern')}/ {'matched' if ok else 'did not match'}"
    if et == "range":
        try:
            v = float(actual)
            lo, hi = float(expected.get("min", -1e18)), float(expected.get("max", 1e18))
            ok = lo <= v <= hi
            return ok, (f"expected {lo} <= x <= {hi}, got {v}" if not ok else "in range")
        except (TypeError, ValueError):
            return False, f"range check requires numeric, got {actual!r}"
    return False, f"unknown expected.type: {et!r}"


def _find_definition(store, assertion_id: str) -> Optional[dict]:
    info = store.active
    path = info.path / "assertions.jsonl"
    if not path.exists():
        return None
    definition = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("action") == "add" and rec.get("id") == assertion_id:
                    definition = rec
            except Exception:
                continue
    return definition


def _find_last_verification(store, assertion_id: str) -> Optional[dict]:
    info = store.active
    path = info.path / "assertions.jsonl"
    if not path.exists():
        return None
    last = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("action") == "verified" and rec.get("id") == assertion_id:
                    last = rec
            except Exception:
                continue
    return last


@mcp.tool()
async def add_assertion(assertion_id: str, description: str, code: str,
                        expected: dict) -> dict:
    """Register a coarse-grained assertion on the active domain.

    Assertions persist at DOMAIN level (not per-run). The code runs in
    page context; the return value is checked against expected.

    Args:
        assertion_id: [a-zA-Z0-9_]+, <= 64 chars, unique within domain.
        description: Human-readable what & why.
        code: JS expression/IIFE; return value is compared to expected.
        expected: One of:
            {"type":"equals","value":<any>}
            {"type":"regex","pattern":"<regex>"}
            {"type":"range","min":<num>,"max":<num>}
    """
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}
        if not re.match(r"^[a-zA-Z0-9_]{1,64}$", assertion_id):
            return {"error": f"invalid assertion_id: {assertion_id!r}"}
        if assertion_id in store.active.active_assertions:
            return {"error": f"assertion {assertion_id!r} already exists"}
        if expected.get("type") not in ("equals", "regex", "range"):
            return {"error": f"expected.type must be equals/regex/range"}

        store.record_assertion_event({
            "action": "add", "id": assertion_id, "description": description,
            "code": code, "expected": expected,
        })
        store.active.active_assertions.append(assertion_id)
        store.flush_manifest()

        initial = None
        if store.active.active_run_id:
            try:
                initial = await verify_assertion(assertion_id)
            except Exception as e:
                initial = {"error": f"initial verification failed: {e}"}

        return {"status": "added", "id": assertion_id, "domain": store.active.domain,
                "total_active": len(store.active.active_assertions),
                "initial_verification": initial}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def verify_assertion(assertion_id: str) -> dict:
    """Re-run an assertion's code and check against expected."""
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}
        if assertion_id not in store.active.active_assertions:
            return {"error": f"no active assertion {assertion_id!r}"}
        definition = _find_definition(store, assertion_id)
        if definition is None:
            return {"error": f"definition for {assertion_id!r} not found"}

        page = await browser_manager.get_active_page()
        t0 = time.time()
        try:
            actual = await page.evaluate(definition["code"])
        except Exception as e:
            store.record_assertion_event({
                "action": "verified", "id": assertion_id,
                "result": "error", "error": str(e),
            })
            return {"id": assertion_id, "passed": False,
                    "error": f"evaluate failed: {e}",
                    "duration_ms": int((time.time() - t0) * 1000)}

        passed, reason = _check_expected(actual, definition["expected"])
        store.record_assertion_event({
            "action": "verified", "id": assertion_id,
            "result": "passed" if passed else "failed",
            "actual": str(actual)[:500] if actual is not None else None,
            "reason": reason,
        })
        return {"id": assertion_id, "passed": passed,
                "actual": actual if isinstance(actual, (int, float, bool, str)) else str(actual)[:500],
                "reason": reason,
                "duration_ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_assertions(include_removed: bool = False) -> dict:
    """List assertions on the active domain."""
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}
        active = []
        for aid in store.active.active_assertions:
            d = _find_definition(store, aid)
            last = _find_last_verification(store, aid)
            active.append({"id": aid,
                           "description": d.get("description") if d else None,
                           "expected": d.get("expected") if d else None,
                           "last_verification": last})
        out = {"domain": store.active.domain, "active": active,
               "active_count": len(active)}
        if include_removed:
            out["removed"] = list(store.active.removed_assertions)
        return out
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def remove_assertion(assertion_id: str) -> dict:
    """Soft-delete an assertion."""
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}
        if assertion_id not in store.active.active_assertions:
            return {"error": f"no active assertion {assertion_id!r}"}
        store.active.active_assertions.remove(assertion_id)
        store.active.removed_assertions.append(assertion_id)
        store.record_assertion_event({"action": "remove", "id": assertion_id})
        store.flush_manifest()
        return {"status": "removed", "id": assertion_id, "domain": store.active.domain}
    except Exception as e:
        return {"error": str(e)}
