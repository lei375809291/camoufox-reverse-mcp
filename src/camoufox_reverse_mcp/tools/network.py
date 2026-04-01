from __future__ import annotations

import json
import time

from ..server import mcp, browser_manager


@mcp.tool()
async def start_network_capture(
    url_pattern: str = "**/*",
    capture_body: bool = False,
) -> dict:
    """Start capturing network requests matching the given URL pattern.

    Captured data includes URL, method, headers, body, status, response headers,
    resource type, timing, etc. Use list_network_requests to view captures.

    Args:
        url_pattern: Glob pattern to filter captured URLs (default "**/*" captures all).
        capture_body: If True, also capture response bodies. This increases memory
            usage but allows inspecting actual response data. Default False.

    Returns:
        dict with status and the active capture pattern.
    """
    try:
        browser_manager._capturing = True
        browser_manager._capture_pattern = url_pattern
        browser_manager._capture_body = capture_body
        return {"status": "capturing", "pattern": url_pattern, "capture_body": capture_body}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def stop_network_capture() -> dict:
    """Stop capturing network requests.

    Returns:
        dict with status and total number of captured requests.
    """
    try:
        browser_manager._capturing = False
        total = len(browser_manager._network_requests)
        return {"status": "stopped", "total_requests": total}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_network_requests(
    url_filter: str | None = None,
    method: str | None = None,
    resource_type: str | None = None,
    status_code: int | None = None,
) -> list[dict]:
    """List captured network requests with optional filters.

    Args:
        url_filter: Substring filter for request URLs.
        method: HTTP method filter (e.g. "GET", "POST").
        resource_type: Resource type filter (e.g. "xhr", "fetch", "script", "document").
        status_code: HTTP status code filter.

    Returns:
        List of request summaries with id, url, method, status, resource_type, duration, size.
    """
    try:
        reqs = list(browser_manager._network_requests)
        if url_filter:
            reqs = [r for r in reqs if url_filter in r["url"]]
        if method:
            reqs = [r for r in reqs if r["method"].upper() == method.upper()]
        if resource_type:
            reqs = [r for r in reqs if r.get("resource_type") == resource_type]
        if status_code is not None:
            reqs = [r for r in reqs if r.get("status") == status_code]

        summaries = []
        for r in reqs:
            body_size = 0
            if r.get("response_body"):
                body_size = len(r["response_body"])
            summaries.append({
                "id": r["id"],
                "url": r["url"],
                "method": r["method"],
                "status": r.get("status"),
                "resource_type": r.get("resource_type"),
                "duration": r.get("duration"),
                "size": body_size,
                "has_body": r.get("response_body") is not None,
            })
        return summaries
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_network_request(
    request_id: int,
    include_body: bool = True,
) -> dict:
    """Get full details of a specific captured network request.

    Args:
        request_id: The ID of the request (from list_network_requests).
        include_body: Whether to include the response body in the result (default True).
            Set to False for large responses to save tokens.

    Returns:
        dict with complete request and response details including headers and body.
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
                return result
        return {"error": f"Request ID {request_id} not found"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_request_initiator(request_id: int) -> dict:
    """Get the JS call stack that initiated a network request.

    This is the golden path for locating encryption functions:
    see an encrypted parameter in a request -> get_request_initiator ->
    find the signing function in the call stack.

    Requires XHR/fetch hooks to be injected first. Use inject_hook_preset("xhr")
    and/or inject_hook_preset("fetch") with persistent=True BEFORE navigating
    to the target page.

    Args:
        request_id: The ID of the request.

    Returns:
        dict with url, initiator_stack, and initiator_type.
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
                            url: logUrl,
                            stack: log.stack || null,
                            type: type,
                            method: log.method,
                            headers: log.headers,
                            body: log.body ? String(log.body).substring(0, 2000) : null,
                            timestamp: log.timestamp
                        }};
                    }}
                    try {{
                        const urlObj1 = new URL(reqUrl, location.origin);
                        const urlObj2 = new URL(logUrl, location.origin);
                        if (urlObj1.pathname === urlObj2.pathname && urlObj1.host === urlObj2.host) {{
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

            const hasXhrHook = !!window.__mcp_xhr_hooked;
            const hasXhrLog = Array.isArray(window.__mcp_xhr_log);
            const xhrLogCount = hasXhrLog ? window.__mcp_xhr_log.length : 0;
            const hasFetchHook = !!window.__mcp_fetch_hooked;
            const hasFetchLog = Array.isArray(window.__mcp_fetch_log);
            const fetchLogCount = hasFetchLog ? window.__mcp_fetch_log.length : 0;

            return {{
                url: reqUrl,
                stack: null,
                type: 'unknown',
                diagnostics: {{
                    xhr_hook_active: hasXhrHook,
                    xhr_log_entries: xhrLogCount,
                    fetch_hook_active: hasFetchHook,
                    fetch_log_entries: fetchLogCount,
                    hint: !hasXhrHook && !hasFetchHook
                        ? 'No hooks detected. Call inject_hook_preset("xhr") and inject_hook_preset("fetch") with persistent=True BEFORE navigating to the page.'
                        : 'Hooks are active but no matching log entry found. The request may have been initiated by a Service Worker or other non-hookable mechanism.'
                }}
            }};
        }}""")

        return {
            "url": result.get("url"),
            "initiator_stack": result.get("stack"),
            "initiator_type": result.get("type"),
            "method": result.get("method"),
            "request_headers": result.get("headers"),
            "request_body": result.get("body"),
            "diagnostics": result.get("diagnostics"),
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
    """Intercept network requests matching a pattern and perform an action.

    Args:
        url_pattern: URL glob pattern to match (e.g. "**/api/login*").
        action: What to do with matched requests:
            - "log": Log the request without modifying it.
            - "block": Block the request entirely.
            - "modify": Modify request headers or body before sending.
            - "mock": Return a mock response without sending the real request.
        modify_headers: Headers to add/override (only for action="modify").
        modify_body: Request body replacement (only for action="modify").
        mock_response: Mock response dict with "status", "headers", "body"
            (only for action="mock").

    Returns:
        dict with status, pattern, and action.
    """
    try:
        page = await browser_manager.get_active_page()

        async def handler(route):
            if action == "log":
                request = route.request
                browser_manager._console_logs.append({
                    "level": "info",
                    "text": f"[INTERCEPT:log] {request.method} {request.url}",
                    "timestamp": time.time() * 1000,
                    "location": None,
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
async def stop_intercept(url_pattern: str | None = None) -> dict:
    """Stop intercepting requests.

    Args:
        url_pattern: Specific pattern to stop intercepting.
            If omitted, stops all interceptions.

    Returns:
        dict with status.
    """
    try:
        page = await browser_manager.get_active_page()
        if url_pattern:
            await page.unroute(url_pattern)
            return {"status": "stopped", "pattern": url_pattern}
        else:
            await page.unroute("**/*")
            return {"status": "stopped_all"}
    except Exception as e:
        return {"error": str(e)}
