# camoufox-reverse-mcp

[中文](README.md) | [English](README_en.md)

> Anti-detection browser MCP server for JavaScript reverse engineering.

An MCP (Model Context Protocol) server that gives AI coding assistants (Claude Code, Cursor, Cline, etc.) the ability to perform JavaScript reverse engineering through the **Camoufox** anti-detection browser — including API parameter analysis, JS source analysis, dynamic debugging, function hooking, network interception, JSVMP bytecode analysis, and cookie/storage management.

## Why Camoufox?

| Feature | chrome-devtools-mcp | **camoufox-reverse-mcp** |
|---------|--------------------|-----------------------|
| Browser Engine | Chrome (Puppeteer) | **Firefox (Camoufox)** |
| Anti-Detection | None | **C++ engine-level fingerprint spoofing** |
| Debug Capability | Limited (no breakpoints) | **Playwright + JS Hook** |
| JSVMP Analysis | None | **Interpreter instrumentation + property tracing + string extraction** |
| Hook Persistence | Not supported | **Context-level persistence, auto re-inject after navigation** |

**Core Advantages:**
- Camoufox modifies fingerprint information at the **C++ engine level**, not JS patches
- Juggler protocol sandbox isolation makes Playwright **completely undetectable** by page JS
- BrowserForge generates fingerprints based on **real-world traffic distribution**
- Works on sites with strong bot detection: Rui Shu, GeeTest, Cloudflare, etc.
- Hooks use `Object.defineProperty` with **override protection**, page scripts cannot restore original methods

---

## Quick Start

### Option 1: Install via AI Chat (Recommended)

Paste the following into your AI coding tool's chat (Cursor / Claude Code / Codex, etc.):

```
Please configure camoufox-reverse-mcp and refer to this MCP for related operations: https://github.com/WhiteNightShadow/camoufox-reverse-mcp
```

The AI will automatically clone, install dependencies, and configure the MCP server.

### Option 2: Manual Installation

**1. Clone the repository**

```bash
git clone https://github.com/WhiteNightShadow/camoufox-reverse-mcp.git
cd camoufox-reverse-mcp
```

**2. Install dependencies**

```bash
pip install -e .
```

Or with uv:

```bash
uv pip install -e .
```

**3. Configure your AI tool**

Add the MCP server config to your tool's configuration file (see "Client Configuration" below).

---

## Usage

### As MCP Server (stdio)

```bash
python -m camoufox_reverse_mcp
```

With options:

```bash
python -m camoufox_reverse_mcp \
  --proxy http://127.0.0.1:7890 \
  --geoip \
  --humanize \
  --os windows
```

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--proxy` | Proxy server URL | None |
| `--headless` | Headless mode | false |
| `--os` | OS fingerprint (windows/macos/linux) | windows |
| `--geoip` | Infer geolocation from proxy IP | false |
| `--humanize` | Humanized mouse movement | false |
| `--block-images` | Block image loading | false |
| `--block-webrtc` | Block WebRTC | false |

### Client Configuration

<details>
<summary><b>Cursor (.cursor/mcp.json)</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": ["-m", "camoufox_reverse_mcp"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": ["-m", "camoufox_reverse_mcp", "--headless"]
    }
  }
}
```

</details>

<details>
<summary><b>Claude Code (with proxy)</b></summary>

```json
{
  "mcpServers": {
    "camoufox-reverse": {
      "command": "python",
      "args": [
        "-m", "camoufox_reverse_mcp",
        "--proxy", "http://127.0.0.1:7890",
        "--geoip",
        "--humanize"
      ]
    }
  }
}
```

</details>

---

## Available Tools (57)

### Navigation & Page
- `launch_browser` — Launch Camoufox (returns full session state if already running)
- `close_browser` — Close browser and release resources
- `navigate` — Navigate to URL
- `reload` / `go_back` — Reload / go back
- `take_screenshot` / `take_snapshot` — Screenshot / accessibility tree (Playwright compat)
- `click` / `type_text` — Click / type into elements
- `wait_for` — Wait for element or URL pattern
- `get_page_info` — Get current page info
- `get_page_content` — **[New]** Export rendered HTML + title + meta + visible text in one call
- `get_session_info` — **[New]** View current session: browser/context/page/capture/hook status

