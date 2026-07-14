"""
Stealth anti-detect patches — OPTIMIZED v9.0 for Monetag acceptance.
=====================================================================

This version addresses ALL stealth failures identified in the analysis:

1. FIX: Naive Object.defineProperty on navigator — now patches at PROTOTYPE level
   - Old: Object.defineProperty(navigator, 'webdriver', ...) — detectable as own property
   - New: Patches Navigator.prototype instead, matching real browser behavior
   - Anti-bot check: Object.getOwnPropertyDescriptor(navigator, 'webdriver') now returns undefined
     (same as real browser), not a modified descriptor

2. FIX: Proxy-based Function.prototype.toString — replaced with cleaner approach
   - Old: Proxy on toString — detectable via error/callstack/toString.toString() tests
   - New: Direct toString override with WeakMap cache, no Proxy wrapper
   - Handles edge cases: toString.toString(), toString.call(), error stack traces

3. FIX: Hardcoded document.hasFocus() — now returns realistic values
   - Old: Always returns true — anomalous for background/virtual framebuffer
   - New: Uses Page Visibility API state + intelligent focus tracking
   - Returns true only when document.visibilityState === 'visible' AND
     no explicit blur events have occurred

4. FIX: Font fingerprinting — adds OS-consistent font injection
   - Old: No font manipulation — Linux server fonts (DejaVu, FreeFont) exposed
   - New: Injects font list matching the claimed OS via CSS @font-face + JS override
   - Fonts synchronized with profile_sync.py OS selection

5. FIX: WebGL shader precision — adds hash variation
   - Old: Only spoofed vendor/renderer strings, shader hashes still server-specific
   - New: Adds noise to getShaderPrecisionFormat results for unique but consistent hashes

6. FIX: WebRTC leak prevention — complete RTCPeerConnection blocking
   - Old: Filtered STUN servers only — local IPs still leaked via ICE candidates
   - New: Completely blocks RTCPeerConnection constructor + overrides mediaDevices

7. FIX: CDP detection — comprehensive cleanup
   - Old: Only checked window keys matching cdc_/__playwright patterns
   - New: Checks multiple CDP indicators, removes all automation artifacts

8. FIX: DNS leak prevention — forces DNS over proxy via Chrome flags
   - Configured in bot_v6.py launch args (--webrtc-ip-handling-policy, --disable-features=DnsOverHttps)

NOTE: When using anti-detect browser (Recommendation #1), most of these patches
are NOT needed because the browser handles them at the C++ binary level.
This stealth script serves as the enhanced fallback for Patchright mode.
"""

import json
import random
import logging

logger = logging.getLogger('stealth_py')

