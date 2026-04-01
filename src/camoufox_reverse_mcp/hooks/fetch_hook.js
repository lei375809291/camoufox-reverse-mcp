(function() {
    if (window.__mcp_fetch_hooked) return;
    window.__mcp_fetch_hooked = true;
    window.__mcp_fetch_log = window.__mcp_fetch_log || [];

    const _fetch = window.fetch;

    const hookedFetch = async function(input, init) {
        init = init || {};
        const url = typeof input === 'string' ? input : (input instanceof Request ? input.url : String(input));
        const method = init.method || (input instanceof Request ? input.method : 'GET') || 'GET';
        const info = {
            url, method,
            headers: init.headers ? (typeof init.headers === 'object' ? Object.assign({}, init.headers) : {}) : {},
            body: init.body ? String(init.body).substring(0, 5000) : null,
            stack: new Error().stack,
            timestamp: Date.now()
        };
        try {
            const response = await _fetch.apply(this, arguments);
            info.status = response.status;
            info.ok = response.ok;
            window.__mcp_fetch_log.push(info);
            if (window.__mcp_fetch_log.length > 500) window.__mcp_fetch_log.shift();
            return response;
        } catch (e) {
            info.error = e.message;
            window.__mcp_fetch_log.push(info);
            throw e;
        }
    };

    hookedFetch.toString = function() { return 'function fetch() { [native code] }'; };

    try {
        Object.defineProperty(window, 'fetch', {
            value: hookedFetch, writable: false, configurable: false
        });
    } catch(e) {
        window.fetch = hookedFetch;
    }
})();
