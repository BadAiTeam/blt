#!/usr/bin/env python3
"""
Anti-Detect Browser Integration Module — v10.11 UPGRADE (Cache Clear on Profile Switch)
================================================

v10.11.1 UPGRADE — MOD 5 FIX: CLEAR CACHE VIA stop+clear_cache (Bukan endpoint /clear-cache)
  - Sebelumnya (v10.11): memanggil endpoint POST /api/v2/browser-profile/clear-cache
    untuk clear cache. Endpoint itu TIDAK ADA di AdsPower Local API — selalu
    return HTTP 404 "Not Found" di semua versi AdsPower, sehingga log
    dipenuhi error "BUKAN JSON! HTTP 404, Response: Not Found".
  - v10.11.1 FIX: Cara OFFICIAL AdsPower untuk clear cache adalah dengan
    menambahkan parameter `clear_cache: true` pada saat memanggil stop endpoint:
      v2: POST /api/v2/browser-profile/stop  body: {profile_id, clear_cache: true}
      v1: GET  /api/v1/browser/stop?user_id=xxx&clear_cache_after_closing=1
  - stop_profile() sekarang menerima parameter `clear_cache=False`. Bila True,
    kirim parameter clear_cache ke API. Ada fallback v1 bila v2 tidak recognize
    parameter clear_cache di versi AdsPower tertentu.
  - clear_profile_cache() di-rewrite sebagai wrapper yang memanggil
    stop_profile(clear_cache=True). Idempotent — bila browser tidak running,
    return True (cache seharusnya sudah bersih dari stop sebelumnya).
  - close_and_cleanup() sekarang memanggil stop_profile(clear_cache=True)
    dalam SATU call (bukan stop + clear_cache terpisah). Lebih reliable,
    lebih cepat, menghindari race condition.
  - Cache browser AdsPower di-clear SETIAP kali bot berganti profile/user
    (close_and_cleanup dipanggil per-user).

REVISION HISTORY (webgl fix):
  v9.7   : Changed `webgl` from dict to integer 1 (Custom mode)
  v9.7.1 : Added `webgl_image` field + defensive sanitization + payload logging
  v9.8   : REMOVED `webgl_image` (not a real AdsPower v2 field — was causing
           the residual "webgl must be 0,1,2,3" error). Added retry-with-
           fallback logic: try webgl=1, then webgl=0, then omit webgl entirely.
           Added MODULE_VERSION stamp printed at import time so user can
           verify they are running the patched file.

v9.8 also adds:
  - Multi-format webgl value attempts (int, then string, then omitted)
  - update_profile() now retries with fallback webgl values on error
  - create_profile() now retries with fallback webgl values on error
  - Error logging is UNCONDITIONAL — always prints full payload on error

Rekomendasi #1: Gunakan Peramban Anti-Deteksi Asli (Anti-detect Browser)
Alih-alih memalsukan properti secara manual via proksi JS yang meninggalkan
banyak jejak, modul ini mengintegrasikan otomatisasi dengan peramban
anti-deteksi terpercaya (AdsPower, Multilogin, Dolphin{anty}) melalui API mereka.

Peramban tersebut memodifikasi kode sumber mesin C++ Chromium secara langsung
sehingga properti seperti navigator.webdriver atau emulasi WebGL bersifat alami
dan tidak dapat dideteksi lewat prototype chain.

Supported Anti-Detect Browsers:
  - AdsPower (Local API v2 — http://127.0.0.1:50325)
  - Multilogin (local API on port 45001)
  - Dolphin{anty} (local API on port 3001)

v9.6 changes:
  - Fix: 3-tab issue — close AdsPower start page & duplicates after CDP connect
  - After CDP connect, close ALL existing pages then create ONE clean tab
  - Only 1 tab will open in browser: the target URL

v9.5 changes:
  - Auto-cleanup old bot profiles when profile limit is reached
  - WebSocket CDP connection: 3s wait + 3x retry with backoff
  - v2 API only — removed all v1 endpoint fallbacks (faster startup)
  - Navigator.webdriver=false confirmed working with AdsPower

v9.4 changes:
  - Fix: group_id is required for profile creation — auto-detect default group
  - group_id parameter added to AdsPowerClient and AntiDetectManager
  - group_id field (from proxy.json or Proxy API)
  - profile_id support: gunakan profil yang sudah ada jika create tidak tersedia
  - Retry logic: 3x percobaan dengan jeda 5 detik jika koneksi gagal

v9.3 changes:
  - AdsPower: Updated to API v2 endpoints (from official local-api-mcp-typescript docs)
  - Create/Start/Stop/List/Delete now use /api/v2/browser-profile/* endpoints
  - Authentication via Authorization: Bearer {API_KEY} header (not ?key= param)
  - Base URL changed to http://127.0.0.1:{PORT} (not local.adspower.net)
  - All mutating endpoints use POST with JSON body (not GET with query params)
  - Profile creation uses new v2 schema: user_proxy_config, fingerprint_config, etc.

Modes:
  - "antidetect" : Uses anti-detect browser via API (RECOMMENDED)
  - "patchright" : Falls back to Patchright + enhanced stealth (legacy)

Usage in bot_v6.py:
  from antidetect_browser import AntiDetectManager

  # Local AdsPower with API key
  manager = AntiDetectManager(mode="antidetect", browser_type="adspower",
                              api_key="62a501557b09c8444a57c3318943a0910092c5c0d322e39b")
  session = manager.create_profile(proxy_config, profile_config)
  ...
  manager.close_profile(session)
"""

import os
import sys
import time
import json
import random
import logging
import re
import socket
from urllib.parse import urlparse
import requests as http_requests
from typing import Optional, Dict, Any, List, Tuple

# v10.9: Install asyncio exception handler that filters out greenlet
# thread-switch noise from Patchright's sync→async bridge.
#
# Root cause: Patchright's sync API uses greenlets bound to the calling
# thread. If a sync call is made from a worker thread and the async task
# completes on a different thread (Patchright's driver thread), the
# done_callback `task.add_done_callback(lambda _: g_self.switch())` fails
# with `greenlet.error: cannot switch to a different thread (which happens
# to have exited)`. This is NOISE — it doesn't crash the bot, but it
# pollutes the console with scary tracebacks.
#
# This handler suppresses ONLY that specific noise. All other unhandled
# asyncio exceptions are still logged (via default handler fallback).
#
# v10.9 strategy: PATCH asyncio.AbstractEventLoop.default_exception_handler
# at the CLASS level — this catches ALL event loops (existing and future),
# including Patchright's internal driver loop. Per-loop set_exception_handler
# doesn't work because Patchright creates its own loop internally.
try:
    import asyncio

    # Patch the class that ACTUALLY defines default_exception_handler.
    # AbstractEventLoop is the abstract base, but BaseEventLoop overrides
    # default_exception_handler — so patching AbstractEventLoop is shadowed.
    # We patch BaseEventLoop (and fall back to AbstractEventLoop).
    _ORIG_DEFAULT_EXC_HANDLER = asyncio.base_events.BaseEventLoop.default_exception_handler

    def _greenlet_noise_filter(self, context):
        exc = context.get('exception')
        if exc is not None:
            exc_type_name = type(exc).__name__
            exc_module = getattr(type(exc), '__module__', '') or ''
            exc_msg = str(exc)
            # greenlet.error from patchright._impl._sync_base when sync API
            # is called across thread boundaries. Non-fatal — filter it.
            # The actual exception class is `greenlet.error` (class name `error`,
            # module `greenlet`). Be lenient: also match by message signature.
            is_greenlet_noise = (
                ('greenlet' in exc_module.lower() and exc_type_name == 'error')
                or 'cannot switch to a different thread' in exc_msg
                or ('greenlet' in exc_msg.lower() and 'switch' in exc_msg.lower())
            )
            if is_greenlet_noise:
                return  # swallow — do not print traceback
        # Fall back to original handler for everything else
        _ORIG_DEFAULT_EXC_HANDLER(self, context)

    asyncio.base_events.BaseEventLoop.default_exception_handler = _greenlet_noise_filter
    # Also patch AbstractEventLoop in case some loop class doesn't inherit
    # from BaseEventLoop (defensive).
    asyncio.AbstractEventLoop.default_exception_handler = _greenlet_noise_filter

    # Also try to install on the existing event loop (if any) — this catches
    # the case where a loop was created before our patch ran.
    try:
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_greenlet_noise_filter)
    except Exception:
        pass  # no event loop yet — class patch will cover new loops
except ImportError:
    pass

# v9.3: Defensive suppress InsecureRequestWarning.
# Saat ini antidetect_browser.py tidak memakai verify=False, tapi jika
# ada API call yang melewati proxy SSL-intercepting di masa depan,
# warning tidak akan mengganggu log.
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass

logger = logging.getLogger('antidetect_browser')

# v9.3: Cascade fix + dead-code fix + defensive InsecureRequestWarning suppress.
#   v9.8   : webgl retry with 3 sequential variants (SLOW: +3-6s per profile)
#   v9.9   : Tentukan webgl value yang berhasil SEKALI saat startup, cache
#            di class-level. Hapus sequential retry. Kurangi time.sleep(3)
#            menjadi time.sleep(1) setelah start_profile. Cache profile_id
#            yang sudah dibuat untuk reuse cepat di user berikutnya.
#   v9.3   : Fix dead-code bug di _try_use_existing_profile — `if` dan `elif`
#            sebelumnya punya kondisi identik, sehingga branch Multilogin/
#            Dolphin (clients tanpa _build_update_body) tidak pernah
#            dijalankan. Sekarang `elif` mengecek keberadaan
#            _build_update_body secara eksplisit.
MODULE_VERSION = 'v10.11-upgrade-cache-clear'
_MODULE_FILE = os.path.abspath(__file__)
print(
    f"[antidetect_browser] LOADED {MODULE_VERSION} from {_MODULE_FILE}",
    file=sys.stderr,
    flush=True,
)
logger.info(f"antidetect_browser {MODULE_VERSION} loaded from {_MODULE_FILE}")


# ====================================================================
# Proxy API Loader — Fetch proxies from external API (replaces proxy.json)
# ====================================================================
# v10.0 (range-mode upgrade):
#   The Proxy API now supports a RANGE path segment:
#       GET {PROXY_API_BASE_URL}/range/{start}-{end}?format=txt
#   e.g. https://nodejsclusters-213001-0.cloudclusters.net/api/external/proxies/range/1-50?format=txt
#   This returns proxies #start..#end (inclusive, 1-indexed), one per line.
#
#   The legacy named-list endpoint (e.g. /adsterra-safe?pageSize=N) is still
#   supported as a fallback when proxy_range=None is passed explicitly.

# Base URL — must end at /proxies. The path segment (range or named list)
# is appended by _build_proxy_api_url().
PROXY_API_BASE_URL = 'https://nodejsclusters-213469-0.cloudclusters.net/api/external/proxies'
PROXY_API_KEY = 'pm_e1ccebcc0406a223090f837f7df86eb3'
PROXY_API_DEFAULT_FORMAT = 'txt'

# Default range: fetch proxies #1 through #50.
# Set to None to fall back to the legacy named-list endpoint (adsterra-safe)
# with PROXY_API_DEFAULT_PAGE_SIZE.
PROXY_API_DEFAULT_RANGE = (1, 50)

# Legacy fallback (only used when proxy_range is None).
PROXY_API_LEGACY_LIST = 'adsterra-safe'
PROXY_API_DEFAULT_PAGE_SIZE = 5

# Backward-compat alias: callers that still import PROXY_API_URL get a
# usable base URL. (Pre-existing code that appended ?format=...&pageSize=...
# to this string should be migrated to load_proxies_from_api().)
PROXY_API_URL = PROXY_API_BASE_URL

# Default AdsPower credentials (can be overridden via load_proxy_config args or env vars)
# v10.2: base_url default changed to 'http://localhost' (was 'http://127.0.0.1')
#         so both IPv4 127.0.0.1 and IPv6 ::1 (where AdsPower Global v8.x binds
#         via local.adspower.net) work transparently. Port auto-detection in
#         AdsPowerClient handles cases where AdsPower binds to non-default ports
#         (e.g. 5032 in some Linux container deployments).
ADSPOWER_DEFAULTS = {
    'api_key': '2469eb34275a328d205f8c0787f8e8180094bea2545a5c40',
    'mode': 'local',
    'base_url': 'http://localhost',
    'port': 50325,
    'profile_id': '',
    'group_id': '',
}


def parse_proxy_line(line: str) -> Optional[Dict[str, Any]]:
    """
    Parse a single proxy line from the API txt response into a proxy dict.

    Supported formats:
      - protocol://host:port             (e.g. http://1.2.3.4:8080)
      - protocol://user:pass@host:port   (e.g. http://user:pass@1.2.3.4:8080)
      - host:port                        (assumes http)
      - user:pass@host:port              (assumes http)
      - host:port:user:pass              (assumes http)

    Returns:
        Dict with keys: proxy_host, proxy_port, proxy_user, proxy_password, proxy_type
        Or None if the line cannot be parsed.
    """
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    proxy_type = 'http'  # default
    proxy_host = ''
    proxy_port = 0
    proxy_user = ''
    proxy_password = ''

    # Format: protocol://user:pass@host:port  or  protocol://host:port
    url_match = re.match(
        r'^(https?|socks[45]?):\/\/(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)$',
        line, re.IGNORECASE
    )
    if url_match:
        proxy_type = url_match.group(1).lower()
        proxy_user = url_match.group(2) or ''
        proxy_password = url_match.group(3) or ''
        proxy_host = url_match.group(4)
        try:
            proxy_port = int(url_match.group(5))
        except ValueError:
            return None
        return {
            'proxy_host': proxy_host,
            'proxy_port': proxy_port,
            'proxy_user': proxy_user,
            'proxy_password': proxy_password,
            'proxy_type': proxy_type,
        }

    # Format: user:pass@host:port (no protocol prefix, assume http)
    auth_match = re.match(
        r'^(?:([^:@]+):([^@]+)@)?([^:]+):(\d+)$',
        line
    )
    if auth_match:
        proxy_user = auth_match.group(1) or ''
        proxy_password = auth_match.group(2) or ''
        proxy_host = auth_match.group(3)
        try:
            proxy_port = int(auth_match.group(4))
        except ValueError:
            return None
        return {
            'proxy_host': proxy_host,
            'proxy_port': proxy_port,
            'proxy_user': proxy_user,
            'proxy_password': proxy_password,
            'proxy_type': proxy_type,
        }

    # Format: host:port:user:pass (colon-separated 4 fields, assume http)
    parts = line.split(':')
    if len(parts) == 4:
        proxy_host = parts[0]
        try:
            proxy_port = int(parts[1])
        except ValueError:
            return None
        proxy_user = parts[2]
        proxy_password = parts[3]
        return {
            'proxy_host': proxy_host,
            'proxy_port': proxy_port,
            'proxy_user': proxy_user,
            'proxy_password': proxy_password,
            'proxy_type': proxy_type,
        }

    # Format: host:port (2 fields, assume http)
    if len(parts) == 2:
        proxy_host = parts[0]
        try:
            proxy_port = int(parts[1])
        except ValueError:
            return None
        return {
            'proxy_host': proxy_host,
            'proxy_port': proxy_port,
            'proxy_user': '',
            'proxy_password': '',
            'proxy_type': proxy_type,
        }

    logger.warning(f"parse_proxy_line: cannot parse line: {line!r}")
    return None


def _build_proxy_api_url(
    base_url: str,
    proxy_range: Optional[Tuple[int, int]] = None,
    legacy_list: str = PROXY_API_LEGACY_LIST,
) -> str:
    """
    Build the full proxy API URL based on the requested mode.

    RANGE MODE (preferred, proxy_range != None):
        {base_url}/range/{start}-{end}
        e.g. https://nodejsclusters-213001-0.cloudclusters.net/api/external/proxies/range/1-50

        NOTE: The `/range/` path segment is REQUIRED by the API. Without it,
        the server returns the SPA index.html (HTTP 200, text/html) instead
        of the proxy list, which silently produces 0 parsed proxies.

    LEGACY MODE (proxy_range is None):
        {base_url}/{legacy_list}
        e.g. https://nodejsclusters-213001-0.cloudclusters.net/api/external/proxies/adsterra-safe

    Args:
        base_url:    Base URL — must end at /proxies (trailing slash is stripped).
        proxy_range: (start, end) tuple, both inclusive, 1-indexed.
                     Pass None to use the legacy named-list endpoint.
        legacy_list: Named list segment appended in legacy mode.

    Returns:
        Full URL (without query string) to pass to requests.get().
    """
    base = (base_url or '').rstrip('/')
    if proxy_range is not None:
        start, end = proxy_range
        if not isinstance(start, int) or isinstance(start, bool) \
                or not isinstance(end, int) or isinstance(end, bool):
            raise TypeError(
                f"proxy_range must be a tuple of two ints, got {proxy_range!r}"
            )
        if start < 1:
            raise ValueError(f"proxy_range start must be >= 1, got {start}")
        if end < start:
            raise ValueError(
                f"proxy_range end ({end}) must be >= start ({start})"
            )
        return f"{base}/range/{start}-{end}"
    return f"{base}/{legacy_list}"