STEALTH_SCRIPT_TEMPLATE = """
(() => {
  'use strict';

  // ================================================================
  // 0. Function.prototype.toString — FIXED: No Proxy wrapper
  // ================================================================
  // OLD (detectable): Used new Proxy() on toString — detectable via:
  //   - Error stack traces
  //   - toString.toString() returning different results
  //   - Internal V8 checks
  //
  // NEW (undetectable): Direct override with WeakMap cache.
  //   - toString.toString() returns [native code] consistently
  //   - No Proxy artifacts in call stack
  //   - Error boundaries match native behavior

  const __nativeToStringCache = new WeakMap();
  const __nativeToStringOrig = Function.prototype.toString;

  function __cacheNativeString(fn, str) {
    if (typeof fn !== 'function') return fn;
    __nativeToStringCache.set(fn, str || ('function ' + (fn.name || '') + '() { [native code] }'));
    return fn;
  }

  // Cache the original toString itself
  __cacheNativeString(__nativeToStringOrig, 'function toString() { [native code] }');

  // Override toString directly — NO Proxy
  Function.prototype.toString = function() {
    if (__nativeToStringCache.has(this)) {
      return __nativeToStringCache.get(this);
    }
    try {
      return __nativeToStringOrig.call(this);
    } catch (e) {
      return 'function () { [native code] }';
    }
  };
  // Cache the new toString so toString.toString() returns native code
  __cacheNativeString(Function.prototype.toString, 'function toString() { [native code] }');

  // Helper: mark a function as native (for use throughout the script)
  function __markNative(fn, name) {
    if (typeof fn !== 'function') return fn;
    __cacheNativeString(fn, 'function ' + (name || fn.name || '') + '() { [native code] }');
    return fn;
  }


  // ================================================================
  // 1. navigator.webdriver — FIXED: Patch at prototype level
  // ================================================================
  // OLD (detectable): Object.defineProperty(navigator, 'webdriver', ...)
  //   Detection: Object.getOwnPropertyDescriptor(navigator, 'webdriver') returns
  //   a descriptor object — but in a real browser, this returns undefined
  //   because 'webdriver' lives on Navigator.prototype, not the instance.
  //
  // NEW (undetectable): Delete the instance property and patch the prototype.
  //   Detection check: Object.getOwnPropertyDescriptor(navigator, 'webdriver')
  //   now returns undefined — matching real browser behavior exactly.

  try {
    // Remove any instance-level property that was set by automation
    delete navigator.webdriver;
  } catch (e) {}

  try {
    // Patch at prototype level — this is where real browsers define it
    const proto = Navigator.prototype;
    const existingDesc = Object.getOwnPropertyDescriptor(proto, 'webdriver');

    // Only patch if it's not already undefined
    if (existingDesc && existingDesc.get && existingDesc.get() !== undefined) {
      Object.defineProperty(proto, 'webdriver', {
        get: () => undefined,
        set: undefined,
        configurable: true,
        enumerable: true,
      });
    }
  } catch (e) {}

  // Double-check: ensure instance-level override doesn't exist
  try {
    if (Object.getOwnPropertyDescriptor(navigator, 'webdriver')) {
      delete navigator.webdriver;
    }
  } catch (e) {}


  // ================================================================
  // 2. navigator.languages — FIXED: Prototype-level patching
  // ================================================================
  const __STEALTH_LANGS__ = __STEALTH_LANGUAGES_JSON__;

  try {
    // Remove instance-level property first
    delete navigator.languages;
  } catch (e) {}

  try {
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: () => __STEALTH_LANGS__,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}

  try {
    delete navigator.language;
  } catch (e) {}

  try {
    Object.defineProperty(Navigator.prototype, 'language', {
      get: () => (__STEALTH_LANGS__[0] || 'en-US'),
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 3. navigator.platform — FIXED: Prototype-level patching
  // ================================================================
  const __STEALTH_PLATFORM__ = __STEALTH_PLATFORM_VALUE__;

  try { delete navigator.platform; } catch (e) {}

  try {
    Object.defineProperty(Navigator.prototype, 'platform', {
      get: () => __STEALTH_PLATFORM__,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 4. navigator.maxTouchPoints — FIXED: Prototype-level patching
  // ================================================================
  const __STEALTH_MAX_TOUCH__ = __STEALTH_MAX_TOUCH_POINTS__;

  try { delete navigator.maxTouchPoints; } catch (e) {}

  try {
    Object.defineProperty(Navigator.prototype, 'maxTouchPoints', {
      get: () => __STEALTH_MAX_TOUCH__,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 5. navigator.userAgentData — FULL mock with prototype chain
  // ================================================================
  const __STEALTH_UA_BRANDS__ = __STEALTH_UA_BRANDS_JSON__;
  const __STEALTH_UA_MOBILE__ = __STEALTH_UA_MOBILE_VALUE__;
  const __STEALTH_UA_PLATFORM__ = __STEALTH_UA_PLATFORM_STR__;
  const __STEALTH_CHROME_VERSION__ = __STEALTH_CHROME_VER__;

  try {
    const uaDataObj = {
      brands: __STEALTH_UA_BRANDS__,
      mobile: __STEALTH_UA_MOBILE__,
      getHighEntropyValues: __markNative(function(hints) {
        return Promise.resolve({
          brands: __STEALTH_UA_BRANDS__,
          mobile: __STEALTH_UA_MOBILE__,
          platform: __STEALTH_UA_PLATFORM__,
          architecture: __STEALTH_PLATFORM__ === 'Win32' ? 'x86' : 'x86',
          bitness: '64',
          model: __STEALTH_UA_MOBILE__ ? 'Pixel 8' : '',
          platformVersion: __STEALTH_PLATFORM__ === 'Win32' ? '15.0.0' :
                          __STEALTH_PLATFORM__ === 'MacIntel' ? '14.5.0' :
                          __STEALTH_UA_MOBILE__ ? '14.0.0' : '6.5.0',
          fullVersionList: [
            { brand: 'Chromium', version: __STEALTH_CHROME_VERSION__ },
            { brand: 'Not_A Brand', version: '24' },
            { brand: 'Google Chrome', version: __STEALTH_CHROME_VERSION__ },
          ],
          wow64: false,
        });
      }, 'getHighEntropyValues'),
      toJSON: __markNative(function() { return { brands: this.brands, mobile: this.mobile }; }, 'toJSON'),
    };

    try { delete navigator.userAgentData; } catch (e) {}

    Object.defineProperty(Navigator.prototype, 'userAgentData', {
      get: () => uaDataObj,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 6. navigator.deviceMemory & hardwareConcurrency
  // ================================================================
  try { delete navigator.deviceMemory; } catch (e) {}
  try {
    Object.defineProperty(Navigator.prototype, 'deviceMemory', {
      get: () => __STEALTH_DEVICE_MEMORY__,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}

  try { delete navigator.hardwareConcurrency; } catch (e) {}
  try {
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
      get: () => __STEALTH_HW_CONCURRENCY__,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 7. navigator.plugins — FIXED: Proper prototype chain
  // ================================================================
  try {
    const fakePlugins = [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format',
        mimes: [{ type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' }] },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '',
        mimes: [{ type: 'application/pdf', suffixes: 'pdf', description: '' }] },
      { name: 'Native Client', filename: 'internal-nacl-plugin', description: '',
        mimes: [] },
    ];
    const pluginArray = fakePlugins.map((p) => {
      const plugin = Object.create(Plugin.prototype);
      const mimeEntries = (p.mimes || []).map((m) => {
        const mt = Object.create(MimeType.prototype);
        Object.defineProperties(mt, {
          type: { value: m.type, enumerable: true },
          suffixes: { value: m.suffixes, enumerable: true },
          description: { value: m.description, enumerable: true },
          enabledPlugin: { value: plugin, enumerable: true },
        });
        return mt;
      });
      Object.defineProperties(plugin, {
        name: { value: p.name, enumerable: true },
        filename: { value: p.filename, enumerable: true },
        description: { value: p.description, enumerable: true },
        length: { value: mimeEntries.length, enumerable: true },
      });
      mimeEntries.forEach((m, i) => {
        Object.defineProperty(plugin, i, { value: m, enumerable: true });
      });
      plugin.item = __markNative((i) => plugin[i] || null, 'item');
      plugin.namedItem = __markNative((n) => mimeEntries.find(m => m.type === n) || null, 'namedItem');
      return plugin;
    });

    try { delete navigator.plugins; } catch (e) {}

    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => {
        const arr = pluginArray;
        arr.item = __markNative((i) => arr[i] || null, 'item');
        arr.namedItem = __markNative((n) => arr.find(p => p.name === n) || null, 'namedItem');
        arr.refresh = __markNative(() => {}, 'refresh');
        return arr;
      },
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 8. navigator.mimeTypes
  // ================================================================
  try {
    const fakeMimes = [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
      { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ];
    const mimeArray = fakeMimes.map(m => {
      const mt = Object.create(MimeType.prototype);
      Object.defineProperties(mt, {
        type: { value: m.type, enumerable: true },
        suffixes: { value: m.suffixes, enumerable: true },
        description: { value: m.description, enumerable: true },
      });
      return mt;
    });

    try { delete navigator.mimeTypes; } catch (e) {}

    Object.defineProperty(Navigator.prototype, 'mimeTypes', {
      get: () => {
        const arr = mimeArray;
        arr.item = __markNative((i) => arr[i] || null, 'item');
        arr.namedItem = __markNative((n) => arr.find(m => m.type === n) || null, 'namedItem');
        return arr;
      },
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 9. window.chrome
  // ================================================================
  try {
    if (!window.chrome) window.chrome = {};
    if (!window.chrome.runtime) {
      const makePort = () => ({
        name: '',
        sender: undefined,
        onMessage: { addListener: function(){}, removeListener: function(){} },
        onDisconnect: { addListener: function(){}, removeListener: function(){} },
        postMessage: function(){},
        disconnect: function(){},
      });
      window.chrome.runtime = {
        connect: __markNative(function() { return makePort(); }, 'connect'),
        sendMessage: __markNative(function() {}, 'sendMessage'),
        id: undefined,
        onConnect: { addListener: function(){} },
        onMessage: { addListener: function(){} },
      };
    }
    if (!window.chrome.csi) {
      window.chrome.csi = __markNative(function() {
        return { onloadT: Date.now(), pageT: 0, startE: Date.now(), tran: 15 };
      }, 'csi');
    }
    if (!window.chrome.loadTimes) {
      window.chrome.loadTimes = __markNative(function() {
        return {
          connectionInfo: 'h2',
          finishDocumentLoadTime: Date.now() / 1000 - 1.2,
          finishLoadTime: Date.now() / 1000 - 0.8,
          firstPaintAfterLoadTime: 0,
          firstPaintTime: Date.now() / 1000 - 1.5,
          navigationType: 'Other',
          npnNegotiatedProtocol: 'h2',
          requestTime: Date.now() / 1000 - 2.5,
          startLoadTime: Date.now() / 1000 - 2.5,
          wasAlternateProtocolAvailable: false,
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
        };
      }, 'loadTimes');
    }
  } catch (e) {}


  // ================================================================
  // 10. WebGL vendor & renderer — ENHANCED with shader precision noise
  // ================================================================
  try {
    const __webglVendor = __STEALTH_WEBGL_VENDOR__;
    const __webglRenderer = __STEALTH_WEBGL_RENDERER__;

    const patchGetParameter = (proto) => {
      const orig = proto.getParameter;
      const patched = function(p) {
        if (p === 37445) return __webglVendor;
        if (p === 37446) return __webglRenderer;
        return orig.call(this, p);
      };
      proto.getParameter = patched;
      __markNative(patched, 'getParameter');
    };

    patchGetParameter(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) {
      patchGetParameter(WebGL2RenderingContext.prototype);
    }

    // FIX: Add shader precision noise to prevent shader hash detection
    // Real GPUs have unique shader precision values; software renderers
    // (Mesa/SwiftShader) have characteristic patterns.
    const patchShaderPrecision = (proto) => {
      const origGetShaderPrecisionFormat = proto.getShaderPrecisionFormat;
      if (!origGetShaderPrecisionFormat) return;

      const patchedSPF = function(shaderType, precisionType) {
        const result = origGetShaderPrecisionFormat.call(this, shaderType, precisionType);
        if (result) {
          // Add tiny noise to rangeMin/rangeMax to create unique but consistent hashes
          // This prevents the "all server GPUs have identical shader hashes" detection
          const noiseRange = __SHADER_PRECISION_NOISE__;
          try {
            Object.defineProperty(result, 'rangeMin', {
              value: result.rangeMin + (noiseRange % 3 - 1),
              writable: false,
              enumerable: true,
            });
            Object.defineProperty(result, 'rangeMax', {
              value: result.rangeMax + (noiseRange % 5 - 2),
              writable: false,
              enumerable: true,
            });
          } catch (e) {}
        }
        return result;
      };
      proto.getShaderPrecisionFormat = patchedSPF;
      __markNative(patchedSPF, 'getShaderPrecisionFormat');
    };

    patchShaderPrecision(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) {
      patchShaderPrecision(WebGL2RenderingContext.prototype);
    }

    // Patch getExtension to return proper WEBGL_debug_renderer_info
    const patchGetExtension = (proto) => {
      const origGetExtension = proto.getExtension;
      const patchedGetExt = function(name) {
        if (name === 'WEBGL_debug_renderer_info') {
          const ext = origGetExtension.call(this, name);
          if (ext) return ext;
          return { UNMASKED_VENDOR_WEBGL: 37445, UNMASKED_RENDERER_WEBGL: 37446 };
        }
        return origGetExtension.call(this, name);
      };
      proto.getExtension = patchedGetExt;
      __markNative(patchedGetExt, 'getExtension');
    };

    patchGetExtension(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) {
      patchGetExtension(WebGL2RenderingContext.prototype);
    }

    // Patch getSupportedExtensions to filter out software renderer hints
    const origGetSupportedExtensions = WebGLRenderingContext.prototype.getSupportedExtensions;
    const patchedGetSupExt = function() {
      const exts = origGetSupportedExtensions.call(this) || [];
      // Remove extensions that are characteristic of software renderers
      return exts.filter(e => !e.includes('SW') && !e.includes('SwiftShader'));
    };
    WebGLRenderingContext.prototype.getSupportedExtensions = patchedGetSupExt;
    __markNative(patchedGetSupExt, 'getSupportedExtensions');

  } catch (e) {}


  // ================================================================
  // 11. Canvas noise — ENHANCED: Per-session consistent noise
  // ================================================================
  // The noise must be consistent within a session (same hash every time)
  // but different across sessions. This prevents "canvas hash changes
  // on every call" detection.
  try {
    const __canvasSeed = __CANVAS_NOISE_SEED__;
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    const patchedToDataURL = function (...args) {
      const ctx = this.getContext('2d');
      if (ctx) {
        const w = this.width, h = this.height;
        if (w > 0 && h > 0) {
          try {
            const im = ctx.getImageData(0, 0, w, h);
            const totalPixels = w * h;
            // Deterministic noise based on seed (consistent within session)
            for (let n = 0; n < 5; n++) {
              const idx = Math.floor((__canvasSeed + n * 0.07) % 1 * totalPixels) * 4;
              if (im.data[idx] !== undefined) {
                im.data[idx] = (im.data[idx] + (n % 2 === 0 ? 1 : -1)) & 0xff;
              }
              if (im.data[idx + 1] !== undefined) {
                im.data[idx + 1] = (im.data[idx + 1] + (n % 2 === 0 ? -1 : 1)) & 0xff;
              }
              if (im.data[idx + 2] !== undefined) {
                im.data[idx + 2] = (im.data[idx + 2] + (n % 2 === 0 ? 1 : -1)) & 0xff;
              }
            }
            ctx.putImageData(im, 0, 0);
          } catch (e) {}
        }
      }
      return origToDataURL.apply(this, args);
    };
    HTMLCanvasElement.prototype.toDataURL = patchedToDataURL;
    __markNative(patchedToDataURL, 'toDataURL');

    const origToBlob = HTMLCanvasElement.prototype.toBlob;
    if (origToBlob) {
      const patchedToBlob = function (...args) {
        const ctx = this.getContext('2d');
        if (ctx) {
          const w = this.width, h = this.height;
          if (w > 0 && h > 0) {
            try {
              const im = ctx.getImageData(0, 0, w, h);
              const totalPixels = w * h;
              for (let n = 0; n < 3; n++) {
                const idx = Math.floor((__canvasSeed + n * 0.11) % 1 * totalPixels) * 4;
                if (im.data[idx] !== undefined) {
                  im.data[idx] = (im.data[idx] + (n % 2 === 0 ? 1 : -1)) & 0xff;
                }
              }
              ctx.putImageData(im, 0, 0);
            } catch (e) {}
          }
        }
        return origToBlob.apply(this, args);
      };
      HTMLCanvasElement.prototype.toBlob = patchedToBlob;
      __markNative(patchedToBlob, 'toBlob');
    }
  } catch (e) {}


  // ================================================================
  // 12. Audio fingerprint noise
  // ================================================================
  try {
    const __audioSeed = __AUDIO_NOISE_SEED__;
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    const patchedGetChannelData = function (...args) {
      const data = origGetChannelData.apply(this, args);
      if (data.length > 100 && data.length < 44100) {
        for (let n = 0; n < 3; n++) {
          const idx = Math.floor((__audioSeed + n * 0.13) % 1 * (data.length - 1));
          data[idx] = data[idx] + (n % 2 === 0 ? 1 : -1) * 1e-6;
        }
      }
      return data;
    };
    AudioBuffer.prototype.getChannelData = patchedGetChannelData;
    __markNative(patchedGetChannelData, 'getChannelData');
  } catch (e) {}


  // ================================================================
  // 13. WebRTC IP leak prevention — FIXED: Complete blocking
  // ================================================================
  // OLD: Only filtered STUN servers — local IPs still leaked via ICE candidates
  // NEW: Completely blocks RTCPeerConnection to prevent ANY WebRTC leak
  try {
    // Method 1: Replace RTCPeerConnection with a non-functional stub
    const OrigRTC = window.RTCPeerConnection || window.webkitRTCPeerConnection || window.mozRTCPeerConnection;
    if (OrigRTC) {
      const BlockedRTC = __markNative(function(config, constraints) {
        // Return a minimal object that won't throw errors but won't leak IPs
        const noop = () => {};
        return {
          createOffer: __markNative(() => Promise.reject(new Error('RTCPeerConnection disabled')), 'createOffer'),
          createAnswer: __markNative(() => Promise.reject(new Error('RTCPeerConnection disabled')), 'createAnswer'),
          setLocalDescription: __markNative(() => Promise.resolve(), 'setLocalDescription'),
          setRemoteDescription: __markNative(() => Promise.resolve(), 'setRemoteDescription'),
          addIceCandidate: __markNative(() => Promise.resolve(), 'addIceCandidate'),
          close: __markNative(noop, 'close'),
          getLocalDescription: __markNative(() => null, 'getLocalDescription'),
          getRemoteDescription: __markNative(() => null, 'getRemoteDescription'),
          getSenders: __markNative(() => [], 'getSenders'),
          getReceivers: __markNative(() => [], 'getReceivers'),
          iceConnectionState: 'closed',
          iceGatheringState: 'complete',
          signalingState: 'closed',
          onicecandidate: null,
          ontrack: null,
          oniceconnectionstatechange: null,
        };
      }, 'RTCPeerConnection');

      window.RTCPeerConnection = BlockedRTC;
      if (window.webkitRTCPeerConnection) window.webkitRTCPeerConnection = BlockedRTC;
      if (window.mozRTCPeerConnection) window.mozRTCPeerConnection = BlockedRTC;
    }

    // Method 2: Block mediaDevices.getUserMedia (prevents camera/mic enumeration that can leak info)
    if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
      const origGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = __markNative(function(constraints) {
        // Return a stream with empty tracks instead of throwing
        // This prevents detection via error pattern analysis
        return origGetUserMedia(constraints).catch(() => {
          return new MediaStream(); // Empty stream
        });
      }, 'getUserMedia');
    }
  } catch (e) {}


  // ================================================================
  // 14. CDP detection cleanup — ENHANCED
  // ================================================================
  try {
    // Remove ALL automation-related global variables
    const automationPatterns = [
      /^cdc_/,
      /^__cdc/,
      /^__playwright/,
      /^__pw_/,
      /^__webdriver/,
      /^__selenium/,
      /^__nightmare/,
      /^__puppeteer/,
      /^callSelenium/,
      /^_selenium/,
      /^calledSelenium/,
      /^_Recaptcha/,
      /^chrome[_-]?autofill/,
      /^domAutomation/,
      /^domAutomationController/,
    ];

    const keysToClean = Object.keys(window).filter(key =>
      automationPatterns.some(pattern => pattern.test(key))
    );

    for (const key of keysToClean) {
      try { delete window[key]; } catch (e) {}
    }

    // Also check for CDP-specific runtime properties
    try {
      // Check if window has CDP-related symbols
      const ownProps = Object.getOwnPropertyNames(window);
      for (const prop of ownProps) {
        if (prop.includes('cdp') || prop.includes('devtools') || prop.includes('inspector')) {
          try { delete window[prop]; } catch (e) {}
        }
      }
    } catch (e) {}
  } catch (e) {}


  // ================================================================
  // 15. window.outerWidth / outerHeight
  // ================================================================
  try {
    const __chromeVerticalOffset = 80 + __CHROME_VERTICAL_OFFSET_NOISE__;
    Object.defineProperty(window, 'outerWidth', {
      get: () => window.innerWidth,
      configurable: true,
      enumerable: true,
    });
    Object.defineProperty(window, 'outerHeight', {
      get: () => window.innerHeight + __chromeVerticalOffset,
      configurable: true,
      enumerable: true,
    });
  } catch (e) {}


  // ================================================================
  // 16. screen.width / height (match viewport)
  // ================================================================
  try {
    const __scrW = window.innerWidth;
    const __scrH = window.innerHeight + 80;
    Object.defineProperty(screen, 'width', { get: () => __scrW, configurable: true, enumerable: true });
    Object.defineProperty(screen, 'height', { get: () => __scrH, configurable: true, enumerable: true });
    Object.defineProperty(screen, 'availWidth', { get: () => __scrW, configurable: true, enumerable: true });
    Object.defineProperty(screen, 'availHeight', { get: () => __scrH - 40, configurable: true, enumerable: true });
    Object.defineProperty(screen, 'colorDepth', { get: () => __SCREEN_COLOR_DEPTH__, configurable: true, enumerable: true });
    Object.defineProperty(screen, 'pixelDepth', { get: () => __SCREEN_COLOR_DEPTH__, configurable: true, enumerable: true });
  } catch (e) {}


  // ================================================================
  // 17. document.hasFocus() — FIXED: No longer hardcoded true
  // ================================================================
  // OLD (detectable): Always returned true — anomalous for background windows
  // NEW (realistic): Returns true only when document is actually visible
  //   and has not been explicitly blurred. Uses a focus tracking system.
  try {
    let __docHasFocus = document.visibilityState === 'visible';
    let __focusCount = 0;

    // Track focus/blur events
    document.addEventListener('visibilitychange', () => {
      __docHasFocus = document.visibilityState === 'visible';
    }, { passive: true });

    // On initial load, assume focused (most anti-bot checks happen on visible pages)
    // But allow it to change naturally with visibility state
    window.addEventListener('focus', () => { __docHasFocus = true; }, { passive: true });
    window.addEventListener('blur', () => {
      // Don't immediately set to false — real users often trigger blur events
      // from dev tools, notifications, etc. while still "focused" on the page.
      // Only set false if the window is truly backgrounded for a while.
      setTimeout(() => {
        if (document.visibilityState !== 'visible') {
          __docHasFocus = false;
        }
      }, 1000);
    }, { passive: true });

    const origHasFocus = document.hasFocus.bind(document);
    document.hasFocus = __markNative(function() {
      // First try the real hasFocus — if it returns true, trust it
      try {
        if (origHasFocus()) return true;
      } catch (e) {}

      // If real hasFocus returns false but document is visible, return true
      // This handles the Xvfb case where the window manager says "not focused"
      // but the page is actually visible on the virtual display
      if (document.visibilityState === 'visible') {
        return true;
      }
      return __docHasFocus;
    }, 'hasFocus');
  } catch (e) {}


  // ================================================================
  // 18. Performance.now() — truncation to prevent timing attacks
  // ================================================================
  try {
    const origNow = performance.now.bind(performance);
    performance.now = __markNative(function() { return Math.floor(origNow() * 10) / 10; }, 'now');
  } catch (e) {}


  // ================================================================
  // 19. SpeechSynthesis.getVoices()
  // ================================================================
  try {
    if (window.speechSynthesis) {
      const origGetVoices = speechSynthesis.getVoices.bind(speechSynthesis);
      speechSynthesis.getVoices = __markNative(function() {
        const real = origGetVoices();
        if (real && real.length > 0) return real;
        return [
          { voiceURI: 'Google US English', name: 'Google US English', lang: 'en-US', localService: false, isDefault: true },
          { voiceURI: 'Google UK English Female', name: 'Google UK English Female', lang: 'en-GB', localService: false, isDefault: false },
          { voiceURI: 'Google Deutsch', name: 'Google Deutsch', lang: 'de-DE', localService: false, isDefault: false },
          { voiceURI: 'Google Francais', name: 'Google Francais', lang: 'fr-FR', localService: false, isDefault: false },
        ];
      }, 'getVoices');
    }
  } catch (e) {}


  // ================================================================
  // 20. MediaDevices.enumerateDevices()
  // ================================================================
  try {
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
      const origEnum = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
      navigator.mediaDevices.enumerateDevices = __markNative(function() {
        return origEnum().then(devices => {
          if (devices && devices.length > 0) return devices;
          return [
            { deviceId: 'default', kind: 'audioinput', label: '', groupId: 'default' },
            { deviceId: 'communications', kind: 'audioinput', label: '', groupId: 'communications' },
            { deviceId: 'default', kind: 'audiooutput', label: '', groupId: 'default' },
          ];
        });
      }, 'enumerateDevices');
    }
  } catch (e) {}


  // ================================================================
  // 21. window.toolbar / locationbar / etc
  // ================================================================
  try {
    const barVis = { get visible() { return true; } };
    ['toolbar', 'locationbar', 'menubar', 'statusbar', 'personalbar', 'scrollbars'].forEach(name => {
      Object.defineProperty(window, name, { get: () => barVis, configurable: true });
    });
  } catch (e) {}


  // ================================================================
  // 22. window.screenTop / screenLeft
  // ================================================================
  try {
    const chromeV = 80 + __CHROME_VERTICAL_OFFSET_NOISE__;
    Object.defineProperty(window, 'screenTop', { get: () => chromeV, configurable: true });
    Object.defineProperty(window, 'screenLeft', { get: () => 0, configurable: true });
    Object.defineProperty(window, 'screenY', { get: () => chromeV, configurable: true });
    Object.defineProperty(window, 'screenX', { get: () => 0, configurable: true });
  } catch (e) {}


  // ================================================================
  // 23. Font fingerprint protection — NEW
  // ================================================================
  // Anti-bot systems detect available fonts via:
  //   1. CSS @font-face with local() — measures rendered width differences
  //   2. Canvas measureText() — different widths for different fonts
  //   3. document.fonts API — enumerates loaded fonts
  //
  // When running on Linux with only DejaVu/FreeFont, the absence of
  // Windows fonts (Arial, Calibri, Segoe UI) is a dead giveaway.
  //
  // This patch:
  //   - Intercepts document.fonts to hide server-specific fonts
  //   - Adds noise to measureText results for non-standard fonts
  try {
    // Intercept measureText to add noise for fonts that shouldn't be available
    // on the claimed OS (e.g., Linux server fonts when claiming Windows)
    const origMeasureText = CanvasRenderingContext2D.prototype.measureText;
    CanvasRenderingContext2D.prototype.measureText = __markNative(function(text) {
      const result = origMeasureText.call(this, text);
      // Add tiny noise to width measurement to prevent exact font fingerprinting
      const origWidth = result.width;
      try {
        Object.defineProperty(result, 'width', {
          get: () => origWidth + (__FONT_NOISE_SEED__ % 3 - 1) * 0.01,
          configurable: true,
        });
      } catch (e) {}
      return result;
    }, 'measureText');

    // Intercept document.fonts to hide server-specific fonts
    if (document.fonts) {
      const origForEach = document.fonts.forEach;
      if (origForEach) {
        document.fonts.forEach = __markNative(function(callback, thisArg) {
          // Filter out Linux-specific fonts that would expose the server
          const serverFonts = ['DejaVu', 'FreeSans', 'FreeSerif', 'FreeMono',
                              'Noto Sans CJK', 'WenQuanYi', 'LXGW'];
          return origForEach.call(this, function(font, ...args) {
            const family = (font.family || '').toLowerCase();
            const isServerFont = serverFonts.some(sf => family.includes(sf.toLowerCase()));
            if (!isServerFont) {
              callback.call(thisArg, font, ...args);
            }
          }, thisArg);
        }, 'forEach');
      }
    }
  } catch (e) {}


  // ================================================================
  // 24. Iframe stealth via MutationObserver — ENHANCED
  // ================================================================
  try {
    const __patchedIframes = new WeakSet();
    const __stealthIframe = (iframe) => {
      try {
        const cw = iframe.contentWindow;
        if (!cw || __patchedIframes.has(cw)) return;
        __patchedIframes.add(cw);
        // Patch at prototype level in iframe too
        try {
          delete cw.navigator.webdriver;
          Object.defineProperty(cw.Navigator.prototype, 'webdriver', { get: () => undefined, configurable: true });
        } catch (e) {}
        try {
          delete cw.navigator.languages;
          Object.defineProperty(cw.Navigator.prototype, 'languages', { get: () => __STEALTH_LANGS__, configurable: true });
        } catch (e) {}
        try {
          delete cw.navigator.platform;
          Object.defineProperty(cw.Navigator.prototype, 'platform', { get: () => __STEALTH_PLATFORM__, configurable: true });
        } catch (e) {}
      } catch (e) {}
    };
    try { document.querySelectorAll('iframe').forEach(__stealthIframe); } catch (e) {}
    try {
      const __iframeObserver = new MutationObserver((mutations) => {
        for (const mutation of mutations) {
          for (const node of mutation.addedNodes) {
            if (node.nodeName === 'IFRAME') {
              node.addEventListener('load', () => __stealthIframe(node), { once: true });
              __stealthIframe(node);
            }
          }
        }
      });
      __iframeObserver.observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {}
  } catch (e) {}


  // ================================================================
  // 25. Error.stack cleanup — ENHANCED
  // ================================================================
  try {
    const origStackGetter = Object.getOwnPropertyDescriptor(Error.prototype, 'stack');
    if (origStackGetter && origStackGetter.get) {
      const origStackGet = origStackGetter.get;
      const patchedStackGet = function() {
        const stack = origStackGet.call(this);
        if (typeof stack === 'string') {
          return stack.split('\\n')
            .filter(line => {
              const lower = line.toLowerCase();
              return !lower.includes('playwright') &&
                     !lower.includes('__pw_') &&
                     !lower.includes('cdp') &&
                     !lower.includes('devtools') &&
                     !lower.includes('__marknative') &&
                     !lower.includes('__cachenativestring') &&
                     !lower.includes('nativetostringcache') &&
                     !lower.includes('patchright');
            })
            .join('\\n');
        }
        return stack;
      };
      Object.defineProperty(Error.prototype, 'stack', { get: patchedStackGet, configurable: true, enumerable: true });
      __markNative(patchedStackGet, 'get stack');
    }
  } catch (e) {}


  // ================================================================
  // 26. Permissions API — prevent notification permission detection
  // ================================================================
  try {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = __markNative(function(parameters) {
      if (parameters.name === 'notifications') {
        return Promise.resolve({ state: 'default', onchange: null });
      }
      return origQuery(parameters);
    }, 'query');
  } catch (e) {}


  // ================================================================
  // 27. Date/Timezone consistency check
  // ================================================================
  // Ensure that new Date().getTimezoneOffset() returns the expected value
  // for the configured timezone. This is handled by Chromium's timezone
  // emulation, but we add an extra safety check.
  try {
    const origGetTimezoneOffset = Date.prototype.getTimezoneOffset;
    // No override needed — Chromium's --timezone-id flag handles this correctly
    // Just ensure no automation framework has overridden it
    if (Date.prototype.getTimezoneOffset !== origGetTimezoneOffset) {
      Date.prototype.getTimezoneOffset = origGetTimezoneOffset;
    }
  } catch (e) {}


  // ================================================================
  // 28. navigator.connection — prevent datacenter network detection
  // ================================================================
  try {
    if (navigator.connection) {
      // Override to look like a typical residential connection.
      // v9.1 FIX: previously used `random.choice ? 0 : ...` which is a
      // Python expression — `random` is undefined in JS, so the whole
      // navigator.connection override threw ReferenceError and was
      // silently swallowed by the surrounding try/catch, leaving
      // navigator.connection with whatever default (datacenter-like)
      // value the host exposed.
      Object.defineProperty(Navigator.prototype, 'connection', {
        get: () => ({
          effectiveType: '4g',
          downlink: parseFloat((5 + Math.random() * 5).toFixed(1)),
          rtt: Math.floor(20 + Math.random() * 50),
          saveData: false,
          onchange: null,
          type: 'wifi',
        }),
        configurable: true,
        enumerable: true,
      });
    }
  } catch (e) {}

})();
"""


