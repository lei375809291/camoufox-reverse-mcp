from __future__ import annotations

import asyncio
import base64
import json as _json
import os

from ..server import mcp, browser_manager

# Timeout for pre-inject operations on about:blank. Some hooks (jsvmp Proxy
# install, cookie descriptor walk) can wedge on opaque-origin blank pages;
# the persistent add_init_script path is what actually matters for the next
# real navigation, so evaluate here is strictly best-effort.
_PRE_INJECT_REGISTER_TIMEOUT = 5.0
_PRE_INJECT_EVAL_TIMEOUT = 3.0


@mcp.tool()
async def launch_browser(
    headless: bool = False,
    os_type: str = "auto",
    locale: str = "auto",
    proxy: str | None = None,
    humanize: bool = False,
    geoip: bool = False,
    block_images: bool = False,
    block_webrtc: bool = False,
) -> dict:
    """Launch the Camoufox anti-detection browser.

    Args:
        headless: Run in headless mode (default False for debugging visibility).
        os_type: OS fingerprint to emulate - "auto" (detect host OS),
            "windows", "macos", or "linux". Using "auto" ensures CJK fonts
            render correctly on the host system.
        locale: Browser locale such as "zh-CN", "en-US". Defaults to "auto"
            which detects the system locale. Affects Accept-Language headers
            and content language preferences.
        proxy: Proxy server URL (e.g. "http://127.0.0.1:7890").
        humanize: Enable humanized mouse movement to mimic real users.
        geoip: Auto-infer geolocation from proxy IP.
        block_images: Block image loading for faster page loads.
        block_webrtc: Block WebRTC to prevent IP leaks.

    Returns:
        dict with status, headless flag, os type, locale, and page list.
        If browser is already running, returns full session state including
        active page, page URLs, context list, and capture status.
    """
    try:
        config = {
            "headless": headless,
            "os": os_type,
            "locale": locale,
            "humanize": humanize,
            "geoip": geoip,
            "block_images": block_images,
            "block_webrtc": block_webrtc,
        }
        if proxy:
            config["proxy"] = {"server": proxy}
        return await browser_manager.launch(config)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def close_browser() -> dict:
    """Close the Camoufox browser and release all resources.

    Returns:
        dict with status "closed".
    """
    try:
        return await browser_manager.close()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def navigate(
    url: str,
    wait_until: str = "load",
    pre_inject_hooks: list[str] | None = None,
    via_blank: bool = False,
    collect_response_chain: bool = True,
) -> dict:
    """Navigate to a URL, with optional hook pre-injection and redirect tracing.

    For sites that detect/challenge on the very first request (Rui Shu,
    Akamai, etc.), normal `navigate` misses the initial JS because probes
    inject AFTER the page loads. Use `pre_inject_hooks` to guarantee hooks
    are installed before ANY target-site JS runs.

    Args:
        url: Target URL.
        wait_until: "load", "domcontentloaded", or "networkidle".
        pre_inject_hooks: Optional list of hook preset names to inject BEFORE
            navigation begins. Accepts any preset from inject_hook_preset
            ("xhr", "fetch", "crypto", "websocket", "debugger_bypass") and
            also the special names:
                - "jsvmp_probe"       - default jsvmp_hook.js probe
                - "cookie_hook"       - document.cookie prototype hook
                - "runtime_probe"     - full runtime_probe.js
            This routes through about:blank first, evaluates all hooks there,
            then navigates to the target URL - guaranteeing hooks are hot
            when the target page's first <script> executes.
        via_blank: If True, always go via about:blank first (even without
            pre_inject_hooks). Useful to ensure persistent scripts from
            previous sessions are applied.
        collect_response_chain: If True (default), record every response
            during this navigation so final_status reflects JS-driven
            redirects (Rui Shu 412 -> 200 after cookie challenge).

    Returns:
        dict with:
            url: Final URL after all redirects
            title: Page title
            initial_status: First HTTP response status (what page.goto saw)
            final_status: Last response status on the main frame
                          (None if no main frame response observed)
            redirect_chain: List of {url, status, ts} for every response
                            (only populated if collect_response_chain=True)
            hooks_injected: List of hook names actually injected
            warnings: Non-fatal issues (failed hook injection, etc.)
    """
    try:
        page = await browser_manager.get_active_page()
        warnings: list[str] = []
        hooks_injected: list[str] = []

        # Reset response chain for this navigation
        if collect_response_chain:
            browser_manager.reset_nav_responses()

        # Pre-inject via about:blank
        needs_blank = bool(pre_inject_hooks) or via_blank
        if needs_blank:
            try:
                await page.goto("about:blank", wait_until="domcontentloaded")
            except Exception as e:
                warnings.append(f"about:blank navigation failed: {e}")

            if pre_inject_hooks:
                for name in pre_inject_hooks:
                    ok, msg = await _inject_hook_by_name(name)
                    if ok:
                        hooks_injected.append(name)
                        if msg != "ok":
                            warnings.append(f"pre-inject '{name}': {msg}")
                    else:
                        warnings.append(f"pre-inject '{name}' failed: {msg}")

        # Main navigation
        resp = await page.goto(url, wait_until=wait_until)
        initial_status = resp.status if resp else None

        # Find final status on main frame
        final_status = None
        chain = []
        if collect_response_chain:
            chain = list(browser_manager._nav_responses)
            # Main-frame candidates: url matches current page.url or was document
            for r in reversed(chain):
                if r["url"] == page.url or r.get("resource_type") == "document":
                    final_status = r["status"]
                    break

        return {
            "url": page.url,
            "title": await page.title(),
            "initial_status": initial_status,
            "final_status": final_status if final_status is not None else initial_status,
            "redirect_chain": chain if collect_response_chain else None,
            "hooks_injected": hooks_injected,
            "warnings": warnings if warnings else None,
        }
    except Exception as e:
        return {"error": str(e)}