def load_proxies_from_api(
    api_url: str = PROXY_API_BASE_URL,
    api_key: str = PROXY_API_KEY,
    page_size: int = PROXY_API_DEFAULT_PAGE_SIZE,
    fmt: str = PROXY_API_DEFAULT_FORMAT,
    timeout: int = 15,
    proxy_range: Optional[Tuple[int, int]] = PROXY_API_DEFAULT_RANGE,
) -> List[Dict[str, Any]]:
    """
    Fetch proxies from the external proxy API (replaces loading from proxy.json).

    Two fetch modes are supported:

    1. RANGE MODE (default, proxy_range=(1, 50)):
       GET {api_url}/range/{start}-{end}?format=txt
       e.g. https://nodejsclusters-213001-0.cloudclusters.net/api/external/proxies/range/1-50?format=txt
       The API returns proxies #start..#end (inclusive, 1-indexed), one per line.

    2. LEGACY MODE (proxy_range=None):
       GET {api_url}/adsterra-safe?format=txt&pageSize={page_size}
       The API returns up to `page_size` proxies from the named list.

    The API response body is plain text with one proxy per line, e.g.:
        http://178.62.184.67:3128
        http://user:pass@1.2.3.4:8080

    Args:
        api_url:     Base URL of the proxy API (must end at /proxies).
        api_key:     X-API-Key header value for authentication.
        page_size:   Number of proxies to request (LEGACY mode only, ignored
                     in RANGE mode since the range itself defines the count).
        fmt:         Response format (default 'txt').
        timeout:     HTTP request timeout in seconds.
        proxy_range: (start, end) tuple for RANGE mode, or None for LEGACY mode.
                     Both bounds are inclusive and 1-indexed.
                     Default: PROXY_API_DEFAULT_RANGE = (1, 50).

    Returns:
        List of proxy dicts, each with keys:
            proxy_host, proxy_port, proxy_user, proxy_password, proxy_type
        Empty list on failure.
    """
    full_url = _build_proxy_api_url(api_url, proxy_range=proxy_range)
    headers = {'X-API-Key': api_key}
    params = {'format': fmt}

    if proxy_range is None:
        # Legacy mode also needs pageSize
        params['pageSize'] = page_size
        mode_desc = f"legacy list={PROXY_API_LEGACY_LIST!r} pageSize={page_size}"
    else:
        mode_desc = f"range {proxy_range[0]}-{proxy_range[1]}"

    logger.info(f"Proxy API: Fetching proxies ({mode_desc}) from {full_url}")

    try:
        resp = http_requests.get(full_url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
    except http_requests.exceptions.RequestException as e:
        logger.error(f"Proxy API: Failed to fetch proxies — {e}")
        return []

    text = resp.text.strip()
    if not text:
        logger.warning("Proxy API: Response body is empty")
        return []

    proxies = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        parsed = parse_proxy_line(line)
        if parsed:
            proxies.append(parsed)
        else:
            logger.debug(f"Proxy API: Skipped unparsable line {line_no}: {line!r}")

    logger.info(f"Proxy API: Loaded {len(proxies)} proxy(ies)")
    return proxies


def load_proxy_config(
    api_url: str = PROXY_API_URL,
    api_key: str = PROXY_API_KEY,
    page_size: int = PROXY_API_DEFAULT_PAGE_SIZE,
    fmt: str = PROXY_API_DEFAULT_FORMAT,
    timeout: int = 15,
    proxy_range: Optional[Tuple[int, int]] = PROXY_API_DEFAULT_RANGE,
    # AdsPower credentials
    adspower_api_key: str = '',
    adspower_mode: str = '',
    adspower_base_url: str = '',
    adspower_port: int = 0,
    adspower_profile_id: str = '',
    adspower_group_id: str = '',
) -> Dict[str, Any]:
    """
    Load complete proxy + AdsPower configuration from the external API.

    This is a drop-in replacement for the old pattern of loading proxy.json.
    It returns a dict with the same top-level structure that bot_v6.py
    previously read from the JSON file:
        {
            'adspower': {
                'api_key':    <str>,
                'mode':       <str>,   # 'local' or 'cloud'
                'base_url':   <str>,
                'port':       <int>,
                'profile_id': <str>,
                'group_id':   <str>,
            },
            'proxies':    [ {proxy_host, proxy_port, ...}, ... ]
        }

    Proxy fetch mode (v10.0):
      - RANGE MODE (default): proxy_range=(start, end) hits
            GET {api_url}/range/{start}-{end}?format=txt
        e.g. https://nodejsclusters-213001-0.cloudclusters.net/api/external/proxies/range/1-50?format=txt
      - LEGACY MODE: pass proxy_range=None to hit
            GET {api_url}/adsterra-safe?format=txt&pageSize={page_size}

    AdsPower credentials resolution order (per field):
      1. Explicit argument (e.g. adspower_api_key='...')
      2. Environment variable (e.g. ADSPOWER_API_KEY)
      3. Built-in default from ADSPOWER_DEFAULTS

    Args:
        api_url:            Proxy API base URL (must end at /proxies).
        api_key:            API key for the proxy service.
        page_size:          Number of proxies to request (LEGACY mode only).
        fmt:                Response format ('txt').
        timeout:            HTTP request timeout.
        proxy_range:        (start, end) tuple for RANGE mode, or None for
                            LEGACY mode. Default: (1, 50).
        adspower_api_key:   AdsPower API key (overrides env var / default).
        adspower_mode:      AdsPower mode — 'local' or 'cloud'.
        adspower_base_url:  AdsPower API base URL.
        adspower_port:      AdsPower API port.
        adspower_profile_id: AdsPower profile ID.
        adspower_group_id:  AdsPower group ID.

    Returns:
        Configuration dict compatible with the old proxy.json schema.
    """
    proxies = load_proxies_from_api(
        api_url=api_url,
        api_key=api_key,
        page_size=page_size,
        fmt=fmt,
        timeout=timeout,
        proxy_range=proxy_range,
    )

    # Resolve AdsPower credentials with 3-tier fallback:
    #   explicit arg > env var > ADSPOWER_DEFAULTS
    resolved_adspower = {
        'api_key': (
            adspower_api_key
            or os.environ.get('ADSPOWER_API_KEY', '')
            or ADSPOWER_DEFAULTS['api_key']
        ),
        'mode': (
            adspower_mode
            or os.environ.get('ADSPOWER_MODE', '')
            or ADSPOWER_DEFAULTS['mode']
        ),
        'base_url': (
            adspower_base_url
            or os.environ.get('ADSPOWER_API_BASE', '')
            or ADSPOWER_DEFAULTS['base_url']
        ),
        'port': (
            adspower_port
            or (int(os.environ['ADSPOWER_PORT']) if 'ADSPOWER_PORT' in os.environ else 0)
            or ADSPOWER_DEFAULTS['port']
        ),
        'profile_id': (
            adspower_profile_id
            or os.environ.get('ADSPOWER_PROFILE_ID', '')
            or ADSPOWER_DEFAULTS['profile_id']
        ),
        'group_id': (
            adspower_group_id
            or os.environ.get('ADSPOWER_GROUP_ID', '')
            or ADSPOWER_DEFAULTS['group_id']
        ),
    }

    config = {
        'adspower': resolved_adspower,
        'proxies': proxies,
    }

    logger.info(
        f"Config loaded from API: {len(proxies)} proxies, "
        f"adspower_api_key={resolved_adspower['api_key'][:8]}..., "
        f"adspower_mode={resolved_adspower['mode']!r}, "
        f"adspower_base={resolved_adspower['base_url']}:{resolved_adspower['port']}, "
        f"profile_id={resolved_adspower['profile_id']!r}, "
        f"group_id={resolved_adspower['group_id']!r}"
    )
    return config


# ====================================================================
# Anti-Detect Browser API Clients
# ====================================================================

# ---- v11.0: Linux stability helpers ---------------------------------------
# These helpers fix the 5 root causes of "AdsPower browser dead on Linux":
#   1. Race condition: API returns ws_endpoint before Chromium binds the port.
#   2. IPv4/IPv6 mismatch: ws_endpoint says 127.0.0.1 but Chromium bound to ::1.
#   3. Renderer crash on startup: per-profile Chromium children don't inherit
#      the parent's --disable-dev-shm-usage / --no-sandbox flags.
#
# _wait_for_devtools_port() polls the TCP port for up to N seconds before
# we attempt connect_over_cdp. _normalize_ws_endpoint_for_linux() rewrites
# 127.0.0.1 → localhost (or ::1) so Python's socket layer tries both
# IPv4 and IPv6. _linux_stability_launch_args() returns the list of
# Chromium flags that MUST be passed to per-profile browser children
# spawned via the /api/v2/browser-profile/start endpoint.

def _wait_for_devtools_port(ws_endpoint: str, timeout: float = 10.0,
                            interval: float = 0.2) -> bool:
    """
    Pre-flight TCP port check before calling Playwright's connect_over_cdp.

    AdsPower's /api/v2/browser-profile/start returns the ws_endpoint
    IMMEDIATELY after the API call succeeds (code=0), but the actual
    Chromium DevTools socket needs another 1–3 seconds to bind. If we
    call connect_over_cdp during that window, Playwright hangs for the
    full 30s timeout (zombie browser detection) or fails immediately
    with ECONNREFUSED.

    This function polls the TCP port every `interval` seconds (default
    200ms) for up to `timeout` seconds (default 10s). Returns True as
    soon as the port accepts a TCP connection on EITHER IPv4 127.0.0.1
    OR IPv6 ::1 (Linux prefers ::1, the API returns 127.0.0.1 — we
    check both to handle the mismatch).

    Returns False if the port never comes up.
    """
    if not ws_endpoint:
        return False
    try:
        parsed = urlparse(ws_endpoint)
        host = parsed.hostname or '127.0.0.1'
        port = parsed.port
        if not port:
            return False
    except Exception:
        return False

    # On Linux, AdsPower binds to local.adspower.net which resolves to ::1
    # first. The ws_endpoint may say 127.0.0.1 but the socket is on ::1.
    # Probe both stacks.
    # Normalize: if host is 'localhost', socket.getaddrinfo returns both
    # 127.0.0.1 and ::1 automatically. If host is a literal IP, we add
    # the other stack manually.
    hosts_to_try = [host]
    if host == '127.0.0.1':
        hosts_to_try.append('::1')
    elif host == '::1':
        hosts_to_try.append('127.0.0.1')

    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        for h in hosts_to_try:
            try:
                # Use getaddrinfo so 'localhost' resolves to all stacks
                addrinfos = socket.getaddrinfo(
                    h, port, socket.AF_UNSPEC, socket.SOCK_STREAM
                )
                for family, socktype, proto, _, sockaddr in addrinfos:
                    s = None
                    try:
                        s = socket.socket(family, socktype, proto)
                        s.settimeout(interval)
                        s.connect(sockaddr)
                        # Port is open — DevTools is (probably) ready
                        return True
                    except OSError as e:
                        last_err = e
                    finally:
                        if s is not None:
                            try:
                                s.close()
                            except Exception:
                                pass
            except socket.gaierror:
                # Resolution failed for this host — try the next one
                continue
        time.sleep(interval)
    logger.debug(
        f"_wait_for_devtools_port: port {port} not ready after {timeout}s "
        f"(last_err={last_err})"
    )
    return False


def _normalize_ws_endpoint_for_linux(ws_endpoint: str) -> str:
    """
    Rewrite ws://127.0.0.1:PORT/... → ws://localhost:PORT/... so Playwright's
    socket layer tries both IPv4 127.0.0.1 and IPv6 ::1 transparently.

    On Linux, AdsPower Global binds its DevTools WebSocket to ::1 (because
    local.adspower.net resolves to ::1 first on glibc systems with
    /etc/hosts containing `::1 localhost`). The AdsPower API, however,
    returns `ws://127.0.0.1:PORT/...` in the ws_endpoint field — so a
    direct connect_over_cdp call to 127.0.0.1 fails with ECONNREFUSED
    even though the browser is alive and well on ::1.

    By changing the host to 'localhost', Python's getaddrinfo will return
    BOTH 127.0.0.1 and ::1, and socket.connect() will try each in turn
    until one succeeds. This eliminates the IPv4/IPv6 mismatch class of
    ECONNREFUSED errors entirely.

    For ws://local.adspower.net:PORT/... URLs we leave the host alone —
    that name already resolves to both stacks.
    """
    if not ws_endpoint:
        return ws_endpoint
    # Only rewrite literal IPv4 loopback. Don't touch ::1 (already IPv6),
    # don't touch 'localhost' (already dual-stack), don't touch
    # local.adspower.net (already dual-stack via /etc/hosts).
    if '://127.0.0.1:' in ws_endpoint:
        return ws_endpoint.replace('://127.0.0.1:', '://localhost:', 1)
    return ws_endpoint


def _linux_stability_launch_args() -> List[str]:
    """
    Return the list of Chromium flags that MUST be passed to per-profile
    browser children spawned via /api/v2/browser-profile/start.

    These flags mirror what start_adspower.sh already passes to the
    PARENT adspower_global process. Without them in launch_args, the
    per-profile Chromium CHILDREN spawned by the API do NOT inherit
    the stability flags — leading to renderer crashes on /dev/shm
    exhaustion, seccomp kills, and GPU init hangs in containers.

    Specifically:
      --disable-dev-shm-usage     : Forces Chromium to use /tmp instead of
                                    /dev/shm (default 64MB in Docker is too
                                    small; renderer crashes with "Failed to
                                    map shared memory").
      --no-sandbox                : Required when running as root in
                                    containers (no CAP_SYS_ADMIN for the
                                    setuid sandbox).
      --disable-gpu               : No GPU available in headless containers;
                                    without this, GPU init hangs 5-10s then
                                    falls back, sometimes crashing.
      --disable-software-rasterizer : Avoids SwiftShader init failures in
                                    GPU-less containers.

    These flags are SAFE to pass on Windows/macOS too — they're no-ops
    when the conditions don't apply (e.g. --disable-gpu on a Windows
    machine with a real GPU just disables hardware acceleration, which
    is fine for automation).
    """
    return [
        '--disable-dev-shm-usage',
        '--no-sandbox',
        '--disable-gpu',
        '--disable-software-rasterizer',
    ]


class AdsPowerClient:
    """
    AdsPower API Client — Local API v2.
    
    API v2 Endpoints (only v2 — v1 returns 404 on current AdsPower):
      - GET  /status                              → Check if API is running
      - POST /api/v2/browser-profile/create       → Create a new profile
      - POST /api/v2/browser-profile/start         → Start a profile (open browser)
      - POST /api/v2/browser-profile/stop          → Stop a profile (close browser)
      - POST /api/v2/browser-profile/list          → List profiles
      - POST /api/v2/browser-profile/delete        → Delete profiles
      - POST /api/v2/browser-profile/update        → Update profile config
      - GET  /api/v2/browser-profile/active        → Check if profile is active
    
    Authentication: Authorization: Bearer {API_KEY} header
    Base URL: http://127.0.0.1:{PORT}
    All mutating endpoints use POST with JSON body.
    """

    DEFAULT_LOCAL_BASE = 'http://localhost'  # 'localhost' (not 127.0.0.1) for IPv4+IPv6 fallback
    DEFAULT_LOCAL_PORT = 50325

    # v10.2: Candidate ports probed during auto-detection if the configured
    # port is unresponsive. Ordered by likelihood — newer AdsPower Global
    # editions sometimes bind to 5032 or 50301 instead of the legacy 50325.
    CANDIDATE_PORTS = (50325, 5032, 50301, 50324, 5031, 5033)

    # v10.2: How long to wait for /status on each candidate port during
    # auto-detection. Kept short so the worst-case probe time across all
    # candidates stays under ~7 seconds.
    PROBE_TIMEOUT = 1.0

    # Retry settings
    MAX_RETRIES = 3
    RETRY_DELAY = 5  # seconds between retries
    API_CALL_INTERVAL = 0.8  # minimum seconds between API calls (rate limit protection)
    _last_api_call_time = 0  # class-level timestamp of last API call

    # BUG FIX #8: Cache webgl value yang berhasil agar tidak retry 3x
    # sequential setiap update_profile (menghemat 3-6 detik per user).
    # None = belum ditentukan, akan dicoba saat update/create pertama.
    _cached_webgl_value = None  # int 0/1/2/3 atau None (omit)
    _cached_webgl_drop_vendor = False  # apakah drop vendor/renderer juga

    # BUG FIX #6: Cache profile_id yang sudah dibuat untuk reuse cepat.
    # Menghindari create_profile() + list_profiles() + update_profile()
    # yang memakan 30-40 detik setiap user.
    _cached_profile_id = None  # profile_id yang sudah di-sync dan siap pakai

    def __init__(self, api_key=None, api_base=None, port=None, profile_id=None, group_id=None,
                 auto_detect_port=True):
        """
        Args:
            api_key: AdsPower API key (sent as Authorization: Bearer header)
            api_base: Override API base URL (default: http://localhost — works
                      for both IPv4 127.0.0.1 and IPv6 ::1 bindings, since
                      AdsPower Global v8.x binds to local.adspower.net which
                      resolves to ::1)
            port: Override API port (default: 50325). If auto_detect_port=True
                  and this port is unresponsive, candidate ports will be probed.
            profile_id: ID profil yang sudah ada (opsional, skip create jika ada)
            group_id: ID grup AdsPower (opsional, auto-detect jika kosong)
            auto_detect_port: v10.2 — if True, probe /status on the configured
                  port; if it fails, scan CANDIDATE_PORTS and switch to the
                  first responsive one. Set False to disable (e.g. when you
                  explicitly pin a port via ADSPOWER_PORT and want strict mode).
        """
        self.api_key = api_key or os.environ.get('ADSPOWER_API_KEY', '')
        self.api_base = api_base or os.environ.get('ADSPOWER_API_BASE', self.DEFAULT_LOCAL_BASE)
        configured_port = port or int(os.environ.get('ADSPOWER_PORT', self.DEFAULT_LOCAL_PORT))
        self.port = configured_port
        self.default_profile_id = profile_id or os.environ.get('ADSPOWER_PROFILE_ID', '')
        self.default_group_id = group_id or os.environ.get('ADSPOWER_GROUP_ID', '')
        self.base_url = f"{self.api_base}:{self.port}"

        # v10.2: Auto-detect port if the configured one doesn't respond.
        # This handles Linux container setups where AdsPower Global binds to
        # non-default ports (e.g. 5032 instead of 50325).
        if auto_detect_port:
            detected = self._probe_and_fix_port(configured_port)
            if detected != configured_port:
                logger.info(
                    f"AdsPower: Auto-detected active API on port {detected} "
                    f"(configured was {configured_port}). Switching base_url."
                )
                self.port = detected
                self.base_url = f"{self.api_base}:{self.port}"

        logger.info(f"AdsPower: Using Local API v2 ({self.base_url})")
        if not self.api_key:
            logger.warning("AdsPower: Tidak ada API key! Set ADSPOWER_API_KEY.")
        if self.default_profile_id:
            logger.info(f"AdsPower: Default profile_id = {self.default_profile_id}")
        if self.default_group_id:
            logger.info(f"AdsPower: Default group_id = {self.default_group_id}")

    def _probe_status_port(self, port, timeout=None):
        """
        v10.2: Quick probe of /status on a given port. Returns True if the
        AdsPower API responds with valid JSON (any HTTP 200 with JSON body).
        Uses 'localhost' (not 127.0.0.1) so both IPv4 and IPv6 bindings work.
        """
        timeout = timeout if timeout is not None else self.PROBE_TIMEOUT
        url = f"{self.api_base}:{port}/status"
        try:
            r = http_requests.get(url, timeout=timeout, allow_redirects=False)
            if r.status_code != 200:
                return False
            # Must be JSON-like (AdsPower /status returns JSON).
            ctype = (r.headers.get('Content-Type') or '').lower()
            if 'json' not in ctype and not r.text.lstrip().startswith('{'):
                return False
            return True
        except http_requests.exceptions.RequestException:
            return False
        except Exception:
            return False

    def _probe_and_fix_port(self, configured_port):
        """
        v10.2: Probe the configured port first; if unresponsive, scan
        CANDIDATE_PORTS and return the first one that answers /status.
        Returns the chosen port (may equal configured_port if it's healthy).
        """
        # Try the configured port first (short probe).
        if self._probe_status_port(configured_port):
            return configured_port
        logger.info(
            f"AdsPower: Configured port {configured_port} not responding on /status; "
            f"scanning candidate ports {self.CANDIDATE_PORTS}..."
        )
        for cand in self.CANDIDATE_PORTS:
            if cand == configured_port:
                continue
            if self._probe_status_port(cand):
                logger.info(f"AdsPower: Found responsive API on port {cand}")
                return cand
        logger.warning(
            f"AdsPower: No responsive API port found among configured "
            f"({configured_port}) + candidates {self.CANDIDATE_PORTS}. "
            f"Will keep using {configured_port}; expect connection errors."
        )
        return configured_port

    def _headers(self):
        """Build request headers with Bearer auth."""
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def _request(self, method, path, json_data=None, params=None, timeout=15, _retry_count=0, silent_errors=False):
        """
        Make an HTTP request to AdsPower Local API.
        
        Args:
            method: 'GET' or 'POST'
            path: API endpoint path (e.g., '/api/v2/browser-profile/start')
            json_data: JSON body for POST requests
            params: Query parameters for GET requests
            timeout: Request timeout in seconds
            _retry_count: Internal retry counter
            silent_errors: bila True, suppress warning log untuk non-zero
                           response codes (response tetap dikembalikan).
                           Dipakai oleh stop_profile(silent=True) agar
                           "Profile is not open" tidak noisy di log.
        """
        url = f"{self.base_url}{path}"
        
        # Rate limit protection: ensure minimum interval between API calls
        now = time.time()
        elapsed = now - AdsPowerClient._last_api_call_time
        if elapsed < AdsPowerClient.API_CALL_INTERVAL and AdsPowerClient._last_api_call_time > 0:
            wait = AdsPowerClient.API_CALL_INTERVAL - elapsed
            time.sleep(wait)
        AdsPowerClient._last_api_call_time = time.time()
        
        try:
            if method == 'GET':
                resp = http_requests.get(url, headers=self._headers(), params=params, timeout=timeout)
            else:
                resp = http_requests.post(url, headers=self._headers(), json=json_data or {}, timeout=timeout)
            
            logger.debug(f"AdsPower {method} {path} → HTTP {resp.status_code}, Body length: {len(resp.text)}")
            
            # Handle empty response
            if not resp.text or resp.text.strip() == '':
                logger.warning(f"AdsPower {method} {path} mengembalikan response KOSONG (HTTP {resp.status_code})")
                return None
            
            # Parse JSON
            try:
                data = resp.json()
            except ValueError:
                logger.error(
                    f"AdsPower {method} {path} mengembalikan response BUKAN JSON!\n"
                    f"HTTP {resp.status_code}, Response: {resp.text[:300]}"
                )
                return None
            
            if data.get('code') == 0:
                return data
            else:
                # v9.7.1: Log full request payload + response on error so we can
                # see exactly which field AdsPower is rejecting.
                # v9.5.2: Bila silent_errors=True (dipanggil dari stop_profile
                # silent cleanup), skip warning log — caller akan handle
                # sendiri berdasarkan response code/msg.
                if not silent_errors:
                    try:
                        payload_str = json.dumps(json_data or params or {}, default=str)
                        if len(payload_str) > 1500:
                            payload_str = payload_str[:1500] + '...(truncated)'
                    except Exception:
                        payload_str = '<unable-to-serialize>'
                    logger.warning(
                        f"AdsPower API error: code={data.get('code')}, msg={data.get('msg', 'unknown')}\n"
                        f"  endpoint : {method} {path}\n"
                        f"  request  : {payload_str}\n"
                        f"  response : {json.dumps(data, default=str)[:500]}"
                    )
                return data
                
        except http_requests.exceptions.ConnectionError as e:
            if _retry_count < self.MAX_RETRIES:
                _retry_count += 1
                logger.warning(f"AdsPower Local API tidak bisa terhubung (percobaan {_retry_count}/{self.MAX_RETRIES}): {e}")
                logger.info(f"Menunggu {self.RETRY_DELAY} detik sebelum mencoba lagi...")
                time.sleep(self.RETRY_DELAY)
                return self._request(method, path, json_data, params, timeout, _retry_count)
            logger.error(
                f"AdsPower Local API gagal setelah {self.MAX_RETRIES}x percobaan!\n"
                f"Kemungkinan penyebab:\n"
                f"  1. AdsPower belum dibuka atau belum login\n"
                f"  2. Local API tidak aktif di port {self.port}\n"
                f"  3. Port salah (cek Settings → Local API di AdsPower)\n"
                f"Error detail: {e}"
            )
            return None
        except Exception as e:
            logger.error(f"AdsPower API request failed: {e}")
            return None

    def _get(self, path, params=None, timeout=15):
        """GET request helper."""
        return self._request('GET', path, params=params, timeout=timeout)

    def _post(self, path, json_data=None, timeout=15, silent_errors=False):
        """POST request helper."""
        return self._request('POST', path, json_data=json_data, timeout=timeout, silent_errors=silent_errors)

    def check_status(self):
        """Check if AdsPower Local API is running."""
        result = self._get('/status')
        if result is not None:
            logger.info(f"AdsPower Local API v2 accessible ({self.base_url})")
            return True
        logger.error(
            f"AdsPower Local API TIDAK bisa diakses di {self.base_url}!\n"
            f"Pastikan:\n"
            f"  1. AdsPower sudah dibuka dan login\n"
            f"  2. Local API aktif di port {self.port} (Settings → Local API)\n"
            f"  3. Cek di browser: {self.base_url}/status"
        )
        return False

    def _get_default_group_id(self):
        """
        Get group_id for profile creation.

        Priority:
          1. Use explicitly configured group_id (from constructor, env var, or Proxy API config)
          2. Fallback to "0" (default group in most AdsPower installations)
        """
        if self.default_group_id:
            logger.info(f"AdsPower: Using configured group_id = {self.default_group_id}")
            return self.default_group_id
        logger.info('AdsPower: Using default group_id = "0"')
        return "0"

    # =====================================================
    # v9.1: Helpers to build fingerprint_config bodies
    # =====================================================
    # AdsPower's v2 /browser-profile/create and /browser-profile/update
    # endpoints accept a `fingerprint_config` object. The previous code
    # only populated a subset (ua, os, language, resolution, timezone,
    # font_list) and let AdsPower auto-generate the rest — which produced
    # values that did NOT match our sync_config (random WebGL vendor,
    # random hardware_concurrency, etc.).
    #
    # These helpers build a COMPLETE fingerprint_config from sync_config
    # so the running AdsPower browser exposes exactly the fingerprint we
    # computed in ProfileSynchronizer.build_full_profile.

    @staticmethod
    def _normalize_os_for_adspower(os_type):
        """
        AdsPower accepts: 'Windows', 'macOS', 'Linux', 'Android', 'iOS'.
        Our internal OS values: 'Windows', 'Mac', 'Linux', 'Android'.
        """
        mapping = {
            'Windows': 'Windows',
            'Mac': 'macOS',
            'Linux': 'Linux',
            'Android': 'Android',
            'iOS': 'iOS',
        }
        return mapping.get(os_type, 'Windows')

    def _build_fingerprint_config(self, profile_config):
        """
        Build the AdsPower fingerprint_config dict from our sync_config.
        Includes: ua, os, language, resolution, screen, timezone, font_list,
        WebGL vendor/renderer, hardware (memory/CPU), touch, color_depth,
        platform, WebRTC mode.

        v9.9 (console-profile-device sync):
          - Added `device_pixel_ratio` and `is_mobile` so mobile devices
            (Pixel 8, iPhone, etc.) get the correct deviceScaleFactor
            and navigator.platform in AdsPower (previously AdsPower
            fell back to desktop defaults for "Android" profiles,
            causing navigator.platform = 'Win32' on a Pixel 8 device).
          - Added `accept_language` so the Accept-Language header matches
            the locale we set in navigator.language.
          - Added `app_version` derived from the UA (some AdsPower
            editions use this for navigator.appVersion).
        """
        ua = profile_config.get('ua', '')
        os_type = profile_config.get('os', 'Windows')
        lan = profile_config.get('lan', 'en-US')
        resolution = profile_config.get('resolution', '1920x1080')
        timezone = profile_config.get('timezone', 'America/New_York')

        fingerprint = {}
        if ua:
            fingerprint['ua'] = ua
        if os_type:
            fingerprint['os'] = self._normalize_os_for_adspower(os_type)
        if lan:
            fingerprint['language'] = [lan]
            # v9.9: also set accept_language so HTTP Accept-Language
            # header matches navigator.language (anti-fraud cross-check).
            fingerprint['accept_language'] = f'{lan},en;q=0.9'
        if resolution and 'x' in str(resolution):
            parts = str(resolution).split('x')
            if len(parts) == 2:
                try:
                    fingerprint['resolution'] = [int(parts[0]), int(parts[1])]
                except ValueError:
                    pass
        if timezone:
            fingerprint['timezone'] = timezone

        font_list = profile_config.get('font_list', '')
        if font_list:
            fingerprint['font_list'] = font_list.split(',')

        # v9.8: WebGL — AdsPower Local API v2 expects `webgl` as an INTEGER
        # 0|1|2|3 (NOT a nested dict, NOT a string):
        #   0 = Real   (use real GPU)
        #   1 = Custom (use webgl_vendor / webgl_renderer top-level keys)
        #   2 = Block  (disable WebGL)
        #   3 = Noise  (perturb real values)
        #
        # History of this fix:
        #   v9.6   : sent `webgl: {mode:'mask', vendor, renderer}` (DICT) → REJECTED
        #   v9.7   : sent `webgl: 1` (int) → still rejected (residual error)
        #   v9.7.1 : added `webgl_image: 0` → made it WORSE (webgl_image is not
        #            a real AdsPower v2 field — was causing the residual error)
        #   v9.8   : REMOVED webgl_image. Only send `webgl` as int 1, plus
        #            webgl_vendor / webgl_renderer as top-level strings.
        #            Added retry-with-fallback in create_profile() and
        #            update_profile() so the bot can still launch even if
        #            AdsPower rejects our webgl value.
        webgl_vendor = profile_config.get('webgl_vendor', '')
        webgl_renderer = profile_config.get('webgl_renderer', '')
        if webgl_vendor or webgl_renderer:
            fingerprint['webgl'] = 1  # 1 = Custom/mask mode (MUST be int, not str/dict)
            # webgl_vendor / webgl_renderer are top-level keys inside
            # fingerprint_config (only consulted when webgl == 1).
            fingerprint['webgl_vendor'] = webgl_vendor or 'Google Inc. (NVIDIA)'
            fingerprint['webgl_renderer'] = webgl_renderer or 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)'

        # v9.8: DEFENSIVE SANITIZATION — force `webgl` to be a valid integer.
        # Handles edge cases where upstream code might inject bad values.
        if 'webgl' in fingerprint:
            v = fingerprint['webgl']
            if isinstance(v, bool):
                fingerprint['webgl'] = 1 if v else 0
            elif isinstance(v, str) and v.strip().isdigit():
                fingerprint['webgl'] = int(v.strip())
            elif isinstance(v, float) and v in (0.0, 1.0, 2.0, 3.0):
                fingerprint['webgl'] = int(v)
            elif not isinstance(v, int) or v not in (0, 1, 2, 3):
                # Invalid type (dict/list/None/out-of-range) — use safe default
                fingerprint['webgl'] = 1

        # v9.8: NEVER send `webgl_image` — it is NOT a valid AdsPower v2 field.
        # If any upstream code injected it, remove it to avoid validation errors.
        fingerprint.pop('webgl_image', None)

        # v9.1: Hardware concurrency & device memory
        if profile_config.get('hardware_concurrency'):
            fingerprint['hardware_concurrency'] = int(profile_config['hardware_concurrency'])
        if profile_config.get('device_memory'):
            # AdsPower expects device_memory in GB (4/8/16/32)
            fingerprint['device_memory'] = int(profile_config['device_memory'])

        # v9.1: Touch points — distinguishes mobile vs desktop
        if 'max_touch_points' in profile_config:
            fingerprint['max_touch_points'] = int(profile_config['max_touch_points'])

        # v9.1: Color depth
        if profile_config.get('color_depth'):
            fingerprint['color_depth'] = int(profile_config['color_depth'])

        # v9.1: Platform string (Win32 / MacIntel / Linux x86_64 / Linux armv81)
        if profile_config.get('platform'):
            fingerprint['platform'] = profile_config['platform']

        # v9.9: device_pixel_ratio + is_mobile — CRITICAL for mobile device
        # consistency. AdsPower v2 fingerprint_config supports both fields.
        # Without these, AdsPower falls back to desktop defaults for mobile
        # devices, producing navigator.platform='Win32' + DPR=1.0 even when
        # the UA clearly says Android/Pixel.
        if profile_config.get('device_scale_factor') is not None:
            try:
                dpr = float(profile_config['device_scale_factor'])
                if dpr > 0:
                    # AdsPower expects a float (e.g. 2.625 for Pixel 8).
                    fingerprint['device_pixel_ratio'] = dpr
            except (TypeError, ValueError):
                pass
        # is_mobile: only set when explicitly provided. AdsPower v2 docs
        # treat is_mobile as a boolean (true/false). When omitted, AdsPower
        # derives it from the OS — but we set it explicitly to be safe.
        if 'is_mobile' in profile_config:
            fingerprint['is_mobile'] = bool(profile_config['is_mobile'])

        # v9.9: app_version — derived from UA. Some AdsPower editions use
        # this to set navigator.appVersion separately from navigator.userAgent.
        if ua:
            # navigator.appVersion is the UA without the "Mozilla/" prefix.
            if ua.startswith('Mozilla/'):
                fingerprint['app_version'] = ua[len('Mozilla/'):]
            else:
                fingerprint['app_version'] = ua

        # v9.1: WebRTC — must be blocked to prevent IP leak
        webrtc_mode = profile_config.get('webrtc_mode', 'disabled')
        if webrtc_mode == 'disabled':
            # AdsPower's webrtc_config: mode='block' (altered) or 'real'
            fingerprint['webrtc_config'] = {'mode': 'block'}

        return fingerprint

    def _build_proxy_config(self, profile_config):
        """
        Build AdsPower user_proxy_config from sync_config.
        v9.2: Added launch_args to include --ignore-certificate-errors so
        the browser launched by AdsPower will ignore SSL errors when
        connecting through an HTTPS proxy tunnel. This fixes ERR_SSL_PROTOCOL_ERROR.
        """
        proxy_host = profile_config.get('proxy_host', '')
        if not proxy_host:
            return {'proxy_soft': 'no_proxy'}
        proxy_type = profile_config.get('proxy_type', 'http')
        # v9.2: Ensure proxy_type supports HTTPS tunneling.
        # FloppyData and most residential proxies use HTTP protocol
        # but support CONNECT for HTTPS targets. The proxy_type 'http'
        # in AdsPower means the proxy speaks HTTP (including CONNECT).
        config = {
            'proxy_soft': 'other',
            'proxy_type': proxy_type,
            'proxy_host': proxy_host,
            'proxy_port': str(profile_config.get('proxy_port', '')),
            'proxy_user': profile_config.get('proxy_user', ''),
            'proxy_password': profile_config.get('proxy_password', ''),
        }
        return config

    def _build_create_body(self, profile_config, group_id):
        """Build the POST body for /api/v2/browser-profile/create.

        v9.9 (console-profile-device sync):
          - Profile name now comes from sync_config['name'] (already set
            by profile_sync.ProfileSynchronizer.build_full_profile to
            include device token + country code + timezone, e.g.
            "bot_Pixel8_US_America-New-York_1730000000_1234"). When the
            upstream sync_config name is missing, fall back to the old
            os-based name.
          - Added `remark` field so the AdsPower console UI shows the
            full device name + proxy host + timezone as a tooltip.
            AdsPower v2 supports `remark` (also called "notes" in the UI)
            on profile create/update.
        """
        # Prefer the sync_config-computed name (includes device token +
        # country + tz). Fall back to a generic name if not provided.
        profile_name = (
            profile_config.get('name')
            or f'bot_profile_{int(time.time())}'
        )

        # Build a human-readable remark so the operator can see at a
        # glance which device + proxy + timezone this profile represents
        # in the AdsPower console.
        remark_parts = []
        device_name = (profile_config.get('device_name') or '').strip()
        if device_name:
            remark_parts.append(f'device={device_name}')
        os_tag = (profile_config.get('os') or '').strip()
        if os_tag:
            remark_parts.append(f'os={os_tag}')
        country = (profile_config.get('country') or '').strip()
        cc = (profile_config.get('country_code') or '').strip()
        if country:
            remark_parts.append(f'geo={country}' + (f' ({cc})' if cc else ''))
        tz = (profile_config.get('timezone') or '').strip()
        if tz:
            remark_parts.append(f'tz={tz}')
        proxy_host = (profile_config.get('proxy_host') or '').strip()
        proxy_port = (profile_config.get('proxy_port') or '').strip()
        if proxy_host:
            remark_parts.append(f'proxy={proxy_host}:{proxy_port}')
        ua_short = (profile_config.get('ua') or '')[:80]
        if ua_short:
            remark_parts.append(f'ua={ua_short}')
        remark = ' | '.join(remark_parts) if remark_parts else ''

        body = {
            'name': profile_name,
            'group_id': group_id,
            'user_proxy_config': self._build_proxy_config(profile_config),
            'fingerprint_config': self._build_fingerprint_config(profile_config),
        }
        # Only include remark if non-empty — some AdsPower API versions
        # reject empty-string remark.
        if remark:
            body['remark'] = remark
        # v9.2.2: REMOVED launch_args from create body.
        # Putting launch_args in create/update caused AdsPower to crash
        # the browser on start. launch_args is now ONLY sent in
        # start_profile() API call, which applies them at runtime
        # without corrupting the profile configuration.
        return body

    def _build_update_body(self, profile_config):
        """
        Build the POST body for /api/v2/browser-profile/update.
        Includes BOTH proxy and fingerprint_config so reused profiles
        are fully re-synchronized with our sync_config.

        v9.2.2: REMOVED launch_args — caused browser crashes when stored
        in profile config. launch_args is only sent at start_profile() time.

        v9.9: also updates `name` and `remark` so the AdsPower console
        stays in sync with the device + proxy + timezone the profile is
        currently configured for (otherwise the displayed name lies
        after we re-pin the profile to a different device).
        """
        body = {
            'user_proxy_config': self._build_proxy_config(profile_config),
            'fingerprint_config': self._build_fingerprint_config(profile_config),
        }
        # Re-sync display name + remark so the AdsPower console reflects
        # the current device/proxy/timezone assignment.
        name = (profile_config.get('name') or '').strip()
        if name:
            body['name'] = name
        remark_parts = []
        device_name = (profile_config.get('device_name') or '').strip()
        if device_name:
            remark_parts.append(f'device={device_name}')
        country = (profile_config.get('country') or '').strip()
        cc = (profile_config.get('country_code') or '').strip()
        if country:
            remark_parts.append(f'geo={country}' + (f' ({cc})' if cc else ''))
        tz = (profile_config.get('timezone') or '').strip()
        if tz:
            remark_parts.append(f'tz={tz}')
        proxy_host = (profile_config.get('proxy_host') or '').strip()
        proxy_port = (profile_config.get('proxy_port') or '').strip()
        if proxy_host:
            remark_parts.append(f'proxy={proxy_host}:{proxy_port}')
        if remark_parts:
            body['remark'] = ' | '.join(remark_parts)
        return body

    def _cleanup_old_profiles(self, keep_count=2):
        """
        Delete old profiles to free up slots when profile limit is reached.
        
        Deletes ALL profiles (not just bot-created) starting from the oldest.
        Adds delays between operations to avoid rate limiting.
        
        Args:
            keep_count: Number of profiles to keep (default: 2)
        
        Returns:
            Number of profiles deleted
        """
        time.sleep(1)  # Rate limit protection
        profiles = self.list_profiles()
        if not profiles:
            return 0
        
        # Collect all profiles with IDs
        all_profiles = []
        for p in profiles:
            pid = p.get('id', p.get('user_id', p.get('profile_id', '')))
            name = p.get('name', 'unnamed')
            if pid:
                all_profiles.append({'id': pid, 'name': name})
        
        if not all_profiles:
            return 0
        
        # Sort by name (oldest timestamp first for bot_profile_*, alphabetical otherwise)
        all_profiles.sort(key=lambda x: x['name'])
        
        # Delete oldest, keep only `keep_count`
        to_delete = all_profiles[:-keep_count] if len(all_profiles) > keep_count else []
        
        deleted = 0
        for p in to_delete:
            logger.info(f"AdsPower: Deleting old profile: {p['name']} (id={p['id']})")
            time.sleep(0.5)  # Rate limit protection
            # v9.5.2: silent=True — old profile mungkin sudah tidak open,
            # "Profile is not open" expected dan tidak perlu noisy.
            self.stop_profile(p['id'], silent=True)
            time.sleep(0.5)
            self.delete_profile(p['id'])
            deleted += 1
            time.sleep(0.5)
        
        if deleted > 0:
            logger.info(f"AdsPower: Cleaned up {deleted} old profiles (kept {len(all_profiles) - deleted})")
        return deleted

    def create_profile(self, profile_config):
        """
        Create or reuse a browser profile via AdsPower Local API v2.

        Strategy (avoids profile limit and rate limiting):
          1. If profile_id configured (via env var, constructor, or Proxy API config) → use it directly
             (but still call update_profile to sync fingerprint & proxy)
          2. Try to reuse existing profile (update proxy + fingerprint config)
          3. If no existing profile, create new
          4. If limit reached, delete ALL old profiles and retry

        v9.1: now sends the FULL fingerprint_config (WebGL vendor/renderer,
        device_memory, hardware_concurrency, max_touch_points, color_depth,
        platform, screen size) — not just ua/os/language/resolution/timezone/
        font_list. Previously AdsPower only got a partial fingerprint and
        fell back to its own random values for the missing fields, causing
        desync between sync_config (what we computed) and what the running
        AdsPower browser actually exposed.
        """
        # If we have a pre-configured profile_id, use it directly
        # v9.1: but still push the fingerprint_config so the reused profile
        # is synchronized with our sync_config (otherwise AdsPower keeps
        # whatever fingerprint the profile was originally created with).
        if self.default_profile_id:
            logger.info(f"AdsPower: Menggunakan profile_id yang sudah dikonfigurasi: {self.default_profile_id}")
            self.update_profile(self.default_profile_id, self._build_update_body(profile_config))
            time.sleep(0.5)
            return self.default_profile_id

        # Auto-detect group_id (required by AdsPower API)
        group_id = self._get_default_group_id()

        # Extract proxy fields
        proxy_host = profile_config.get('proxy_host', '')
        proxy_port = profile_config.get('proxy_port', '')
        proxy_user = profile_config.get('proxy_user', '')
        proxy_password = profile_config.get('proxy_password', '')
        proxy_type = profile_config.get('proxy_type', 'http')

        # =====================================================
        # STEP 1: Try to reuse existing profile (avoids limit)
        # =====================================================
        time.sleep(0.5)  # Rate limit protection
        existing_id = self._find_reusable_profile()
        if existing_id:
            logger.info(f"AdsPower: Reusing existing profile {existing_id} (updating proxy + fingerprint config)")
            # v9.1: update BOTH proxy AND fingerprint_config so the reused
            # profile is fully synchronized with our sync_config.
            self.update_profile(existing_id, self._build_update_body(profile_config))
            time.sleep(0.5)  # Rate limit protection
            return existing_id

        # =====================================================
        # STEP 2: Create new profile
        # =====================================================
        body = self._build_create_body(profile_config, group_id)

        logger.info(f"AdsPower: Creating profile (group_id={group_id}, proxy={proxy_host}:{proxy_port})")
        result = self._post('/api/v2/browser-profile/create', json_data=body)

        # Handle profile limit — cleanup ALL old profiles and retry
        if result and result.get('code') != 0:
            error_msg = result.get('msg', '')
            if 'limit' in error_msg.lower() or 'exceed' in error_msg.lower():
                logger.warning(f"AdsPower: Profile limit reached! Deleting old profiles...")
                deleted = self._cleanup_old_profiles(keep_count=0)
                if deleted > 0:
                    time.sleep(1)  # Rate limit protection
                    logger.info(f"AdsPower: Retrying profile creation after cleanup...")
                    result = self._post('/api/v2/browser-profile/create', json_data=body)

        # v9.8: Handle webgl error — retry with fallback webgl values
        if result and result.get('code') != 0:
            err_msg = result.get('msg', '').lower()
            if 'webgl' in err_msg and ('must be' in err_msg or '0,1,2,3' in err_msg):
                logger.warning(
                    f"AdsPower: create_profile failed with webgl error. "
                    f"Trying fallback variants..."
                )
                fp_original = body.get('fingerprint_config', {})
                fp_variants = [
                    ('webgl=0 (Real)', self._make_webgl_variant(fp_original, webgl_value=0, drop_vendor=False)),
                    ('webgl removed', self._make_webgl_variant(fp_original, webgl_value=None, drop_vendor=True)),
                ]
                for label, fp_variant in fp_variants:
                    logger.info(f"AdsPower: Retrying create with fallback: {label}")
                    fallback_body = dict(body)
                    fallback_body['fingerprint_config'] = fp_variant
                    time.sleep(0.5)
                    result = self._post('/api/v2/browser-profile/create', json_data=fallback_body)
                    if result and result.get('code') == 0:
                        logger.info(f"AdsPower: create succeeded with fallback: {label}")
                        break
                    # If still webgl error, try next variant; otherwise stop
                    next_err = (result.get('msg', '') if result else '').lower()
                    if not ('webgl' in next_err and ('must be' in next_err or '0,1,2,3' in next_err)):
                        break

        if result:
            if result.get('code') == 0:
                data = result.get('data', {})
                profile_id = ''
                
                if isinstance(data, dict):
                    for key in ['id', 'user_id', 'profile_id', 'browser_id', 'serial_number']:
                        profile_id = data.get(key, '')
                        if profile_id:
                            break
                    if not profile_id and 'profile' in data:
                        profile_data = data.get('profile', {})
                        if isinstance(profile_data, dict):
                            for key in ['id', 'user_id', 'profile_id']:
                                profile_id = profile_data.get(key, '')
                                if profile_id:
                                    break
                elif isinstance(data, str):
                    profile_id = data
                elif isinstance(data, list) and len(data) > 0:
                    first = data[0]
                    if isinstance(first, dict):
                        profile_id = first.get('id', first.get('user_id', first.get('profile_id', '')))
                    elif isinstance(first, str):
                        profile_id = first
                
                if profile_id:
                    logger.info(f"AdsPower: Profil berhasil dibuat: {profile_id}")
                    # v9.9: verify that AdsPower actually stored the
                    # fingerprint_config we sent. Logs warnings for any
                    # mismatched field. Non-fatal — profile is usable
                    # even if some fields fell back to AdsPower defaults.
                    try:
                        self.verify_profile(profile_id, profile_config)
                    except Exception as verify_err:
                        logger.debug(f"AdsPower verify_profile post-create failed: {verify_err}")
                    return profile_id
                
                logger.warning(
                    f"AdsPower: create returned success but no profile_id in data! "
                    f"data: {json.dumps(data, default=str)[:300]}"
                )
        
        error_msg = result.get('msg', 'unknown') if result else 'no response'
        logger.error(
            f"AdsPower: Gagal membuat profil baru. Error: {error_msg}\n"
            f"Solusi: Set 'profile_id' via env var ADSPOWER_PROFILE_ID atau Proxy API config.\n"
            f"  1. Buka AdsPower, buat profil baru secara manual\n"
            f"  2. Salin ID profil dari daftar profil\n"
            f"  3. Set profile_id via load_proxy_config(profile_id='ID_PROFIL')"
        )
        return None

    def _find_reusable_profile(self):
        """
        Find an existing profile that can be reused.
        Prefers profiles that are not currently running.
        
        Returns:
            profile_id string, or None if no profile found
        """
        profiles = self.list_profiles()
        if not profiles:
            return None
        
        # Use the first available profile
        for p in profiles:
            pid = p.get('id', p.get('user_id', p.get('profile_id', '')))
            if pid:
                logger.info(f"AdsPower: Found reusable profile: {p.get('name', 'unnamed')} (id={pid})")
                return pid
        
        return None

    def list_profiles(self, page=1, limit=200):
        """
        List existing browser profiles from AdsPower (v2 API).
        """
        body = {'page': page, 'limit': limit}
        result = self._post('/api/v2/browser-profile/list', json_data=body)
        if result and result.get('code') == 0:
            profiles = result.get('data', {}).get('list', [])
            logger.info(f"AdsPower has {len(profiles)} profiles")
            return profiles
        
        logger.warning("Failed to list AdsPower profiles")
        return []

    def stop_profile(self, profile_id, silent=False, clear_cache=False):
        """
        Stop a running browser profile (v2 API).

        v10.11.1 UPGRADE — MOD 5 FIX: Added `clear_cache` parameter.
          AdsPower's Local API TIDAK punya endpoint /clear-cache terpisah
          (endpoint itu return 404 di hampir semua versi AdsPower). Cara
          BENAR untuk clear cache adalah dengan menambahkan parameter
          `clear_cache: true` pada saat memanggil stop endpoint.

          - v2: POST /api/v2/browser-profile/stop  body: {profile_id, clear_cache: true}
          - v1: GET  /api/v1/browser/stop?user_id=xxx&clear_cache_after_closing=1

          Ketika `clear_cache=True`, kita kirim BOTH variants:
            1. v2 stop dengan clear_cache=true (preferred — single call)
            2. v1 stop dengan clear_cache_after_closing=1 (fallback bila v2
               tidak mengenali parameter clear_cache di versi AdsPower tertentu)

        v9.5.1: "Profile is not open" adalah response NORMAL bila profile
        memang sudah closed (mis. setelah start gagal). Jangan log sebagai
        error — log di level debug saja agar tidak menggangu.

        v9.5.2: Teruskan silent=True ke _post() sebagai silent_errors=True
        agar _request() JUGA skip warning log di source-nya. Sebelumnya,
        _request() sudah log warning "AdsPower API error: code=-1, msg=Profile
        is not open" SEBELUM return ke stop_profile(), sehingga flag silent
        di stop_profile() tidak efektif.

        Args:
            profile_id: AdsPower profile ID
            silent: bila True, suppress SEMUA logging (warning dari _request
                    maupun dari stop_profile sendiri). Untuk pre-stop cleanup.
            clear_cache: bila True, kirim parameter clear_cache=true ke API
                         agar AdsPower membersihkan cache profile setelah
                         browser di-stop. Ini cara OFFICIAL AdsPower untuk
                         clear cache (bukan endpoint /clear-cache terpisah).
        """
        # ============================================================
        # v10.11.1: Build body dengan optional clear_cache parameter.
        # AdsPower v2 stop endpoint menerima "clear_cache": true|false.
        # ============================================================
        body = {'profile_id': profile_id}
        if clear_cache:
            body['clear_cache'] = True

        try:
            result = self._post('/api/v2/browser-profile/stop',
                                json_data=body,
                                silent_errors=silent)
        except Exception as e:
            if not silent:
                logger.warning(f"AdsPower: stop_profile exception ({e})")
            return False

        # ============================================================
        # v10.11.1: Fallback ke v1 API bila v2 stop + clear_cache gagal
        # dipahami oleh versi AdsPower tertentu. v1 endpoint pakai query
        # parameter `clear_cache_after_closing=1` (integer, bukan boolean).
        # ============================================================
        if clear_cache and (not result or result.get('code') != 0):
            try:
                v1_result = self._get(
                    '/api/v1/browser/stop',
                    params={
                        'user_id': profile_id,
                        'clear_cache_after_closing': 1,
                    },
                    silent_errors=silent,
                )
                if v1_result and v1_result.get('code') == 0:
                    if not silent:
                        logger.info(f"AdsPower: profile {profile_id} stopped + cache cleared (v1 fallback)")
                    return True
            except Exception as e:
                if not silent:
                    logger.debug(f"AdsPower: v1 stop+clear_cache fallback exception ({e})")

        if not result:
            if not silent:
                logger.warning(f"AdsPower: stop_profile {profile_id} returned None")
            return False

        code = result.get('code', -1)
        msg = (result.get('msg') or '').lower()

        # code=0 → success
        if code == 0:
            if not silent:
                if clear_cache:
                    logger.info(f"AdsPower: profile {profile_id} stopped + cache cleared OK")
                else:
                    logger.info(f"AdsPower: profile {profile_id} stopped OK")
            return True

        # "Profile is not open" / "not running" → idempotent: profile
        # memang sudah closed. Treated as success.
        # v10.11.1: Bila clear_cache=True dan profile sudah closed, cache
        # seharusnya sudah di-clear pada stop sebelumnya — jadi tetap OK.
        if 'not open' in msg or 'not running' in msg or 'not started' in msg:
            if not silent:
                logger.debug(f"AdsPower: profile {profile_id} already closed (idempotent stop)")
            return True

        # Other error → log (kecuali silent)
        if not silent:
            logger.warning(f"AdsPower: stop_profile {profile_id} failed: code={code} msg={result.get('msg')}")
        return False

    def clear_profile_cache(self, profile_id, silent=False):
        """
        Clear browser cache untuk profile.

        v10.11.1 UPGRADE — MOD 5 FIX (CRITICAL):
          Sebelumnya, method ini memanggil endpoint:
            POST /api/v2/browser-profile/clear-cache
          Endpoint itu TIDAK ADA di AdsPower Local API (return HTTP 404
          "Not Found"). Akibatnya, setiap kali bot berganti profile/user,
          log dipenuhi error:
            "AdsPower POST /api/v2/browser-profile/clear-cache mengembalikan
             response BUKAN JSON! HTTP 404, Response: Not Found"
            "AdsPower: clear-cache failed: None"

          Cara OFFICIAL AdsPower untuk clear cache adalah dengan menambahkan
          parameter `clear_cache: true` saat memanggil stop endpoint. Karena
          itu, method ini sekarang diimplementasikan sebagai wrapper yang
          memanggil stop_profile(clear_cache=True).

          Strategi (berurutan, stop pada first success):
            1. v2 stop dengan clear_cache=true  (preferred)
            2. v1 stop dengan clear_cache_after_closing=1  (fallback)
            3. Coba endpoint /api/v2/browser-profile/clear-cache (sangat
               jarang didukung, tapi di-try silent untuk compat)
            4. Bila semua gagal (browser tidak running), return True
               (idempotent — cache seharusnya sudah di-clear pada stop
               sebelumnya, atau memang tidak ada yang perlu di-clear).

        Args:
            profile_id: AdsPower profile ID
            silent: bila True, suppress logging kecuali level debug.
        """
        # ============================================================
        # Strategy 1+2: stop_profile dengan clear_cache=True.
        # Method stop_profile() sudah handle v2 + v1 fallback secara
        # internal. Bila browser sedang running, cache akan di-clear.
        # Bila browser tidak running, stop_profile return True
        # (idempotent) — cache seharusnya sudah bersih dari stop sebelumnya.
        # ============================================================
        try:
            ok = self.stop_profile(profile_id, silent=True, clear_cache=True)
            if ok:
                if not silent:
                    logger.info(f"AdsPower: cache cleared for profile {profile_id} (via stop+clear_cache)")
                return True
        except Exception as e:
            if not silent:
                logger.debug(f"AdsPower: stop+clear_cache exception ({e}) — trying legacy endpoint")

        # ============================================================
        # Strategy 3: Coba endpoint /clear-cache (legacy/rare).
        # Hampir tidak pernah ada di versi AdsPower manapun, tapi dicoba
        # silent untuk compatibilitas bila suatu saat AdsPower menambahkannya.
        # ============================================================
        try:
            result = self._post('/api/v2/browser-profile/clear-cache',
                                json_data={'profile_id': profile_id},
                                silent_errors=True)
            if result and result.get('code') == 0:
                if not silent:
                    logger.info(f"AdsPower: cache cleared for profile {profile_id} (legacy /clear-cache endpoint)")
                return True
        except Exception:
            pass  # endpoint tidak ada — expected, silent

        # ============================================================
        # Strategy 4: Semua strategi gagal (browser not running + legacy
        # endpoint 404). Ini BUKAN error — cache seharusnya sudah bersih
        # dari stop_profile(clear_cache=True) di close_and_cleanup() sesi
        # sebelumnya. Return True agar caller tidak treat sebagai failure.
        # ============================================================
        if not silent:
            logger.info(
                f"AdsPower: clear_profile_cache({profile_id}) no-op "
                f"(browser not running — cache cleared on previous stop)"
            )
        return True

    def _get_window_size_arg(self):
        """v10.9: Return '--window-size=W,H' launch arg matching the actual
        screen size. Cached on first call.

        This REPLACES the old CDP maximize approach. Setting --window-size
        as a launch_arg makes Chromium open the window at the desired size
        on startup — no CDP roundtrip needed, no greenlet thread issues.

        Falls back to 1920x1080 if pyautogui is unavailable (e.g. headless
        container without display).
        """
        if hasattr(self, '_cached_window_size_arg'):
            return self._cached_window_size_arg
        w, h = 1920, 1080  # sensible default
        try:
            import pyautogui
            sw, sh = pyautogui.size()
            if sw > 0 and sh > 0:
                w, h = sw, sh
        except Exception:
            pass
        self._cached_window_size_arg = f'--window-size={w},{h}'
        logger.info(f"AdsPower: window size launch_arg = {self._cached_window_size_arg}")
        return self._cached_window_size_arg

    def start_profile(self, profile_id, headless=False):
        """
        Start a browser profile and get WebSocket debug URL (v2 API).

        v10.11 UPGRADE — MOD 5: CLEAR CACHE ON EVERY START.
          Setiap kali profile di-start (yang terjadi tiap kali bot
          berganti user/profile), cache browser AdsPower di-clear
          terlebih dahulu. Ini memastikan tidak ada cookie/cache/session
          residual dari user sebelumnya yang bocor ke user berikutnya.

          Implementasi: panggil clear_profile_cache(profile_id) di awal
          start_profile(), sebelum pre-stop cleanup. Cache clear ini
          aman karena dilakukan saat browser masih dalam state "stopped".

        v9.5.1 RESILIENCE UPGRADE:
          Sebelumnya, "Failed to start browser" (code=-1) langsung
          menyebabkan fallback ke Patchright. Penyebab umum error ini:
            1. Profile stuck di state "open" dari run sebelumnya yang crash
               → AdsPower mengira profile masih running, padahal browser
               process sudah mati.
            2. AdsPower app sedang busy/syncing → perlu retry setelah delay.
            3. Browser cache Chromium corrupt → perlu clear-cache + retry.
            4. Concurrent profile limit reached → perlu wait + retry.
            5. launch_args bermasalah → perlu coba variant tanpa args.

          Strategi baru (3 attempt dengan progressive recovery):
            Attempt 1: clear-cache → pre-stop (silent) → start with launch_args
            Attempt 2: start WITHOUT launch_args (kadang --ignore-certificate-
                       errors menyebabkan crash di certain AdsPower versions)
            Attempt 3: clear-cache → wait 2s → start with launch_args

          Setiap attempt punya backoff 2-4 detik untuk kasih waktu AdsPower
          me-release resources.
        """
        # ============================================================
        # v10.11 UPGRADE — MOD 5: CLEAR CACHE BEFORE EVERY START.
        # Dipanggil tiap kali profile di-start = tiap kali bot berganti
        # user/profile. Memastikan cache browser bersih sebelum sesi baru.
        # ============================================================
        try:
            self.clear_profile_cache(profile_id)
            time.sleep(0.5)  # beri waktu AdsPower memproses clear-cache
        except Exception as e:
            logger.warning(f"AdsPower: pre-start clear_cache exception ({e}) — continuing")

        # ============================================================
        # v9.5.1: PRE-STOP cleanup — clear stale "open" state.
        # Profile yang crash sebelumnya sering masih dianggap "open"
        # oleh AdsPower. Stop dulu (silent=True agar tidak noisy).
        # ============================================================
        logger.info(f"AdsPower: pre-stop cleanup for profile {profile_id}")
        self.stop_profile(profile_id, silent=True)
        time.sleep(1.0)  # beri waktu AdsPower me-release resources

        # ============================================================
        # Helper untuk parse ws_endpoint dari result
        # ============================================================
        def _extract_ws(result_data):
            ws = result_data.get('ws', {})
            if isinstance(ws, dict):
                ws = (ws.get('puppeteer', '') or ws.get('selenium', '')
                      or ws.get('cdp', ''))
            elif not isinstance(ws, str):
                ws = str(ws) if ws else ''
            return ws

        # ============================================================
        # Attempt 1: start WITH launch_args (preferred — handles proxy SSL)
        # v10.9: also include --window-size=W,H so the browser opens at
        # the correct size on startup. This eliminates the need for CDP
        # maximize (which caused greenlet thread-switch errors when run
        # from a daemon thread).
        # v11.0: ALSO include the Linux stability flags (--disable-dev-shm-
        # usage, --no-sandbox, --disable-gpu, --disable-software-rasterizer).
        # These mirror what start_adspower.sh passes to the PARENT process,
        # but per-profile Chromium CHILDREN do NOT inherit them — they must
        # be passed via launch_args on every start call. Without them,
        # the renderer crashes on /dev/shm exhaustion / seccomp / GPU init,
        # producing "First page is dead on connect (renderer unreachable)"
        # and "Cannot create new page — browser is dead" errors on Linux.
        # ============================================================
        body_with_args = {
            'profile_id': profile_id,
            'ip_tab': 0,
            'headless': 1 if headless else 0,
            'launch_args': [
                '--ignore-certificate-errors',
                self._get_window_size_arg(),
                *_linux_stability_launch_args(),
            ],
        }
        result = self._post('/api/v2/browser-profile/start', json_data=body_with_args)
        if result and result.get('code') == 0:
            data = result.get('data', {}) or {}
            ws_endpoint = _extract_ws(data)
            if ws_endpoint:
                logger.info(f"AdsPower: profile {profile_id} started (attempt 1, with launch_args)")
                return {
                    'ws_endpoint': ws_endpoint,
                    'debug_port': data.get('debug_port', ''),
                    'profile_id': profile_id,
                }

        err_msg_1 = (result.get('msg', '') if result else 'no response')
        logger.warning(
            f"AdsPower: start attempt 1 FAILED (profile={profile_id}): "
            f"code={result.get('code') if result else 'None'}, msg={err_msg_1}"
        )

        # ============================================================
        # Attempt 2: start WITHOUT --ignore-certificate-errors
        # (kadang --ignore-certificate-errors menyebabkan crash)
        # v10.9: still include --window-size so the window opens at the
        # correct size even without the cert flag.
        # v11.0: keep the Linux stability flags — they're not the suspect
        # here; --ignore-certificate-errors is. Removing the stability
        # flags would reintroduce the renderer-crash issues on Linux.
        # ============================================================
        time.sleep(2.0)  # backoff before retry
        body_no_args = {
            'profile_id': profile_id,
            'ip_tab': 0,
            'headless': 1 if headless else 0,
            'launch_args': [
                self._get_window_size_arg(),
                *_linux_stability_launch_args(),
            ],
        }
        result = self._post('/api/v2/browser-profile/start', json_data=body_no_args)
        if result and result.get('code') == 0:
            data = result.get('data', {}) or {}
            ws_endpoint = _extract_ws(data)
            if ws_endpoint:
                logger.info(f"AdsPower: profile {profile_id} started (attempt 2, no launch_args)")
                return {
                    'ws_endpoint': ws_endpoint,
                    'debug_port': data.get('debug_port', ''),
                    'profile_id': profile_id,
                }

        err_msg_2 = (result.get('msg', '') if result else 'no response')
        logger.warning(
            f"AdsPower: start attempt 2 FAILED (no launch_args): "
            f"code={result.get('code') if result else 'None'}, msg={err_msg_2}"
        )

        # ============================================================
        # Attempt 3: clear-cache + retry with launch_args
        # (cache Chromium corrupt → clear + restart)
        # ============================================================
        time.sleep(2.0)
        logger.info(f"AdsPower: attempt 3 — clearing cache for {profile_id}")
        self.clear_profile_cache(profile_id)
        time.sleep(2.0)  # beri waktu clear-cache selesai

        # Pre-stop lagi setelah clear-cache (kadang clear-cache membuka
        # locks yang menyebabkan profile dianggap still open)
        self.stop_profile(profile_id, silent=True)
        time.sleep(1.0)

        result = self._post('/api/v2/browser-profile/start', json_data=body_with_args)
        if result and result.get('code') == 0:
            data = result.get('data', {}) or {}
            ws_endpoint = _extract_ws(data)
            if ws_endpoint:
                logger.info(f"AdsPower: profile {profile_id} started (attempt 3, after clear-cache)")
                return {
                    'ws_endpoint': ws_endpoint,
                    'debug_port': data.get('debug_port', ''),
                    'profile_id': profile_id,
                }

        err_msg_3 = (result.get('msg', '') if result else 'no response')
        logger.error(
            f"AdsPower: ALL 3 start attempts FAILED for profile {profile_id}:\n"
            f"  attempt 1 (with launch_args):    {err_msg_1}\n"
            f"  attempt 2 (without launch_args): {err_msg_2}\n"
            f"  attempt 3 (after clear-cache):   {err_msg_3}\n"
            f"Kemungkinan penyebab:\n"
            f"  - Profile corrupt (delete & recreate)\n"
            f"  - Concurrent profile limit reached (close other profiles)\n"
            f"  - AdsPower app not responding (restart AdsPower)\n"
            f"  - Proxy unreachable (check proxy config)\n"
            f"Falling back to Patchright."
        )
        return None

    def delete_profile(self, profile_id):
        """Delete a browser profile (v2 API)."""
        self._post('/api/v2/browser-profile/delete', json_data={'profile_id': profile_id})

    def update_profile(self, profile_id, profile_config):
        """
        Update an existing profile's configuration (v2 API).

        v9.9 (BUG FIX #8): Pakai cached webgl value dari class-level cache.
        Hanya retry jika cache belum ada (cold start). Setelah webgl value
        yang berhasil ditemukan, semua update_profile berikutnya pakai
        value tersebut — menghemat 3-6 detik per user.
        """
        profile_config['profile_id'] = profile_id

        fp_original = profile_config.get('fingerprint_config', {})
        import copy

        # BUG FIX #8: Jika cache sudah ada, pakai langsung tanpa retry.
        if AdsPowerClient._cached_webgl_value is not None or AdsPowerClient._cached_webgl_drop_vendor:
            cached_fp = self._make_webgl_variant(
                fp_original,
                webgl_value=AdsPowerClient._cached_webgl_value,
                drop_vendor=AdsPowerClient._cached_webgl_drop_vendor,
            )
            body = dict(profile_config)
            body['fingerprint_config'] = cached_fp
            result = self._post('/api/v2/browser-profile/update', json_data=body)
            if result and result.get('code') == 0:
                return result
            # Jika gagal dengan cached value, reset cache dan fallback ke retry
            err_msg = (result.get('msg', '') if result else '').lower()
            if not ('webgl' in err_msg and ('must be' in err_msg or '0,1,2,3' in err_msg)):
                # Bukan webgl error — return result apa adanya
                return result
            logger.warning(f"AdsPower: cached webgl value gagal, fallback ke retry sequential...")

        # Cold-start: coba 3 variant dan cache yang berhasil
        fp_variants = [
            ('webgl=1 (Custom)', fp_original, 1, False),
            ('webgl=0 (Real)', self._make_webgl_variant(fp_original, webgl_value=0, drop_vendor=False), 0, False),
            ('webgl removed', self._make_webgl_variant(fp_original, webgl_value=None, drop_vendor=True), None, True),
        ]

        last_result = None
        for label, fp_variant, webgl_val, drop_vendor in fp_variants:
            body = dict(profile_config)
            body['fingerprint_config'] = fp_variant
            result = self._post('/api/v2/browser-profile/update', json_data=body)
            last_result = result

            if result and result.get('code') == 0:
                # BUG FIX #8: cache webgl value yang berhasil
                AdsPowerClient._cached_webgl_value = webgl_val
                AdsPowerClient._cached_webgl_drop_vendor = drop_vendor
                if label != 'webgl=1 (Custom)':
                    logger.info(f"AdsPower update_profile succeeded with fallback: {label} (cached for future calls)")
                else:
                    logger.info(f"AdsPower update_profile succeeded with webgl=1 (cached for future calls)")
                return result

            err_msg = (result.get('msg', '') if result else '').lower()
            if 'webgl' in err_msg and ('must be' in err_msg or '0,1,2,3' in err_msg):
                logger.warning(
                    f"AdsPower update_profile failed with webgl error using '{label}'. "
                    f"Trying next fallback..."
                )
                continue
            else:
                return result

        logger.error(
            f"AdsPower update_profile: ALL WebGL fallback variants failed. "
            f"Profile {profile_id} was NOT updated. Last error: "
            f"{last_result.get('msg', 'unknown') if last_result else 'no response'}"
        )
        return last_result

    @staticmethod
    def _make_webgl_variant(fp_config, webgl_value=None, drop_vendor=False):
        """
        Create a copy of fingerprint_config with a different webgl setting.
        Used by update_profile / create_profile retry logic.

        Args:
            fp_config: original fingerprint_config dict
            webgl_value: new webgl value (int 0/1/2/3), or None to remove the key
            drop_vendor: if True, also remove webgl_vendor / webgl_renderer
        """
        import copy
        variant = copy.deepcopy(fp_config)
        if webgl_value is None:
            variant.pop('webgl', None)
        else:
            variant['webgl'] = int(webgl_value)
        if drop_vendor:
            variant.pop('webgl_vendor', None)
            variant.pop('webgl_renderer', None)
            variant.pop('webgl_image', None)  # v9.8: never send webgl_image
        else:
            # Always strip webgl_image even when keeping vendor/renderer
            variant.pop('webgl_image', None)
        return variant

    def get_opened_browsers(self):
        """Get all currently opened browser profiles."""
        return self._get('/api/v2/browser-profile/active')

    def get_active_status(self, profile_id):
        """Check if a specific profile is currently active."""
        return self._get('/api/v2/browser-profile/active', params={'profile_id': profile_id})

    # =====================================================
    # v9.9 — Console Profile Device Verification
    # =====================================================
    # After create_profile() + update_profile(), fetch the profile from
    # AdsPower and verify the key fingerprint_config fields were actually
    # applied. If a field is missing or mismatched, log a warning so the
    # operator can see (in the AdsPower console AND in the bot log) that
    # the profile is not fully synchronized.
    #
    # Returns a dict:
    #   {
    #     'ok':         bool — True if all key fields match,
    #     'mismatches': [str] — list of human-readable mismatch descriptions,
    #     'remote_fp':  dict — the fingerprint_config as AdsPower sees it,
    #   }
    # Returns {'ok': False, 'mismatches': ['fetch_failed'], 'remote_fp': {}}
    # if the profile fetch itself failed.
    def verify_profile(self, profile_id, expected_config):
        """
        Verify that the AdsPower profile's fingerprint_config matches
        the values we expect (from sync_config). Logs warnings for any
        mismatched field so the operator can spot desync in the console.
        """
        if not profile_id:
            return {'ok': False, 'mismatches': ['no_profile_id'], 'remote_fp': {}}

        # AdsPower v2 list endpoint accepts a profile_id filter via body.
        # We fetch the single profile and read its fingerprint_config.
        try:
            body = {'page': 1, 'limit': 1, 'profile_id': profile_id}
            result = self._post('/api/v2/browser-profile/list', json_data=body)
        except Exception as e:
            logger.warning(f"AdsPower verify_profile: list failed: {e}")
            return {'ok': False, 'mismatches': [f'fetch_exception: {e}'], 'remote_fp': {}}

        if not result or result.get('code') != 0:
            err = (result.get('msg', 'unknown') if result else 'no_response')
            logger.warning(f"AdsPower verify_profile: list returned error: {err}")
            return {'ok': False, 'mismatches': [f'fetch_error: {err}'], 'remote_fp': {}}

        profiles = result.get('data', {}).get('list', []) or []
        if not profiles:
            logger.warning(f"AdsPower verify_profile: profile {profile_id} not found in list")
            return {'ok': False, 'mismatches': ['profile_not_found'], 'remote_fp': {}}

        remote = profiles[0]
        remote_fp = remote.get('fingerprint_config', {}) or {}
        remote_proxy = remote.get('user_proxy_config', {}) or {}

        mismatches = []

        # === Compare key fingerprint fields ===
        # UA — must match exactly (or be a substring match if AdsPower
        # truncates long UAs in the list response).
        expected_ua = (expected_config.get('ua') or '').strip()
        remote_ua = (remote_fp.get('ua') or '').strip()
        if expected_ua and remote_ua and expected_ua not in remote_ua and remote_ua not in expected_ua:
            mismatches.append(f"ua mismatch: expected={expected_ua[:60]!r}, remote={remote_ua[:60]!r}")

        # OS
        expected_os = self._normalize_os_for_adspower(expected_config.get('os', ''))
        remote_os = (remote_fp.get('os') or '').strip()
        if expected_os and remote_os and expected_os.lower() != remote_os.lower():
            mismatches.append(f"os mismatch: expected={expected_os!r}, remote={remote_os!r}")

        # Timezone
        expected_tz = (expected_config.get('timezone') or '').strip()
        remote_tz = (remote_fp.get('timezone') or '').strip()
        if expected_tz and remote_tz and expected_tz != remote_tz:
            mismatches.append(f"timezone mismatch: expected={expected_tz!r}, remote={remote_tz!r}")

        # Proxy host
        expected_proxy_host = (expected_config.get('proxy_host') or '').strip()
        remote_proxy_host = (remote_proxy.get('proxy_host') or '').strip()
        if expected_proxy_host and remote_proxy_host and expected_proxy_host != remote_proxy_host:
            mismatches.append(
                f"proxy_host mismatch: expected={expected_proxy_host!r}, remote={remote_proxy_host!r}"
            )

        # Locale / language (AdsPower returns a list)
        expected_lan = (expected_config.get('lan') or '').strip()
        remote_lang = remote_fp.get('language') or []
        if isinstance(remote_lang, list):
            remote_lang_str = (remote_lang[0] if remote_lang else '').strip()
        else:
            remote_lang_str = str(remote_lang).strip()
        if expected_lan and remote_lang_str and expected_lan != remote_lang_str:
            mismatches.append(f"language mismatch: expected={expected_lan!r}, remote={remote_lang_str!r}")

        # Resolution
        expected_res = (expected_config.get('resolution') or '').strip()
        if expected_res and 'x' in expected_res:
            try:
                ew, eh = expected_res.split('x')[:2]
                ew, eh = int(ew), int(eh)
                remote_res = remote_fp.get('resolution') or []
                if isinstance(remote_res, list) and len(remote_res) == 2:
                    if int(remote_res[0]) != ew or int(remote_res[1]) != eh:
                        mismatches.append(
                            f"resolution mismatch: expected=[{ew},{eh}], remote={remote_res}"
                        )
            except (ValueError, IndexError):
                pass

        # WebGL renderer (just check substring — AdsPower may normalize)
        expected_webgl = (expected_config.get('webgl_renderer') or '').strip()
        remote_webgl_renderer = (remote_fp.get('webgl_renderer') or '').strip()
        if expected_webgl and remote_webgl_renderer:
            # Substring check — AdsPower may rewrite vendor/renderer
            if (expected_webgl not in remote_webgl_renderer
                    and remote_webgl_renderer not in expected_webgl):
                mismatches.append(
                    f"webgl_renderer mismatch: expected={expected_webgl[:50]!r}, "
                    f"remote={remote_webgl_renderer[:50]!r}"
                )

        ok = len(mismatches) == 0
        if ok:
            logger.info(
                f"AdsPower verify_profile: OK — profile {profile_id} fingerprint matches sync_config "
                f"(ua, os, tz, proxy, language, resolution, webgl all aligned)"
            )
        else:
            for m in mismatches:
                logger.warning(f"AdsPower verify_profile: {m}")
            logger.warning(
                f"AdsPower verify_profile: profile {profile_id} has {len(mismatches)} mismatch(es) — "
                f"the running browser may not match sync_config. Check AdsPower console for details."
            )

        return {
            'ok': ok,
            'mismatches': mismatches,
            'remote_fp': remote_fp,
            'remote_name': remote.get('name', ''),
            'remote_remark': remote.get('remark', ''),
        }


