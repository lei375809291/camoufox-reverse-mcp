from __future__ import annotations

import json
import time
import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from ..utils.domains import domain_matches

from ..server import mcp, browser_manager


@mcp.tool()
async def network_capture(
    action: str,
    url_pattern: str = "**/*",
    capture_body: bool = False,
    max_body_size: int = 200000,
    wait_timeout_ms: int = 0,
) -> dict:
    """Unified network capture control (v0.9.0).

    Replaces start_network_capture / stop_network_capture.

    Args:
        action:
          "start"  — begin capturing network events
          "stop"   — stop capturing (buffer retained)
          "clear"  — clear the capture buffer
          "status" — return current capture state
        url_pattern: Glob pattern for "start" (default "**/*" captures all).
        capture_body: For "start" only; capture response bodies (more memory).
        max_body_size: For start; retained characters per response, 0..2000000.
            Playwright still reads the complete response before truncation.
        wait_timeout_ms: For stop; wait up to 30000ms for captured responses.
            New requests stop immediately; unfinished work is reported, not replayed.
            Clear cancels pending work; IDs remain monotonic until browser close.

    Returns:
        dict with action result + current status snapshot.
    """
    if not 0 <= wait_timeout_ms <= 30000:
        return {"error": "wait_timeout_ms must be between 0 and 30000"}
    if action == "start":
        if not 0 <= max_body_size <= 2_000_000:
            return {"error": "max_body_size must be between 0 and 2000000"}
        browser_manager._capture_body_limit = max_body_size
        browser_manager._capturing = True
        browser_manager._capture_pattern = url_pattern
        browser_manager._capture_body = capture_body
        return {"status": "capturing", "pattern": url_pattern,
                "capture_body": capture_body}
    elif action == "stop":
        browser_manager._capturing = False
        deadline = asyncio.get_running_loop().time() + wait_timeout_ms / 1000
        while (browser_manager._request_entries or browser_manager._capture_tasks):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.02, remaining))
        return {"status": "stopped",
                "total_requests": len(browser_manager._network_requests),
                **browser_manager.capture_status()}
    elif action == "clear":
        count = browser_manager.clear_network_capture()
        return {"status": "cleared", "cleared_count": count}
    elif action == "status":
        return browser_manager.capture_status()
    else:
        return {"error": f"unknown action: {action}. Use start/stop/clear/status"}