async def _inject_hook_by_name(name: str) -> tuple[bool, str]:
    """Dispatch pre-inject hooks by symbolic name. Returns (ok, msg).

    Two-step flow:
      1. Register the script at context level (add_init_script). This is the
         load-bearing step — it guarantees the hook runs on the NEXT real
         page.goto(url) before any target-site JS executes.
      2. Best-effort evaluate on the current about:blank page so the hook is
         also live there. Wrapped in JS try/catch AND an asyncio timeout:
         some hooks (jsvmp Proxy install, cookie descriptor walk) can wedge
         on opaque blank pages, and we must not let that block navigation.
    """
    hooks_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "hooks")

    preset_files = {
        "xhr": "xhr_hook.js",
        "fetch": "fetch_hook.js",
        "crypto": "crypto_hook.js",
        "websocket": "websocket_hook.js",
        "debugger_bypass": "debugger_trap.js",
        "cookie_hook": "cookie_hook.js",
        "runtime_probe": "runtime_probe.js",
    }

    # Step A: build the JS
    try:
        if name == "jsvmp_probe":
            with open(os.path.join(hooks_dir, "jsvmp_hook.js"), "r", encoding="utf-8") as f:
                tpl = f.read()
            default_proxy = ["navigator", "screen", "history", "localStorage",
                             "sessionStorage", "performance"]
            js = (tpl
                .replace("{{SCRIPT_URL}}", "")
                .replace("{{MAX_ENTRIES}}", "10000")
                .replace("{{TRACK_CALLS}}", "true")
                .replace("{{TRACK_PROPS}}", "true")
                .replace("{{TRACK_REFLECT}}", "true")
                .replace("'{{PROXY_OBJECTS}}'", _json.dumps(_json.dumps(default_proxy)))
            )
            persist_name = "pre_inject:jsvmp_probe"
        elif name in preset_files:
            fpath = os.path.join(hooks_dir, preset_files[name])
            if not os.path.exists(fpath):
                return False, f"hook file not found: {preset_files[name]}"
            with open(fpath, "r", encoding="utf-8") as f:
                js = f.read()
            persist_name = f"pre_inject:{name}"
        else:
            return False, f"unknown hook name: {name}"
    except Exception as e:
        return False, f"prepare failed: {e}"

    # Step B: register at context level (must succeed)
    try:
        await asyncio.wait_for(
            browser_manager.add_persistent_script(persist_name, js),
            timeout=_PRE_INJECT_REGISTER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return False, "add_persistent_script timed out"
    except Exception as e:
        return False, f"add_persistent_script failed: {e}"

    # Step C: best-effort evaluate on current (about:blank) page. Wrap in
    # JS try/catch so a failing hook body does not leave the page in a
    # half-installed state, and wrap in asyncio.wait_for so a wedged
    # evaluate cannot block the whole navigate call.
    try:
        page = await browser_manager.get_active_page()
    except Exception as e:
        return True, f"registered (no active page for eval: {e})"

    wrapped = (
        "(function(){try{" + js + "}catch(e){"
        "try{console.warn('[pre_inject:" + name + "] '+e.message);}catch(_){}}"
        "})()"
    )
    try:
        await asyncio.wait_for(page.evaluate(wrapped), timeout=_PRE_INJECT_EVAL_TIMEOUT)
        return True, "ok"
    except asyncio.TimeoutError:
        return True, "registered (blank-page eval timed out; will fire on goto)"
    except Exception as e:
        return True, f"registered (blank-page eval error: {e})"


@mcp.tool()
async def reload(wait_until: str = "load") -> dict:
    """Reload the current page, preserving any init scripts.

    Args:
        wait_until: "load", "domcontentloaded", or "networkidle".

    Returns:
        dict with url and title after reload.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.reload(wait_until=wait_until)
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def go_back() -> dict:
    """Navigate back in browser history.

    Returns:
        dict with url and title after going back.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.go_back()
        return {"url": page.url, "title": await page.title()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def take_screenshot(full_page: bool = False, selector: str | None = None) -> dict:
    """Take a screenshot of the current page or a specific element.

    Args:
        full_page: Capture the entire scrollable page (default False).
        selector: CSS selector of a specific element to capture.

    Returns:
        dict with base64-encoded PNG image data.
    """
    try:
        page = await browser_manager.get_active_page()
        if selector:
            elem = await page.query_selector(selector)
            if not elem:
                return {"error": f"Element not found: {selector}"}
            data = await elem.screenshot()
        else:
            data = await page.screenshot(full_page=full_page)
        return {
            "screenshot_base64": base64.b64encode(data).decode(),
            "format": "png",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def take_snapshot() -> dict:
    """Get the accessibility tree of the current page as a structured text representation.

    More token-efficient than screenshots for AI analysis. Returns the page's
    semantic structure including roles, names, and values of UI elements.

    Returns:
        dict with the accessibility tree snapshot.
    """
    try:
        page = await browser_manager.get_active_page()
        # Try modern Playwright API first, fall back to legacy
        try:
            snapshot = await page.accessibility.snapshot()
        except AttributeError:
            # Playwright >= 1.42 removed page.accessibility, use JS fallback
            snapshot = await page.evaluate("""() => {
                function walk(node) {
                    if (!node) return null;
                    const item = {};
                    const tag = node.tagName ? node.tagName.toLowerCase() : '';
                    const role = node.getAttribute ? (node.getAttribute('role') || tag) : '';
                    if (role) item.role = role;
                    const name = node.getAttribute ? (node.getAttribute('aria-label')
                        || node.getAttribute('alt') || node.getAttribute('title')
                        || (node.tagName === 'INPUT' ? node.getAttribute('placeholder') : '')
                        || '') : '';
                    if (name) item.name = name;
                    if (['INPUT','TEXTAREA','SELECT'].includes(node.tagName)) {
                        item.value = node.value || '';
                    }
                    const text = [];
                    const children = [];
                    for (const child of (node.childNodes || [])) {
                        if (child.nodeType === 3) {
                            const t = child.textContent.trim();
                            if (t) text.push(t);
                        } else if (child.nodeType === 1) {
                            const c = walk(child);
                            if (c) children.push(c);
                        }
                    }
                    if (text.length && !children.length) item.text = text.join(' ');
                    if (children.length) item.children = children;
                    if (!item.role && !item.name && !item.text && !children.length) return null;
                    return item;
                }
                return walk(document.body);
            }""")
        return {"snapshot": snapshot}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def click(selector: str) -> dict:
    """Click on a page element.

    Args:
        selector: CSS selector of the element to click.

    Returns:
        dict with status and the selector that was clicked.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.click(selector)
        return {"status": "clicked", "selector": selector}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def type_text(selector: str, text: str, delay: int = 50) -> dict:
    """Type text into an input field with realistic keystroke delays.

    Args:
        selector: CSS selector of the input element.
        text: Text to type.
        delay: Delay between keystrokes in milliseconds (default 50).

    Returns:
        dict with status, selector, and the text typed.
    """
    try:
        page = await browser_manager.get_active_page()
        await page.type(selector, text, delay=delay)
        return {"status": "typed", "selector": selector, "text": text}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def wait_for(
    selector: str | None = None,
    url_pattern: str | None = None,
    timeout: int = 30000,
) -> dict:
    """Wait for an element to appear or a network request matching a URL pattern.

    Args:
        selector: CSS selector to wait for (element appearance).
        url_pattern: URL glob pattern to wait for (network request completion).
        timeout: Maximum wait time in milliseconds (default 30000).

    Returns:
        dict with status and what was waited for.
    """
    try:
        page = await browser_manager.get_active_page()
        if selector:
            await page.wait_for_selector(selector, timeout=timeout)
            return {"status": "found", "selector": selector}
        elif url_pattern:
            await page.wait_for_url(url_pattern, timeout=timeout)
            return {"status": "matched", "url_pattern": url_pattern}
        else:
            return {"error": "Provide either selector or url_pattern"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_page_info() -> dict:
    """Get information about the current page including URL, title, and viewport size.

    Returns:
        dict with url, title, and viewport dimensions.
    """
    try:
        page = await browser_manager.get_active_page()
        viewport = page.viewport_size or {}
        return {
            "url": page.url,
            "title": await page.title(),
            "viewport_width": viewport.get("width"),
            "viewport_height": viewport.get("height"),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_page_content() -> dict:
    """Export the rendered DOM content of the current page in one call.

    Returns rendered HTML, page title, meta tags, and visible text.
    Saves writing ad-hoc evaluate_js scripts for common extraction needs.

    Returns:
        dict with title, meta tags, rendered_html (first 50KB), and visible_text (first 20KB).
    """
    try:
        page = await browser_manager.get_active_page()
        result = await page.evaluate("""() => {
            const title = document.title || '';
            const metas = Array.from(document.querySelectorAll('meta')).map(m => {
                const o = {};
                for (const a of m.attributes) o[a.name] = a.value;
                return o;
            });
            const html = document.documentElement.outerHTML;
            // Extract visible text: walk body, skip script/style/hidden
            function visibleText(node) {
                if (!node) return '';
                if (node.nodeType === 3) return node.textContent;
                if (node.nodeType !== 1) return '';
                const tag = node.tagName;
                if (['SCRIPT','STYLE','NOSCRIPT','SVG'].includes(tag)) return '';
                const style = window.getComputedStyle(node);
                if (style.display === 'none' || style.visibility === 'hidden') return '';
                let text = '';
                for (const child of node.childNodes) text += visibleText(child);
                if (['P','DIV','BR','LI','H1','H2','H3','H4','H5','H6','TR','DT','DD'].includes(tag)) {
                    text = '\\n' + text + '\\n';
                }
                return text;
            }
            let visible = visibleText(document.body).replace(/\\n{3,}/g, '\\n\\n').trim();
            return { title, metas, html, visible };
        }""")
        html = result.get("html", "")
        visible = result.get("visible", "")
        MAX_HTML = 50000
        MAX_TEXT = 20000
        resp = {
            "title": result.get("title"),
            "meta": result.get("metas"),
            "rendered_html": html[:MAX_HTML],
            "visible_text": visible[:MAX_TEXT],
        }
        if len(html) > MAX_HTML:
            resp["html_truncated"] = True
            resp["html_total_size"] = len(html)
        if len(visible) > MAX_TEXT:
            resp["text_truncated"] = True
            resp["text_total_size"] = len(visible)
        return resp
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_session_info() -> dict:
    """Get the current session state: browser, contexts, pages, capture status.

    Shows exactly what browser/context/page is active and what hooks/captures
    are in place. Use this to understand the current debugging environment.

    Returns:
        dict with browser status, context list, page list, active page,
        capture state, persistent scripts, and init scripts.
    """
    try:
        running = browser_manager.browser is not None
        contexts = list(browser_manager.contexts.keys())
        pages = {}
        for name, p in browser_manager.pages.items():
            try:
                pages[name] = {"url": p.url, "title": await p.title()}
            except Exception:
                pages[name] = {"url": "unknown", "title": "unknown"}
        persistent = [s["name"] for s in browser_manager._persistent_scripts]
        return {
            "browser_running": running,
            "contexts": contexts,
            "pages": pages,
            "active_page": browser_manager.active_page_name,
            "network_capture": {
                "active": browser_manager._capturing,
                "pattern": browser_manager._capture_pattern,
                "capture_body": browser_manager._capture_body,
                "captured_count": len(browser_manager._network_requests),
            },
            "persistent_scripts": persistent,
            "init_scripts_count": len(browser_manager._init_scripts),
            "console_log_count": len(browser_manager._console_logs),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def reload_with_hooks(
    clear_log: bool = True,
    wait_until: str = "load",
) -> dict:
    """Reload the current page so that persistent hooks run BEFORE page JS.

    Common use case: you navigate to a page, THEN install jsvmp probe, then
    want to re-trigger the VMP with probe hot. This is the canonical way -
    it uses context-level add_init_script (which survives reload) AND clears
    the log so you get a clean capture from the re-run.

    Args:
        clear_log: Clear window.__mcp_jsvmp_log and window.__mcp_prop_access_log
            before reload (default True).
        wait_until: load / domcontentloaded / networkidle.

    Returns:
        dict with url, title, final_status, response_chain.
    """
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
            "url": page.url,
            "title": await page.title(),
            "initial_status": resp.status if resp else None,
            "final_status": final_status if final_status is not None else (resp.status if resp else None),
            "redirect_chain": chain,
        }
    except Exception as e:
        return {"error": str(e)}