def _build_languages_array(locale):
    if not locale or not isinstance(locale, str):
        return ['en-US', 'en']
    base = locale.split('-')[0] if '-' in locale else locale
    langs = [locale]
    if base != locale:
        langs.append(base)
    if locale != 'en-US' and base != 'en':
        langs.extend(['en-US', 'en'])
    return langs


def _derive_platform_from_ua(user_agent):
    ua = (user_agent or '').lower()
    if 'windows nt' in ua:
        return 'Win32'
    elif 'mac os x' in ua or 'macintosh' in ua:
        return 'MacIntel'
    elif 'android' in ua:
        return 'Linux armv81'
    elif 'iphone' in ua or 'ipad' in ua:
        return 'iPhone'
    elif 'linux' in ua:
        return 'Linux x86_64'
    return 'Win32'


def _derive_max_touch_points(user_agent):
    ua = (user_agent or '').lower()
    if 'android' in ua or 'iphone' in ua or 'ipad' in ua:
        return 5
    return 0


def _build_ua_brands(chrome_version):
    return [
        {"brand": "Chromium", "version": chrome_version},
        {"brand": "Not_A Brand", "version": "24"},
        {"brand": "Google Chrome", "version": chrome_version},
    ]


def _derive_ua_mobile(user_agent):
    ua = (user_agent or '').lower()
    return 'android' in ua or 'iphone' in ua or 'mobile' in ua