@mcp.tool()
async def list_network_requests(
    url_filter: str | None = None,
    url_contains_domain: str | None = None,
    method: str | None = None,
    resource_type: str | None = None,
    status_code: int | None = None,
    limit: int | None = None,
    after_id: int | None = None,
) -> list[dict]:
    """List captured network requests with optional filters.

    Args:
        url_filter: Substring filter for request URLs.
        url_contains_domain: Host/subdomain boundary filter (e.g. "example.com").
        method: HTTP method filter (e.g. "GET", "POST").
        resource_type: Resource type filter (e.g. "xhr", "fetch", "script", "document").
        status_code: HTTP status code filter.
        limit: Optional page size (1..2000); omitted preserves the full list.
        after_id: Return IDs greater than this cursor, in capture order.
            Check network_capture(status).dropped_requests for lost history.

    Returns:
        List of request summaries with id, url, method, status, type, ms, size.
    """
    try:
        if limit is not None and not 1 <= limit <= 2000:
            return [{"error": "limit must be between 1 and 2000"}]
        if after_id is not None and after_id < 0:
            return [{"error": "after_id must be non-negative"}]
        reqs = list(browser_manager._network_requests)
        if after_id is not None:
            reqs = [r for r in reqs if r["id"] > after_id]
        if url_filter:
            reqs = [r for r in reqs if url_filter in r["url"]]
        if url_contains_domain:
            reqs = [r for r in reqs if domain_matches(urlsplit(r["url"]).hostname or "", url_contains_domain)]
        if method:
            reqs = [r for r in reqs if r["method"].upper() == method.upper()]
        if resource_type:
            reqs = [r for r in reqs if r.get("resource_type") == resource_type]
        if status_code is not None:
            reqs = [r for r in reqs if r.get("status") == status_code]

        if limit is not None:
            reqs = reqs[:limit]
        summaries = []
        for r in reqs:
            body_size = len(r["response_body"]) if r.get("response_body") else 0
            summaries.append({
                "id": r["id"], "url": r["url"][:200], "method": r["method"],
                "status": r.get("status"), "type": r.get("resource_type"),
                "ms": r.get("duration"), "size": body_size,
                "has_body": r.get("response_body") is not None,
                "state": r.get("state"), "body_state": r.get("body_state"),
                "failure": r.get("failure"),
                "body_truncated": r.get("response_body_capture_truncated", False),
            })
        return summaries
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_network_request(
    request_id: int,
    include_body: bool = False,
    include_headers: bool = True,
    max_body_size: int = 5000,
) -> dict:
    """Get full details of a specific captured network request.

    Args:
        request_id: The ID of the request (from list_network_requests).
        include_body: Include response body (default False).
        include_headers: Include request/response headers (default True).
        max_body_size: Max chars of body when include_body=True. Pass -1 for unlimited.

    Returns:
        dict with request and response details.
    """
    try:
        for r in browser_manager._network_requests:
            if r["id"] == request_id:
                result = dict(r)
                if not include_body:
                    body = result.pop("response_body", None)
                    result["response_body_available"] = body is not None
                    if body:
                        result["response_body_size"] = len(body)
                else:
                    body = result.get("response_body")
                    if body is not None:
                        capture_truncated = bool(r.get("response_body_capture_truncated",
                                                       r.get("response_body_truncated", False)))
                        return_truncated = max_body_size >= 0 and len(body) > max_body_size
                        returned = body[:max_body_size] if return_truncated else body
                        result.update(
                            response_body=returned,
                            response_body_truncated=capture_truncated or return_truncated,
                            response_body_capture_truncated=capture_truncated,
                            response_body_return_truncated=return_truncated,
                            response_body_original_size=r.get("response_body_total_size", len(body)),
                            response_body_stored_size=len(body),
                            response_body_size_returned=len(returned),
                        )
                if not include_headers:
                    result.pop("request_headers", None)
                    result.pop("response_headers", None)
                return result
        return {"error": f"Request ID {request_id} not found"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_request_initiator(request_id: int) -> dict:
    """Get the JS call stack that initiated a network request.

    Golden path: see encrypted param -> get_request_initiator -> find signing function.
    Requires inject_hook_preset("xhr"/"fetch") BEFORE navigating.

    KNOWN LIMITATIONS (v0.8.1+):
      1. For requests modified by an interceptor registered BEFORE MCP's
         hooks (e.g. SDKs loaded via sync <script>), the initiator will be
         the interceptor's call, not the original business code.
         Workaround: use reload_with_hooks().
      2. For fetch on Firefox, Playwright-native initiator is often null.
         Requires inject_hook_preset('fetch', persistent=True).

    Args:
        request_id: The ID of the request.

    Returns:
        dict with url, initiator_stack, source, diagnostics.
    """
    try:
        target_entry = None
        for r in browser_manager._network_requests:
            if r["id"] == request_id:
                target_entry = r
                break
        if target_entry is None:
            return {"error": f"Request ID {request_id} not found"}

        page = await browser_manager.get_active_page()
        req_url = target_entry["url"]
        escaped_url = json.dumps(req_url)

        result = await page.evaluate(f"""() => {{
            const reqUrl = {escaped_url};
            function searchLogs(logs, type) {{
                if (!logs || !logs.length) return null;
                for (let i = logs.length - 1; i >= 0; i--) {{
                    const log = logs[i];
                    const logUrl = log.url || '';
                    if (reqUrl === logUrl || reqUrl.includes(logUrl) || logUrl.includes(reqUrl)) {{
                        return {{
                            url: logUrl, stack: log.stack || null, type: type,
                            method: log.method, headers: log.headers,
                            body: log.body ? String(log.body).substring(0, 2000) : null,
                            timestamp: log.timestamp
                        }};
                    }}
                    try {{
                        const u1 = new URL(reqUrl, location.origin);
                        const u2 = new URL(logUrl, location.origin);
                        if (u1.pathname === u2.pathname && u1.host === u2.host) {{
                            return {{
                                url: logUrl, stack: log.stack || null, type: type,
                                method: log.method, headers: log.headers,
                                body: log.body ? String(log.body).substring(0, 2000) : null,
                                timestamp: log.timestamp
                            }};
                        }}
                    }} catch(e) {{}}
                }}
                return null;
            }}
            const xhrResult = searchLogs(window.__mcp_xhr_log, 'xhr');
            if (xhrResult) return xhrResult;
            const fetchResult = searchLogs(window.__mcp_fetch_log, 'fetch');
            if (fetchResult) return fetchResult;
            const fetchInitLog = window.__mcp_fetch_initiator_log || [];
            for (let i = fetchInitLog.length - 1; i >= 0; i--) {{
                const entry = fetchInitLog[i];
                const logUrl = entry.url || '';
                if (reqUrl === logUrl || reqUrl.includes(logUrl) || logUrl.includes(reqUrl)) {{
                    return {{ url: logUrl, stack: entry.stack || null, type: 'fetch_hook',
                              method: entry.method, timestamp: entry.ts }};
                }}
                try {{
                    const u1 = new URL(reqUrl, location.origin);
                    const u2 = new URL(logUrl, location.origin);
                    if (u1.pathname === u2.pathname && u1.host === u2.host) {{
                        return {{ url: logUrl, stack: entry.stack || null, type: 'fetch_hook',
                                  method: entry.method, timestamp: entry.ts }};
                    }}
                }} catch(e) {{}}
            }}
            return {{
                url: reqUrl, stack: null, type: 'unknown',
                diagnostics: {{
                    xhr_hook_active: !!window.__mcp_xhr_hooked,
                    fetch_hook_active: !!window.__mcp_fetch_hooked,
                    hint: !window.__mcp_xhr_hooked && !window.__mcp_fetch_hooked
                        ? 'No hooks detected. Call inject_hook_preset("xhr"/"fetch") BEFORE navigating.'
                        : 'Hooks active but no matching URL found in logs.'
                }}
            }};
        }}""")

        source = result.get("type", "unknown")
        return {
            "url": result.get("url"),
            "initiator_stack": result.get("stack"),
            "initiator_type": source,
            "source": source,
            "method": result.get("method"),
            "request_headers": result.get("headers"),
            "request_body": result.get("body"),
            "diagnostics": result.get("diagnostics"),
            "diagnostic": (
                {
                    "likely_causes": [
                        "hook registered after SDK (try reload_with_hooks)",
                        "request made inside a sync-loaded SDK interceptor",
                        "fetch_hook.js not injected",
                    ],
                    "recommended_action": "Use reload_with_hooks() or inject hooks before navigate.",
                }
                if source in ("unknown", None) else None
            ),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def intercept_request(
    url_pattern: str,
    action: str = "log",
    modify_headers: dict | None = None,
    modify_body: str | None = None,
    mock_response: dict | None = None,
) -> dict:
    """Intercept network requests matching a pattern.

    Args:
        url_pattern: URL glob pattern (e.g. "**/api/login*").
        action: "log", "block", "modify", "mock", or "stop" (unroute).
        modify_headers: Headers to add/override (action="modify").
        modify_body: Request body replacement (action="modify").
        mock_response: Dict with "status", "headers", "body" (action="mock").
    """
    try:
        page = await browser_manager.get_active_page()

        if action == "stop":
            if url_pattern:
                await page.unroute(url_pattern)
                return {"status": "stopped", "pattern": url_pattern}
            else:
                await page.unroute("**/*")
                return {"status": "stopped_all"}

        async def handler(route):
            if action == "log":
                browser_manager._console_logs.append({
                    "level": "info",
                    "text": f"[INTERCEPT:log] {route.request.method} {route.request.url}",
                    "timestamp": time.time() * 1000, "location": None,
                })
                await route.continue_()
            elif action == "block":
                await route.abort()
            elif action == "modify":
                overrides = {}
                if modify_headers:
                    overrides["headers"] = {**dict(route.request.headers), **modify_headers}
                if modify_body:
                    overrides["post_data"] = modify_body
                await route.continue_(**overrides)
            elif action == "mock":
                resp = mock_response or {}
                await route.fulfill(
                    status=resp.get("status", 200),
                    headers=resp.get("headers", {"content-type": "application/json"}),
                    body=resp.get("body", "{}"),
                )

        await page.route(url_pattern, handler)
        return {"status": "intercepting", "pattern": url_pattern, "action": action}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def export_network_capture(
    save_path: str,
    include_body: bool = False,
    include_sensitive: bool = False,
    url_filter: str | None = None,
) -> dict:
    """Export a versioned JSON snapshot of this manager's retained capture.

    Args:
        save_path: New local JSON path; existing files are never overwritten.
        include_body: Include captured request/response bodies only together
            with include_sensitive=True. Does not fetch or replay requests.
        include_sensitive: Opt in to original headers, query values and bodies.
            Default masks all header/query values, removes URL credentials and
            fragments, and omits bodies. URL paths are retained: this is not full
            anonymization. Keep original captures out of public repositories.
        url_filter: Optional URL substring filter.

    Returns:
        Path, count, redaction mode and capture status including pending/dropped
        work. For a settled snapshot call network_capture(stop, wait_timeout_ms).
    """
    try:
        if include_body and not include_sensitive:
            return {"error": "include_body requires include_sensitive=True"}
        rows = []
        for entry in browser_manager._network_requests:
            if url_filter and url_filter not in entry["url"]:
                continue
            row = dict(entry)
            if not include_body:
                row.pop("request_post_data", None)
                row.pop("response_body", None)
            if not include_sensitive:
                parts = urlsplit(row["url"])
                row["url"] = urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1],
                    parts.path, urlencode([(key, "[REDACTED]") for key, _ in
                                            parse_qsl(parts.query, keep_blank_values=True)]), ""))
                for field in ("request_headers", "response_headers"):
                    row[field] = {key: "[REDACTED]" for key in (row.get(field) or {})}
                # Transport error strings can contain original URLs.
                for field in ("failure", "headers_error", "body_error"):
                    if row.get(field):
                        row[field] = "[REDACTED]"
            rows.append(row)
        from .. import __version__
        status = browser_manager.capture_status()
        payload = {"schema_version": 1, "mcp_version": __version__,
                   "exported_at": int(time.time() * 1000),
                   "include_sensitive": include_sensitive, "include_body": include_body,
                   "capture": status, "requests": rows}
        if not include_sensitive:
            payload["capture"] = {**status, "pattern": "[REDACTED]"}
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        path = Path(save_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create prevents accidentally destroying another capture.
        with path.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        return {"status": "exported", "path": str(path), "count": len(rows),
                "schema_version": 1, "redacted": not include_sensitive,
                "capture": payload["capture"]}
    except Exception as exc:
        return {"error": str(exc)}