### Script Analysis (Reverse Engineering Core)
- `list_scripts` — List all loaded JS scripts
- `get_script_source` — Get full JS source code
- `search_code` — Search keyword across all scripts (returns total matches & script list)
- `search_code_in_script` — Search in a specific script (more precise, avoids full scan)
- `save_script` — Save JS file locally
- `get_page_html` — Get page HTML

### Debugging (Reverse Engineering Core)
- `evaluate_js` — Execute arbitrary JS in page context
- `evaluate_js_handle` — Execute JS and inspect complex objects
- `add_init_script` — Inject scripts that run before page JS (**persistent** support)
- `freeze_prototype` — Freeze prototype methods to prevent hook overrides
- `set_breakpoint_via_hook` — Set pseudo-breakpoints via JS hooks (**persistent** support)
- `get_breakpoint_data` — Get captured breakpoint data
- `get_console_logs` — Get page console output

### Hooking (Reverse Engineering Core)
- `trace_function` — Trace function calls without pausing (**persistent**, cross-navigation)
- `get_trace_data` — Get collected trace data (merges page + persistent data)
- `hook_function` — Inject custom hook code (before/after/replace, **non_overridable**)
- `inject_hook_preset` — One-click preset hooks (xhr/fetch/crypto/websocket/debugger_bypass, **persistent by default**)
- `trace_property_access` — Track property access (Proxy-level), reveals JSVMP env reads
- `get_property_access_log` — Get property access records
- `remove_hooks` — Remove all hooks (optionally keep persistent ones)

### Network Analysis (Reverse Engineering Core)
- `start_network_capture` / `stop_network_capture` — Capture network traffic (**capture_body** support)
- `list_network_requests` — List captured requests with filters
- `get_network_request` — Get full request details (**include_headers=False** to save tokens)
- `get_request_initiator` — Get JS call stack that initiated a request
- `intercept_request` — Intercept: log / block / modify / mock
- `stop_intercept` — Stop interception
- `search_response_body` — **[New]** Full-text search across all captured response bodies
- `get_response_body_page` — **[New]** Paginated reading of large response bodies
- `search_json_path` — **[New]** Extract JSON data by dot-notation path (`data[*].id` wildcard)

### JSVMP Reverse Analysis
- `hook_jsvmp_interpreter` — Instrument JSVMP interpreter: trace API calls & property reads
- `get_jsvmp_log` — Get JSVMP execution log (with API call stats & property summary)
- `dump_jsvmp_strings` — Extract JSVMP string table: decode obfuscated strings, find API names
- `compare_env` — Collect browser env fingerprint for comparison with Node.js/jsdom

### Storage Management
- `get_cookies` / `set_cookies` / `delete_cookies` — Cookie management
- `get_storage` / `set_storage` — localStorage / sessionStorage
- `export_state` / `import_state` — Save / restore browser state

### Fingerprint & Anti-Detection
- `get_fingerprint_info` — Inspect current browser fingerprint
- `check_detection` — Test anti-detection on bot detection sites
- `bypass_debugger_trap` — Bypass anti-debugging traps

---

## Usage Scenarios

### Scenario 1: Reverse Engineer Login API Signing

```
AI workflow:
1. launch_browser(headless=False, os_type="windows")
2. inject_hook_preset("xhr")           ← Inject XHR hook (persistent by default)
3. inject_hook_preset("crypto")        ← Inject crypto hooks
4. navigate("https://example.com/login")
5. type_text("#username", "test_user")
6. type_text("#password", "test_pass")
7. click("#login-btn")
8. list_network_requests(method="POST") ← Find encrypted request
9. get_network_request(request_id=3)    ← View full params
10. get_request_initiator(request_id=3) ← Find signing at main.js:1234
11. get_script_source("https://example.com/js/main.js")
12. search_code("sign")                 ← Search signing code
13. hook_function("window.getSign", ...)
14. reload → get_trace_data("window.getSign")
15. Output complete signing algorithm reconstruction
```

### Scenario 2: JSVMP-Protected Sites

