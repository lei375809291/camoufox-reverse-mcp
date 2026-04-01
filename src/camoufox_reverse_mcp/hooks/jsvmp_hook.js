(function() {
    window.__mcp_jsvmp_log = window.__mcp_jsvmp_log || [];
    const MAX_ENTRIES = 5000;
    const scriptUrl = '{{SCRIPT_URL}}';

    const browserAPIs = [
        'navigator', 'screen', 'document', 'window', 'location', 'history',
        'localStorage', 'sessionStorage', 'performance', 'crypto'
    ];
    const sensitiveProps = [
        'navigator.userAgent', 'navigator.platform', 'navigator.language',
        'navigator.languages', 'navigator.hardwareConcurrency', 'navigator.deviceMemory',
        'navigator.plugins', 'navigator.mimeTypes', 'navigator.webdriver',
        'navigator.vendor', 'navigator.appVersion', 'navigator.cookieEnabled',
        'screen.width', 'screen.height', 'screen.availWidth', 'screen.availHeight',
        'screen.colorDepth', 'screen.pixelDepth', 'screen.orientation',
        'document.cookie', 'document.referrer', 'document.title',
        'location.href', 'location.origin', 'location.protocol'
    ];

    const trackedCalls = new Set([
        'createElement', 'getElementById', 'querySelector', 'querySelectorAll',
        'getContext', 'toDataURL', 'getImageData', 'toBlob',
        'createOscillator', 'createAnalyser', 'createDynamicsCompressor',
        'getChannelData', 'createBuffer',
        'getExtension', 'getParameter', 'getSupportedExtensions',
        'getShaderPrecisionFormat',
        'toString', 'charCodeAt', 'fromCharCode', 'charAt',
        'substr', 'substring', 'slice', 'split', 'replace', 'match',
        'push', 'pop', 'shift', 'unshift', 'join', 'reverse', 'sort', 'splice',
        'atob', 'btoa', 'parseInt', 'parseFloat', 'encodeURIComponent', 'decodeURIComponent',
        'setTimeout', 'setInterval', 'requestAnimationFrame',
        'getTimezoneOffset', 'toISOString',
        'open', 'send', 'setRequestHeader'
    ]);

    const _apply = Function.prototype.apply;
    const _call = Function.prototype.call;

    function isFromTargetScript(stack) {
        if (!scriptUrl) return true;
        return stack && stack.includes(scriptUrl);
    }

    const origApply = Function.prototype.apply;
    Function.prototype.apply = function(thisArg, argsArray) {
        if (window.__mcp_jsvmp_log.length < MAX_ENTRIES) {
            try {
                const stack = new Error().stack;
                if (isFromTargetScript(stack) && this.name && trackedCalls.has(this.name)) {
                    const entry = {
                        type: 'api_call',
                        func: this.name,
                        args_preview: argsArray ? JSON.stringify(Array.from(argsArray)).substring(0, 500) : '[]',
                        thisType: thisArg ? (thisArg.constructor?.name || typeof thisArg) : 'null',
                        timestamp: Date.now()
                    };
                    window.__mcp_jsvmp_log.push(entry);
                }
            } catch(e) {}
        }
        return origApply.call(this, thisArg, argsArray);
    };
    Function.prototype.apply.toString = function() { return 'function apply() { [native code] }'; };

    for (const propPath of sensitiveProps) {
        try {
            const parts = propPath.split('.');
            let parent = window;
            for (let i = 0; i < parts.length - 1; i++) {
                parent = parent[parts[i]];
                if (!parent) break;
            }
            if (!parent) continue;
            const prop = parts[parts.length - 1];
            const desc = Object.getOwnPropertyDescriptor(parent, prop);
            const origVal = parent[prop];
            if (desc && desc.get) {
                const origGetter = desc.get;
                Object.defineProperty(parent, prop, {
                    get: function() {
                        const val = origGetter.call(this);
                        if (window.__mcp_jsvmp_log.length < MAX_ENTRIES) {
                            const stack = new Error().stack;
                            if (isFromTargetScript(stack)) {
                                let sv;
                                try { sv = JSON.stringify(val); if (sv && sv.length > 200) sv = sv.substring(0, 200); }
                                catch(e) { sv = String(val).substring(0, 100); }
                                window.__mcp_jsvmp_log.push({
                                    type: 'prop_read', property: propPath,
                                    value: sv, timestamp: Date.now()
                                });
                            }
                        }
                        return val;
                    },
                    set: desc.set,
                    enumerable: desc.enumerable,
                    configurable: true
                });
            } else if (typeof origVal !== 'function') {
                let currentVal = origVal;
                Object.defineProperty(parent, prop, {
                    get: function() {
                        if (window.__mcp_jsvmp_log.length < MAX_ENTRIES) {
                            const stack = new Error().stack;
                            if (isFromTargetScript(stack)) {
                                let sv;
                                try { sv = JSON.stringify(currentVal); } catch(e) { sv = String(currentVal); }
                                if (sv && sv.length > 200) sv = sv.substring(0, 200);
                                window.__mcp_jsvmp_log.push({
                                    type: 'prop_read', property: propPath,
                                    value: sv, timestamp: Date.now()
                                });
                            }
                        }
                        return currentVal;
                    },
                    set: function(v) { currentVal = v; },
                    enumerable: true,
                    configurable: true
                });
            }
        } catch(e) {}
    }

    console.log('[JSVMP] Interpreter instrumentation active, tracking', sensitiveProps.length, 'properties +', trackedCalls.size, 'API calls');
})();