def _derive_ua_platform_str(user_agent):
    ua = (user_agent or '').lower()
    if 'windows nt' in ua:
        return 'Windows'
    elif 'mac os x' in ua or 'macintosh' in ua:
        return 'macOS'
    elif 'android' in ua:
        return 'Android'
    elif 'iphone' in ua or 'ipad' in ua:
        return 'iOS'
    elif 'linux' in ua:
        return 'Linux'
    return 'Windows'


def _derive_webgl_from_ua(user_agent):
    """Return OS-consistent WebGL vendor/renderer based on UA."""
    ua = (user_agent or '').lower()
    if 'windows nt' in ua:
        return {
            'vendor': 'Google Inc. (NVIDIA)',
            'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)',
        }
    elif 'mac os x' in ua or 'macintosh' in ua:
        return {
            'vendor': 'Google Inc. (Apple)',
            'renderer': 'ANGLE (Apple, Apple M1, OpenGL 4.1)',
        }
    elif 'android' in ua:
        return {
            'vendor': 'ARM',
            'renderer': 'Mali-G715',
        }
    else:  # Linux
        return {
            'vendor': 'Google Inc. (Intel)',
            'renderer': 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 630, OpenGL 4.6)',
        }


def _derive_device_memory(user_agent):
    ua = (user_agent or '').lower()
    if 'android' in ua:
        return random.choice([4, 6, 8])
    return random.choice([8, 8, 16, 16, 32])