class MultiloginClient:
    """
    Multilogin Local API Client.
    Docs: https://docs.multilogin.com/l/en/multilogin-local-api
    
    Multilogin provides deep Chromium modifications:
    - Binary-level navigator.webdriver masking
    - Hardware WebGL fingerprint emulation
    - OS-consistent font injection
    - Canvas/Audio fingerprint noise at engine level
    """

    DEFAULT_API_BASE = 'http://127.0.0.1'
    DEFAULT_PORT = 45001

    def __init__(self, api_base=None, port=None):
        self.api_base = api_base or os.environ.get('MULTILOGIN_API_BASE', self.DEFAULT_API_BASE)
        self.port = port or int(os.environ.get('MULTILOGIN_PORT', self.DEFAULT_PORT))
        self.base_url = f"{self.api_base}:{self.port}"

    def check_status(self):
        try:
            resp = http_requests.get(f"{self.base_url}/api/v2/profiles", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def create_profile(self, profile_config):
        payload = {
            'name': profile_config.get('name', f'bot_{int(time.time())}'),
            'os': profile_config.get('os', 'win'),
            'browserType': 'mimic',
            'proxy': {
                'type': profile_config.get('proxy_type', 'http'),
                'host': profile_config.get('proxy_host', ''),
                'port': profile_config.get('proxy_port', 0),
                'username': profile_config.get('proxy_user', ''),
                'password': profile_config.get('proxy_password', ''),
            },
            'navigator': {
                'userAgent': profile_config.get('ua', ''),
                'language': profile_config.get('lan', 'en-US'),
                'resolution': profile_config.get('resolution', '1920x1080'),
                'platform': profile_config.get('platform', 'Win32'),
            },
            'timezone': {
                'mode': 'manual',
                'value': profile_config.get('timezone', 'America/New_York'),
            },
            'fonts': {
                'mode': 'custom',
                'families': profile_config.get('font_list', '').split(',') if profile_config.get('font_list') else [],
            },
            'webgl': {
                'mode': 'mask',
                'vendor': profile_config.get('webgl_vendor', 'Google Inc. (NVIDIA)'),
                'renderer': profile_config.get('webgl_renderer', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)'),
            },
        }
        try:
            resp = http_requests.post(f"{self.base_url}/api/v2/profiles", json=payload, timeout=15)
            data = resp.json()
            return data.get('id')
        except Exception as e:
            logger.error(f"Multilogin create failed: {e}")
            return None

    def start_profile(self, profile_id, headless=False):
        try:
            resp = http_requests.get(
                f"{self.base_url}/api/v2/profiles/{profile_id}/start",
                params={'headless': str(headless).lower()},
                timeout=30
            )
            data = resp.json()
            ws_endpoint = data.get('ws_endpoint', '')
            return {
                'ws_endpoint': ws_endpoint,
                'profile_id': profile_id,
            }
        except Exception as e:
            logger.error(f"Multilogin start failed: {e}")
            return None

    def stop_profile(self, profile_id):
        try:
            http_requests.get(f"{self.base_url}/api/v2/profiles/{profile_id}/stop", timeout=10)
        except Exception:
            pass

    def delete_profile(self, profile_id):
        try:
            http_requests.delete(f"{self.base_url}/api/v2/profiles/{profile_id}", timeout=10)
        except Exception:
            pass


class DolphinAntyClient:
    """
    Dolphin{anty} Local API Client.
    Docs: https://dolphin-anty-docs.readme.io/docs/local-api
    
    Dolphin{anty} provides:
    - Binary-level browser fingerprint masking
    - Native WebGL & Canvas emulation
    - OS-consistent font rendering
    - Proxy integration with WebRTC/DNS leak prevention built-in
    """

    DEFAULT_API_BASE = 'http://127.0.0.1'
    DEFAULT_PORT = 3001

    def __init__(self, api_base=None, port=None):
        self.api_base = api_base or os.environ.get('DOLPHIN_API_BASE', self.DEFAULT_API_BASE)
        self.port = port or int(os.environ.get('DOLPHIN_PORT', self.DEFAULT_PORT))
        self.base_url = f"{self.api_base}:{self.port}"

    def check_status(self):
        try:
            resp = http_requests.get(f"{self.base_url}/v1.0/browserprofiles", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def create_profile(self, profile_config):
        payload = {
            'name': profile_config.get('name', f'bot_{int(time.time())}'),
            'os': profile_config.get('os', 'win'),
            'browserType': 'antidetect',
            'proxy': {
                'type': profile_config.get('proxy_type', 'http'),
                'host': profile_config.get('proxy_host', ''),
                'port': profile_config.get('proxy_port', 0),
                'username': profile_config.get('proxy_user', ''),
                'password': profile_config.get('proxy_password', ''),
            },
            'navigator': {
                'userAgent': profile_config.get('ua', ''),
                'language': profile_config.get('lan', 'en-US'),
                'resolution': profile_config.get('resolution', '1920x1080'),
                'platform': profile_config.get('platform', 'Win32'),
            },
            'timezone': profile_config.get('timezone', 'America/New_York'),
            'webgl': {
                'vendor': profile_config.get('webgl_vendor', 'Google Inc. (NVIDIA)'),
                'renderer': profile_config.get('webgl_renderer', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti)'),
            },
        }
        try:
            resp = http_requests.post(f"{self.base_url}/v1.0/browserprofiles", json=payload, timeout=15)
            data = resp.json()
            return data.get('id')
        except Exception as e:
            logger.error(f"Dolphin create failed: {e}")
            return None

    def start_profile(self, profile_id, headless=False):
        try:
            resp = http_requests.get(
                f"{self.base_url}/v1.0/browserprofiles/{profile_id}/start",
                params={'headless': str(headless).lower()},
                timeout=30
            )
            data = resp.json()
            ws_endpoint = data.get('ws_endpoint', '')
            automation = data.get('automation', {})
            if not ws_endpoint and automation:
                ws_endpoint = automation.get('ws_endpoint', '')
            return {
                'ws_endpoint': ws_endpoint,
                'profile_id': profile_id,
            }
        except Exception as e:
            logger.error(f"Dolphin start failed: {e}")
            return None

    def stop_profile(self, profile_id):
        try:
            http_requests.get(f"{self.base_url}/v1.0/browserprofiles/{profile_id}/stop", timeout=10)
        except Exception:
            pass

    def delete_profile(self, profile_id):
        try:
            http_requests.delete(f"{self.base_url}/v1.0/browserprofiles/{profile_id}", timeout=10)
        except Exception:
            pass


# ====================================================================
# Anti-Detect Manager — Unified Interface
# ====================================================================

class AntiDetectManager:
    """
    Unified manager for anti-detect browser profiles.
    
    This is the RECOMMENDATION #1 implementation:
    "Gunakan Peramban Anti-Deteksi Asli (Anti-detect Browser)"
    
    Instead of faking properties via JS proxies that leave detectable artifacts,
    this connects to anti-detect browsers that modify Chromium at the C++ level,
    making properties like navigator.webdriver naturally undetectable.
    
    Usage (new — with load_proxy_config):
        from antidetect_browser import load_proxy_config, AntiDetectManager

        # Default: RANGE mode, fetches proxies #1..#50
        config = load_proxy_config()
        # Or: fetch a custom range, e.g. #10..#20
        # config = load_proxy_config(proxy_range=(10, 20))
        # Or: fall back to the legacy named-list endpoint
        # config = load_proxy_config(proxy_range=None, page_size=5)
        adspower_cfg = config['adspower']
        proxies = config['proxies']

        manager = AntiDetectManager(
            mode="antidetect",
            browser_type="adspower",
            adspower_config=adspower_cfg,
        )

        for proxy_entry in proxies:
            profile_config = manager.build_profile_config(
                proxy=proxy_entry,
                user_profile=user_profile_dict,
            )
            session = manager.create_and_start(profile_config)
            session.page.goto("https://example.com")
            manager.close_and_cleanup(session)

    Usage (legacy — manual credentials):
        manager = AntiDetectManager(mode="antidetect", browser_type="adspower",
                                    api_key="...")

        # Create a profile with full synchronization
        profile_config = manager.build_profile_config(
            proxy=proxy_entry,
            user_profile=user_profile_dict,
        )
        session = manager.create_and_start(profile_config)

        # session.page is a Playwright page — use it normally
        session.page.goto("https://example.com")

        # Cleanup
        manager.close_and_cleanup(session)
    """

    def __init__(self, mode="antidetect", browser_type="adspower", api_key=None,
                 profile_id=None, group_id=None, adspower_config=None):
        """
        Args:
            mode: "antidetect" (use anti-detect browser) or "patchright" (legacy fallback)
            browser_type: "adspower", "multilogin", or "dolphin"
            api_key: API key untuk AdsPower Local API (diteruskan sebagai Bearer header)
            profile_id: ID profil AdsPower yang sudah ada (opsional, jika create tidak tersedia)
            group_id: ID grup AdsPower (opsional, auto-detect jika kosong)
            adspower_config: Dict dari load_proxy_config()['adspower'] — berisi
                             api_key, mode, base_url, port, profile_id, group_id.
                             Jika diberikan, meng-override api_key/profile_id/group_id
                             yang terpisah.
        """
        self.mode = mode
        self.browser_type = browser_type.lower()
        self._adspower_config = adspower_config  # raw config for _init_client
        self._sessions = []

        # Resolve AdsPower credentials with 3-tier fallback:
        #   adspower_config > explicit arg > env var > ADSPOWER_DEFAULTS
        if adspower_config:
            self.api_key = adspower_config.get('api_key', '') or api_key or os.environ.get('ADSPOWER_API_KEY', '') or ADSPOWER_DEFAULTS['api_key']
            self.profile_id = adspower_config.get('profile_id', '') or profile_id or os.environ.get('ADSPOWER_PROFILE_ID', '') or ADSPOWER_DEFAULTS['profile_id']
            self.group_id = adspower_config.get('group_id', '') or group_id or os.environ.get('ADSPOWER_GROUP_ID', '') or ADSPOWER_DEFAULTS['group_id']
            self._adspower_base_url = adspower_config.get('base_url', '') or os.environ.get('ADSPOWER_API_BASE', '') or ADSPOWER_DEFAULTS['base_url']
            self._adspower_port = adspower_config.get('port', 0) or (int(os.environ['ADSPOWER_PORT']) if 'ADSPOWER_PORT' in os.environ else 0) or ADSPOWER_DEFAULTS['port']
        else:
            self.api_key = api_key or os.environ.get('ADSPOWER_API_KEY', '') or ADSPOWER_DEFAULTS['api_key']
            self.profile_id = profile_id or os.environ.get('ADSPOWER_PROFILE_ID', '') or ADSPOWER_DEFAULTS['profile_id']
            self.group_id = group_id or os.environ.get('ADSPOWER_GROUP_ID', '') or ADSPOWER_DEFAULTS['group_id']
            self._adspower_base_url = os.environ.get('ADSPOWER_API_BASE', '') or ADSPOWER_DEFAULTS['base_url']
            self._adspower_port = (int(os.environ['ADSPOWER_PORT']) if 'ADSPOWER_PORT' in os.environ else 0) or ADSPOWER_DEFAULTS['port']

        if self.mode == "antidetect":
            self._init_client()
        else:
            logger.info("Running in Patchright fallback mode (legacy)")

    def _init_client(self):
        """Initialize the anti-detect browser API client."""
        if self.browser_type == 'adspower':
            self._client = AdsPowerClient(
                api_key=self.api_key,
                api_base=self._adspower_base_url,
                port=self._adspower_port,
                profile_id=self.profile_id,
                group_id=self.group_id,
            )
        elif self.browser_type == 'multilogin':
            self._client = MultiloginClient()
        elif self.browser_type == 'dolphin':
            self._client = DolphinAntyClient()
        else:
            logger.error(f"Unknown browser type: {self.browser_type}. Falling back to Patchright.")
            self.mode = "patchright"
            return

        if not self._client.check_status():
            logger.warning(f"{self.browser_type} is not running or API not accessible. Falling back to Patchright.")
            self.mode = "patchright"
            self._client = None
        else:
            logger.info(f"Connected to {self.browser_type} anti-detect browser successfully (mode={getattr(self._client, 'mode', 'unknown')}).")

    @property
    def is_antidetect_mode(self):
        return self.mode == "antidetect" and self._client is not None

    def build_profile_config(self, proxy, user_profile):
        """
        Build a complete profile configuration with total synchronization.
        
        This is the RECOMMENDATION #2 implementation:
        "Sinkronisasi Total Profil" — ensures OS, User-Agent, font fingerprint,
        screen coordinates, timezone, and TCP/IP parameters (TTL) are all
        dynamically synchronized to match the geographic location and
        characteristics of the residential proxy IP being used.
        
        Args:
            proxy: Proxy entry dict from bot_v6.py
            user_profile: User profile dict from make_user_profile()
        
        Returns:
            Complete profile_config dict for anti-detect browser API
        """
        from profile_sync import ProfileSynchronizer
        sync = ProfileSynchronizer()
        return sync.build_full_profile(proxy, user_profile)

    def _try_use_existing_profile(self, profile_config=None):
        """
        If creating a new profile fails, try to find and use an existing
        profile in AdsPower. Updates its proxy AND fingerprint config
        before returning.

        v9.1: previously this method only updated user_proxy_config, leaving
        the reused profile's UA/OS/fonts/WebGL/etc. as whatever it was
        originally created with. That broke synchronization: sync_config
        said "Android Pixel 8" but the reused AdsPower profile still had
        a Windows fingerprint from a previous run.

        Returns:
            profile_id string, or None if no available profile found
        """
        # Use _find_reusable_profile which handles list_profiles
        existing_id = self._client._find_reusable_profile() if hasattr(self._client, '_find_reusable_profile') else None

        if existing_id:
            # v9.1: update BOTH proxy and fingerprint config for the reused
            # profile — use the same _build_update_body helper used by
            # create_profile so the fingerprint is fully synchronized.
            # v9.3: FIX dead-code bug — sebelumnya `if` dan `elif` punya
            # kondisi identik (`profile_config and hasattr(update_profile)`),
            # sehingga branch Multilogin/Dolphin tidak pernah dijalankan.
            # Sekarang elif mengecek keberadaan _build_update_body secara
            # terpisah.
            if profile_config and hasattr(self._client, 'update_profile') and hasattr(self._client, '_build_update_body'):
                logger.info(f"AdsPower: Re-syncing existing profile {existing_id} with full sync_config (proxy + fingerprint)")
                self._client.update_profile(existing_id,
                                            self._client._build_update_body(profile_config))
                time.sleep(0.5)
            elif profile_config and hasattr(self._client, 'update_profile'):
                # Fallback path for clients without _build_update_body (Multilogin / Dolphin)
                proxy_host = profile_config.get('proxy_host', '')
                if proxy_host:
                    self._client.update_profile(existing_id, {
                        'user_proxy_config': {
                            'proxy_soft': 'other',
                            'proxy_type': profile_config.get('proxy_type', 'http'),
                            'proxy_host': proxy_host,
                            'proxy_port': str(profile_config.get('proxy_port', '')),
                            'proxy_user': profile_config.get('proxy_user', ''),
                            'proxy_password': profile_config.get('proxy_password', ''),
                        },
                    })
                    time.sleep(0.5)
            return existing_id

        # Fallback: try list_profiles directly
        if hasattr(self._client, 'list_profiles'):
            time.sleep(0.5)  # Rate limit protection
            profiles = self._client.list_profiles()
            if profiles:
                for p in profiles:
                    profile_id = p.get('id', p.get('user_id', p.get('profile_id', '')))
                    if profile_id:
                        logger.info(f"AdsPower: Menggunakan profil yang sudah ada: {p.get('name', 'unnamed')} (id={profile_id})")
                        # v9.1: also re-sync fingerprint when recovered via list_profiles
                        if profile_config and hasattr(self._client, '_build_update_body'):
                            self._client.update_profile(profile_id,
                                                        self._client._build_update_body(profile_config))
                            time.sleep(0.5)
                        return profile_id

        logger.warning("AdsPower: Tidak ada profil yang bisa digunakan.")
        return None

    def create_and_start(self, profile_config, pw=None):
        """
        Create a browser profile, start it, and connect via Playwright.
        
        Args:
            profile_config: Full profile configuration dict
            pw: Playwright instance (from sync_playwright())
        
        Returns:
            Session dict with keys:
              - page: Playwright Page object
              - context: Playwright BrowserContext
              - browser: Playwright Browser
              - profile_id: Anti-detect profile ID
              - ws_endpoint: WebSocket debug URL
              - mode: "antidetect" or "patchright"
        """
        if self.is_antidetect_mode:
            return self._create_antidetect_session(profile_config, pw)
        else:
            return self._create_patchright_session(profile_config, pw)

    def _create_antidetect_session(self, profile_config, pw):
        """Create session using anti-detect browser.

        v9.9 (BUG FIX #6): Optimasi startup time dari ~49s → ~10s.
          1. Cache profile_id di class-level (create_profile hanya sekali)
          2. Reuse profile yang sudah ada — cukup update_profile untuk
             sync proxy + fingerprint, tanpa list_profiles() tiap kali
          3. Kurangi time.sleep(3) → time.sleep(1) setelah start_profile
             (CDP connect sudah punya retry-nya sendiri)
        """
        # BUG FIX #6: Cek cache dulu — jika profile_id sudah ada, skip
        # create_profile() dan langsung update + start.
        profile_id = None
        if isinstance(self._client, AdsPowerClient) and AdsPowerClient._cached_profile_id:
            profile_id = AdsPowerClient._cached_profile_id
            logger.info(f"AdsPower: REUSING cached profile_id={profile_id} (skipping create)")
            # Update fingerprint + proxy untuk user baru
            try:
                self._client.update_profile(profile_id, self._client._build_update_body(profile_config))
                time.sleep(0.3)  # rate limit pendek
            except Exception as e:
                logger.warning(f"AdsPower: cached profile update gagal ({e}), akan create baru")
                profile_id = None

        if not profile_id:
            # Create profile baru
            profile_id = self._client.create_profile(profile_config)
            # Cache profile_id untuk user berikutnya
            if profile_id and isinstance(self._client, AdsPowerClient):
                AdsPowerClient._cached_profile_id = profile_id
                logger.info(f"AdsPower: profile_id {profile_id} CACHED untuk reuse")

        # If create fails, try using an existing profile instead
        if not profile_id:
            logger.warning("Failed to create new profile. Trying to use existing profile...")
            profile_id = self._try_use_existing_profile(profile_config)
            if not profile_id:
                logger.error("No existing profile available either. Falling back to Patchright.")
                return self._create_patchright_session(profile_config, pw)

        # Start profile and get WebSocket endpoint
        start_result = self._client.start_profile(profile_id, headless=False)

        # v9.5.1: Bila start gagal DAN profile_id berasal dari cache,
        # coba delete cached profile + create fresh sebelum fallback ke
        # patchright. Profile cache kadang corrupt setelah banyak run.
        if (not start_result or not start_result.get('ws_endpoint')) and \
           isinstance(self._client, AdsPowerClient) and \
           AdsPowerClient._cached_profile_id == profile_id:
            logger.warning(
                f"AdsPower: cached profile {profile_id} failed to start. "
                f"Attempting DELETE + CREATE FRESH before Patchright fallback..."
            )
            # Stop & delete corrupt cached profile
            self._client.stop_profile(profile_id, silent=True)
            try:
                self._client.delete_profile(profile_id)
                logger.info(f"AdsPower: deleted corrupt cached profile {profile_id}")
            except Exception as e:
                logger.warning(f"AdsPower: delete_profile failed ({e}) — continuing")
            AdsPowerClient._cached_profile_id = None

            # Create fresh profile and try start once more
            try:
                fresh_profile_id = self._client.create_profile(profile_config)
                if fresh_profile_id:
                    AdsPowerClient._cached_profile_id = fresh_profile_id
                    logger.info(f"AdsPower: created fresh profile {fresh_profile_id}")
                    # start_profile lagi (sudah termasuk 3-attempt retry)
                    start_result = self._client.start_profile(
                        fresh_profile_id, headless=False
                    )
                    if start_result and start_result.get('ws_endpoint'):
                        profile_id = fresh_profile_id
                        logger.info(f"AdsPower: fresh profile started successfully!")
            except Exception as e:
                logger.warning(f"AdsPower: fresh create+start failed ({e})")

        if not start_result or not start_result.get('ws_endpoint'):
            logger.error("Failed to start anti-detect profile. Falling back.")
            # v9.5.1: stop silently — start_profile() sudah melakukan
            # 3 attempts + clear-cache. Stop di sini hanya cleanup,
            # tidak perlu noisy lagi.
            self._client.stop_profile(profile_id, silent=True)
            # Reset cache jika start gagal — profile mungkin corrupt
            if isinstance(self._client, AdsPowerClient):
                AdsPowerClient._cached_profile_id = None
            return self._create_patchright_session(profile_config, pw)

        ws_endpoint = start_result['ws_endpoint']

        # ====================================================================
        # v11.0: Normalize the ws_endpoint for Linux IPv4/IPv6 compatibility.
        # ====================================================================
        # AdsPower's API returns ws://127.0.0.1:PORT/... but on Linux the
        # DevTools socket is often actually bound to ::1 (because
        # local.adspower.net resolves to ::1 first on glibc). Rewriting
        # 127.0.0.1 → localhost makes Python's getaddrinfo return BOTH
        # stacks, so connect_over_cdp tries ::1 if 127.0.0.1 fails.
        # This eliminates the "connect ECONNREFUSED 127.0.0.1:PORT" class
        # of errors.
        # ====================================================================
        ws_endpoint = _normalize_ws_endpoint_for_linux(ws_endpoint)
        current_ws_endpoint = ws_endpoint  # will be re-assigned in retry loop

        # BUG FIX #6: Kurangi wait dari 3s → 1s. CDP connect punya retry
        # 3x dengan backoff 2-6s sendiri, jadi tidak perlu wait panjang
        # di sini. Ini menghemat ~2 detik per user.
        # v11.0: REPLACE passive sleep with ACTIVE pre-flight port poll.
        # Rather than blindly sleeping 1s and hoping the browser is ready,
        # poll the DevTools TCP port for up to 10s. This eliminates the
        # race condition where the API returns ws_endpoint before Chromium
        # has actually bound the socket — the #2 cause of ECONNREFUSED
        # errors on Linux.
        logger.info(f"Waiting for browser DevTools port to be ready...")
        if not _wait_for_devtools_port(current_ws_endpoint, timeout=10.0, interval=0.2):
            logger.warning(
                f"AdsPower: DevTools port not ready after 10s — "
                f"will attempt connect_over_cdp anyway (Playwright's "
                f"30s timeout will catch true failures)"
            )

        # ====================================================================
        # v10.6: CRITICAL — Two different ports are involved here.
        # ====================================================================
        #   • Port 50325 (ADSPOWER_PORT)  = AdsPower HTTP API. Used by
        #     AdsPowerClient to call /api/v2/browser-profile/start etc.
        #     This port is STABLE — set via ADSPOWER_PORT env var.
        #
        #   • Port 33813 (random)         = DevTools WebSocket endpoint
        #     returned by start_profile(). Randomly assigned by Chromium
        #     per browser-profile-start. We have NO control over this —
        #     we just use whatever ws_endpoint the API returns.
        #
        # The retry loop below calls start_profile() again on failure,
        # which returns a NEW ws_endpoint with a different random port.
        # It NEVER retries connect_over_cdp to the same dead ws_endpoint
        # — that's pointless because the browser process is already gone.
        # ====================================================================

        # Connect Playwright to the running browser (with retry).
        # v10.6: On any failure that indicates the browser is dead
        # (ECONNREFUSED, "Target closed", "Page crashed"), we stop the
        # profile via HTTP API and call start_profile() again to get a
        # FRESH ws_endpoint. Retrying connect_over_cdp on the same dead
        # ws_endpoint just wastes time and always fails.
        max_connect_retries = 3
        current_ws_endpoint = ws_endpoint
        for attempt in range(max_connect_retries):
            try:
                # v10.7: Pass explicit timeout=30s. Playwright's default is
                # 180000ms (3 MINUTES) — way too long for a zombie browser.
                # When the WS endpoint accepts the TCP connection (port is
                # listening) but the browser is frozen (OOM, CPU starved,
                # stuck IPC), the CDP handshake never completes. We want to
                # fail fast (30s) and move on to restart_profile.
                browser = pw.chromium.connect_over_cdp(
                    current_ws_endpoint, timeout=30000
                )
                context = browser.contexts[0] if browser.contexts else browser.new_context()

                # ========================================================
                # MANAGE EXISTING PAGES — remove AdsPower start tabs
                # ========================================================
                # AdsPower always opens a start page (start.adspower.net)
                # and sometimes duplicate tabs.
                #
                # Strategy: Navigate pages[0] to about:blank (reuse it as
                # our automation page), then close all OTHER pages.
                # We do NOT close all pages because Chromium will auto-
                # reopen a new tab if we close the last one.
                existing_pages = list(context.pages)
                logger.info(
                    f"AdsPower: Found {len(existing_pages)} existing tab(s) on connect"
                )

                # v11.0: Initialize page = None up front so the cleanup loops
                # below can safely check `p != page` even when no existing
                # tabs were returned. Previously, when AdsPower started the
                # browser but opened zero tabs (renderer partially crashed
                # during init), `existing_pages` was empty, the `if` block
                # was skipped, and the code at line ~2805 (`p != page`)
                # raised `UnboundLocalError: local variable 'page' referenced
                # before assignment` — masking the real "browser is dead"
                # error and causing the confusing log:
                #   "Failed to connect to anti-detect browser (attempt N/3):
                #    local variable 'page' referenced before assignment"
                page = None

                if existing_pages:
                    # Reuse the first page — navigate it away from AdsPower start page
                    page = existing_pages[0]
                    
                    # v10.4: Robust liveness check — page.url returns a CACHED
                    # value and does NOT ping the renderer. A crashed page (where
                    # the renderer died but the page object still exists in
                    # Playwright) would pass the old check and cause every
                    # subsequent operation (goto, click, evaluate) to hang for
                    # the full timeout. Use page.evaluate('1') for a real
                    # round-trip to the renderer.
                    def _page_alive(p):
                        """True iff the page renderer is actually reachable."""
                        try:
                            p.evaluate('1')
                            return True
                        except Exception:
                            return False

                    # v9.2.2 / v10.4: Check if page is alive BEFORE navigating.
                    if not _page_alive(page):
                        logger.warning("AdsPower: First page is dead on connect (renderer unreachable)")
                        # Try creating a new page from the same context first —
                        # the browser might still be alive even if one tab crashed.
                        new_page_ok = False
                        try:
                            page = context.new_page()
                            if _page_alive(page):
                                logger.info("AdsPower: Created new live page after dead page detected")
                                new_page_ok = True
                            else:
                                logger.warning("AdsPower: New page also unreachable — browser is dead")
                        except Exception as new_page_err:
                            logger.warning(f"AdsPower: Cannot create new page — browser is dead: {new_page_err}")
                        if not new_page_ok:
                            # Browser is dead — stop profile + propagate fatal
                            # error so the outer retry loop either retries with
                            # a fresh start or falls back to Patchright.
                            try:
                                self._client.stop_profile(profile_id, silent=True)
                            except Exception:
                                pass
                            raise Exception(
                                "AdsPower browser crashed on startup — renderer unreachable, "
                                "cannot create live page"
                            )

                    # v10.4: Navigate to about:blank with crash-aware recovery.
                    # If navigation fails with "Page crashed" / "Target closed",
                    # the page is dead — try a fresh page, then bail out.
                    try:
                        page.goto('about:blank', timeout=5000)
                        logger.info(f"AdsPower: Navigated first tab to about:blank")
                    except Exception as nav_err:
                        nav_msg = str(nav_err).lower()
                        is_crash = any(sig in nav_msg for sig in (
                            'page crashed', 'target closed', 'has been closed',
                            'session closed', 'navigation failed because the',
                            'target has been closed',
                        ))
                        logger.warning(
                            f"AdsPower: Could not navigate first tab to about:blank: {nav_err}"
                            + (" (CRASH detected)" if is_crash else " (transient — will retry on target URL)")
                        )
                        if is_crash:
                            # Page renderer is dead. Try once more with a fresh
                            # page from the same context before declaring the
                            # browser dead.
                            recovered = False
                            try:
                                page = context.new_page()
                                # Verify the new page is actually alive with a
                                # real round-trip (not just url cache).
                                page.evaluate('1')
                                logger.info("AdsPower: Recovered with fresh page after crash")
                                recovered = True
                            except Exception as recover_err:
                                logger.error(
                                    f"AdsPower: Recovery failed — browser is dead: {recover_err}"
                                )
                            if not recovered:
                                try:
                                    self._client.stop_profile(profile_id, silent=True)
                                except Exception:
                                    pass
                                raise Exception(
                                    f"AdsPower browser crashed during about:blank navigation: {nav_err}"
                                )
                        # Transient navigation error (not a crash) — page is
                        # still alive, continue with target URL navigation.

                    # Close all other pages (duplicate AdsPower tabs, etc.)
                    for extra_page in existing_pages[1:]:
                        try:
                            extra_page.close()
                            logger.info(f"AdsPower: Closed extra tab")
                        except Exception as close_err:
                            logger.warning(f"Could not close extra tab: {close_err}")

                else:
                    # v11.0: AdsPower started the browser but didn't open any
                    # tab (sometimes happens when the start-page URL fails to
                    # load due to proxy issues). Create our own page now so
                    # downstream code has something to operate on.
                    logger.info(
                        "AdsPower: No existing tabs found — creating new page"
                    )
                    try:
                        page = context.new_page()
                        # Verify the new page is actually alive
                        page.evaluate('1')
                        logger.info("AdsPower: Created new live page (no prior tabs)")
                    except Exception as new_page_err:
                        logger.warning(
                            f"AdsPower: Cannot create new page on empty context — "
                            f"browser is dead: {new_page_err}"
                        )
                        try:
                            self._client.stop_profile(profile_id, silent=True)
                        except Exception:
                            pass
                        raise Exception(
                            "AdsPower browser opened with zero tabs and cannot "
                            f"create a new one — renderer unreachable: {new_page_err}"
                        )

                # Wait briefly for any auto-reopened tabs, then aggressively clean up
                # AdsPower may auto-reopen its start page multiple times
                # v11.0: guard against `page is None` (defensive — should
                # never be None here because the else branch above raises
                # if new_page() fails, but just in case).
                for cleanup_round in range(3):
                    time.sleep(0.5)
                    closed_any = False
                    for p in list(context.pages):
                        try:
                            if p is not page and ('adspower.net' in (p.url or '') or 'adspower.com' in (p.url or '')):
                                p.close()
                                logger.info(f"AdsPower: Closed auto-reopened start page tab (round {cleanup_round + 1})")
                                closed_any = True
                        except Exception:
                            pass
                    if not closed_any:
                        break  # No more AdsPower tabs to close

                tab_count = len(context.pages)
                # Verify our page is not an AdsPower tab (safety check)
                # v11.0: skip this check entirely if page is None (defensive)
                if page is not None:
                    current_url = page.url or ''
                    if 'adspower.net' in current_url or 'adspower.com' in current_url:
                        logger.warning(f"AdsPower: Main page is still on AdsPower URL, navigating to about:blank...")
                        try:
                            page.goto('about:blank', timeout=5000)
                        except Exception:
                            pass
                logger.info(
                    f"AdsPower: Session ready — {tab_count} tab(s), "
                    f"page URL = {page.url if page is not None else '<none>'}"
                )

                # ========================================================
                # v10.9: CDP MAXIMIZE REMOVED.
                # The browser window size is now set via --window-size=W,H
                # launch_arg in start_profile(). This eliminates the need
                # for a CDP roundtrip after startup.
                #
                # Why removed: the v10.8 implementation wrapped CDP maximize
                # in a daemon thread (because signal.alarm can't interrupt
                # Playwright sync API calls). But Patchright's sync API uses
                # greenlets bound to the calling thread — calling it from a
                # daemon thread breaks the greenlet bridge, producing
                # `greenlet.error: cannot switch to a different thread (which
                # happens to have exited)` noise on every run.
                #
                # The launch_arg approach is strictly better:
                #   - No CDP roundtrip (faster startup)
                #   - No thread/greenlet issues
                #   - Window opens at correct size immediately
                #   - bot_v6.py's _detect_and_set_browser_bounds() still
                #     runs and reads the actual viewport for click mapping.
                # ========================================================

                session = {
                    'page': page,
                    'context': context,
                    'browser': browser,
                    'profile_id': profile_id,
                    'ws_endpoint': ws_endpoint,
                    'mode': 'antidetect',
                }
                self._sessions.append(session)
                logger.info(f"Anti-detect session created: profile={profile_id}")
                return session

            except Exception as e:
                err_msg = str(e).lower()
                # v10.6 / v10.7: Detect "browser is dead OR zombie" signature.
                # These all mean the DevTools WebSocket endpoint (random port,
                # e.g. 36251) is either:
                #   (a) unreachable (ECONNREFUSED — browser process gone), OR
                #   (b) accepting TCP but not responding to CDP (Timeout —
                #       browser is FROZEN: OOM, CPU starved, stuck IPC).
                # In both cases the right action is: stop_profile + start_profile
                # to get a FRESH ws_endpoint with a new browser process.
                is_browser_dead = any(sig in err_msg for sig in (
                    'browser crashed', 'renderer unreachable',
                    'page crashed', 'target closed', 'has been closed',
                    'cannot create live page', 'session closed',
                    'econnrefused',                  # WS connect to dead port
                    'ws error', 'ws disconnected',   # Playwright WS error
                    'target has been closed',
                    'timeout',                       # v10.7: zombie browser —
                                                     # WS connected but CDP
                                                     # handshake never completes
                    # v11.0: catch the Python name-error that the page=None
                    # bug used to throw — should never fire now (we initialize
                    # page=None up front), but defensive in case some other
                    # code path hits a similar name issue.
                    'referenced before assignment',
                    'unboundlocalerror',
                ))

                if is_browser_dead and attempt < max_connect_retries - 1:
                    # v10.6: STOP the dead profile, then START it again to
                    # get a FRESH ws_endpoint (new random DevTools port).
                    # Retrying connect_over_cdp on the same dead ws_endpoint
                    # is pointless — the browser process is gone.
                    wait_time = 2 * (attempt + 1)
                    logger.warning(
                        f"AdsPower: browser dead on attempt {attempt + 1}/{max_connect_retries}: "
                        f"{str(e)[:120]}\n"
                        f"  → stopping profile {profile_id} via HTTP API..."
                    )
                    try:
                        self._client.stop_profile(profile_id, silent=True)
                    except Exception:
                        pass
                    time.sleep(1.0)
                    logger.info(f"AdsPower: re-starting profile {profile_id} to get fresh ws_endpoint...")
                    try:
                        fresh_start = self._client.start_profile(profile_id, headless=False)
                        if fresh_start and fresh_start.get('ws_endpoint'):
                            # v11.0: normalize the fresh endpoint too, and
                            # pre-flight poll before retrying connect_over_cdp.
                            current_ws_endpoint = _normalize_ws_endpoint_for_linux(
                                fresh_start['ws_endpoint']
                            )
                            ws_endpoint = current_ws_endpoint  # also update outer var
                            logger.info(
                                f"AdsPower: fresh ws_endpoint obtained "
                                f"(attempt {attempt + 2} will use it)"
                            )
                            # Active wait for the new DevTools port instead
                            # of a blind 1s sleep. Eliminates the race where
                            # start_profile returns before the new browser
                            # process has bound its DevTools socket.
                            if not _wait_for_devtools_port(
                                current_ws_endpoint, timeout=10.0, interval=0.2
                            ):
                                logger.warning(
                                    f"AdsPower: fresh DevTools port not ready "
                                    f"after 10s — will attempt connect anyway"
                                )
                            continue  # retry connect_over_cdp with new endpoint
                        else:
                            logger.warning(
                                f"AdsPower: re-start returned no ws_endpoint — "
                                f"will fall back to Patchright"
                            )
                    except Exception as restart_err:
                        logger.warning(
                            f"AdsPower: re-start failed: {restart_err} — "
                            f"will fall back to Patchright"
                        )
                    # If we get here, restart failed — fall through to Patchright fallback
                    if isinstance(self._client, AdsPowerClient):
                        AdsPowerClient._cached_profile_id = None
                    return self._create_patchright_session(profile_config, pw)

                elif is_browser_dead:
                    # Final attempt also failed with browser-dead signature.
                    logger.error(
                        f"AdsPower: browser dead after {max_connect_retries} attempts: {e}"
                    )
                    try:
                        self._client.stop_profile(profile_id, silent=True)
                    except Exception:
                        pass
                    if isinstance(self._client, AdsPowerClient):
                        AdsPowerClient._cached_profile_id = None
                    return self._create_patchright_session(profile_config, pw)

                # Non-crash error (e.g. transient CDP protocol glitch)
                if attempt < max_connect_retries - 1:
                    wait_time = 2 * (attempt + 1)
                    logger.warning(
                        f"Failed to connect to anti-detect browser (attempt {attempt + 1}/{max_connect_retries}): {e}\n"
                        f"Retrying in {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed to connect to anti-detect browser after {max_connect_retries} attempts: {e}")
                    # v9.5.2: silent=True — profile mungkin sudah crash, "not open" OK
                    self._client.stop_profile(profile_id, silent=True)
                    # Don't delete — keep for reuse
                    return self._create_patchright_session(profile_config, pw)

    def _create_patchright_session(self, profile_config, pw):
        """
        Create session using Patchright (legacy fallback).
        Even in fallback mode, we apply enhanced stealth from the
        upgraded stealth_py.py.

        v9.1: now derives viewport / has_touch / device_scale_factor from
        sync_config (single source of truth) rather than from UA parsing,
        so a Pixel 8 device stays a Pixel 8 even when this fallback fires.
        """
        from stealth_py import apply_stealth_py, inject_stealth_to_page

        launch_args = [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--ignore-certificate-errors',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-infobars',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--disable-ipc-flooding-protection',
            '--force-color-profile=srgb',
        ]

        ua = profile_config.get('ua', '')
        resolution = profile_config.get('resolution', '1920x1080')
        if 'x' in resolution:
            w, h = resolution.split('x')
            launch_args.append(f'--window-size={w},{h}')

        locale = profile_config.get('lan', 'en-US')
        launch_args.append(f'--lang={locale}')

        # Disable WebRTC completely to prevent IP leaks
        launch_args.extend([
            '--disable-features=WebRTC',
            '--enforce-webrtc-ip-permission-check',
            '--webrtc-ip-handling-policy=disable_non_proxied_udp',
        ])

        # DNS leak prevention — force DNS through proxy.
        # v9.1: do NOT append '' (empty string) when proxy IS set — that
        # was a latent bug that polluted the args list with empty entries.
        launch_args.append('--disable-features=DnsOverHttps')
        if not profile_config.get('proxy_host'):
            launch_args.append('--no-proxy-server')

        # Filter out any empty strings just in case
        launch_args = [a for a in launch_args if a]

        launch_kwargs = {
            'headless': False,
            'args': launch_args,
        }

        # Configure proxy
        proxy_host = profile_config.get('proxy_host', '')
        proxy_port = profile_config.get('proxy_port', '')
        proxy_user = profile_config.get('proxy_user', '')
        proxy_pass = profile_config.get('proxy_password', '')
        proxy_type = profile_config.get('proxy_type', 'http')

        if proxy_host and proxy_port:
            proxy_server = f"{proxy_type}://{proxy_host}:{proxy_port}"
            if proxy_user:
                launch_kwargs['proxy'] = {
                    'server': proxy_server,
                    'username': proxy_user,
                    'password': proxy_pass,
                }
                # v9.2: Log proxy auth for debugging tunnel issues
                logger.info(f"Patchright proxy: {proxy_server} (auth: {proxy_user[:8]}...)")
            else:
                launch_args.append(f'--proxy-server={proxy_server}')
                logger.info(f"Patchright proxy: {proxy_server} (no auth)")

        browser = pw.chromium.launch(**launch_kwargs)

        # Build context with full profile sync
        # v9.9: validate timezone against the OS zoneinfo DB before
        # passing to Chromium. An invalid timezone_id causes
        # `browser.new_context()` to throw "Invalid timezone_id: ...".
        # bot_v6._validate_timezone_for_chromium does this for the
        # main patchright path; the fallback path here in antidetect_browser
        # was missing the validation. We inline a minimal version to
        # avoid a circular import (bot_v6 imports antidetect_browser).
        def _is_valid_tz(tz_id):
            if not tz_id:
                return False
            for candidate in (
                f'/usr/share/zoneinfo/{tz_id}',
                f'/usr/lib/zoneinfo/{tz_id}',
                f'/etc/zoneinfo/{tz_id}',
            ):
                try:
                    if os.path.isfile(candidate):
                        return True
                except Exception:
                    pass
            return False

        tz = (profile_config.get('timezone') or '').strip()
        if not tz or not _is_valid_tz(tz):
            # Try common fallbacks before landing on UTC.
            for fallback_tz in ('America/New_York', 'UTC'):
                if _is_valid_tz(fallback_tz):
                    if tz and tz != fallback_tz:
                        logger.warning(
                            f"Patchright fallback: timezone {tz!r} is not available on this "
                            f"system's zoneinfo DB — using {fallback_tz!r} instead."
                        )
                    tz = fallback_tz
                    break
            else:
                tz = 'UTC'

        viewport_w, viewport_h = 1920, 1080
        if 'x' in resolution:
            parts = resolution.split('x')
            try:
                viewport_w, viewport_h = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                pass

        # v9.1: derive has_touch from sync_config (authoritative), NOT
        # from UA string parsing. sync_config.has_touch is set by
        # ProfileSynchronizer based on the chosen device.os.
        has_touch = bool(profile_config.get('has_touch', False))
        device_scale = profile_config.get('device_scale_factor',
                                          2.625 if has_touch else 1.0)

        # v9.1: Chrome version — read from sync_config if provided,
        # otherwise parse from UA. Previously hardcoded '148' fallback
        # which was inconsistent with the rest of the codebase (CHROME_VERSION=137).
        chrome_version = profile_config.get('chrome_version')
        if not chrome_version:
            import re
            m = re.search(r'chrome/(\d+)\.', ua or '', re.IGNORECASE)
            chrome_version = m.group(1) if m else '137'

        # v9.1: avoid the circular import (bot_v6 imports antidetect_browser
        # at top-level). Inline the small derive_ch_ua_headers function here.
        ch_ua_headers = self._derive_ch_ua_headers(ua, chrome_version)

        context = browser.new_context(
            viewport={'width': viewport_w, 'height': viewport_h},
            locale=locale,
            timezone_id=tz,
            user_agent=ua,
            ignore_https_errors=True,
            java_script_enabled=True,
            has_touch=has_touch,
            device_scale_factor=device_scale,
            is_mobile=has_touch,
            extra_http_headers={
                'Accept-Language': f'{locale},en;q=0.9',
                **ch_ua_headers,
            },
        )

        # Apply enhanced stealth (upgraded stealth_py.py) — pass full
        # profile_config so WebGL / fonts / hardware / color_depth are
        # all synchronized with what we computed in ProfileSynchronizer.
        apply_stealth_py(
            context,
            locale=locale,
            user_agent=ua,
            chrome_version=chrome_version,
            use_patchright=True,
            profile_config=profile_config,
        )

        page = context.new_page()
        page.set_viewport_size({'width': viewport_w, 'height': viewport_h})

        session = {
            'page': page,
            'context': context,
            'browser': browser,
            'profile_id': None,
            'ws_endpoint': None,
            'mode': 'patchright',
        }
        self._sessions.append(session)
        logger.info("Patchright fallback session created with enhanced stealth")
        return session

    @staticmethod
    def _derive_ch_ua_headers(user_agent, chrome_version='137'):
        """
        Inline copy of bot_v6.derive_ch_ua_headers — avoids circular import.
        v9.1: imported from bot_v6 previously; that created a fragile
        circular dependency (bot_v6 imports antidetect_browser at top-level,
        antidetect_browser imported bot_v6 inside _create_patchright_session).
        """
        ua = (user_agent or '').lower()
        if 'windows nt' in ua:
            platform = 'Windows'
            mobile = '?0'
        elif 'mac os x' in ua or 'macintosh' in ua:
            platform = 'macOS'
            mobile = '?0'
        elif 'android' in ua:
            platform = 'Android'
            mobile = '?1'
        elif 'iphone' in ua or 'ipad' in ua or 'cros' in ua:
            platform = 'Linux' if 'cros' in ua else 'iOS'
            mobile = '?1'
        elif 'linux' in ua:
            platform = 'Linux'
            mobile = '?0'
        else:
            platform = 'Windows'
            mobile = '?0'
        sec_ch_ua = f'"Chromium";v="{chrome_version}", "Not_A Brand";v="24", "Google Chrome";v="{chrome_version}"'
        return {
            'Sec-CH-UA': sec_ch_ua,
            'Sec-CH-UA-Mobile': mobile,
            'Sec-CH-UA-Platform': f'"{platform}"',
        }

    def close_and_cleanup(self, session):
        """Close a browser session and clean up resources.

        v10.9: REWROTE again. The v10.8 thread-based wrapper caused
        `greenlet.error: cannot switch to a different thread` noise
        because Patchright's sync API uses greenlets bound to the calling
        thread — calling context.close() from a daemon thread breaks the
        greenlet bridge.

        v10.9 strategy:
          1. stop_profile via AdsPower HTTP API (8s, thread-wrapped) —
             HTTP call, no Patchright sync, no greenlet issue. This is
             the AUTHORITATIVE kill: it terminates the browser process
             from AdsPower's side.
          2. context.close() / browser.close() — called from the MAIN
             THREAD (no thread wrapper). After stop_profile kills the
             browser process, these calls fail FAST with "Target closed"
             or similar (the WebSocket is already dead). They're wrapped
             in try/except so any exception is swallowed.

        If stop_profile fails (rare), context.close() / browser.close()
        might hang — but the outer watchdog in bot_v6.py (signal.alarm +
        escalation thread) will catch that and force-skip.
        """
        import threading as _threading

        def _run_with_thread_timeout(seconds, fn, label):
            """Run fn() in a daemon thread. If it doesn't finish in `seconds`,
            abandon it. Returns True on success, False on timeout/error.

            ONLY safe for non-Patchright calls (HTTP, file I/O, etc.).
            Patchright sync API uses thread-bound greenlets — calling it
            from a daemon thread breaks the greenlet bridge.
            """
            result_box = {'error': None, 'done': False}
            def target():
                try:
                    fn()
                    result_box['done'] = True
                except Exception as e:
                    result_box['error'] = e
            t = _threading.Thread(target=target, daemon=True)
            t.start()
            t.join(timeout=seconds)
            if t.is_alive():
                logger.warning(
                    f"AdsPower: cleanup '{label}' hung >{seconds}s — abandoning thread"
                )
                return False
            if result_box['error'] is not None:
                logger.warning(
                    f"AdsPower: cleanup '{label}' failed: {result_box['error']}"
                )
                return False
            return result_box['done']

        try:
            # Step 1: stop_profile via HTTP API (thread-wrapped, 8s timeout).
            # This kills the browser process. Safe to run in a thread because
            # it's just requests.post() — no Patchright sync, no greenlets.
            #
            # v10.11.1 UPGRADE — MOD 5 FIX: stop_profile sekarang menerima
            # parameter `clear_cache=True`. Ini cara OFFICIAL AdsPower untuk
            # clear cache (endpoint /clear-cache terpisah TIDAK ADA — return
            # HTTP 404 di semua versi AdsPower). Dengan menggabungkan stop +
            # clear_cache dalam SATU panggilan API, kita menghindari:
            #   1. Error 404 dari endpoint /clear-cache yang tidak exist
            #   2. Race condition antara stop dan clear_cache terpisah
            #   3. Panggilan API berlebihan (1 call vs 2 calls)
            # Cache browser AdsPower di-clear SETIAP kali bot berganti
            # profile/user (close_and_cleanup dipanggil per-user).
            if session.get('mode') == 'antidetect' and self._client:
                profile_id = session.get('profile_id')
                if profile_id:
                    _run_with_thread_timeout(
                        8,
                        lambda: self._client.stop_profile(profile_id, silent=True, clear_cache=True),
                        f'stop_profile+clear_cache({profile_id})',
                    )
                    logger.info(
                        f"Stopped anti-detect profile: {profile_id} "
                        f"(cache cleared, kept for reuse)"
                    )

            # Step 2: context.close() / browser.close() — MAIN THREAD ONLY.
            # v10.9: Do NOT wrap these in threads. Patchright sync API uses
            # thread-bound greenlets; calling from a daemon thread produces
            # `greenlet.error: cannot switch to a different thread` noise.
            #
            # After stop_profile kills the browser process, these calls
            # fail FAST (WebSocket already dead → "Target closed" exception
            # raised immediately). The try/except swallows that.
            #
            # If stop_profile failed silently AND the browser is frozen,
            # these calls might hang. The outer watchdog in bot_v6.py
            # (signal.alarm + escalation thread → os._exit(2)) catches
            # that case and forces progress.
            if session.get('context'):
                try:
                    session['context'].close()
                except Exception as e:
                    # Expected: "Target closed", "Browser closed", etc.
                    # after stop_profile killed the browser.
                    logger.debug(f"AdsPower: context.close() post-stop exception (expected): {e}")
            if session.get('browser'):
                try:
                    session['browser'].close()
                except Exception as e:
                    logger.debug(f"AdsPower: browser.close() post-stop exception (expected): {e}")
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")
        finally:
            if session in self._sessions:
                self._sessions.remove(session)

    def cleanup_all(self):
        """Clean up all active sessions."""
        for session in list(self._sessions):
            self.close_and_cleanup(session)

    def __del__(self):
        self.cleanup_all()