```
AI workflow:
1. launch_browser(headless=False)
2. bypass_debugger_trap()                ← Bypass anti-debugging first
3. inject_hook_preset("xhr")             ← Persistent hook
4. inject_hook_preset("fetch")           ← Persistent hook
5. hook_jsvmp_interpreter("webmssdk.es5.js")  ← JSVMP instrumentation
6. trace_property_access(["navigator.*", "screen.*", "document.cookie"])
7. navigate("https://target.com")
8. Trigger target actions (pagination, search, etc.)
9. get_jsvmp_log()                       ← See which APIs JSVMP accesses
10. get_property_access_log()            ← See which env properties are read
11. dump_jsvmp_strings("webmssdk.es5.js") ← Extract string table
12. compare_env()                        ← Collect browser env for Node.js comparison
13. Reconstruct algorithm from API calls and property access records
```

### Scenario 3: Verify Anti-Detection

```
AI workflow:
1. launch_browser(os_type="windows", humanize=True)
2. check_detection()                     ← Open bot.sannysoft.com and screenshot
3. get_fingerprint_info()                ← View detailed fingerprint
4. navigate("https://browserscan.net")   ← Test more detection sites
5. take_screenshot(full_page=True)
```

### Scenario 4: Persistent Hook Workflow

```
AI workflow:
1. launch_browser()
2. inject_hook_preset("xhr", persistent=True)    ← Context-level persistence
3. inject_hook_preset("fetch", persistent=True)
4. trace_function("XMLHttpRequest.prototype.open", persistent=True)
5. navigate("https://page1.com")                 ← Hooks auto-active
6. get_trace_data()                              ← Collect data
7. navigate("https://page2.com")                 ← Hooks auto re-inject!
8. get_trace_data()                              ← Data includes both pages
9. freeze_prototype("XMLHttpRequest", "open")    ← Prevent page override
```

### Scenario 5: Large Response Body Analysis + DOM Export

```
AI workflow:
1. launch_browser()
2. start_network_capture(capture_body=True)       ← Enable body capture
3. navigate("https://example.com/data")
4. get_session_info()                             ← Confirm session & capture state
5. list_network_requests(resource_type="xhr")     ← Find target API
6. search_response_body("token")                  ← Search keyword in all bodies
7. search_json_path(request_id=5, json_path="data.list[*].sign")  ← Extract JSON data
8. get_response_body_page(request_id=5, offset=0, length=10000)   ← Paginated large body
9. get_page_content()                             ← Export rendered HTML + visible text
```

---

## Changelog

### v0.3.0 (2026-04-03) — Stability Fixes + Response Search + DOM Export + Session Management

> Fix real-world stability issues, add response body search, rendered DOM export, and session management. Tools: 52 → 57.

**New Tools (5)**
| Tool | Description |
|------|-------------|
| `search_response_body` | Full-text search across all captured response bodies |
| `get_response_body_page` | Paginated reading of large response bodies |
| `search_json_path` | Extract JSON data by dot-notation path (supports `[*]` wildcard) |
| `get_page_content` | One-click export rendered HTML + title + meta + visible text |
| `get_session_info` | View current session: browser/context/page/capture/hook status |

**Bug Fixes**
- **take_snapshot**: Fix `Page object has no attribute accessibility` for Playwright >= 1.42, auto fallback to JS implementation
- **trace_property_access**: Fix `JSON.parse` error caused by template substitution stripping JS quotes

**Improvements**
- **launch_browser**: Returns full session state when already running (page URLs, contexts, capture status)
- **get_network_request**: New `include_headers=False` option to save tokens
- **list_network_requests**: Shorter field names, URL truncated to 200 chars
- **Tool descriptions refined**: Clearer parameter docs and usage guidance for all tools

### v0.2.0 (2026-04-01) — Hook Persistence + JSVMP Analysis

> Solve the core pain point of hooks lost after navigation. Add JSVMP interpreter instrumentation, property tracing, string extraction. Tools: 44 → 52.

### v0.1.0 (2026-03-31) — Initial Release

> Camoufox anti-detection browser MCP server, 44 tools covering the full JS reverse engineering workflow.

## License

MIT