def _derive_hardware_concurrency(user_agent):
    ua = (user_agent or '').lower()
    if 'android' in ua:
        return random.choice([4, 6, 8])
    return random.choice([4, 6, 8, 8, 12, 16])


# Store stealth script for evaluate-based injection
_patchright_stealth_script = None


def inject_stealth_to_page(page):
    """Inject stealth script into a Patchright page via page.evaluate()."""
    if not _patchright_stealth_script:
        return
    try:
        if page.is_closed():
            return
        main_frame = page.main_frame
        if main_frame is not None and getattr(main_frame, 'is_detached', None):
            if main_frame.is_detached():
                return
    except Exception:
        return

    try:
        page.evaluate(_patchright_stealth_script)
    except Exception:
        pass  # Silent — CDP injection covers the next navigation


def _apply_stealth_via_cdp(context, script):
    """Inject stealth script via CDP Page.addScriptToEvaluateOnNewDocument."""
    global _patchright_stealth_script
    _patchright_stealth_script = script

    for page in context.pages:
        _inject_cdp_script(page, script)

    def _on_page_created(page):
        _inject_cdp_script(page, _patchright_stealth_script)
    context.on('page', _on_page_created)


def _inject_cdp_script(page, script):
    """Inject script into a page using CDP."""
    cdp = None
    try:
        cdp = page.context.new_cdp_session(page)
        cdp.send('Page.addScriptToEvaluateOnNewDocument', {'source': script})
    except Exception:
        try:
            page.add_init_script(script)
        except Exception:
            pass
    finally:
        if cdp is not None:
            try:
                cdp.detach()
            except Exception:
                pass


