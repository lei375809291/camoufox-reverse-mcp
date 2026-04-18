"""Domain-keyed session lifecycle tools."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..server import mcp, browser_manager
from ..domain_session import get_store, normalize_domain


@mcp.tool()
async def start_reverse_session(
    domain: Optional[str] = None,
    note: str = "",
    related_cases: Optional[list[str]] = None,
    record_tool_calls: bool = True,
    record_network: bool = True,
    record_instrumentation: bool = True,
    record_console: bool = False,
) -> dict:
    """Start a new analysis run in the domain-keyed session archive.

    Sessions are organized by eTLD+1 domain. Each call starts a new "run"
    inside that domain. Events are tagged with run_id; assertions and
    samples persist at the domain level across runs.

    Storage: ~/.camoufox-reverse/sessions/<domain>/

    Args:
        domain: eTLD+1 domain. If omitted, derived from current page URL.
        note: Free-form note about this run.
        related_cases: Case filenames that apply to this domain.
        record_*: Toggle what gets archived.
    """
    try:
        store = get_store()
        if not domain:
            try:
                page = await browser_manager.get_active_page()
                url = page.url
                if not url or url == "about:blank":
                    return {"error": "no active page or page is blank; "
                                     "call navigate() first or pass domain explicitly"}
                domain = normalize_domain(url)
            except Exception as e:
                return {"error": f"cannot auto-derive domain: {e}"}

        target_url = ""
        try:
            page = await browser_manager.get_active_page()
            target_url = page.url
        except Exception:
            pass

        recording = {"tool_calls": record_tool_calls, "network_events": record_network,
                     "instrumentation": record_instrumentation, "console": record_console}
        dom_path = store.root / domain
        is_new = not dom_path.exists()

        info, run = store.start_run(domain=domain, target_url=target_url,
                                     note=note, recording=recording,
                                     related_cases=related_cases)
        return {
            "status": "run_started", "domain": domain, "run_id": run.run_id,
            "path": str(info.path), "is_new_domain": is_new,
            "previous_run_count": len(info.runs) - 1,
            "existing_assertions": len(info.active_assertions),
            "related_cases": info.related_cases,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def stop_reverse_session(status: str = "closed") -> dict:
    """End the active run. Domain archive stays."""
    try:
        result = get_store().stop_run(status=status)
        if result is None:
            return {"status": "no_active_run"}
        info, run = result
        return {"status": "run_stopped", "domain": info.domain,
                "run_id": run.run_id, "run_status": run.status,
                "counters": run.counters, "domain_counters": info.domain_counters}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def get_session_snapshot(
    sections: Optional[list[str]] = None,
    run_id: str = "current",
    max_events_per_section: int = 50,
) -> dict:
    """Return a compact snapshot of the active domain session."""
    try:
        return get_store().snapshot(sections, run_id, max_events_per_section)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def list_sessions() -> dict:
    """List all domain session archives on disk."""
    try:
        return {"domains": get_store().list_domains()}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def attach_domain_readonly(domain: str) -> dict:
    """Attach an existing domain archive as active WITHOUT starting a new run."""
    try:
        info = get_store().attach_domain(domain)
        return {"status": "attached_readonly", "domain": info.domain,
                "run_count": len(info.runs),
                "active_assertions": len(info.active_assertions)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def export_session(output_path: Optional[str] = None) -> dict:
    """Zip the entire active domain archive."""
    try:
        store = get_store()
        if store.active is None:
            return {"error": "no active domain"}
        out = store.export_domain(Path(output_path) if output_path else None)
        return {"status": "exported", "domain": store.active.domain,
                "archive_path": str(out),
                "size_kb": round(out.stat().st_size / 1024, 1)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def import_session(archive_path: str, merge_strategy: str = "replace",
                         auto_attach: bool = True) -> dict:
    """Import a domain session zip."""
    try:
        store = get_store()
        info = store.import_domain(Path(archive_path), merge_strategy)
        result = {"status": "imported", "domain": info.domain,
                  "run_count": len(info.runs),
                  "active_assertions": len(info.active_assertions),
                  "merge_strategy": merge_strategy}
        if auto_attach:
            store.attach_domain(info.domain)
            result["attached_readonly"] = True
        return result
    except Exception as e:
        return {"error": str(e)}
