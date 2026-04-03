from __future__ import annotations

from ..server import mcp, browser_manager


@mcp.tool()
async def evaluate_js(expression: str, await_promise: bool = True) -> dict:
    """Execute an arbitrary JavaScript expression in the page context and return the result.

    This is the most fundamental and powerful tool for reverse engineering.
    Use it to inspect variables, call functions, check prototypes, etc.

    Examples:
        - evaluate_js("window._token")
        - evaluate_js("window.encrypt('test')")
        - evaluate_js("Object.getOwnPropertyNames(XMLHttpRequest.prototype)")

    Args:
        expression: JavaScript expression to evaluate.
        await_promise: If True, awaits Promise results (default True).

    Returns:
        dict with result value and its JavaScript type.
    """
    try:
        page = await browser_manager.get_active_page()
        if await_promise:
            result = await page.evaluate(f"""async () => {{
                try {{
                    const r = await (async () => {{ return {expression}; }})();
                    return {{ result: JSON.parse(JSON.stringify(r)), type: typeof r }};
                }} catch(e) {{
                    return {{ error: e.message, type: 'error' }};
                }}
            }}""")
        else:
            result = await page.evaluate(f"""() => {{
                try {{
                    const r = (() => {{ return {expression}; }})();
                    return {{ result: JSON.parse(JSON.stringify(r)), type: typeof r }};
                }} catch(e) {{
                    return {{ error: e.message, type: 'error' }};
                }}
            }}""")
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def evaluate_js_handle(expression: str) -> dict:
    """Execute a JavaScript expression and return a structured view of the result object.

    Unlike evaluate_js, this preserves complex object references and enumerates
    their properties, useful for inspecting DOM elements, prototypes, etc.

    Args:
        expression: JavaScript expression to evaluate.

    Returns:
        dict with the object's properties (name, type, value preview).
    """
    try:
        page = await browser_manager.get_active_page()
        handle = await page.evaluate_handle(expression)
        properties = await handle.get_properties()
        result = {}
        for name, prop in properties.items():
            try:
                val = await prop.json_value()
                result[name] = val
            except Exception:
                result[name] = str(await prop.evaluate("x => typeof x"))
        await handle.dispose()
        return {"properties": result, "keys_count": len(result)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def add_init_script(
    script: str | None = None,
    path: str | None = None,
    persistent: bool = False,
    name: str | None = None,
) -> dict:
    """Inject a script that runs automatically before any page JavaScript on every navigation.

    This is the core method for installing hooks — the hook code runs before
    the target site's JS loads, ensuring interception is in place.

    Args:
        script: JavaScript code string to inject.
        path: Path to a .js file to inject (alternative to script).
        persistent: If True, inject at context level so the script survives page
            navigation, new tabs, and reload automatically. Recommended for hooks
            that must always be present.
        name: Optional identifier for persistent scripts (for later removal).

    Returns:
        dict with status and the method used (inline or file path).
    """
    try:
        content = None
        method = "inline"
        if script:
            content = script
        elif path:
            method = "file"
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        else:
            return {"error": "Provide either script or path"}

        if persistent:
            script_name = name or f"init_script_{len(browser_manager._persistent_scripts)}"
            await browser_manager.add_persistent_script(script_name, content)
            browser_manager._init_scripts.append(f"persistent:{script_name}")
            return {
                "status": "injected",
                "method": method,
                "persistent": True,
                "name": script_name,
                "length": len(content),
            }
        else:
            page = await browser_manager.get_active_page()
            await page.add_init_script(script=content)
            browser_manager._init_scripts.append(content[:200])
            return {"status": "injected", "method": method, "persistent": False, "length": len(content)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def freeze_prototype(class_name: str, method_name: str) -> dict:
    """Make a prototype method non-overridable by page scripts.

    After calling this, any attempt by page JS to reassign the method will
    silently fail (or throw in strict mode). Use after installing hooks to
    prevent the target site from restoring original methods.

    Args:
        class_name: The class/constructor name, e.g. "XMLHttpRequest",
            "Document", "Navigator".
        method_name: The method name on the prototype, e.g. "open", "send".

    Returns:
        dict with status and the frozen target.
    """
    try:
        page = await browser_manager.get_active_page()
        js = f"""(() => {{
    const cls = {repr(class_name)};
    const method = {repr(method_name)};
    let target;
    try {{ target = eval(cls); }} catch(e) {{ return {{ error: 'Class not found: ' + cls }}; }}
    const proto = target.prototype || target;
    const current = proto[method];
    if (typeof current !== 'function' && current === undefined) {{
        return {{ error: 'Method not found: ' + method + ' on ' + cls }};
    }}
    try {{
        Object.defineProperty(proto, method, {{
            value: current, writable: false, configurable: false
        }});
        return {{ status: 'frozen', target: cls + '.prototype.' + method }};
    }} catch(e) {{
        return {{ error: 'Failed to freeze: ' + e.message }};
    }}
}})();"""
        result = await page.evaluate(js)
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def set_breakpoint_via_hook(
    target_function: str,
    script_url_pattern: str | None = None,
    persistent: bool = False,
) -> dict:
    """Set a pseudo-breakpoint on a function via JS hooking.

    When the target function is called, captures its arguments, call stack,
    this context, timestamp, and return value. Data is stored in
    window.__mcp_breakpoints and can be retrieved with get_breakpoint_data.

    Args:
        target_function: Full path to the function (e.g. "window.encrypt",
            "XMLHttpRequest.prototype.open").
        script_url_pattern: Optional URL pattern to limit which scripts are affected.
        persistent: If True, the breakpoint survives page navigation.

    Returns:
        dict with status and the target function name.
    """
    try:
        hook_js = f"""(() => {{
    const path = {repr(target_function)};
    const parts = path.split('.');
    let parent = window;
    for (let i = 0; i < parts.length - 1; i++) {{
        parent = parent[parts[i]];
        if (!parent) {{ console.warn('[BREAKPOINT] Cannot find:', path); return; }}
    }}
    const funcName = parts[parts.length - 1];
    const _orig = parent[funcName];
    if (typeof _orig !== 'function') {{
        console.warn('[BREAKPOINT] Not a function:', path);
        return;
    }}
    window.__mcp_breakpoints = window.__mcp_breakpoints || [];
    const wrapper = function(...args) {{
        const info = {{
            target: path,
            args: (() => {{ try {{ return JSON.stringify(args).substring(0, 5000); }} catch(e) {{ return String(args); }} }})(),
            stack: new Error().stack,
            thisContext: typeof this,
            timestamp: Date.now()
        }};
        const result = _orig.apply(this, args);
        try {{ info.returnValue = JSON.stringify(result).substring(0, 5000); }}
        catch(e) {{ info.returnValue = String(result); }}
        window.__mcp_breakpoints.push(info);
        if (window.__mcp_breakpoints.length > 500) window.__mcp_breakpoints.shift();
        return result;
    }};
    Object.defineProperty(wrapper, 'name', {{ value: funcName }});
    Object.defineProperty(wrapper, 'length', {{ value: _orig.length }});
    wrapper.toString = function() {{ return _orig.toString(); }};
    try {{
        Object.defineProperty(parent, funcName, {{
            value: wrapper, writable: false, configurable: false
        }});
    }} catch(e) {{
        parent[funcName] = wrapper;
    }}
    console.log('[BREAKPOINT] Set on:', path);
}})();"""
        if persistent:
            bp_name = f"breakpoint:{target_function}"
            await browser_manager.add_persistent_script(bp_name, hook_js)
            return {"status": "set", "target": target_function, "persistent": True}
        else:
            page = await browser_manager.get_active_page()
            await page.evaluate(hook_js)
            return {"status": "set", "target": target_function, "persistent": False}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_breakpoint_data(clear: bool = False) -> list[dict]:
    """Retrieve all data captured by pseudo-breakpoints.

    Args:
        clear: If True, clear the captured data after retrieval.

    Returns:
        List of dicts with args, stack, thisContext, returnValue, timestamp.
    """
    try:
        page = await browser_manager.get_active_page()
        data = await page.evaluate("window.__mcp_breakpoints || []")
        if clear:
            await page.evaluate("window.__mcp_breakpoints = []")
        return data
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_console_logs(
    level: str | None = None,
    keyword: str | None = None,
    clear: bool = False,
) -> list[dict]:
    """Get console output collected from the page.

    Args:
        level: Filter by log level - "log", "warn", "error", or "info".
        keyword: Filter logs containing this keyword in the text.
        clear: If True, clear the log buffer after retrieval.

    Returns:
        List of dicts with level, text, timestamp, and location.
    """
    try:
        logs = list(browser_manager._console_logs)
        if level:
            logs = [l for l in logs if l["level"] == level]
        if keyword:
            logs = [l for l in logs if keyword in (l.get("text") or "")]
        if clear:
            browser_manager._console_logs.clear()
        return logs
    except Exception as e:
        return [{"error": str(e)}]
