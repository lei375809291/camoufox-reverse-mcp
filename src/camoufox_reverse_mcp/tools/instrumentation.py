"""
instrumentation.py - Source-level JSVMP instrumentation (v0.9.0 unified).

Merges instrument_jsvmp_source / get_instrumentation_log /
stop_instrumentation / reload_with_hooks into a single tool.
"""
from __future__ import annotations
import time

from ..server import mcp, browser_manager
from ..utils.js_rewriter import (
    regex_rewrite,
    INSTRUMENT_RUNTIME,
    ACORN_REWRITE_JS_TEMPLATE,
)
from ..utils.ast_rewriter import ast_rewrite as _ast_rewrite_py

# Module-level state for active instrumentation routes
_active_routes: dict[str, dict] = {}


@mcp.tool()
async def instrumentation(
    action: str,
    url_pattern: str = "",
    mode: str = "ast",
    tag: str = "vmp",
    rewrite_member_access: bool = True,
    rewrite_calls: bool = True,
    max_rewrites: int = 20000,
    fallback_on_error: bool = True,
    ignore_csp: bool = False,
    clear_log: bool = True,
    wait_until: str = "load",
    tag_filter: str | None = None,
    type_filter: str | None = None,
    key_filter: str | None = None,
    limit: int = 500,
    clear: bool = False,
) -> dict:
    """JSVMP source-level instrumentation (v0.9.0 unified).

    Replaces instrument_jsvmp_source / get_instrumentation_log /
    stop_instrumentation / reload_with_hooks.

    Args:
        action:
          "install" — register route + AST/regex rewrite on matched scripts.
                      Requires url_pattern. (was: instrument_jsvmp_source)
          "log"     — fetch accumulated tap events from instrumented code.
                      (was: get_instrumentation_log)
          "stop"    — unregister instrumentation route.
                      (was: stop_instrumentation)
          "reload"  — reload page so persistent hooks fire before page JS.
                      (was: reload_with_hooks)
          "status"  — show active instrumentations and stats.
                      (was: get_instrumentation_status)
        url_pattern: For "install"/"stop" — glob pattern matching VMP script URLs.
        mode: For "install" — "ast" (default) or "regex".
        tag: For "install"/"log" — group identifier.
        rewrite_member_access: For "install" — tap obj[key] reads.
        rewrite_calls: For "install" — tap fn(args) calls.
        max_rewrites: For "install" — hard cap on rewrites per file.
        fallback_on_error: For "install" — auto-fallback to regex if AST fails.
        ignore_csp: For "install" — skip CSP pre-flight check.
        clear_log: For "reload" — clear JSVMP logs before reload.
        wait_until: For "reload" — "load" / "domcontentloaded" / "networkidle".
        tag_filter: For "log" — filter by tag.
        type_filter: For "log" — "tap_get", "tap_call", "tap_method", "tap_call_err".
        key_filter: For "log" — substring match on property/method name.
        limit: For "log" — max entries to return.
        clear: For "log" — clear log after retrieval.

    Returns:
        dict with action-specific results.
    """
    if action == "install":
        return await _install(url_pattern, mode, tag, rewrite_member_access,
                              rewrite_calls, max_rewrites, fallback_on_error, ignore_csp)
    elif action == "log":
        return await _get_log(tag_filter, type_filter, key_filter, limit, clear)
    elif action == "stop":
        return await _stop(url_pattern or None)
    elif action == "reload":
        return await _reload_with_hooks(clear_log, wait_until)
    elif action == "status":
        return _get_status()
    else:
        return {"error": f"unknown action: {action}. Use install/log/stop/reload/status"}


def _get_status() -> dict:
    return {
        "active_patterns": [
            {
                "pattern": pat, "mode": info["mode"], "tag": info["tag"],
                "files_rewritten": info["stats"]["files_rewritten"],
                "total_edits": info["stats"]["total_edits"],
                "last_url": info["stats"]["last_url"],
                "cached_urls": len(info["cache"]),
            }
            for pat, info in _active_routes.items()
        ],
        "total_patterns": len(_active_routes),
    }


async def _detect_csp_risk(page) -> dict:
    try:
        probe = await page.evaluate(r"""
          (async () => {
            return new Promise(resolve => {
              var marker = '__mcp_csp_probe_' + Math.random().toString(36).slice(2);
              var s = document.createElement('script');
              s.textContent = 'window["' + marker + '"] = 1;';
              var violated = false;
              var handler = function(e) {
                if (e.violatedDirective && e.violatedDirective.indexOf('script-src') !== -1) {
                  violated = true;
                }
              };
              document.addEventListener('securitypolicyviolation', handler, { once: true });
              try { document.head.appendChild(s); } catch (e) {}
              try { document.head.removeChild(s); } catch (e) {}
              setTimeout(() => {
                document.removeEventListener('securitypolicyviolation', handler);
                var ran = window[marker] === 1;
                try { delete window[marker]; } catch (e) {}
                var meta = document.querySelector('meta[http-equiv="Content-Security-Policy"]');
                resolve({ ran: ran, violated: violated, csp_meta: meta ? meta.content : null });
              }, 80);
            });
          })()
        """)
    except Exception:
        return {"blocks": False, "csp_meta": None, "reasons": []}
    reasons: list[str] = []
    blocks = bool(probe.get("violated") or not probe.get("ran"))
    if blocks:
        reasons.append("inline <script> execution blocked or violated CSP")
    return {"blocks": blocks, "csp_meta": probe.get("csp_meta"), "reasons": reasons}