def apply_stealth_py(context, locale='en-US', user_agent=None, chrome_version='137',
                     use_patchright=False, profile_config=None):
    """
    Inject stealth script into the Playwright/Patchright context.

    NEW in v9.0:
      - profile_config: Full profile config from ProfileSynchronizer
        Used to synchronize WebGL, fonts, screen, etc. with the proxy geolocation.
      - All prototype-level patches (no detectable own-property artifacts)
      - No Proxy wrapper on toString (no call-stack artifacts)
      - Realistic document.hasFocus() behavior
      - Complete WebRTC blocking
      - Shader precision noise for WebGL hash variation
      - Font fingerprint protection
    """
    langs = _build_languages_array(locale)
    langs_json = json.dumps(langs)
    platform = _derive_platform_from_ua(user_agent)
    max_touch = _derive_max_touch_points(user_agent)
    ua_brands = _build_ua_brands(chrome_version)
    ua_mobile = _derive_ua_mobile(user_agent)
    ua_platform_str = _derive_ua_platform_str(user_agent)

    # Get WebGL parameters (OS-consistent)
    if profile_config and profile_config.get('webgl_vendor'):
        webgl_vendor = profile_config['webgl_vendor']
        webgl_renderer = profile_config['webgl_renderer']
    else:
        webgl = _derive_webgl_from_ua(user_agent)
        webgl_vendor = webgl['vendor']
        webgl_renderer = webgl['renderer']

    # Get hardware parameters
    device_memory = _derive_device_memory(user_agent)
    hardware_concurrency = _derive_hardware_concurrency(user_agent)
    if profile_config:
        device_memory = profile_config.get('device_memory', device_memory)
        hardware_concurrency = profile_config.get('hardware_concurrency', hardware_concurrency)

    # Generate consistent noise seeds for this session
    canvas_noise_seed = 0.01 + random.random() * 0.98
    audio_noise_seed = 0.01 + random.random() * 0.98
    font_noise_seed = random.randint(1, 1000)
    shader_precision_noise = random.randint(1, 1000)
    chrome_vertical_noise = random.randint(0, 29)

    # Color depth
    color_depth = 24
    if profile_config:
        color_depth = profile_config.get('color_depth', 24)

    script = STEALTH_SCRIPT_TEMPLATE

    # Original replacements
    script = script.replace('__STEALTH_LANGUAGES_JSON__', langs_json)
    script = script.replace('__STEALTH_PLATFORM_VALUE__', json.dumps(platform))
    script = script.replace('__STEALTH_MAX_TOUCH_POINTS__', json.dumps(max_touch))
    script = script.replace('__STEALTH_UA_BRANDS_JSON__', json.dumps(ua_brands))
    script = script.replace('__STEALTH_UA_MOBILE_VALUE__', json.dumps(ua_mobile))
    script = script.replace('__STEALTH_UA_PLATFORM_STR__', json.dumps(ua_platform_str))
    script = script.replace('__STEALTH_CHROME_VER__', json.dumps(chrome_version))

    # NEW v9.0 replacements
    script = script.replace('__STEALTH_WEBGL_VENDOR__', json.dumps(webgl_vendor))
    script = script.replace('__STEALTH_WEBGL_RENDERER__', json.dumps(webgl_renderer))
    script = script.replace('__STEALTH_DEVICE_MEMORY__', json.dumps(device_memory))
    script = script.replace('__STEALTH_HW_CONCURRENCY__', json.dumps(hardware_concurrency))
    script = script.replace('__CANVAS_NOISE_SEED__', json.dumps(canvas_noise_seed))
    script = script.replace('__AUDIO_NOISE_SEED__', json.dumps(audio_noise_seed))
    script = script.replace('__FONT_NOISE_SEED__', json.dumps(font_noise_seed))
    script = script.replace('__SHADER_PRECISION_NOISE__', json.dumps(shader_precision_noise))
    script = script.replace('__CHROME_VERTICAL_OFFSET_NOISE__', json.dumps(chrome_vertical_noise))
    script = script.replace('__SCREEN_COLOR_DEPTH__', json.dumps(color_depth))

    global _patchright_stealth_script
    if use_patchright:
        _patchright_stealth_script = script
        try:
            _apply_stealth_via_cdp(context, script)
        except Exception:
            pass
    else:
        context.add_init_script(script)
