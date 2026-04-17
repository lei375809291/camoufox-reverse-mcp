from __future__ import annotations

import os

from ..server import mcp, browser_manager


@mcp.tool()
async def list_scripts() -> list[dict]:
    """List all JavaScript scripts loaded in the current page.

    Collects both external <script src="..."> and inline <script> elements.
    For inline scripts, returns a preview of the first 200 characters.

    Returns:
        List of dicts with src, type, inline_length, is_module, and preview fields.
    """
    try:
        page = await browser_manager.get_active_page()
        scripts = await page.evaluate("""() => {
            const scripts = document.querySelectorAll('script');
            return Array.from(scripts).map((s, i) => ({
                index: i,
                src: s.src || null,
                type: s.type || 'text/javascript',
                is_module: s.type === 'module',
                inline_length: s.src ? 0 : (s.textContent || '').length,
                preview: s.src ? null : (s.textContent || '').substring(0, 200)
            }));
        }""")
        return scripts
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
async def get_script_source(url: str) -> str:
    """Get the full source code of a JavaScript file by URL.

    For external scripts, fetches the source via the browser's fetch API.
    For inline scripts, pass the index as "inline:<index>" (e.g. "inline:0")
    to get the textContent of the corresponding <script> element.

    Args:
        url: The script URL, or "inline:<index>" for inline scripts.

    Returns:
        The raw JavaScript source code string.
    """
    try:
        page = await browser_manager.get_active_page()
        if url.startswith("inline:"):
            idx = int(url.split(":")[1])
            source = await page.evaluate(f"""() => {{
                const scripts = document.querySelectorAll('script');
                return scripts[{idx}] ? scripts[{idx}].textContent : null;
            }}""")
            return source or f"Inline script at index {idx} not found"
        else:
            source = await page.evaluate(f"""async () => {{
                try {{
                    const resp = await fetch("{url}");
                    return await resp.text();
                }} catch(e) {{
                    return "Fetch error: " + e.message;
                }}
            }}""")
            return source
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
async def search_code(keyword: str, max_results: int = 50) -> dict:
    """Search for a keyword across all loaded JavaScript sources in the page.

    Fetches each script's source and searches for the keyword, returning
    matching lines with 2 lines of surrounding context.

    Args:
        keyword: The keyword or pattern to search for (case-sensitive substring match).
        max_results: Maximum number of matches to return (default 50, max 200).

    Returns:
        dict with matches list, total_matches count, scripts_searched count,
        and scripts_with_matches list. When results are truncated, total_matches
        shows the real total so you know how many were omitted.
    """
    try:
        if max_results > 200:
            max_results = 200

        page = await browser_manager.get_active_page()
        results = await page.evaluate(f"""async () => {{
            const keyword = {repr(keyword)};
            const scripts = document.querySelectorAll('script');
            const matches = [];
            const maxResults = {max_results};
            let totalMatches = 0;
            let scriptsSearched = 0;
            const scriptsWithMatches = [];

            for (const s of scripts) {{
                let source = '';
                let scriptUrl = '';
                if (s.src) {{
                    scriptUrl = s.src;
                    try {{
                        const resp = await fetch(s.src);
                        source = await resp.text();
                    }} catch(e) {{ continue; }}
                }} else {{
                    scriptUrl = 'inline:' + scriptsSearched;
                    source = s.textContent || '';
                }}
                scriptsSearched++;

                const lines = source.split('\\n');
                let scriptMatchCount = 0;
                for (let i = 0; i < lines.length; i++) {{
                    if (lines[i].includes(keyword)) {{
                        totalMatches++;
                        scriptMatchCount++;
                        if (matches.length < maxResults) {{
                            const start = Math.max(0, i - 2);
                            const end = Math.min(lines.length, i + 3);
                            const contextLines = lines.slice(start, end);
                            const contextStr = contextLines.join('\\n');
                            matches.push({{
                                script_url: scriptUrl,
                                line_number: i + 1,
                                match: lines[i].trim().substring(0, 500),
                                context: contextStr.length > 2000
                                    ? contextStr.substring(0, 2000) + '...(truncated)'
                                    : contextStr
                            }});
                        }}
                    }}
                }}
                if (scriptMatchCount > 0) {{
                    scriptsWithMatches.push({{
                        url: scriptUrl,
                        match_count: scriptMatchCount,
                        source_length: source.length
                    }});
                }}
            }}
            return {{
                matches: matches,
                total_matches: totalMatches,
                returned_matches: matches.length,
                scripts_searched: scriptsSearched,
                scripts_with_matches: scriptsWithMatches,
                truncated: totalMatches > matches.length
            }};
        }}""")
        return results
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def search_code_in_script(
    script_url: str,
    keyword: str,
    context_lines: int = 3,
    context_chars: int = 200,
    max_results: int = 200,
) -> dict:
    """Search for a keyword within a specific script file.

    v0.6.0: Auto-detects single-line / minified files (<10 lines or any
    line >5000 chars) and returns CHARACTER-BASED context windows
    (keyword ± context_chars) instead of line-based. Critical for minified
    JSVMP files (single-line, 240KB+).

    Args:
        script_url: URL of the script to search in. Use "inline:<index>" for
            inline scripts.
        keyword: The keyword to search for (case-sensitive substring match).
        context_lines: Context window in line mode (default 3).
        context_chars: Context window in char mode (default 200 = ±200 chars).
        max_results: Maximum number of matches (default 200).

    Returns:
        dict with total_matches, returned, mode ("line" | "char"), results.
    """
    import json as _json
    try:
        page = await browser_manager.get_active_page()

        if script_url.startswith("inline:"):
            idx = int(script_url.split(":")[1])
            src = await page.evaluate(f"""() => {{
                const scripts = document.querySelectorAll('script');
                return scripts[{idx}] ? (scripts[{idx}].textContent || '') : null;
            }}""")
            if src is None:
                return {"error": f"Inline script not found at index {idx}"}
        else:
            src = await page.evaluate(
                f"fetch({_json.dumps(script_url)}, {{cache: 'force-cache'}}).then(r => r.text())"
            )

        if not isinstance(src, str):
            return {"error": f"script not fetchable: got {type(src).__name__}"}

        lines = src.split("\n")
        max_line_len = max((len(l) for l in lines), default=0)
        use_char_mode = len(lines) < 10 or max_line_len > 5000

        results: list[dict] = []
        total = 0

        if use_char_mode:
            i = 0
            while True:
                pos = src.find(keyword, i)
                if pos == -1:
                    break
                total += 1
                if len(results) < max_results:
                    start = max(0, pos - context_chars)
                    end = min(len(src), pos + len(keyword) + context_chars)
                    results.append({
                        "position": pos,
                        "context_start": start,
                        "context_end": end,
                        "context": src[start:end],
                        "match_highlight_range": [pos - start, pos - start + len(keyword)],
                    })
                i = pos + len(keyword)

            return {
                "total_matches": total,
                "returned": len(results),
                "script_url": script_url,
                "mode": "char",
                "source_size": len(src),
                "total_lines": len(lines),
                "max_line_length": max_line_len,
                "context_chars": context_chars,
                "results": results,
            }

        # Line-based mode
        for idx, line in enumerate(lines):
            if keyword in line:
                total += 1
                if len(results) < max_results:
                    start = max(0, idx - context_lines)
                    end = min(len(lines), idx + context_lines + 1)
                    ctx = "\n".join(lines[start:end])
                    results.append({
                        "line": idx + 1,
                        "context": ctx[:3000] + ("...(truncated)" if len(ctx) > 3000 else ""),
                        "context_range": [start + 1, end],
                    })

        return {
            "total_matches": total,
            "returned": len(results),
            "script_url": script_url,
            "mode": "line",
            "total_lines": len(lines),
            "results": results,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def save_script(url: str, save_path: str) -> dict:
    """Download a JavaScript file from the page and save it to a local path.

    Args:
        url: URL of the script to download.
        save_path: Local file path to save the script to.

    Returns:
        dict with status, save path, and file size in bytes.
    """
    try:
        page = await browser_manager.get_active_page()
        source = await page.evaluate(f"""async () => {{
            const resp = await fetch("{url}");
            return await resp.text();
        }}""")
        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(source)
        return {"status": "saved", "path": save_path, "size": len(source)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_page_html(selector: str | None = None) -> str:
    """Get the full page HTML or the outerHTML of a specific element.

    Args:
        selector: Optional CSS selector. If provided, returns only that
            element's outerHTML. If omitted, returns the full page HTML.

    Returns:
        HTML string of the page or selected element.
    """
    try:
        page = await browser_manager.get_active_page()
        if selector:
            html = await page.evaluate(f"""() => {{
                const el = document.querySelector("{selector}");
                return el ? el.outerHTML : null;
            }}""")
            return html or f"Element not found: {selector}"
        else:
            return await page.content()
    except Exception as e:
        return f"Error: {e}"