async def _install(url_pattern, mode, tag, rewrite_member_access,
                   rewrite_calls, max_rewrites, fallback_on_error, ignore_csp) -> dict:
    try:
        if not url_pattern:
            return {"error": "url_pattern is required for action='install'"}
        page = await browser_manager.get_active_page()

        if not ignore_csp:
            csp = await _detect_csp_risk(page)
            if csp["blocks"]:
                return {
                    "status": "refused_csp_blocks_inline",
                    "reasons": csp["reasons"],
                    "recommended": "Use hook_jsvmp_interpreter(mode='transparent') or ignore_csp=True",
                }

        cache: dict[str, str] = {}
        stats = {"files_rewritten": 0, "total_edits": 0, "last_url": None,
                 "last_mode_used": None}

        async def route_handler(route):
            try:
                req_url = route.request.url
                if req_url in cache:
                    await route.fulfill(
                        status=200,
                        headers={"content-type": "application/javascript; charset=utf-8"},
                        body=cache[req_url],
                    )
                    return
                resp = await route.fetch()
                body_bytes = await resp.body()
                try:
                    src = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    src = body_bytes.decode("latin-1")

                rewritten = src
                edit_count = 0
                mode_used = mode

                if mode == "ast":
                    ast_out, ast_stats = _ast_rewrite_py(
                        src, tag=tag, rewrite_member_access=rewrite_member_access,
                        rewrite_calls=rewrite_calls, max_edits=max_rewrites,
                    )
                    if ast_out is not None:
                        rewritten = ast_out
                        edit_count = ast_stats.get("edits", 0)
                    elif fallback_on_error:
                        mode_used = "regex (fallback)"
                        rw, rstats = regex_rewrite(
                            src, tag=tag, rewrite_member_access=rewrite_member_access,
                            max_rewrites=max_rewrites,
                        )
                        rewritten = rw
                        edit_count = rstats.get("member_access_rewrites", 0)
                elif mode == "regex":
                    rw, rstats = regex_rewrite(
                        src, tag=tag, rewrite_member_access=rewrite_member_access,
                        max_rewrites=max_rewrites,
                    )
                    rewritten = rw
                    edit_count = rstats.get("member_access_rewrites", 0)

                cache[req_url] = rewritten
                stats["files_rewritten"] += 1
                stats["total_edits"] += edit_count
                stats["last_url"] = req_url
                stats["last_mode_used"] = mode_used

                headers = dict(resp.headers)
                headers.pop("content-length", None)
                headers.pop("Content-Length", None)
                headers["content-type"] = "application/javascript; charset=utf-8"
                await route.fulfill(status=resp.status, headers=headers, body=rewritten)
            except Exception as e:
                try:
                    await route.continue_()
                except Exception:
                    pass

        await page.route(url_pattern, route_handler)
        _active_routes[url_pattern] = {
            "handler": route_handler, "cache": cache, "stats": stats,
            "mode": mode, "tag": tag,
        }
        return {
            "status": "instrumenting", "pattern": url_pattern,
            "mode": mode, "tag": tag,
            "note": "Navigate or reload to trigger rewrite.",
        }
    except Exception as e:
        return {"error": str(e)}


async def _get_log(tag_filter, type_filter, key_filter, limit, clear) -> dict:
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

        key_count: dict[str, int] = {}
        method_count: dict[str, int] = {}
        for e in data:
            if e.get("type") == "tap_get":
                k = e.get("key", "?")
                key_count[k] = key_count.get(k, 0) + 1
            elif e.get("type") == "tap_method":
                m = f"{e.get('objType', '?')}.{e.get('method', '?')}"
                method_count[m] = method_count.get(m, 0) + 1

        if clear:
            await page.evaluate("window.__mcp_vmp_log = []")
        return {
            "entries": data[-limit:] if len(data) > limit else data,
            "total_entries": len(data), "returned": min(len(data), limit),
            "truncated": len(data) > limit,
            "summary": {
                "hot_keys": dict(sorted(key_count.items(), key=lambda x: -x[1])[:30]),
                "hot_methods": dict(sorted(method_count.items(), key=lambda x: -x[1])[:30]),
            },
        }
    except Exception as e:
        return {"error": str(e)}


async def _stop(url_pattern) -> dict:
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


async def _reload_with_hooks(clear_log: bool = True, wait_until: str = "load") -> dict:
    try:
        page = await browser_manager.get_active_page()
        if clear_log:
            try:
                await page.evaluate("""() => {
                    if (window.__mcp_jsvmp_log) window.__mcp_jsvmp_log.length = 0;
                    if (window.__mcp_prop_access_log) window.__mcp_prop_access_log.length = 0;
                    if (window.__mcp_cookie_log) window.__mcp_cookie_log.length = 0;
                }""")
            except Exception:
                pass
        browser_manager.reset_nav_responses()
        resp = await page.reload(wait_until=wait_until)
        chain = list(browser_manager._nav_responses)
        final_status = None
        for r in reversed(chain):
            if r["url"] == page.url or r.get("resource_type") == "document":
                final_status = r["status"]
                break
        return {
            "url": page.url, "title": await page.title(),
            "initial_status": resp.status if resp else None,
            "final_status": final_status or (resp.status if resp else None),
            "redirect_chain": chain,
        }
    except Exception as e:
        return {"error": str(e)}
