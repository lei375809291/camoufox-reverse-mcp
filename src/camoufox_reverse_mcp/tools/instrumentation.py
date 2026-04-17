"""
instrumentation.py - Source-level JSVMP instrumentation tools.

Hooks into the HTTP response for target JS files, rewrites the source to
insert taps at every member access and function call, then continues the
response. The rewritten code still executes normally but records every
bytecode-dispatch-loop interaction with the host environment.

This is the most powerful VMP analysis technique available because it
doesn't rely on the VMP routing through hookable JS APIs - it instruments
the bytecode dispatcher itself.
"""
from __future__ import annotations
import time

from ..server import mcp, browser_manager
from ..utils.js_rewriter import (
    regex_rewrite,
    INSTRUMENT_RUNTIME,
    ACORN_REWRITE_JS_TEMPLATE,
)


# Module-level state for active instrumentation routes
_active_routes: dict[str, dict] = {}


@mcp.tool()
async def instrument_jsvmp_source(
    url_pattern: str,
    mode: str = "regex",
    tag: str = "vmp",
    rewrite_member_access: bool = True,
    rewrite_calls: bool = True,
    max_rewrites: int = 5000,
    cache_rewritten: bool = True,
) -> dict:
    """Intercept a JSVMP script and instrument its source at the HTTP layer.

    This is the most powerful technique for analyzing JSVMP-protected scripts.
    Unlike runtime hooks that only see what the VMP routes through hookable
    APIs, source-level instrumentation inserts taps at EVERY member access
    and function call - including inside the bytecode dispatch loop's
    switch(opcode) { ... } cases.

    After this tool succeeds, subsequent loads of url_pattern return rewritten
    source. The VMP still runs normally but every internal operation is
    logged to window.__mcp_vmp_log. Use get_instrumentation_log to retrieve.

    Args:
        url_pattern: Glob pattern matching the VMP script URL(s), e.g.:
            "**/webmssdk.es5.js"
            "**/FuckCookie_*.js"
            "https://target.com/sdenv-*.js"
        mode: "regex" (fast, ~80% coverage, no CDN) or "ast"
            (precise, loads acorn from CDN into the page).
        tag: String tag attached to every log entry (for filtering when
            instrumenting multiple scripts).
        rewrite_member_access: Tap every obj[key] / obj.key read.
        rewrite_calls: Tap every fn(args) / obj.method(args) call.
        max_rewrites: Hard cap on rewrites per file (safety).
        cache_rewritten: Cache the rewritten source so repeated fetches
            don't re-run the rewriter (recommended True).

    Returns:
        dict with status and pattern. The actual rewrite happens when the
        browser loads the matched URL. Watch console for "[INSTRUMENT]" logs.
    """
    try:
        page = await browser_manager.get_active_page()
        cache: dict[str, str] = {}
        stats = {"files_rewritten": 0, "total_edits": 0, "last_url": None}

        async def route_handler(route):
            try:
                req_url = route.request.url
                # Try cache first
                if cache_rewritten and req_url in cache:
                    await route.fulfill(
                        status=200,
                        headers={"content-type": "application/javascript; charset=utf-8"},
                        body=cache[req_url],
                    )
                    return

                resp = await route.fetch()
                status = resp.status
                body_bytes = await resp.body()
                try:
                    src = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    src = body_bytes.decode("latin-1")

                rewritten = src
                edit_count = 0
                if mode == "regex":
                    rewritten, rstats = regex_rewrite(
                        src, tag=tag,
                        rewrite_member_access=rewrite_member_access,
                        max_rewrites=max_rewrites,
                    )
                    edit_count = rstats.get("member_access_rewrites", 0)
                elif mode == "ast":
                    opts = {
                        "rewriteMemberAccess": rewrite_member_access,
                        "rewriteCalls": rewrite_calls,
                    }
                    result = await page.evaluate(
                        ACORN_REWRITE_JS_TEMPLATE,
                        [src, tag, opts]
                    )
                    if result.get("ok"):
                        rewritten = INSTRUMENT_RUNTIME + "\n" + result["src"]
                        edit_count = result.get("edit_count", 0)
                    else:
                        rewritten = src  # AST failed, pass through unchanged
                        browser_manager._console_logs.append({
                            "level": "warn",
                            "text": f"[INSTRUMENT] AST rewrite failed for {req_url}: {result.get('error')}",
                            "timestamp": int(time.time() * 1000),
                            "location": None,
                        })
                else:
                    rewritten = src  # unknown mode; safety no-op

                if cache_rewritten:
                    cache[req_url] = rewritten

                stats["files_rewritten"] += 1
                stats["total_edits"] += edit_count
                stats["last_url"] = req_url

                # Preserve original headers (minus content-length which we're changing)
                headers = dict(resp.headers)
                headers.pop("content-length", None)
                headers.pop("Content-Length", None)
                headers["content-type"] = "application/javascript; charset=utf-8"

                await route.fulfill(
                    status=status,
                    headers=headers,
                    body=rewritten,
                )
                browser_manager._console_logs.append({
                    "level": "info",
                    "text": f"[INSTRUMENT] rewrote {req_url} ({edit_count} edits, mode={mode})",
                    "timestamp": int(time.time() * 1000),
                    "location": None,
                })
            except Exception as e:
                browser_manager._console_logs.append({
                    "level": "error",
                    "text": f"[INSTRUMENT] handler error: {e}",
                    "timestamp": int(time.time() * 1000),
                    "location": None,
                })
                try:
                    await route.continue_()
                except Exception:
                    pass

        await page.route(url_pattern, route_handler)
        _active_routes[url_pattern] = {
            "handler": route_handler,
            "cache": cache,
            "stats": stats,
            "mode": mode,
            "tag": tag,
        }

        return {
            "status": "instrumenting",
            "pattern": url_pattern,
            "mode": mode,
            "tag": tag,
            "note": "Navigate or reload to trigger rewrite. "
                    "Check get_instrumentation_status() for stats.",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_instrumentation_status() -> dict:
    """Show currently active source-level instrumentations and their stats."""
    try:
        return {
            "active_patterns": [
                {
                    "pattern": pat,
                    "mode": info["mode"],
                    "tag": info["tag"],
                    "files_rewritten": info["stats"]["files_rewritten"],
                    "total_edits": info["stats"]["total_edits"],
                    "last_url": info["stats"]["last_url"],
                    "cached_urls": len(info["cache"]),
                }
                for pat, info in _active_routes.items()
            ],
            "total_patterns": len(_active_routes),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def stop_instrumentation(url_pattern: str | None = None) -> dict:
    """Stop a specific or all source-level instrumentation routes.

    Args:
        url_pattern: The exact pattern passed to instrument_jsvmp_source.
            If omitted, stops all.
    """
    try:
        page = await browser_manager.get_active_page()
        removed = []
        if url_pattern is not None:
            if url_pattern in _active_routes:
                try:
                    await page.unroute(url_pattern)
                except Exception:
                    pass
                del _active_routes[url_pattern]
                removed.append(url_pattern)
        else:
            for pat in list(_active_routes.keys()):
                try:
                    await page.unroute(pat)
                except Exception:
                    pass
                del _active_routes[pat]
                removed.append(pat)
        return {"status": "stopped", "removed": removed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_instrumentation_log(
    tag_filter: str | None = None,
    type_filter: str | None = None,
    key_filter: str | None = None,
    limit: int = 500,
    clear: bool = False,
) -> dict:
    """Retrieve logs emitted by instrumented VMP source.

    Args:
        tag_filter: Filter by instrumentation tag (the tag= argument you
            passed to instrument_jsvmp_source).
        type_filter: "tap_get", "tap_call", "tap_method", or "tap_call_err".
        key_filter: Substring match on accessed property/method name.
        limit: Max entries to return.
        clear: If True, clear the log after retrieval.

    Returns:
        dict with entries, summary (hottest keys / methods), and counts.
    """
    try:
        page = await browser_manager.get_active_page()
        data = await page.evaluate("window.__mcp_vmp_log || []")

        if tag_filter:
            data = [d for d in data if d.get("tag") == tag_filter]
        if type_filter:
            data = [d for d in data if d.get("type") == type_filter]
        if key_filter:
            data = [d for d in data
                    if key_filter in (d.get("key") or "")
                    or key_filter in (d.get("method") or "")
                    or key_filter in (d.get("name") or "")]

        # Summary: hottest keys accessed, hottest methods called
        key_count: dict[str, int] = {}
        method_count: dict[str, int] = {}
        fn_count: dict[str, int] = {}
        for e in data:
            if e.get("type") == "tap_get":
                k = e.get("key", "?")
                key_count[k] = key_count.get(k, 0) + 1
            elif e.get("type") == "tap_method":
                m = f"{e.get('objType', '?')}.{e.get('method', '?')}"
                method_count[m] = method_count.get(m, 0) + 1
            elif e.get("type") == "tap_call":
                n = e.get("name", "anon")
                fn_count[n] = fn_count.get(n, 0) + 1

        if clear:
            await page.evaluate("window.__mcp_vmp_log = []")

        return {
            "entries": data[-limit:] if len(data) > limit else data,
            "total_entries": len(data),
            "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "hot_keys": dict(sorted(key_count.items(), key=lambda x: -x[1])[:30]),
                "hot_methods": dict(sorted(method_count.items(), key=lambda x: -x[1])[:30]),
                "hot_functions": dict(sorted(fn_count.items(), key=lambda x: -x[1])[:30]),
            },
        }
    except Exception as e:
        return {"error": str(e)}
