from __future__ import annotations

from ..server import mcp, browser_manager


@mcp.tool()
async def evaluate_js(expression: str, await_promise: bool = True) -> dict:
    """Execute an arbitrary JavaScript expression in the page context and return the result.

    Return value is aggressively cleaned (strips BOM, fixes lone surrogates,
    trims whitespace, auto-parses JSON strings). If direct evaluate fails
    with serialization error, automatically falls back to evaluate_handle.

    Args:
        expression: JavaScript expression to evaluate.
        await_promise: If True, awaits Promise results (default True).

    Returns:
        dict with keys:
          value       - cleaned value (parsed JSON if applicable)
          value_raw   - raw string before cleaning (only when cleaning applied)
          type        - "primitive" | "json" | "handle_fallback" | "error"
          warnings    - list of applied cleanups, if any
    """
    import asyncio as _asyncio
    import json as _json
    import re as _re

    def _clean_str(s: str) -> tuple[str, list[str]]:
        warns: list[str] = []
        if not isinstance(s, str):
            return s, warns
        if s.startswith("\ufeff"):
            s = s.lstrip("\ufeff")
            warns.append("stripped BOM")
        try:
            s.encode("utf-8")
        except UnicodeEncodeError:
            s = s.encode("utf-8", "replace").decode("utf-8")
            warns.append("replaced invalid unicode")
        stripped = s.strip()
        if stripped != s and stripped:
            s = stripped
            warns.append("trimmed whitespace")
        return s, warns

    def _parse_smart(s: str, warns: list[str]) -> tuple:
        if not isinstance(s, str) or not s.strip():
            return s, None
        first_char = s.lstrip()[:1]
        if first_char not in '[{"':
            return s, None
        e1_msg = ""
        try:
            return _json.loads(s), None
        except Exception as e1:
            e1_msg = str(e1)[:100]
        cleaned = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        if cleaned != s:
            try:
                val = _json.loads(cleaned)
                warns.append("stripped control chars")
                return val, None
            except Exception:
                pass
        if s.startswith('"') and s.endswith('"'):
            try:
                unwrapped = _json.loads(s)
                if isinstance(unwrapped, str) and unwrapped.lstrip()[:1] in '[{"':
                    try:
                        val = _json.loads(unwrapped)
                        warns.append("unwrapped double-encoded JSON")
                        return val, None
                    except Exception:
                        pass
            except Exception:
                pass
        return s, f"all JSON parse strategies failed: {e1_msg}"

    try:
        page = await browser_manager.get_active_page()
        try:
            if await_promise:
                raw = await page.evaluate(f"""async () => {{
                    try {{
                        const r = await (async () => {{ return {expression}; }})();
                        return {{ result: JSON.parse(JSON.stringify(r)), type: typeof r }};
                    }} catch(e) {{
                        return {{ error: e.message, type: 'error' }};
                    }}
                }}""")
            else:
                raw = await page.evaluate(f"""() => {{
                    try {{
                        const r = (() => {{ return {expression}; }})();
                        return {{ result: JSON.parse(JSON.stringify(r)), type: typeof r }};
                    }} catch(e) {{
                        return {{ error: e.message, type: 'error' }};
                    }}
                }}""")
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            if any(kw in low for kw in ("unexpected", "serialize", "cloneable", "circular", "cyclic")):
                try:
                    handle = await page.evaluate_handle(expression)
                    descr = await handle.evaluate(
                        "obj => ({"
                        "  type: typeof obj,"
                        "  ctor: obj && obj.constructor ? obj.constructor.name : null,"
                        "  keys: obj && typeof obj === 'object' ? "
                        "        Object.keys(obj).slice(0, 40) : null,"
                        "  preview: (function(){"
                        "    try { var s = JSON.stringify(obj); "
                        "          return s ? s.substring(0, 500) : String(obj).substring(0, 500); }"
                        "    catch(e) { return String(obj).substring(0, 500); }"
                        "  })()"
                        "})"
                    )
                    try:
                        await handle.dispose()
                    except Exception:
                        pass
                    return {
                        "type": "handle_fallback",
                        "value": descr,
                        "warnings": [f"direct evaluate failed, used handle fallback: {msg[:200]}"],
                    }
                except Exception as e2:
                    return {"type": "error", "error": f"both paths failed: {msg[:200]} / {e2}"}
            raise

        if isinstance(raw, dict) and "error" in raw:
            return {"type": "error", "error": raw["error"]}

        result_val = raw.get("result") if isinstance(raw, dict) else raw
        warnings_list: list[str] = []

        if isinstance(result_val, str):
            cleaned, w = _clean_str(result_val)
            warnings_list.extend(w)
            parsed, parse_err = _parse_smart(cleaned, warnings_list)
            if parse_err is None and parsed is not cleaned:
                return {
                    "type": "json", "value": parsed,
                    "value_raw": result_val if warnings_list else None,
                    "warnings": warnings_list if warnings_list else None,
                }
            if parse_err is not None:
                warnings_list.append(parse_err)
            return {
                "type": "primitive", "value": cleaned,
                "value_raw": result_val if warnings_list else None,
                "warnings": warnings_list if warnings_list else None,
            }

        return {
            "type": "primitive" if not isinstance(result_val, (dict, list)) else "json",
            "value": result_val,
        }
    except Exception as e:
        return {"type": "error", "error": str(e)}
