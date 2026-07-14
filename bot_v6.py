#!/usr/bin/env python3
"""
Visit Bot Python — v12.0 UPGRADE (Deadlines + Click Cap + Adsterra Wait + Port Fix)
======================================================================================

v12.0 UPGRADE — 7 issue fixes from production log analysis:
  1. FIX Issue 1 — Antibot checkbox off-screen Y coordinates (y=14493):
     Delegated to antibot.py v8.0 (scroll_into_view + viewport sanity clamp).
  2. FIX Issue 2 — Random clicks runaway (54+ clicks exceeding budget):
     _random_click_on_page() now has HARD CAP min(max_clicks, 5) and a
     30-second time budget. _humanlike_interact_with_ad_page() calls with
     max_clicks=random.randint(1, 3) instead of randint(2, 4).
  3. FIX Issue 3 — Playwright wedge / watchdog escalation:
     Added _check_deadline() helper. _humanlike_interact_with_ad_page(),
     _random_click_on_page(), and click_native_adsterra_and_process() all
     accept a `deadline` parameter and bail early if exceeded.
  4. FIX Issue 4 — Port 8080 already in use on restart:
     web_preview.py run_server() now uses SO_REUSEADDR and falls back to
     port+1..port+10 if the primary port is occupied.
  5. FIX Issue 5 — 0 ad clicks (Adsterra scripts not yet loaded):
     Added ADSTERRA_WAIT_FOR_RENDER_S = 3 constant. After scrolling to
     footer, wait 2-4s for scripts to inject content. Retry loop (up to
     2 retries) if no ads found. Relaxed _has_adsterra_signature check.
     Fallback to discover_ads() (legacy) and div[id^="container-"] click.
  6. FIX Issue 6 — CPM optimization (reduce targets, prioritize view time):
     MIN_AD_CLICKS_PER_SESSION reduced from 4 to 3. After successful ad
     click, ensure at least 20s view time (reduced from 30s minimum).
     _humanlike_interact_with_ad_page() prioritizes idle/wait over clicks.
  7. FIX Issue 7 — Xlib.xauth warning suppression:
     Set XAUTHORITY env var BEFORE any Xlib import. ensure_xauthority()
     creates blank .Xauthority file before calling xauth add.

v11.1 UPGRADE — 3 modifikasi baru:
  1. UPGRADE 1 — Age Verification Function: saat tab iklan dibuka dan
     menampilkan age verification prompt (18+, Enter, Confirm, dll),
     bot otomatis mendeteksi dan mengklik tombol "Yes"/"I am 18+"/
     "Enter"/"Confirm". Implementasi:
       - Fungsi baru handle_age_verification(page, logger) yang mencari
         age gate overlays/dialogs berdasarkan selector dan text pattern.
       - Dipanggil di 2 tempat:
         a) _humanlike_interact_with_ad_page() — setelah antibot check
         b) _click_ad_inner() — setelah early antibot check
  2. UPGRADE 2 — Read Article/Content Only 1x: max_articles diubah dari
     rng.randint(2, 4) menjadi tepat 1. Bot hanya membaca 1 artikel
     per session, fokus pada klik iklan.
  3. UPGRADE 3 — Click 4 Banner Ads in Footer Randomly: footer banner
     sekarang di-shuffle secara random dan diklik SEMUA (up to 4) tanpa
     MIN_AD_CLICKS_PER_SESSION break check. Perubahan di:
       - Homepage footer section: shuffle + klik semua
       - Article footer section: shuffle + klik semua
       - Catch-up section: target tetap minimal 4 total ads

v10.11 UPGRADE — UPGRADE BOT (5 modifikasi):
  1. MOD 1 — Ubah perilaku interaksi bot: setelah halaman web tujuan
     terbuka, scroll ke footer, arahkan mouse ke advertisement di footer,
     lalu klik 1 dari 4 native banner Adsterra secara RANDOM.
  2. MOD 2 — Hilangkan fungsi deteksi banner/popunder/iklan adsterra yang
     kompleks (EffectiveCPMNetwork detection, popunder handling, generic
     discover_ads fallback). Diganti dengan simple footer banner click.
  3. MOD 3 — Persingkat waktu membuka article/content:
       - Homepage browse: 8-15s → 3-5s
       - Article read: 6-12s → 2-4s
       - Final browse: 5-10s → 2-4s
       - Berbagai time.sleep dikurangi.
  4. MOD 4 — Ganti semua profile device menjadi Android (tablet/smartphone):
       - DEVICE_TEMPLATES hanya berisi 10 smartphone + 2 tablet Android.
       - ProfileSynchronizer(prefer_os='Android') untuk paksa OS Android.
       - Legacy DEVICES fallback juga Android-only.
  5. MOD 5 — Clear cache browser AdsPower tiap kali berganti profile/user:
       - v10.11.1 FIX: clear_profile_cache() sekarang memanggil
         stop_profile(clear_cache=True) — cara OFFICIAL AdsPower untuk
         clear cache. Endpoint /api/v2/browser-profile/clear-cache
         TIDAK ADA (HTTP 404) — fixed.
       - close_and_cleanup() panggil stop_profile(clear_cache=True) dalam
         SATU API call (bukan stop + clear terpisah).
       - start_profile() panggil clear_profile_cache() di awal sebagai
         belt-and-suspenders (idempotent — no-op bila browser tidak running).

v10.11.2 UPGRADE — AD TAB LOAD TIMEOUT (MAX 10s):
  - Saat tab iklan Adsterra dibuka (via click-capture atau manual goto),
    tunggu maksimal 10 detik untuk loading. Bila gagal (timeout, DNS error,
    chrome-error://, about:blank, page crash), tutup tab iklan dan
    LANJUTKAN aktivitas bot di web utama.

v9.5 (sebelumnya): EffectiveCPMNetwork Detection + Auto Device Pool
v9.4 (sebelumnya): Native AdSterra detection + deep click
v9.3 (sebelumnya): urllib3 InsecureRequestWarning suppress + proxy leak fix
v9.2 (sebelumnya): CDP cert fix + CONNECT pre-flight + robust retry
v9.0 (sebelumnya): Anti-Detect Browser + Total Profile Sync
"""

import os
import sys
import ssl
import time
import random
import argparse
import threading
import subprocess
import tempfile
import shutil
import json
import signal
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ====================================================================
# Suppress urllib3 InsecureRequestWarning GLOBALY
# ====================================================================
# Root cause: test_proxy() memakai verify=False karena residential proxy
# (911Proxy/FloppyData) melakukan SSL interception pada CONNECT tunnel.
# Setiap request memunculkan:
#   InsecureRequestWarning: Unverified HTTPS request is being made to
#   host 'us.911proxy.net'. Adding certificate verification is strongly
#   advised.
# Warning ini intentional dan sudah accepted risk. Suppress di sini
# SEKALI saja agar tidak menggangu log.
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    # urllib3 tidak terinstall → requests juga tidak ada → fallback ke urllib
    pass

# Module-level reusable Session untuk test_proxy().
# Pool connections hemat resource + auto-retry pada transient errors.
_PROXY_TEST_SESSION = None
_PROXY_TEST_SESSION_LOCK = threading.Lock()


def _get_proxy_test_session():
    """
    Return a process-wide requests.Session for proxy pre-flight tests.
    Session dipakai ulang antar call → connection pooling, fewer TLS
    handshakes, lebih cepat.
    """
    global _PROXY_TEST_SESSION
    if _PROXY_TEST_SESSION is not None:
        return _PROXY_TEST_SESSION
    try:
        import requests as http_requests
        from requests.adapters import HTTPAdapter
        try:
            from urllib3.util.retry import Retry
        except ImportError:
            Retry = None
    except ImportError:
        return None
    with _PROXY_TEST_SESSION_LOCK:
        if _PROXY_TEST_SESSION is None:
            sess = http_requests.Session()
            # retries=0 karena test_proxy mengelola retry-nya sendiri
            # (kita ingin gagal cepat, bukan auto-retry)
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
            if Retry is not None:
                retry = Retry(total=0, connect=1, read=1, backoff_factor=0)
                adapter = HTTPAdapter(
                    max_retries=retry,
                    pool_connections=4,
                    pool_maxsize=8,
                )
            sess.mount('http://', adapter)
            sess.mount('https://', adapter)
            _PROXY_TEST_SESSION = sess
    return _PROXY_TEST_SESSION

# ====================================================================
# Xvfb setup (tidak diubah)
# ====================================================================
os.environ.setdefault('DISPLAY', ':99')
# v12.0 Issue 7 FIX: Set XAUTHORITY BEFORE any Xlib import to suppress
# "Xlib.xauth: warning, no xauthority details available" noise.
if 'XAUTHORITY' not in os.environ:
    home = Path.home()
    xauth_file = home / '.Xauthority'
    os.environ['XAUTHORITY'] = str(xauth_file)
else:
    xauth_file = Path(os.environ['XAUTHORITY'])

def ensure_xauthority(display):
    """v12.0: Create blank .Xauthority file BEFORE calling xauth add
    to suppress Xlib.xauth warning about missing/empty file.
    """
    xauth_file.parent.mkdir(parents=True, exist_ok=True)
    # v12.0: Create blank file first so xauth can find/parse it
    try:
        if not xauth_file.exists() or xauth_file.stat().st_size == 0:
            xauth_file.touch()
            # Write a minimal blank xauth entry so Xlib doesn't warn
            try:
                xauth_file.write_bytes(b'\x01\x00')  # minimal valid header
            except Exception:
                pass
    except Exception:
        pass
    cookie = os.urandom(16).hex()
    if shutil.which('xauth'):
        try:
            subprocess.check_call(
                ['xauth', 'add', display, '.', cookie],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass
    try:
        xauth_file.touch()
    except Exception as e:
        print(f"Failed to create Xauthority file: {e}")
        sys.exit(1)

def start_xvfb():
    display = os.environ.get('DISPLAY', ':99')
    if display != ':99':
        print(f"Using existing display {display}")
        return
    try:
        subprocess.run(['pgrep', '-f', 'Xvfb :99'], check=True, capture_output=True)
        print("Xvfb :99 already running.")
        ensure_xauthority(display)
        return
    except subprocess.CalledProcessError:
        pass
    if not shutil.which('Xvfb'):
        print("Xvfb not found. Install with: apt-get install xvfb")
        print("NOTE: Anti-detect browser mode (--mode antidetect) does NOT require Xvfb.")
        return
    print("Starting Xvfb :99 (1920x1080x24) ...")
    proc = subprocess.Popen(
        ['Xvfb', ':99', '-screen', '0', '1920x1080x24'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    time.sleep(1.5)
    try:
        subprocess.run(['pgrep', '-f', 'Xvfb :99'], check=True, capture_output=True)
        print("Xvfb started successfully.")
    except subprocess.CalledProcessError:
        print("Failed to start Xvfb. Anti-detect mode does not require it.")
    ensure_xauthority(display)

# ====================================================================
# Patchright import & monkey-patch (tidak diubah)
# ====================================================================
try:
    from patchright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("ERROR: patchright not installed. Install with:")
    print("  pip install patchright && patchright install chromium")
    sys.exit(1)

import patchright._impl._browser_context as _patchright_bc
import patchright._impl._page as _patchright_pg
import patchright._impl._helper as _patchright_helper
import inspect as _inspect

def _needs_patch(cls):
    try:
        src = _inspect.getsource(cls.install_inject_route)
        if '_sync_route_handler' in src:
            return False
        if 'mapping.wrap_handler(route_handler)' in src and 'False' in src:
            return True
    except Exception:
        pass
    return True

def _make_sync_route_handler():
    def _sync_route_handler(route, request):
        try:
            if (
                request.resource_type == "document"
                and request.url.startswith("http")
            ):
                route._check_not_handled()
                route.request._apply_fallback_overrides({"patchrightInitScript": True})
                route._report_handled(False)
            else:
                route._check_not_handled()
                route._report_handled(False)
        except Exception:
            try:
                if route._handling_future:
                    route._report_handled(False)
            except Exception:
                pass
    return _sync_route_handler

_sync_route_handler = _make_sync_route_handler()
_orig_bc_install_inject_route = _patchright_bc.BrowserContext.install_inject_route
_orig_pg_install_inject_route = _patchright_pg.Page.install_inject_route

async def _fixed_install_inject_route_bc(self):
    if self.route_injecting:
        return
    if self._connection._is_sync:
        self._routes.insert(
            0,
            _patchright_helper.RouteHandler(
                self._options.get("baseURL"),
                "**/*",
                _sync_route_handler,
                True,
                None,
            ),
        )
        await self._update_interception_patterns()
        self.route_injecting = True
    else:
        await _orig_bc_install_inject_route(self)

async def _fixed_install_inject_route_pg(self):
    if self.route_injecting:
        return
    if self.context.route_injecting:
        self.route_injecting = True
        return
    if self._connection._is_sync:
        self._routes.insert(
            0,
            _patchright_helper.RouteHandler(
                self._browser_context._options.get("baseURL"),
                "**/*",
                _sync_route_handler,
                True,
                None,
            ),
        )
        await self._update_interception_patterns()
        self.route_injecting = True
    else:
        await _orig_pg_install_inject_route(self)

if _needs_patch(_patchright_bc.BrowserContext):
    _patchright_bc.BrowserContext.install_inject_route = _fixed_install_inject_route_bc
if _needs_patch(_patchright_pg.Page):
    _patchright_pg.Page.install_inject_route = _fixed_install_inject_route_pg

import console as log
from web_preview import start_server_in_thread, update_state, push_log, state
from stealth_py import apply_stealth_py, inject_stealth_to_page
from antibot import solve_antibot_if_present, detect_antibot, is_antibot_verified
from antidetect_browser import AntiDetectManager
from profile_sync import ProfileSynchronizer

# v11.0 UPGRADE — MOD 3: Import modular Android profiles
from android_profiles import generate_android_profiles, ANDROID_PROFILE_COUNT

# v12.0 Issue 6 FIX: Reduced from 4 to 3 (more achievable within time budget)
MIN_AD_CLICKS_PER_SESSION = 3

# v12.0 Issue 6 FIX: Optimal CPM view time — reduced minimum from 30s to 20s
# because antibot solving eats into the budget. 20s is still enough for CPM credit.
AD_VIEW_TIME_MIN_S = 20
AD_VIEW_TIME_MAX_S = 60

# v12.0 Issue 5 FIX: Wait this many seconds after page load before looking
# for Adsterra ads — their invoke.js needs time to inject ad content into
# container divs.
ADSTERRA_WAIT_FOR_RENDER_S = 3

# ====================================================================
# Configuration (tidak diubah)
# ====================================================================
TARGET_URL = 'https://globalupdate.2bd.net/'
BROWSER_X = 0
BROWSER_Y = 0
BROWSER_W = 1920
BROWSER_H = 1080
CHROME_MAJOR_VERSION = '137'
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Mobile Safari/537.36',
]
COOLDOWN_MIN = 30.0
COOLDOWN_MAX = 120.0
SKIP_PROBABILITY = 0.10
SKIP_EXTRA_COOLDOWN_MIN = 60.0
SKIP_EXTRA_COOLDOWN_MAX = 180.0

# ====================================================================
# v9.5 — AUTO DEVICE POOL (menggantikan DEVICES hardcode 4 entry)
# ====================================================================
# Sebelumnya DEVICES hanya berisi 4 entry hardcode. Sekarang pool
# di-generate secara dinamis sesuai --limit. Setiap profile dapat
# device fingerprint yang unik (OS + model + viewport + UA).

# Base templates — tiap entry akan di-clone dengan Chrome major version
# yang berbeda-beda untuk memperkaya diversity fingerprint.

# v10.11 UPGRADE: Semua profile device sekarang HANYA Android (smartphone +
# tablet). Desktop Windows/Mac/Linux dan iOS/iPad telah dihapus sesuai
# requirement: "ganti semua profile device menjadi android (tablet atau
# smartphone)". Pool sekarang berisi 12 template Android (10 smartphone +
# 2 tablet) yang akan di-clone dengan 5 Chrome major version → 60
# kombinasi unik per round.
DEVICE_TEMPLATES = [
    # ============ Android Smartphone ============
    {'name': 'Pixel 8',         'viewport': {'width': 412, 'height': 915},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.625,
     'mobile_model': 'Pixel 8'},
    {'name': 'Pixel 7 Pro',     'viewport': {'width': 412, 'height': 892},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 3.5,
     'mobile_model': 'Pixel 7 Pro'},
    {'name': 'Pixel 9 Pro',     'viewport': {'width': 412, 'height': 892},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 3.5,
     'mobile_model': 'Pixel 9 Pro'},
    {'name': 'Samsung Galaxy S24', 'viewport': {'width': 412, 'height': 915},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 3.0,
     'mobile_model': 'SM-S921B'},
    {'name': 'Samsung Galaxy S23', 'viewport': {'width': 360, 'height': 780},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 3.0,
     'mobile_model': 'SM-S911B'},
    {'name': 'Samsung Galaxy S22', 'viewport': {'width': 360, 'height': 780},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 3.0,
     'mobile_model': 'SM-S901B'},
    {'name': 'Samsung Galaxy A54', 'viewport': {'width': 412, 'height': 915},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.625,
     'mobile_model': 'SM-A546B'},
    {'name': 'OnePlus 12',       'viewport': {'width': 412, 'height': 915},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.625,
     'mobile_model': 'CPH2581'},
    {'name': 'OnePlus 11',       'viewport': {'width': 412, 'height': 915},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.625,
     'mobile_model': 'CPH2449'},
    {'name': 'Xiaomi 14 Pro',    'viewport': {'width': 412, 'height': 915},
     'ua_platform': 'android', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.625,
     'mobile_model': '23116PN5BC'},

    # ============ Android Tablet ============
    {'name': 'Samsung Galaxy Tab S9', 'viewport': {'width': 800, 'height': 1280},
     'ua_platform': 'android_tab', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.0,
     'mobile_model': 'SM-X716B'},
    {'name': 'Samsung Galaxy Tab S8', 'viewport': {'width': 800, 'height': 1280},
     'ua_platform': 'android_tab', 'os': 'Android', 'has_touch': True,  'device_scale_factor': 2.0,
     'mobile_model': 'SM-X706B'},
]


# Chrome major versions yang akan di-rotate untuk diversity fingerprint.
# Versi modern 137 / 138 / 139 / 140 adalah versi yang umum dipakai 2025-2026.
CHROME_VERSION_POOL = ['137', '138', '139', '140', '141']


def _build_ua(template, chrome_major):
    """Build User-Agent string dari template + Chrome major version."""
    platform = template['ua_platform']
    if platform == 'win':
        return (f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                f'(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36')
    elif platform == 'mac':
        return (f'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                f'(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36')
    elif platform == 'linux':
        return (f'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                f'(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36')
    elif platform == 'android':
        model = template.get('mobile_model', 'Pixel 8')
        return (f'Mozilla/5.0 (Linux; Android 14; {model}) AppleWebKit/537.36 '
                f'(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Mobile Safari/537.36')
    elif platform == 'android_tab':
        model = template.get('mobile_model', 'SM-X716B')
        # Tablet Android: tidak pakai "Mobile" token
        return (f'Mozilla/5.0 (Linux; Android 14; {model}) AppleWebKit/537.36 '
                f'(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36')
    elif platform == 'iphone':
        # iOS UA — Chrome di iOS sebenarnya WebKit, tapi untuk fingerprint
        # bot konsistensi, kita pakai CriOS-style UA.
        return (f'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) '
                f'AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/{chrome_major}.0.0.0 '
                f'Mobile/15E148 Safari/604.1')
    elif platform == 'ipad':
        return (f'Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) '
                f'AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/{chrome_major}.0.0.0 '
                f'Mobile/15E148 Safari/604.1')
    # Fallback
    return (f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            f'(KHTML, like Gecko) Chrome/{chrome_major}.0.0.0 Safari/537.36')


def generate_device_pool(target_count):
    """
    Generate pool device unik sebanyak target_count.

    Strategi:
      1. Ambil seluruh DEVICE_TEMPLATES (39+ entries).
      2. Untuk tiap template, clone dengan 5 Chrome major versions.
         → 39 * 5 = 195 kombinasi unik pada round pertama.
      3. Bila target_count > 195, lanjut round kedua dengan menambahkan
         suffix index ke name + variasi viewport minor (offset ±1px).
      4. Shuffle deterministically (seed tetap, sehingga pool reproducible).
      5. Return exactly target_count entries.

    Args:
        target_count: int — jumlah device yang dibutuhkan (biasanya args.limit)

    Returns:
        list of dict device dengan keys: name, viewport, ua, os, has_touch,
        device_scale_factor, mobile_model (opsional)
    """
    pool = []
    # Round 1: tiap template × tiap Chrome version
    for tmpl in DEVICE_TEMPLATES:
        for chrome_major in CHROME_VERSION_POOL:
            entry = {
                'name': f"{tmpl['name']} [C{chrome_major}]",
                'viewport': dict(tmpl['viewport']),
                'ua': _build_ua(tmpl, chrome_major),
                'os': tmpl['os'],
                'has_touch': tmpl.get('has_touch', False),
                'device_scale_factor': tmpl.get('device_scale_factor', 1.0),
            }
            if 'mobile_model' in tmpl:
                entry['mobile_model'] = tmpl['mobile_model']
            pool.append(entry)

    # Bila target_count > pool size, expand round 2 dengan offset viewport
    if len(pool) < target_count:
        rng = random.Random(0xC0FFEE)  # Deterministic
        round_idx = 1
        while len(pool) < target_count:
            for tmpl in DEVICE_TEMPLATES:
                if len(pool) >= target_count:
                    break
                for chrome_major in CHROME_VERSION_POOL:
                    if len(pool) >= target_count:
                        break
                    # Offset viewport 1-2 px agar fingerprint berbeda
                    offset = rng.randint(-2, 2)
                    vp = dict(tmpl['viewport'])
                    vp['width']  = max(320, vp['width']  + offset)
                    vp['height'] = max(400, vp['height'] + rng.randint(-2, 2))
                    entry = {
                        'name': f"{tmpl['name']} [C{chrome_major}#{round_idx}]",
                        'viewport': vp,
                        'ua': _build_ua(tmpl, chrome_major),
                        'os': tmpl['os'],
                        'has_touch': tmpl.get('has_touch', False),
                        'device_scale_factor': tmpl.get('device_scale_factor', 1.0),
                    }
                    if 'mobile_model' in tmpl:
                        entry['mobile_model'] = tmpl['mobile_model']
                    pool.append(entry)
                round_idx += 1

    # Shuffle deterministically
    rng = random.Random(0x5EED1234)
    rng.shuffle(pool)

    return pool[:target_count]


# Backward-compat: simpan device fallback bila pool belum di-init.
# Akan di-overwrite oleh main() saat startup via set_device_pool().
# v10.11: fallback juga Android-only (smartphone + tablet).
_USER_AGENTS_LEGACY = [
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; SM-X716B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/' + CHROME_MAJOR_VERSION + '.0.0.0 Mobile Safari/537.36',
]
DEVICES = [
    {'name': 'Pixel 8',                'viewport': {'width': 412, 'height': 915}, 'ua': _USER_AGENTS_LEGACY[0], 'os': 'Android'},
    {'name': 'Samsung Galaxy S24',     'viewport': {'width': 412, 'height': 915}, 'ua': _USER_AGENTS_LEGACY[1], 'os': 'Android'},
    {'name': 'Samsung Galaxy Tab S9',  'viewport': {'width': 800, 'height': 1280}, 'ua': _USER_AGENTS_LEGACY[2], 'os': 'Android'},
    {'name': 'Pixel 7 Pro',            'viewport': {'width': 412, 'height': 892}, 'ua': _USER_AGENTS_LEGACY[3], 'os': 'Android'},
]


def set_device_pool(new_pool):
    """Ganti global DEVICES dengan pool baru (dipanggil dari main())."""
    global DEVICES
    DEVICES = list(new_pool)
    log.info('', f'DEVICE POOL updated: {len(DEVICES)} unique devices loaded')

LOCALES = ['en-US', 'en-GB', 'en-AU', 'en-CA', 'en-IE']
TIMEZONES = [
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu',
]
FALLBACK_TZ_LOCALE_PAIRS = [
    ('America/New_York', 'en-US'),
    ('America/Chicago', 'en-US'),
    ('America/Denver', 'en-US'),
    ('America/Los_Angeles', 'en-US'),
    ('America/Phoenix', 'en-US'),
    ('America/Toronto', 'en-CA'),
    ('America/Vancouver', 'en-CA'),
    ('Europe/London', 'en-GB'),
    ('Europe/Dublin', 'en-IE'),
    ('Australia/Sydney', 'en-AU'),
    ('Australia/Melbourne', 'en-AU'),
    ('Asia/Singapore', 'en-SG'),
    ('Asia/Kolkata', 'en-IN'),
    ('Asia/Jakarta', 'id-ID'),
]
REFERRERS = [
    'https://www.google.com/',
    'https://www.bing.com/',
    'https://duckduckgo.com/',
    'https://www.yahoo.com/',
    'https://news.google.com/',
    'https://www.reddit.com/',
    'https://t.co/',
    'https://www.facebook.com/',
    '',
]
OXYLABS_USERNAME = os.environ.get('OXYLABS_USERNAME', '')
OXYLABS_PASSWORD = os.environ.get('OXYLABS_PASSWORD', '')
OXYLABS_ENTRY_HOST = 'dc.oxylabs.io'
OXYLABS_ENTRY_PORT = 8000
PROXY911_USER = os.environ.get('PROXY911_USER', '')
PROXY911_PASSWORD = os.environ.get('PROXY911_PASSWORD', '')
PROXY911_HOST = os.environ.get('PROXY911_HOST', 'us.911proxy.net')
PROXY911_PORT = int(os.environ.get('PROXY911_PORT', '2600'))
PROXY911_AREA = os.environ.get('PROXY911_AREA', 'US')
PROXY911_SESSION_LIFE = int(os.environ.get('PROXY911_SESSION_LIFE', '5'))
ADSPOWER_API_KEY = os.environ.get('ADSPOWER_API_KEY', '')
ADSPOWER_MODE = os.environ.get('ADSPOWER_MODE', 'local')
# v9.2: ADSPOWER_BASE_URL default changed from 'http://127.0.0.1' to
# 'http://localhost' to match antidetect_browser.ADSPOWER_DEFAULTS['base_url'].
# AdsPower Global v8.x binds to local.adspower.net which resolves to ::1
# (IPv6 loopback) — using 'localhost' lets Python's requests library fall
# through to IPv6 transparently when 127.0.0.1 doesn't answer.
# Also: the env var name we READ here is 'ADSPOWER_API_BASE' (the same
# name antidetect_browser.py expects), not 'ADSPOWER_BASE_URL' (which
# was previously read but never consumed anywhere). This fixes a silent
# inconsistency where setting ADSPOWER_BASE_URL=http://... had no effect.
ADSPOWER_BASE_URL = os.environ.get('ADSPOWER_API_BASE', 'http://localhost')
ADSPOWER_PORT = int(os.environ.get('ADSPOWER_PORT', '50325'))
ADSPOWER_PROFILE_ID = os.environ.get('ADSPOWER_PROFILE_ID', '')
# v9.3: ADSPOWER_GROUP_ID dideklarasi sebagai module-level global.
# Sebelumnya hanya dibuat sebagai local variable di load_proxies(),
# sehingga import ADSPOWER_GROUP_ID dari modul lain akan gagal.
ADSPOWER_GROUP_ID = os.environ.get('ADSPOWER_GROUP_ID', '')

COUNTRY_CODE_TO_NAME = {
    'US': 'United States',
    'GB': 'United Kingdom', 'UK': 'United Kingdom',
    'CA': 'Canada',
    'AU': 'Australia',
    'IE': 'Ireland',
    'DE': 'Germany',
    'FR': 'France',
    'NL': 'Netherlands',
    'JP': 'Japan',
    'SG': 'Singapore',
    'IN': 'India',
    'ID': 'Indonesia',
    'BR': 'Brazil',
    'KR': 'South Korea',
    'HK': 'Hong Kong',
    'RU': 'Russia',
    'ES': 'Spain',
    'IT': 'Italy',
    'PT': 'Portugal',
    'MX': 'Mexico',
    'AR': 'Argentina',
    'ZA': 'South Africa',
    'AE': 'United Arab Emirates',
    'SA': 'Saudi Arabia',
    'TH': 'Thailand',
    'VN': 'Vietnam',
    'PH': 'Philippines',
    'MY': 'Malaysia',
    'TW': 'Taiwan',
    'CN': 'China',
    'SE': 'Sweden',
    'NO': 'Norway',
    'FI': 'Finland',
    'DK': 'Denmark',
    'CH': 'Switzerland',
    'AT': 'Austria',
    'BE': 'Belgium',
    'PL': 'Poland',
    'TR': 'Turkey',
    'IL': 'Israel',
    'EG': 'Egypt',
    'NG': 'Nigeria',
    'KE': 'Kenya',
}
PROXY_PREFLIGHT_TIMEOUT = 8
PROXY_PREFLIGHT_URL = 'https://www.google.com/generate_204'
MAX_PROFILE_REGENERATIONS = 5

# ====================================================================
# Derive Sec-CH-UA headers (tidak diubah)
# ====================================================================
import re as _re

def derive_ch_ua_headers(user_agent, chrome_version=CHROME_MAJOR_VERSION):
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
    m = _re.search(r'chrome/(\d+)\.', user_agent or '', _re.IGNORECASE)
    ver = (m.group(1) if m else chrome_version)
    sec_ch_ua = f'"Chromium";v="{ver}", "Not_A Brand";v="24", "Google Chrome";v="{ver}"'
    return {
        'Sec-CH-UA': sec_ch_ua,
        'Sec-CH-UA-Mobile': mobile,
        'Sec-CH-UA-Platform': f'"{platform}"',
    }

# ====================================================================
# Timezone validator (tidak diubah)
# ====================================================================
_SAFE_TZ_CACHE = {}

def _is_valid_timezone(tz):
    if not tz:
        return False
    if tz in _SAFE_TZ_CACHE:
        return _SAFE_TZ_CACHE[tz]
    import os as _os
    candidates = [
        f'/usr/share/zoneinfo/{tz}',
        f'/usr/lib/zoneinfo/{tz}',
        f'/etc/zoneinfo/{tz}',
    ]
    ok = any(_os.path.isfile(p) for p in candidates)
    _SAFE_TZ_CACHE[tz] = ok
    return ok

def _validate_timezone_for_chromium(tz, fallback='America/New_York'):
    if _is_valid_timezone(tz):
        return tz
    if _is_valid_timezone(fallback):
        return fallback
    return 'UTC'

# ====================================================================
# Geo-IP matching (tidak diubah)
# ====================================================================
COUNTRY_TZ_LOCALE = {
    'United States': [
        ('America/New_York', 'en-US'),
        ('America/Chicago', 'en-US'),
        ('America/Denver', 'en-US'),
        ('America/Los_Angeles', 'en-US'),
        ('America/Phoenix', 'en-US'),
        ('America/Anchorage', 'en-US'),
        ('Pacific/Honolulu', 'en-US'),
    ],
    'United Kingdom': [('Europe/London', 'en-GB')],
    'Canada': [
        ('America/Toronto', 'en-CA'),
        ('America/Vancouver', 'en-CA'),
        ('America/Edmonton', 'en-CA'),
        ('America/Halifax', 'en-CA'),
    ],
    'Australia': [
        ('Australia/Sydney', 'en-AU'),
        ('Australia/Melbourne', 'en-AU'),
        ('Australia/Brisbane', 'en-AU'),
        ('Australia/Perth', 'en-AU'),
    ],
    'Ireland': [('Europe/Dublin', 'en-IE')],
    'Germany': [('Europe/Berlin', 'de-DE')],
    'France': [('Europe/Paris', 'fr-FR')],
    'Netherlands': [('Europe/Amsterdam', 'nl-NL')],
    'Japan': [('Asia/Tokyo', 'ja-JP')],
    'Singapore': [('Asia/Singapore', 'en-SG')],
    'India': [('Asia/Kolkata', 'en-IN')],
    'Indonesia': [('Asia/Jakarta', 'id-ID')],
    'Brazil': [('America/Sao_Paulo', 'pt-BR')],
    'South Korea': [('Asia/Seoul', 'ko-KR')],
    'Hong Kong': [('Asia/Hong_Kong', 'zh-HK')],
    'Russia': [('Europe/Moscow', 'ru-RU')],
}

def geo_match_proxy(proxy_entry):
    if not proxy_entry or 'raw_entry' not in proxy_entry:
        return None, None
    info = proxy_entry['raw_entry'].get('info', {}) or {}
    country = (info.get('country') or '').strip()
    if not country:
        return None, None
    matches = COUNTRY_TZ_LOCALE.get(country)
    if not matches:
        for key, val in COUNTRY_TZ_LOCALE.items():
            if key.lower() in country.lower() or country.lower() in key.lower():
                matches = val
                break
    if not matches:
        return None, None
    return random.choice(matches)

# ====================================================================
# Profiles directory (tidak diubah)
# ====================================================================
PROFILES_DIR = Path('profiles')
PROFILES_DIR.mkdir(exist_ok=True)

def profile_path(uid):
    return PROFILES_DIR / f'{uid}.json'

# ====================================================================
# MonetagVerifier (tidak diubah)
# ====================================================================
MONETAG_DOMAINS = [
    'monetag.com', 'monetag', 'propellerads', 'propeller',
    'rtmark.net', 'rtmark', 'omg10.com', 'omg2.com', 'omg3.com',
    'omg4.com', 'omg5.com', 'omg6.com', 'omg7.com', 'omg8.com', 'omg9.com',
    'onclkds.com', 'adskeeper', 'mgid.com',
]

class MonetagVerifier:
    @staticmethod
    def check_publisher_page(page, logger=None):
        try:
            result = page.evaluate('''() => {
                const scripts = [...document.scripts]
                    .map(s => s.src || '')
                    .filter(s => s && (
                        s.toLowerCase().includes('monetag') ||
                        s.toLowerCase().includes('propeller') ||
                        s.toLowerCase().includes('rtmark')
                    ));
                const iframes = [...document.querySelectorAll('iframe')]
                    .map(f => ({
                        src: f.src || f.getAttribute('data-src') || '',
                        width: f.width, height: f.height,
                        visible: !!(f.offsetWidth || f.offsetHeight)
                    }))
                    .filter(f => f.src);
                const cookies = document.cookie.split(';')
                    .map(c => c.trim())
                    .filter(c => {
                        const lower = c.toLowerCase();
                        return lower.includes('sub_id') ||
                               lower.includes('monetag') ||
                               lower.includes('rtmark') ||
                               lower.includes('propeller');
                    });
                const hasMonetag = !!(window.monetag_script_loaded || window.MoneyTagSDK || scripts.length > 0);
                const hasPropellerAds = !!(window.propellerAds || window.propellerAdsSDK ||
                    scripts.some(s => s.includes('propeller')));
                const adLinks = [...document.querySelectorAll('a[href*="trivo.id"], a[href*="omg10.com"], a[href*="omg2.com"], a[href*="monetag"], a[href*="propeller"]')]
                    .map(a => ({ href: a.href, hasImg: !!a.querySelector('img') }));
                return {
                    hasMonetag: hasMonetag,
                    hasPropellerAds: hasPropellerAds,
                    scripts: scripts,
                    iframes: iframes,
                    monetagCookies: cookies,
                    adLinks: adLinks,
                };
            }''')
            if logger:
                logger.info(f'Publisher page: monetag_sdk={result["hasMonetag"]} ad_links={len(result["adLinks"])}')
            return result
        except Exception as e:
            if logger:
                logger.warn(f'Publisher page check failed: {e}')
            return {'hasMonetag': False, 'hasPropellerAds': False, 'scripts': [], 'iframes': [], 'monetagCookies': [], 'adLinks': []}

    @staticmethod
    def check_landing_page(page, logger=None):
        try:
            url = page.url or ''
            on_monetag_domain = any(d in url.lower() for d in [
                'omg10.com', 'omg2.com', 'omg3.com', 'omg4.com', 'omg5.com',
                'omg6.com', 'omg7.com', 'omg8.com', 'omg9.com',
                'monetag.com', 'propellerads.com',
            ])
            result = page.evaluate('''() => {
                const html = document.documentElement.outerHTML || '';
                const rtmarkMatch = html.match(/rtmark\\.net\\/img\\.gif\\?[^"\\s]+/g) || [];
                const userIdMatch = html.match(/userId=([a-f0-9]+)/i);
                const sendBeaconMatch = html.match(/sendBeacon\\(["']https?:\\/\\/[\\w./-]*rtmark\\.net[^"']+["']/g) || [];
                const beaconUrls = html.match(/https?:\\/\\/[\\w.-]*rtmark\\.net[^"\\s'<>]+/g) || [];
                return {
                    rtmarkBeaconFound: rtmarkMatch.length > 0,
                    sendBeaconCall: sendBeaconMatch.length > 0,
                    userId: userIdMatch ? userIdMatch[1] : null,
                    beaconUrls: beaconUrls.slice(0, 5),
                    htmlSize: html.length,
                };
            }''')
            result['onMonetagDomain'] = on_monetag_domain
            result['url'] = url
            if logger:
                if result['rtmarkBeaconFound']:
                    logger.ok(f'rtmark beacon found (userId={result["userId"] or "?"})')
                else:
                    logger.warn(f'rtmark beacon NOT found in HTML')
            return result
        except Exception as e:
            if logger:
                logger.warn(f'Landing page check failed: {e}')
            return {'onMonetagDomain': False, 'rtmarkBeaconFound': False, 'sendBeaconCall': False, 'userId': None, 'beaconUrls': [], 'htmlSize': 0, 'url': page.url if page else ''}

    @staticmethod
    def wait_for_sdk(page, logger=None, timeout_ms=8000):
        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            check = MonetagVerifier.check_publisher_page(page)
            if check['hasMonetag'] or check['hasPropellerAds'] or check['iframes'] or check['adLinks']:
                return True
            time.sleep(0.5)
        return False

def make_request_logger(logger, request_log, uid=''):
    def on_request(req):
        try:
            u = (req.url or '').lower()
            for d in MONETAG_DOMAINS:
                if d in u:
                    entry = {
                        'url': req.url,
                        'method': req.method,
                        'resource_type': req.resource_type,
                        'time': datetime.now().isoformat(),
                        'headers': dict(req.headers) if hasattr(req, 'headers') else {},
                    }
                    request_log.append(entry)
                    logger.ad(f'   {req.method} {req.url[:140]}')
                    break
        except Exception:
            pass
    return on_request

def make_response_logger(logger, response_log, request_log):
    def on_response(resp):
        try:
            u = (resp.url or '').lower()
            for d in MONETAG_DOMAINS:
                if d in u:
                    response_log.append({
                        'url': resp.url,
                        'status': resp.status,
                        'time': datetime.now().isoformat(),
                    })
                    logger.ad(f'   <- HTTP {resp.status} {resp.url[:120]}')
                    break
        except Exception:
            pass
    return on_response

# ====================================================================
# Load proxies from Proxy API (menggantikan proxy.json)
# ====================================================================
def _looks_like_oxylabs_entry(entry):
    if not isinstance(entry, dict):
        return False
    return 'entryPoint' in entry or ('ip' in entry and 'port' in entry)

def _oxylabs_proxy_server(entry):
    host = entry.get('entryPoint') or OXYLABS_ENTRY_HOST
    port = entry.get('port') or OXYLABS_ENTRY_PORT
    return f"http://{host}:{port}"

def _oxylabs_session_id_for_profile(profile_index, ip_hint):
    import uuid
    seed = f"{ip_hint or 'residential'}-{profile_index}-{int(time.time()) % 86400}"
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex[:12]

def _looks_like_911proxy_entry(entry):
    if not isinstance(entry, dict):
        return False
    return entry.get('type') == '911proxy' or (
        'host' in entry and 'port' in entry and 'username' in entry
        and '_session-' in entry.get('username', '')
    )

def _911proxy_generate_session_id():
    import string
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

def _911proxy_build_username(user, area, session_id, life_minutes):
    return f"{user}_area-{area}_session-{session_id}_life-{life_minutes}"

def _911proxy_parse_username(username):
    result = {'user': '', 'area': '', 'session_id': '', 'life': 5}
    if not username:
        return result
    parts = username.split('_')
    result['user'] = parts[0]
    for part in parts[1:]:
        if part.startswith('area-'):
            result['area'] = part[5:]
        elif part.startswith('session-'):
            result['session_id'] = part[8:]
        elif part.startswith('life-'):
            try:
                result['life'] = int(part[5:])
            except ValueError:
                pass
    return result

def _parse_requests_style_proxy(proxy_dict):
    from urllib.parse import urlparse, unquote
    if not isinstance(proxy_dict, dict):
        return None
    raw_url = proxy_dict.get('https') or proxy_dict.get('http') or proxy_dict.get('socks5')
    if not raw_url:
        return None
    try:
        parsed = urlparse(raw_url)
    except Exception as e:
        log.warn('', f'Failed to parse proxy URL {raw_url!r}: {e}')
        return None
    scheme = (parsed.scheme or 'http').lower()
    if scheme in ('socks5h', 'socks4a'):
        scheme = 'socks5'
    if scheme not in ('http', 'https', 'socks5', 'socks4'):
        scheme = 'http'
    host = parsed.hostname
    if not host:
        log.warn('', f'Proxy URL {raw_url!r} has no host')
        return None
    default_port = {'http': 8080, 'https': 443, 'socks5': 1080, 'socks4': 1080}.get(scheme, 8080)
    port = parsed.port or default_port
    username = unquote(parsed.username) if parsed.username else ''
    password = unquote(parsed.password) if parsed.password else ''
    country = (proxy_dict.get('country') or '').strip()
    country_code = (proxy_dict.get('countryCode') or '').strip().upper()
    if not country and country_code:
        country = COUNTRY_CODE_TO_NAME.get(country_code, '')
    isp = (proxy_dict.get('isp') or '').strip()
    server = f"{scheme}://{host}:{port}"
    raw_str = f"{host}:{port}"
    raw_entry = {
        'proxy': raw_str, 'protocol': scheme,
        'info': {'country': country, 'isp': isp or 'requests-style proxy', 'ip': host},
        'source': 'requests-style', 'raw_url': raw_url,
    }
    return {
        'server': server, 'protocol': scheme, 'raw': raw_str, 'raw_entry': raw_entry,
        'country': country, 'isp': isp, 'username': username, 'password': password,
        'session_id': '', 'expected_egress_ip': host,
    }

def _parse_proxy_string(s):
    from urllib.parse import urlparse, unquote
    if not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    if '://' in s:
        return _parse_requests_style_proxy({'https': s, 'http': s})
    parts = s.split(':')
    if len(parts) == 2:
        host, port = parts
        username = password = ''
    elif len(parts) == 4:
        host, port, username, password = parts
    elif len(parts) == 3:
        host, port, username = parts
        password = ''
    else:
        return None
    try:
        port_int = int(port)
    except ValueError:
        return None
    server = f"http://{host}:{port_int}"
    raw_str = f"{host}:{port_int}"
    raw_entry = {
        'proxy': raw_str, 'protocol': 'http',
        'info': {'country': '', 'isp': 'string-format proxy', 'ip': host},
        'source': 'string', 'raw_url': s,
    }
    return {
        'server': server, 'protocol': 'http', 'raw': raw_str, 'raw_entry': raw_entry,
        'country': '', 'isp': '', 'username': username, 'password': password,
        'session_id': '', 'expected_egress_ip': host,
    }

def load_proxies(filter_no_country=True):
    """
    Load proxies from Proxy API (antidetect_browser.load_proxy_config).

    proxy.json TIDAK DIPAKAI LAGI. Semua proxy & AdsPower credentials
    diambil dari Proxy API secara eksklusif.

    v9.2: selain mengisi global ADSPOWER_* dan proxies list, juga
    menyimpan dict adspower_config ke global _ADSPOWER_CONFIG_RESOLVED
    supaya main() bisa meneruskannya langsung ke AntiDetectManager
    (lebih andal daripada membaca env vars satu per satu).
    """
    global ADSPOWER_API_KEY, ADSPOWER_MODE, ADSPOWER_BASE_URL
    global ADSPOWER_PORT, ADSPOWER_PROFILE_ID, ADSPOWER_GROUP_ID
    global _ADSPOWER_CONFIG_RESOLVED, _LAST_PROXY_CONFIG

    log.info('', 'Fetching proxies from Proxy API...')

    try:
        from antidetect_browser import load_proxy_config
        config = load_proxy_config()
    except Exception as e:
        log.error('', f'Proxy API fetch failed: {e}')
        return []

    # v9.2: simpan config lengkap untuk diteruskan ke AntiDetectManager
    _LAST_PROXY_CONFIG = config
    _ADSPOWER_CONFIG_RESOLVED = config.get('adspower', {}) or {}

    # Set AdsPower globals from API config
    adsp_cfg = config.get('adspower', {})
    if adsp_cfg.get('api_key'):
        ADSPOWER_API_KEY = adsp_cfg['api_key']
        os.environ['ADSPOWER_API_KEY'] = ADSPOWER_API_KEY
    if adsp_cfg.get('mode'):
        ADSPOWER_MODE = adsp_cfg['mode']
    if adsp_cfg.get('base_url'):
        ADSPOWER_BASE_URL = adsp_cfg['base_url']
        os.environ['ADSPOWER_API_BASE'] = ADSPOWER_BASE_URL
    if adsp_cfg.get('port'):
        ADSPOWER_PORT = adsp_cfg['port']
        os.environ['ADSPOWER_PORT'] = str(ADSPOWER_PORT)
    if adsp_cfg.get('profile_id'):
        ADSPOWER_PROFILE_ID = adsp_cfg['profile_id']
        os.environ['ADSPOWER_PROFILE_ID'] = ADSPOWER_PROFILE_ID
    if adsp_cfg.get('group_id'):
        ADSPOWER_GROUP_ID = adsp_cfg['group_id']
        os.environ['ADSPOWER_GROUP_ID'] = ADSPOWER_GROUP_ID

    # Convert API proxy dicts to bot_v6 proxy format
    api_proxies = config.get('proxies', [])
    proxies = []
    for p in api_proxies:
        host = p.get('proxy_host', '')
        port = p.get('proxy_port', 0)
        user = p.get('proxy_user', '')
        pwd = p.get('proxy_password', '')
        ptype = p.get('proxy_type', 'http')
        server = f"{ptype}://{host}:{port}"
        proxies.append({
            'server': server, 'protocol': ptype, 'raw': f"{host}:{port}",
            'raw_entry': p, 'country': '', 'isp': '',
            'username': user, 'password': pwd,
            'session_id': '', 'expected_egress_ip': host,
        })

    pid_info = f', profile_id={ADSPOWER_PROFILE_ID}' if ADSPOWER_PROFILE_ID else ''
    gid_info = f', group_id={ADSPOWER_GROUP_ID}' if ADSPOWER_GROUP_ID else ''
    log.ok('', f'Loaded {len(proxies)} proxy(ies) from Proxy API '
                f'(adspower_key={ADSPOWER_API_KEY[:8]}..., '
                f'base={ADSPOWER_BASE_URL}:{ADSPOWER_PORT}{pid_info}{gid_info})')
    return proxies

# v9.2: module-level holders for the resolved AdsPower config + last
# proxy API response. Populated by load_proxies(); consumed by main()
# when constructing AntiDetectManager so we pass the full dict instead
# of separate api_key/profile_id/group_id args.
_ADSPOWER_CONFIG_RESOLVED: dict = {}
_LAST_PROXY_CONFIG: dict = {}

# ====================================================================
# Proxy pre-flight check — v9.9 REWRITE: pakai `requests` (bukan urllib)
# ====================================================================
# BUG FIX #1, #3, #4 (root cause cascade failure):
#   #1  urllib.request.ProxyHandler tidak mengirim header
#       'Proxy-Authorization' pada request HTTPS CONNECT (Python bug
#       tracker #7898). FloppyData residential proxy menolak dengan
#       407 Proxy Authentication Required → URLError. `requests`
#       menangani CONNECT auth dengan benar.
#   #3  Response object tidak pernah .close() → connection leak.
#       `requests.Response` di-close otomatis via context manager.
#   #4  TCP socket test tanpa auth ke proxy host dianggap abuse
#       attempt oleh banyak residential proxy provider dan menambah
#       rate-limit counter. TCP test dihapus — yang penting HTTP
#       tunneling dengan auth, bukan raw TCP.
# --------------------------------------------------------------------
def test_proxy(proxy_entry, timeout=PROXY_PREFLIGHT_TIMEOUT):
    """
    Uji proxy dengan HTTPS CONNECT melalui `requests`.
    Mengirim 'Proxy-Authorization' dengan benar untuk tunneling.
    Mengembalikan (ok, duration_ms, error_msg).

    v9.3:
      - Pakai module-level requests.Session (connection pooling).
      - Pakai context manager `with` → response selalu di-close, tidak
        ada connection leak meskipun terjadi exception.
      - verify=False + InsecureRequestWarning sudah di-suppress globally
        di module-level (lihat header file).
    """
    if not proxy_entry:
        return False, 0, 'no proxy entry'
    from urllib.parse import urlparse
    try:
        import requests as http_requests
    except ImportError:
        # Fallback ke urllib jika requests tidak tersedia (jarang terjadi)
        return _test_proxy_urllib_fallback(proxy_entry, timeout)
    try:
        parsed = urlparse(proxy_entry['server'])
        proxy_host = parsed.hostname
        if not proxy_host:
            return False, 0, 'invalid proxy host'
        proxy_port = parsed.port or (1080 if 'socks' in proxy_entry.get('protocol', 'http').lower() else 8080)
        scheme = (parsed.scheme or proxy_entry.get('protocol') or 'http').lower()
        if scheme not in ('http', 'https', 'socks5', 'socks4'):
            scheme = 'http'
        username = proxy_entry.get('username') or parsed.username or ''
        password = proxy_entry.get('password') or parsed.password or ''

        # Bangun proxy URL dengan auth embedded (requests akan kirim
        # Proxy-Authorization header pada CONNECT request untuk HTTPS).
        if username:
            auth_part = f"{username}:{password}@"
        else:
            auth_part = ''
        proxy_url = f"{scheme}://{auth_part}{proxy_host}:{proxy_port}"
        proxies = {'http': proxy_url, 'https': proxy_url}

        # v9.3: reuse Session untuk connection pooling.
        sess = _get_proxy_test_session()
        if sess is None:
            # Fallback ke ad-hoc request jika Session gagal dibuat
            # (seharusnya tidak pernah terjadi jika requests terinstall).
            sess = http_requests.Session()
        # Update proxies pada session (selalu set karena bisa berganti
        # antar call dengan proxy berbeda).
        sess.proxies.update(proxies)

        start = time.time()
        try:
            # verify=False karena beberapa residential proxy melakukan
            # SSL interception. InsecureRequestWarning sudah di-suppress
            # di module-level, jadi tidak akan muncul di log.
            # Context manager memastikan response di-close → tidak leak.
            with sess.get(
                PROXY_PREFLIGHT_URL,
                timeout=timeout,
                verify=False,
                allow_redirects=False,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; ProxyTest/1.0)'},
                stream=True,  # jangan langsung download body — kita cuma cek status
            ) as resp:
                # 204 (google) atau 200/30x keduanya OK
                ok_status = resp.status_code in (200, 204, 301, 302, 303, 307, 308)
                duration_ms = int((time.time() - start) * 1000)
                if ok_status:
                    return True, duration_ms, None
                # 407 = proxy auth gagal; 502/503 = proxy sibuk/rate-limited
                return False, duration_ms, f'http_status_{resp.status_code}'
        except http_requests.exceptions.ProxyError as e:
            duration_ms = int((time.time() - start) * 1000)
            return False, duration_ms, f'proxy_error: {type(e).__name__}'
        except http_requests.exceptions.ConnectTimeout:
            duration_ms = int((time.time() - start) * 1000)
            return False, duration_ms, 'connect_timeout'
        except http_requests.exceptions.ReadTimeout:
            duration_ms = int((time.time() - start) * 1000)
            return False, duration_ms, 'read_timeout'
        except http_requests.exceptions.SSLError:
            duration_ms = int((time.time() - start) * 1000)
            # SSL error pada HTTPS tunnel biasanya karena proxy
            # melakukan interception. Tapi koneksi tetap berhasil.
            # Tandai OK dengan warning.
            return True, duration_ms, 'ssl_warning'
        except http_requests.exceptions.ConnectionError:
            duration_ms = int((time.time() - start) * 1000)
            return False, duration_ms, 'connection_error'
        except Exception as e:
            duration_ms = int((time.time() - start) * 1000)
            return False, duration_ms, f'{type(e).__name__}: {str(e)[:80]}'
    except Exception as e:
        return False, 0, f'exception: {type(e).__name__}'


def _test_proxy_urllib_fallback(proxy_entry, timeout=PROXY_PREFLIGHT_TIMEOUT):
    """
    Fallback jika library `requests` tidak tersedia.
    Tidak direkomendasikan — urllib tidak menangani CONNECT auth dengan benar.

    v9.3: Tambah ssl._create_unverified_context() ke HTTPSHandler agar
    fallback path juga kompatibel dengan SSL-intercepting residential proxy.
    Tanpa ini, urllib akan menolak sertifikat proxy yang ditandatangani
    sendiri dan pre-flight selalu gagal.
    """
    import urllib.request
    from urllib.parse import urlparse
    try:
        parsed = urlparse(proxy_entry['server'])
        proxy_host = parsed.hostname
        proxy_port = parsed.port or 8080
        username = proxy_entry.get('username') or ''
        password = proxy_entry.get('password') or ''
        if username:
            proxy_url = f"http://{username}:{password}@{proxy_host}:{proxy_port}"
        else:
            proxy_url = f"http://{proxy_host}:{proxy_port}"
        # v9.3: pass ssl._create_unverified_context() ke HTTPSHandler.
        # Ini menonaktifkan certificate verification untuk HTTPS targets,
        # equivalent dengan verify=False pada requests.
        ssl_ctx = ssl._create_unverified_context()
        https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
        proxy_handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
        opener = urllib.request.build_opener(proxy_handler, https_handler)
        req = urllib.request.Request(
            PROXY_PREFLIGHT_URL,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; ProxyTest/1.0)'},
        )
        start = time.time()
        resp = opener.open(req, timeout=timeout)
        try:
            resp.read()
            duration_ms = int((time.time() - start) * 1000)
            return True, duration_ms, None
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except Exception as e:
        return False, 0, f'urllib_fallback_failed: {type(e).__name__}'

# ====================================================================
# 1:1 profile-proxy binding (tidak diubah)
# ====================================================================
_used_proxy_keys = set()
_used_proxy_keys_lock = threading.Lock()
_profile_sync = ProfileSynchronizer(prefer_os='Android')  # v10.11: force Android-only OS

def _proxy_key(proxy):
    if proxy.get('username'):
        return f"auth:{proxy['username']}"
    return f"open:{proxy['server']}"

def make_user_profile(index, proxies):
    rng = random.Random(index * 1000 + 7 + int(time.time()) % 10000)
    # v9.5: index-based deterministic device selection — setiap profile
    # mendapat device unik dari pool (modulo pool size). Tidak lagi
    # rng.choice yang bisa menduplikasi device antar profile.
    if DEVICES:
        device = DEVICES[index % len(DEVICES)]
    else:
        # Fallback bila pool belum di-init
        device = {
            'name': f'Desktop Chrome #{index}',
            'viewport': {'width': 1920, 'height': 1080},
            'ua': _USER_AGENTS_LEGACY[0],
            'os': 'Windows',
        }
    proxy = None
    if proxies:
        with _used_proxy_keys_lock:
            available = [p for p in proxies if _proxy_key(p) not in _used_proxy_keys]
            if available:
                proxy = rng.choice(available)
                _used_proxy_keys.add(_proxy_key(proxy))
    if proxy is None and proxies:
        log.warn('', f'No unused proxies for profile {index}')
        return None
    profile = {
        'id': f'user_{index:03d}',
        'index': index,
        'device': device,
        'referrer': rng.choice(REFERRERS),
        'target_duration': rng.randint(180, 360),
        'max_articles': 1,  # v11.1 UPGRADE 2: Read article only 1x (was rng.randint(2, 4))
        'max_ads_per_article': rng.randint(1, 2),
        'user_agent': device['ua'],
        'proxy': proxy,
    }
    profile['sync_config'] = _profile_sync.build_full_profile(proxy, profile)
    sync_cfg = profile['sync_config']
    profile['timezone'] = sync_cfg.get('timezone', 'America/New_York')
    profile['locale']   = sync_cfg.get('lan', 'en-US')
    profile['user_agent'] = sync_cfg.get('ua', profile['user_agent'])
    # v9.2: propagate country + country_code from sync_config back into
    # the profile dict so downstream code (logging, AdsPower remark,
    # geo_match_proxy) can read it without re-running the GeoIP lookup.
    # profile_sync.build_full_profile() stores these fields after running
    # geo_lookup_proxy() — they may be empty if GeoIP failed.
    profile['country'] = sync_cfg.get('country', '') or proxy.get('country', '')
    profile['country_code'] = sync_cfg.get('country_code', '') or proxy.get('country_code', '')
    # v9.3: HAPUS double-assignment geo_matched (bug lama menulis dua kali
    # dengan logic berbeda — yang pertama broken, yang kedua overwrite
    # dengan nilai authoritatif). Sekarang hanya assign sekali dari
    # raw_entry.info.country (sumber kebenaran).
    # v9.2: also true if sync_config.country was populated via GeoIP.
    raw_info = (proxy.get('raw_entry', {}) or {}).get('info', {}) or {}
    profile['geo_matched'] = bool(raw_info.get('country')) or bool(profile.get('country'))
    return profile

def release_proxy(profile):
    proxy = profile.get('proxy')
    if proxy:
        with _used_proxy_keys_lock:
            _used_proxy_keys.discard(_proxy_key(proxy))

# ====================================================================
# LiveLogger (tidak diubah)
# ====================================================================
class LiveLogger:
    def __init__(self, user_id, device_name):
        self.uid = user_id
        self.device = device_name

    def _emit(self, fn, msg):
        fn(self.uid, msg)
        try:
            ts = datetime.now().strftime('%H:%M:%S')
            push_log(f'{ts} [{self.uid}] {msg}')
        except Exception:
            pass

    def start(self, m): self._emit(log.start, m)
    def info(self, m):  self._emit(log.info, m)
    def ok(self, m):    self._emit(log.ok, m)
    def warn(self, m):  self._emit(log.warn, m)
    def error(self, m): self._emit(log.error, m)
    def step(self, m):  self._emit(log.step, m)
    def ad(self, m):    self._emit(log.ad, m)
    def article(self, m): self._emit(log.article, m)

def set_phase(phase, progress=None):
    update_state(current_phase=phase)
    if progress is not None:
        update_state(progress=progress)

# ====================================================================
# Bot logic — discover articles / ads (tidak diubah)
# ====================================================================
def discover_articles(page, max_count=10):
    selectors = [
        '#articleGrid .article-card',
        '#featuredGrid .article-card',
        '.article-card',
    ]
    found = []
    seen_ids = set()
    for sel in selectors:
        if len(found) >= max_count:
            break
        try:
            locs = page.locator(sel).all()
            for loc in locs:
                if len(found) >= max_count:
                    break
                try:
                    if not loc.is_visible():
                        continue
                    data_id = loc.get_attribute('data-id') or ''
                    if not data_id:
                        data_id = loc.inner_text()[:60]
                    if data_id in seen_ids:
                        continue
                    seen_ids.add(data_id)
                    found.append(loc)
                except Exception:
                    continue
        except Exception:
            continue
    return found

# ====================================================================
# v9.4 — NATIVE ADSTERRA BANNER DETECTION
# ====================================================================
# AdSterra Native Banner sering muncul di bagian bawah halaman home
# dan di bagian bawah modal article. Selector di bawah sudah mencakup
# pola umum yang dipakai AdSterra/Monetag/PropellerAds untuk native ads.

ADSTERRA_NATIVE_CONTAINER_SELECTORS = [
    # ============ v9.5 NEW: EffectiveCPMNetwork native banner ============
    # Pola: <div id="container-<hash>"></div> di home bottom
    # Setelah invoke.js dari pl*.effectivecpmnetwork.com di-load,
    # script inject ad content ke dalam div container-<hash>.
    'div[id^="container-"]',
    'div[id*="effectivecpmnetwork"]',
    'div[class*="effectivecpmnetwork"]',
    # Iframe injected oleh effectivecpmnetwork invoke.js
    'iframe[src*="effectivecpmnetwork"]',
    'iframe[src*="cpmnetwork"]',

    # Direct AdSterra native container IDs/classes
    'div[id*="adsterra"]',
    'div[id*="adsterra-native"]',
    'div[id^="ss-"]',                  # AdSterra short-link container
    'div[class*="adsterra"]',
    'div[class*="adsslot"]',
    'div[class*="native-ad"]',
    'div[class*="native_ad"]',
    'div[class*="nativebanner"]',
    'div.native-ad',
    # Iframe-based native ads (AdSterra sering pakai iframe)
    'iframe[src*="adsterra"]',
    'iframe[src*="qualitypage"]',
    'iframe[src*="propellerads"]',
    'iframe[src*="monetag"]',
    'iframe[src*="omg"]',
    # Container dengan data attributes khas AdSterra
    'div[data-zone-id]',
    'div[data-key]',
    'div[data-zone]',
    # Generic ad slots yang sering diisi AdSterra
    'ins.adsbygoogle',
    'div[id*="monetag"]',
    'div[id*="propeller"]',
    'div[id*="adsterra"]',
    # Wrapper footer/home bottom banner
    '#footerBanner',
    '#bottomBanner',
    '#homeBottomAd',
    '#articleBottomAd',
    '.footer-banner',
    '.bottom-ad',
    '.home-bottom-ad',
    '.article-bottom-ad',
]

ADSTERRA_CLICKABLE_SELECTORS = [
    'a[href][target="_blank"]',
    'a[href]:not([href^="javascript:"]):not([href="#"]):not([href=""])',
    'a[href]',
    'a.native-ad-link',
    'a[class*="ad-link"]',
    'a[class*="cta"]',
    'a[class*="button"]',
    'div[onclick]',
    'button[onclick]',
    'span[onclick]',
    'div[role="button"]',
    'div[role="link"]',
]

# v9.5: Tanda tangan iklan yang diperluas — sekarang termasuk
# EffectiveCPMNetwork dan domain-domain ad server terkait.
ADSTERRA_SIGNATURES = (
    'adsterra', 'qualitypage', 'propellerads', 'propeller',
    'monetag', 'rtmark', 'data-zone-id', 'data-key',
    'ss-', 'native-ad', 'native_banner', 'adsslot',
    'adsbygoogle', 'omg', 'mgid', 'adskeeper', 'onclkds',
    # v9.5 NEW:
    'effectivecpmnetwork', 'cpmnetwork', 'invoke.js',
    'container-',  # prefix div id container-<hash>
    'pl30117744',  # example publisher ID dari sample HTML user
    'pl30117743',  # example publisher ID untuk social banner
)

# v9.5 NEW: domain ad networks yang dianggap tracking beacon valid.
# Used by click_native_adsterra_and_process() untuk verify tracking.
EFFECTIVE_CPM_DOMAINS = (
    'effectivecpmnetwork.com',
    'cpmnetwork.com',
    'pl30117744',  # sample publisher
    'pl30117743',  # sample publisher (social)
)


def _is_in_bottom_section(page, box, threshold_ratio=0.55):
    """
    Return True jika elemen berada di bagian bawah halaman.
    "Bawah" = posisi Y absolut elemen >= threshold_ratio * total_height,
    ATAU posisi Y relatif viewport >= threshold_ratio * viewport_height.
    Fail-open (return True) bila tidak bisa diukur agar tidak skip ad valid.
    """
    if not box:
        return True
    try:
        info = page.evaluate('''() => ({
            scrollY: window.scrollY || window.pageYOffset || 0,
            vh: window.innerHeight || document.documentElement.clientHeight || 0,
            total: document.documentElement.scrollHeight || 0
        })''')
        scroll_y = info.get('scrollY', 0)
        vh = info.get('vh', 0)
        total = info.get('total', 0)
        elem_y_abs = scroll_y + (box.get('y') or 0)
        if total > 0 and elem_y_abs >= total * threshold_ratio:
            return True
        if vh > 0 and (box.get('y') or 0) >= vh * threshold_ratio:
            return True
    except Exception:
        pass
    return True


def _has_adsterra_signature(locator):
    """Cek apakah HTML dalam locator mengandung signature AdSterra/Monetag.
    v12.0 Issue 5 FIX: Relaxed check — some ad containers may not have
    Adsterra signatures in their HTML yet because the script is still
    loading. Accept containers that match known Adsterra selectors even
    without signature verification.
    """
    try:
        html = locator.inner_html(timeout=1500)
    except Exception:
        # v12.0: Don't reject just because inner_html() fails —
        # the container might still be loading its content
        return None  # indeterminate — let caller decide based on selector
    if not html or len(html.strip()) < 5:
        # v12.0: Empty container might still be loading — return indeterminate
        return None
    html_lower = html.lower()
    return any(sig in html_lower for sig in ADSTERRA_SIGNATURES)


def discover_native_adsterra_ads(page, max_count=4, location='auto'):
    """
    Deteksi native AdSterra banner di bagian BOTTOM halaman.

    Args:
        page: Playwright page object
        max_count: maksimal jumlah ad yang dikembalikan
        location:
            'auto'         — terima semua posisi
            'home_bottom'  — hanya ad di bagian bawah homepage
            'article_bottom' — hanya ad di bagian bawah modal article

    Returns:
        list of dict: {
            'locator':   <Locator> clickable element,
            'container': <Locator> parent container,
            'href':      str,
            'box':       dict | None,
            'label':     str (untuk log),
        }
    """
    found = []
    seen_hrefs = set()
    seen_locator_ids = set()

    for csel in ADSTERRA_NATIVE_CONTAINER_SELECTORS:
        if len(found) >= max_count:
            break
        try:
            conts = page.locator(csel).all()
        except Exception:
            continue
        for cont in conts:
            if len(found) >= max_count:
                break
            try:
                if not cont.is_visible(timeout=1500):
                    continue
                # Filter berdasarkan lokasi (bottom section)
                if location in ('home_bottom', 'article_bottom'):
                    try:
                        cbox = cont.bounding_box()
                    except Exception:
                        cbox = None
                    if not _is_in_bottom_section(page, cbox, threshold_ratio=0.55):
                        continue
                # Verify signature AdSterra (relaxed — skip bila tidak yakin)
                # v12.0 Issue 5 FIX: _has_adsterra_signature now returns None
                # for indeterminate cases (script still loading). We accept
                # None (indeterminate) as well as True (confirmed).
                sig_result = _has_adsterra_signature(cont)
                if sig_result is False:
                    # Definitely NOT an ad — skip
                    # Bila selector-nya sudah sangat spesifik (id/class adsterra),
                    # tetap terima; bila selector generic, skip.
                    if not any(tok in csel.lower() for tok in
                               ('adsterra', 'adsslot', 'native-ad', 'data-zone', 'data-key',
                                'footer', 'bottom', 'monetag', 'propeller', 'omg',
                                'adsbygoogle', 'ss-', 'container-')):
                        continue
                # Skip placeholder kosong
                try:
                    html = cont.inner_html(timeout=1500) or ''
                except Exception:
                    html = ''
                if 'advertisement space available' in html.lower():
                    continue
                # Cari clickable child
                for clksel in ADSTERRA_CLICKABLE_SELECTORS:
                    if len(found) >= max_count:
                        break
                    try:
                        clks = cont.locator(clksel).all()
                    except Exception:
                        continue
                    for clk in clks:
                        if len(found) >= max_count:
                            break
                        try:
                            if not clk.is_visible(timeout=1200):
                                continue
                            clk_id = id(clk)
                            if clk_id in seen_locator_ids:
                                continue
                            href = clk.get_attribute('href') or ''
                            if href.startswith('javascript:') or href in ('#', ''):
                                # Untuk div[onclick] / button tanpa href, tetap terima
                                if not any(s in clksel for s in ('onclick', 'button', 'role=')):
                                    continue
                                href = ''
                            # FIX: Protocol-relative "//" URLs are valid Adsterra ad links.
                            # Don't skip them — they resolve to https://... at runtime.
                            # Also normalize for dedup: treat "//" same as "https://"
                            if href and href in seen_hrefs:
                                continue
                            if href:
                                seen_hrefs.add(href)
                                # Also add normalized form to avoid duplicates
                                if href.startswith('//'):
                                    seen_hrefs.add('https:' + href)
                            seen_locator_ids.add(clk_id)
                            try:
                                box = clk.bounding_box()
                            except Exception:
                                box = None
                            found.append({
                                'locator': clk,
                                'container': cont,
                                'href': href,
                                'box': box,
                                'label': f'{csel} > {clksel}',
                            })
                        except Exception:
                            continue
            except Exception:
                continue
    return found


# ====================================================================
# v9.5 — EFFECTIVECPMNETWORK NATIVE & SOCIAL BANNER DETECTION
# ====================================================================
# Pola banner EffectiveCPMNetwork yang spesifik (dari sample user):
#
#   NATIVE (home bottom):
#     <script async src="https://plXXXXX.effectivecpmnetwork.com/<hash>/invoke.js"></script>
#     <div id="container-<hash>"></div>
#   → invoke.js inject ad content ke dalam div container-<hash>.
#     Clickable element bisa berupa: <a>, <iframe>, atau <div onclick>
#     yang di-inject script ke dalam container.
#
#   SOCIAL (article view):
#     <script src="https://plXXXXX.effectivecpmnetwork.com/<a2/d1/05>/...js"></script>
#   → Tidak ada container-<hash> eksplisit. Script langsung inject
#     iframe/widget social bar ke posisi script tag berada.
#     Clickable element biasanya iframe atau div wrapper yang baru
#     di-create oleh script.

EFFECTIVE_CPM_SCRIPT_SELECTOR = 'script[src*="effectivecpmnetwork.com"]'
EFFECTIVE_CPM_CONTAINER_SELECTOR = 'div[id^="container-"]'
EFFECTIVE_CPM_INVOKE_SELECTOR = 'script[src*="/invoke.js"][src*="effectivecpmnetwork"]'


def _find_effective_cpm_containers(page, max_count=4, location='auto'):
    """
    Cari div[id^="container-"] yang berdekatan dengan script
    effectivecpmnetwork. Return list of locator container.
    """
    containers = []
    try:
        conts = page.locator(EFFECTIVE_CPM_CONTAINER_SELECTOR).all()
    except Exception:
        return containers
    for cont in conts:
        if len(containers) >= max_count:
            break
        try:
            if not cont.is_visible(timeout=1500):
                continue
            # Bila location filter aktif, cek posisi bottom
            if location in ('home_bottom', 'article_bottom'):
                try:
                    cbox = cont.bounding_box()
                except Exception:
                    cbox = None
                if not _is_in_bottom_section(page, cbox, threshold_ratio=0.55):
                    continue
            # Cek apakah ada script effectivecpmnetwork di document
            # (cukup satu kali cek saja, tidak perlu per-container)
            containers.append(cont)
        except Exception:
            continue
    return containers


def _find_effective_cpm_scripts(page, max_count=4, location='auto'):
    """
    Cari script[src*="effectivecpmnetwork.com"] di halaman. Return
    list of locator script. Script ini biasanya inject iframe/widget
    setelah dirinya — kita pakai ini untuk locate social banner.
    """
    scripts = []
    try:
        scs = page.locator(EFFECTIVE_CPM_SCRIPT_SELECTOR).all()
    except Exception:
        return scripts
    for sc in scs:
        if len(scripts) >= max_count:
            break
        try:
            # Script tag sendiri tidak visible (display:none by default),
            # jadi kita pakai parent/sibling untuk dapat visible wrapper.
            # Ambil parent element dari script sebagai anchor.
            parent = sc.locator('xpath=..')
            try:
                parent_box = parent.bounding_box()
            except Exception:
                parent_box = None
            if location in ('home_bottom', 'article_bottom') and parent_box:
                if not _is_in_bottom_section(page, parent_box, threshold_ratio=0.55):
                    continue
            scripts.append({
                'script': sc,
                'parent': parent,
                'parent_box': parent_box,
            })
        except Exception:
            continue
    return scripts


def discover_effective_cpm_native_ads(page, max_count=4, location='home_bottom'):
    """
    Deteksi native banner EffectiveCPMNetwork di halaman home bottom.

    Pola: div[id^="container-"] + script effectivecpmnetwork/invoke.js
    Container awalnya kosong, lalu invoke.js inject ad content (iframe
    atau <a> link).

    Returns:
        list of dict {locator, container, href, box, label}
    """
    found = []
    seen_hrefs = set()

    # Cek apakah halaman punya script effectivecpmnetwork/invoke.js
    try:
        invoke_scripts = page.locator(EFFECTIVE_CPM_INVOKE_SELECTOR).count()
    except Exception:
        invoke_scripts = 0

    if invoke_scripts == 0:
        # Tidak ada invoke.js → bukan halaman dengan native banner ECPM
        return found

    # Cari semua div container-<hash>
    containers = _find_effective_cpm_containers(page, max_count=max_count,
                                                location=location)
    for cont in containers:
        if len(found) >= max_count:
            break
        try:
            # Klik container langsung sering tidak efek — kita cari
            # clickable child (a, iframe, div[onclick]) yang di-inject
            # oleh invoke.js.
            for clksel in ADSTERRA_CLICKABLE_SELECTORS + [
                'iframe',
                'div[onclick]',
                'a[href*="effectivecpmnetwork"]',
                'a[href*="cpmnetwork"]',
            ]:
                if len(found) >= max_count:
                    break
                try:
                    clks = cont.locator(clksel).all()
                except Exception:
                    continue
                for clk in clks:
                    if len(found) >= max_count:
                        break
                    try:
                        if not clk.is_visible(timeout=1200):
                            continue
                        href = clk.get_attribute('href') or ''
                        if href.startswith('javascript:') or href in ('#', ''):
                            if not any(s in clksel for s in ('onclick', 'iframe', 'button', 'role=')):
                                continue
                            href = ''
                        if href and href in seen_hrefs:
                            continue
                        if href:
                            seen_hrefs.add(href)
                        try:
                            box = clk.bounding_box()
                        except Exception:
                            box = None
                        # Bila clk adalah iframe, kita pakai iframe box
                        # sebagai click target.
                        if clksel == 'iframe' and not box:
                            continue
                        found.append({
                            'locator': clk,
                            'container': cont,
                            'href': href,
                            'box': box,
                            'label': f'effectivecpmnative:{clksel}',
                        })
                    except Exception:
                        continue
            # Bila tidak ada clickable child yang visible (container
            # masih kosong / invoke.js belum selesai inject), gunakan
            # container itu sendiri sebagai click target — kadang
            # invoke.js pasang onclick handler di container.
            if len(found) < max_count:
                try:
                    cbox = cont.bounding_box()
                    if cbox and cbox.get('width', 0) > 50 and cbox.get('height', 0) > 30:
                        found.append({
                            'locator': cont,
                            'container': cont,
                            'href': '',
                            'box': cbox,
                            'label': 'effectivecpmnative:container-click',
                        })
                except Exception:
                    pass
        except Exception:
            continue
    return found


def discover_effective_cpm_social_ads(page, max_count=4, location='article_bottom'):
    """
    Deteksi social banner EffectiveCPMNetwork di halaman article view.

    Pola: <script src="https://plXXXXX.effectivecpmnetwork.com/a2/d1/05/...js">
    Script langsung inject iframe/widget social bar (share buttons, social
    feed) tanpa container-<hash> eksplisit.

    Returns:
        list of dict {locator, container, href, box, label}
    """
    found = []
    seen_hrefs = set()

    # Cari script effectivecpmnetwork (non-invoke, karena social pakai path
    # seperti /a2/d1/05/...js bukan /invoke.js)
    script_entries = _find_effective_cpm_scripts(page, max_count=max_count,
                                                 location=location)
    for entry in script_entries:
        if len(found) >= max_count:
            break
        sc = entry['script']
        parent = entry['parent']
        parent_box = entry['parent_box']
        try:
            src = sc.get_attribute('src') or ''
        except Exception:
            src = ''
        # Skip script invoke.js (itu native banner, bukan social)
        if '/invoke.js' in src:
            continue

        # Cari clickable element di dalam parent atau sibling
        # Social banner biasanya inject iframe atau div wrapper.
        candidates = []
        for sel in ['iframe[src]', 'iframe', 'a[href]',
                    'a[href][target="_blank"]', 'div[onclick]',
                    'div[role="button"]', 'div[role="link"]']:
            try:
                els = parent.locator(sel).all()
            except Exception:
                continue
            for el in els:
                try:
                    if not el.is_visible(timeout=1000):
                        continue
                    box = el.bounding_box()
                    if not box or box.get('width', 0) < 30 or box.get('height', 0) < 20:
                        continue
                    href = el.get_attribute('href') or ''
                    candidates.append({'locator': el, 'box': box, 'href': href, 'sel': sel})
                except Exception:
                    continue

        # Bila parent tidak punya clickable, cari di sibling berikutnya
        # (script sering inject widget tepat setelah dirinya).
        if not candidates:
            try:
                # next sibling element
                next_sib = parent.locator('xpath=following-sibling::*[1]')
                if next_sib.count() > 0:
                    for sel in ['iframe[src]', 'iframe', 'a[href]',
                                'div[onclick]', 'div[role="button"]']:
                        try:
                            els = next_sib.locator(sel).all()
                        except Exception:
                            continue
                        for el in els:
                            try:
                                if not el.is_visible(timeout=800):
                                    continue
                                box = el.bounding_box()
                                if not box or box.get('width', 0) < 30:
                                    continue
                                href = el.get_attribute('href') or ''
                                candidates.append({'locator': el, 'box': box,
                                                   'href': href, 'sel': f'sibling:{sel}'})
                            except Exception:
                                continue
            except Exception:
                pass

        # Bila masih tidak ada, klik parent box langsung (sering
        # social bar pasang onclick di wrapper)
        if not candidates and parent_box and parent_box.get('width', 0) > 50:
            candidates.append({
                'locator': parent, 'box': parent_box, 'href': '',
                'sel': 'parent-wrapper-click',
            })

        for c in candidates:
            if len(found) >= max_count:
                break
            if c['href'] and c['href'] in seen_hrefs:
                continue
            if c['href']:
                seen_hrefs.add(c['href'])
            found.append({
                'locator': c['locator'],
                'container': parent,
                'href': c['href'],
                'box': c['box'],
                'label': f"effectivecpmsocial:{c['sel']}",
            })
    return found


# ====================================================================
# v9.4 — HUMAN-LIKE INTERACTION DENGAN HALAMAN IKLAN
# ====================================================================

def _click_inside_ad_page(new_page, logger, context, request_log, depth=0):
    """
    Cari elemen clickable di halaman iklan (CTA, button, link) lalu klik
    dengan gaya manusia. Bila klik membuka tab baru, proses rekursif
    dengan max depth=1.

    Returns True bila ada inner click yang dilakukan.
    """
    import human_input as hi

    inner_click_selectors = [
        'a[href][target="_blank"]',
        'a.btn',
        'a.button',
        'a[class*="cta"]',
        'a[class*="button"]',
        'a[class*="download"]',
        'a[class*="learn"]',
        'a[class*="more"]',
        'a[class*="next"]',
        'a[href]:not([href^="javascript:"]):not([href="#"]):not([href=""])',
        'a[href]',
        'button',
        'button[onclick]',
        'div[onclick]',
        'div[role="button"]',
        'div[role="link"]',
        'input[type="submit"]',
        'input[type="button"]',
    ]

    candidates = []
    for sel in inner_click_selectors:
        try:
            els = new_page.locator(sel).all()
        except Exception:
            continue
        for el in els:
            try:
                if not el.is_visible(timeout=900):
                    continue
                box = el.bounding_box()
                if not box or box.get('width', 0) < 10 or box.get('height', 0) < 10:
                    continue
                # Skip nav bar / footer menu yang sangat lebar & pendek
                if box.get('width', 0) > 1200 and box.get('height', 0) < 40:
                    continue
                href = el.get_attribute('href') or ''
                try:
                    cur_url = new_page.url or ''
                    if href and href.startswith('#'):
                        continue
                    if href and cur_url and href.rstrip('/').lower() == cur_url.rstrip('/').lower():
                        continue
                except Exception:
                    pass
                candidates.append({'locator': el, 'box': box, 'href': href, 'sel': sel})
            except Exception:
                continue
        if len(candidates) >= 14:
            break

    if not candidates:
        logger.ad('   Tidak ada elemen clickable di halaman iklan untuk inner-click')
        return False

    # Pilih kandidat: sort by area desc, lalu pick random dari top-5
    candidates.sort(key=lambda c: (c['box'].get('width', 0) * c['box'].get('height', 0)),
                    reverse=True)
    top_n = min(5, len(candidates))
    chosen = random.choice(candidates[:top_n])

    logger.ad(f'   Inner-click elemen iklan ({chosen["sel"]}, href={chosen["href"][:60]})')

    inner_new_page = None
    try:
        with context.expect_page(timeout=5000) as inner_info:
            try:
                chosen['locator'].scroll_into_view_if_needed(timeout=2500)
                time.sleep(random.uniform(0.4, 0.9))
                try:
                    chosen['box'] = chosen['locator'].bounding_box() or chosen['box']
                except Exception:
                    pass
                pyautogui_click_element(chosen['box'], logger, page=new_page)
            except Exception as e:
                logger.warn(f'   pyautogui inner-click gagal ({e}); fallback .click()')
                try:
                    chosen['locator'].click(timeout=3000)
                except Exception:
                    pass
        inner_new_page = inner_info.value
    except Exception as e:
        # Tidak membuka tab baru — klik tetap dihitung engagement
        logger.ad(f'   Inner click tidak membuka tab baru ({str(e)[:80]})')

    if inner_new_page and inner_new_page != new_page:
        try:
            inner_url = inner_new_page.url or ''
            logger.ad(f'   Tab dalam iklan terbuka: {inner_url[:80]}')
            if depth < 1:
                try:
                    inject_stealth_to_page(inner_new_page)
                except Exception:
                    pass
                _humanlike_interact_with_ad_page(
                    inner_new_page, logger, context, request_log,
                    depth=depth + 1, max_depth=1
                )
        except Exception as e:
            logger.warn(f'   Proses tab dalam iklan gagal: {e}')
        finally:
            try:
                inner_new_page.close()
                logger.ad('   Tab dalam iklan ditutup')
            except Exception:
                pass

    time.sleep(random.uniform(0.8, 1.6))
    return True


# ====================================================================
# v11.1 UPGRADE 1 — Age Verification Handler
# ====================================================================
def handle_age_verification(page, logger):
    """
    v11.1 UPGRADE 1 — Detect and click through age verification overlays/dialogs.

    When an ad tab opens and shows an age verification prompt (common on
    adult/ad-related landing pages), this function detects the age gate
    and clicks the confirmation button automatically.

    Detection strategy:
      1. Look for common age gate container selectors (class/id patterns)
      2. Search for buttons/links/inputs with confirmation text patterns
         ("Yes", "I am 18", "18+", "Enter", "Confirm", "I agree",
          "Verify", "Continue" — case-insensitive)
      3. Click the found button with a human-like delay (0.5-1.5s)

    Args:
        page: Playwright Page object (ad tab)
        logger: LiveLogger instance

    Returns:
        True if age verification was found and clicked, False otherwise
    """
    # Age gate container selectors — common patterns used by age verification systems
    AGE_GATE_CONTAINER_SELECTORS = [
        '.age-gate', '.age-verification', '.age-verify',
        '#age-gate', '#ageVerification', '#ageVerify',
        '[class*="age"]', '[class*="verify"]',
        '[id*="age"]', '.modal-age', '.adult-warning',
        '[class*="age-gate"]', '[class*="age-verify"]',
        '[id*="age-gate"]', '[id*="age-verify"]',
        '.age-confirmation', '#ageConfirmation',
        '[class*="adult"]', '[class*="restricted"]',
        '.restricted-modal', '#restrictedModal',
    ]

    # Text patterns to look for in clickable elements (case-insensitive)
    AGE_VERIFY_TEXT_PATTERNS = [
        'yes', 'i am 18', '18+', 'enter', 'confirm',
        'i agree', 'verify', 'continue', 'i\'m 18',
        'im 18', 'over 18', 'age verification',
        'am 18', 'older', 'of age',
    ]

    # Step 1: Check if any age gate container exists
    gate_found = False
    for sel in AGE_GATE_CONTAINER_SELECTORS:
        try:
            count = page.locator(sel).count()
            if count > 0:
                # Verify at least one is visible
                for i in range(count):
                    try:
                        if page.locator(sel).nth(i).is_visible(timeout=1000):
                            gate_found = True
                            break
                    except Exception:
                        continue
                if gate_found:
                    break
        except Exception:
            continue

    # Step 2: Also check page content for age-related text in body
    if not gate_found:
        try:
            body_text = page.evaluate('() => (document.body.innerText || "").toLowerCase()')
            age_keywords = ['age verification', 'age gate', 'are you 18', 'are you over 18',
                            'confirm your age', 'verify your age', 'adult content',
                            'must be 18', 'must be 21', 'restricted content']
            if any(kw in body_text for kw in age_keywords):
                gate_found = True
        except Exception:
            pass

    if not gate_found:
        return False

    logger.ad('   Age verification overlay terdeteksi — mencari tombol konfirmasi...')

    # Step 3: Find and click the confirmation button
    # Try multiple strategies to locate the confirm button
    click_selectors = [
        'button', 'a', 'input[type="submit"]', 'input[type="button"]',
        'div[role="button"]', 'span[role="button"]',
        'div[onclick]', 'span[onclick]',
    ]

    for sel in click_selectors:
        try:
            elements = page.locator(sel).all()
            for el in elements:
                try:
                    if not el.is_visible(timeout=800):
                        continue
                    # Get element text content
                    try:
                        text = (el.inner_text(timeout=500) or '').lower().strip()
                    except Exception:
                        text = ''
                    # Also check value attribute (for input elements)
                    try:
                        value = (el.get_attribute('value') or '').lower().strip()
                    except Exception:
                        value = ''
                    # Also check aria-label
                    try:
                        aria = (el.get_attribute('aria-label') or '').lower().strip()
                    except Exception:
                        aria = ''
                    # Also check title attribute
                    try:
                        title = (el.get_attribute('title') or '').lower().strip()
                    except Exception:
                        title = ''

                    combined_text = f'{text} {value} {aria} {title}'

                    # Check if any age verification text pattern matches
                    if any(pattern in combined_text for pattern in AGE_VERIFY_TEXT_PATTERNS):
                        # Human-like delay before clicking
                        time.sleep(random.uniform(0.5, 1.5))
                        try:
                            el.click(timeout=3000)
                            logger.ad(f'   Age verification clicked: "{text[:40]}" (selector: {sel})')
                            # Brief wait after clicking for page to respond
                            time.sleep(random.uniform(1.0, 2.0))
                            return True
                        except Exception as click_err:
                            logger.warn(f'   Age verification click gagal: {click_err}')
                            continue
                except Exception:
                    continue
        except Exception:
            continue

    # Step 4: If no text-match button found, try clicking within age gate containers
    for container_sel in AGE_GATE_CONTAINER_SELECTORS:
        try:
            containers = page.locator(container_sel).all()
            for cont in containers:
                try:
                    if not cont.is_visible(timeout=800):
                        continue
                    # Find any button-like element inside the container
                    for btn_sel in ['button', 'a', 'input[type="submit"]',
                                    'div[role="button"]', 'div[onclick]']:
                        try:
                            btns = cont.locator(btn_sel).all()
                            if btns:
                                # Click the first visible one
                                for btn in btns:
                                    try:
                                        if btn.is_visible(timeout=500):
                                            time.sleep(random.uniform(0.5, 1.5))
                                            btn.click(timeout=3000)
                                            btn_text = ''
                                            try:
                                                btn_text = btn.inner_text(timeout=300)[:40]
                                            except Exception:
                                                pass
                                            logger.ad(f'   Age verification clicked (container fallback): '
                                                      f'"{btn_text}" ({container_sel} > {btn_sel})')
                                            time.sleep(random.uniform(1.0, 2.0))
                                            return True
                                    except Exception:
                                        continue
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            continue

    logger.ad('   Age verification terdeteksi tapi tombol konfirmasi tidak ditemukan')
    return False


def _humanlike_interact_with_ad_page(new_page, logger, context, request_log,
                                      depth=0, max_depth=1, ad_page_enter_time=None,
                                      deadline=None):
    """
    Interaksi tab iklan secara manusiawi:
      1. Wait DOM + inject stealth + solve antibot (jika ada)
      2. Scroll bertingkat dengan pause acak
      3. Mouse move dengan tremor + hesitation
      4. Hover acak di elemen-elemen halaman
      5. Idle / keystroke acak
      6. Inner-click elemen di halaman iklan (deep click)
      7. Tunggu tracking beacon

    Args:
        ad_page_enter_time: timestamp saat masuk ad page (dari caller).
            Bila None, fallback ke time.time() (view time tidak akurat tapi
            tidak crash).
        deadline: v12.0 Issue 3 FIX — absolute time deadline (time.time()).
            If current time exceeds deadline, bail immediately.
    """
    import human_input as hi

    # v12.0 Issue 3 FIX: Helper to check deadline
    def _bail():
        return deadline is not None and time.time() >= deadline

    # 1. Settle + stealth + antibot
    time.sleep(random.uniform(1.2, 2.2))
    if _bail(): return False
    try:
        new_page.bring_to_front()
    except Exception:
        pass
    try:
        inject_stealth_to_page(new_page)
    except Exception:
        pass
    try:
        ab_result = solve_antibot_if_present(new_page, logger=logger, max_total_time_s=25)
        if ab_result.get('detected') and ab_result.get('solved'):
            logger.ok('   Antibot halaman iklan terpecahkan (initial)')
            time.sleep(random.uniform(1.0, 2.0))
            try:
                inject_stealth_to_page(new_page)
            except Exception:
                pass
    except Exception:
        pass

    # v11.1 UPGRADE 1 — Age Verification: check for age gate and click Yes/Confirm
    try:
        handle_age_verification(new_page, logger)
    except Exception:
        pass

    if _bail(): return False

    try:
        # v10.11.2 UPGRADE — AD TAB LOAD TIMEOUT: 15s → 10s.
        # Saat masuk _humanlike_interact_with_ad_page, tab sudah pasti loaded
        # (caller _wait_for_ad_tab_loaded sudah verify). Call ini biasanya
        # no-op (langsung return). Tapi bila ada re-navigation (mis. ad
        # redirect ke landing page lain), tetap dibatasi 10s max.
        new_page.wait_for_load_state('domcontentloaded',
                                     timeout=AD_TAB_LOAD_TIMEOUT_S * 1000)
    except Exception:
        pass

    # v12.0 Issue 6 FIX: Prioritize CPM view time over random clicks.
    # Calculate remaining view time FIRST, then decide if there's budget for clicks.
    if ad_page_enter_time is not None and ad_page_enter_time > 0:
        elapsed_in_ad = time.time() - ad_page_enter_time
    else:
        elapsed_in_ad = 0
    target_view_time = random.uniform(AD_VIEW_TIME_MIN_S, AD_VIEW_TIME_MAX_S)
    remaining_view = max(0, target_view_time - elapsed_in_ad)

    # 2. Scroll bertingkat (manusia tidak scroll linear)
    if _bail(): return False
    logger.ad('   Sesi scroll human-like di halaman iklan')
    try:
        # v12.0 Issue 6: Scale scroll time to fit within remaining view time
        scroll_duration = min(random.uniform(AD_VIEW_TIME_MIN_S * 0.3, AD_VIEW_TIME_MAX_S * 0.3),
                              remaining_view * 0.4 if remaining_view > 0 else 6.0)
        hi.mixed_browse_session(duration_s=scroll_duration)
    except Exception as e:
        logger.warn(f'   Sesi scroll gagal: {e}')

    # 3. Mouse move dengan tremor + hesitation
    if _bail(): return False
    try:
        for _ in range(random.randint(2, 4)):
            if _bail(): break
            dx = random.randint(-300, 300)
            dy = random.randint(-200, 200)
            hi.human_move_relative(dx, dy, duration=random.uniform(0.5, 1.2))
            time.sleep(random.uniform(0.5, 1.2))
    except Exception:
        pass

    # 4. Hover elemen acak di halaman (manusia sering hover sebelum klik)
    if _bail(): return False
    try:
        hover_targets = new_page.locator('a, button, div[onclick], img, [role="button"]').all()
        if hover_targets:
            sample_size = min(3, len(hover_targets))
            for hover_el in random.sample(hover_targets, sample_size):
                if _bail(): break
                try:
                    if not hover_el.is_visible(timeout=800):
                        continue
                    hbox = hover_el.bounding_box()
                    if not hbox:
                        continue
                    vx = hbox['x'] + hbox['width'] * random.uniform(0.2, 0.8)
                    vy = hbox['y'] + hbox['height'] * random.uniform(0.2, 0.8)
                    sx, sy = _viewport_to_screen(new_page, vx, vy)
                    sx = max(10, min(sx, hi.SCREEN_W - 10))
                    sy = max(10, min(sy, hi.SCREEN_H - 10))
                    hi.human_move_to(sx, sy, duration=random.uniform(0.4, 0.9))
                    time.sleep(random.uniform(0.6, 1.4))  # hover dwell
                except Exception:
                    continue
    except Exception:
        pass

    # 5. Idle + keystroke acak
    if _bail(): return False
    try:
        if random.random() < 0.45:
            hi.random_keystrokes(count=random.randint(1, 2))
        hi.random_idle()
    except Exception:
        pass

    # 6. Inner-click elemen di halaman iklan (deep click)
    clicked_inner = False
    if depth < max_depth and not _bail():
        try:
            clicked_inner = _click_inside_ad_page(
                new_page, logger, context, request_log, depth=depth
            )
            if clicked_inner:
                logger.ok('   Inner-click pada halaman iklan berhasil')
        except Exception as e:
            logger.warn(f'   Inner-click gagal: {e}')

    # v12.0 Issue 2+3 FIX: Random click dengan hard cap (1-3), deadline check
    if not _bail():
        try:
            random_clicks = _random_click_on_page(
                new_page, logger,
                max_clicks=random.randint(1, 3),  # v12.0: reduced from randint(2,4)
                deadline=deadline
            )
            if random_clicks > 0:
                logger.ok(f'   {random_clicks} random klik dilakukan di halaman iklan')
        except Exception as e:
            logger.warn(f'   Random click pada iklan gagal: {e}')

    # v12.0 Issue 6 FIX: CPM view time is MORE important than random clicks.
    # Prioritize idle/wait time over everything else for CPM revenue.
    if ad_page_enter_time is not None and ad_page_enter_time > 0:
        elapsed_in_ad = time.time() - ad_page_enter_time
    else:
        elapsed_in_ad = 0
    remaining_view = max(0, target_view_time - elapsed_in_ad)
    if remaining_view > 0:
        logger.ad(f'   CPM view: menunggu {remaining_view:.1f}s lagi untuk optimal view time')
        # Spend remaining time with realistic idle + micro-scroll
        while remaining_view > 0:
            if _bail():
                break
            chunk = min(remaining_view, random.uniform(2.0, 5.0))
            time.sleep(chunk)
            remaining_view -= chunk
            if random.random() < 0.3:
                try:
                    hi.human_scroll(down=random.random() < 0.85, steps=random.randint(1, 2))
                except Exception:
                    pass
            if random.random() < 0.15:
                try:
                    hi.human_move_relative(
                        int(random.gauss(0, 40)),
                        int(random.gauss(0, 20)),
                        duration=random.uniform(0.3, 0.6),
                    )
                except Exception:
                    pass

    # 7. Tunggu tracking beacon (poll request_log)
    try:
        time.sleep(random.uniform(0.8, 1.6))
        rtmark_hits = [r for r in request_log
                       if 'rtmark.net' in r['url'].lower() and 'img.gif' in r['url'].lower()]
        if rtmark_hits:
            logger.ok(f'   Beacon rtmark terdeteksi ({len(rtmark_hits)} req)')
    except Exception:
        pass

    return clicked_inner


def _random_click_on_page(new_page, logger, max_clicks=3, deadline=None):
    """
    v12.0 UPGRADE — Random click pada halaman iklan (tab baru).
    Bot melakukan klik acak pada elemen visible di halaman iklan
    agar terlihat seperti manusia yang benar-benar membaca iklan.

    v12.0 changes (Issue 2+3):
      - HARD CAP: max_clicks is capped to min(max_clicks, 5)
      - Time budget: if running for > 30s, break immediately
      - Accepts deadline parameter — bail if exceeded

    Args:
        new_page: Playwright Page (ad tab)
        logger: LiveLogger instance
        max_clicks: maksimal klik yang dilakukan (default 3)
        deadline: v12.0 — absolute time deadline. Bail if exceeded.

    Returns:
        int: jumlah klik yang berhasil dilakukan
    """
    import human_input as hi

    # v12.0 Issue 2 FIX: HARD CAP — never allow more than 5 random clicks
    max_clicks = min(max_clicks, 5)

    click_count = 0
    func_start = time.time()  # v12.0 Issue 2: time budget tracking

    click_selectors = [
        'a[href]:not([href^="javascript:"]):not([href="#"]):not([href=""])',
        'a[href]',
        'button',
        'div[onclick]',
        'div[role="button"]',
        'div[role="link"]',
        'input[type="submit"]',
        'input[type="button"]',
        'span[onclick]',
    ]

    # Gather all visible clickable elements
    candidates = []
    for sel in click_selectors:
        try:
            els = new_page.locator(sel).all()
        except Exception:
            continue
        for el in els:
            try:
                if not el.is_visible(timeout=800):
                    continue
                box = el.bounding_box()
                if not box or box.get('width', 0) < 10 or box.get('height', 0) < 10:
                    continue
                # Skip nav/header/footer that are very wide and short
                if box.get('width', 0) > 1200 and box.get('height', 0) < 40:
                    continue
                href = el.get_attribute('href') or ''
                # Skip same-page anchors
                if href.startswith('#'):
                    continue
                candidates.append({'locator': el, 'box': box, 'href': href, 'sel': sel})
            except Exception:
                continue

    if not candidates:
        logger.ad('   Tidak ada elemen clickable di halaman iklan untuk random click')
        return 0

    # Shuffle and pick up to max_clicks
    random.shuffle(candidates)
    chosen = candidates[:max(min(max_clicks, 5), len(candidates))]

    for i, c in enumerate(chosen):
        # v12.0 Issue 2: Time budget — if running > 30s, break
        if time.time() - func_start > 30:
            logger.warn('   Random click: 30s time budget exceeded, stopping')
            break
        # v12.0 Issue 3: Deadline check
        if deadline is not None and time.time() >= deadline:
            break
        try:
            box = c['box']
            # Move mouse to element with human-like bezier path
            vx = box['x'] + box['width'] * random.uniform(0.2, 0.8)
            vy = box['y'] + box['height'] * random.uniform(0.2, 0.8)
            sx, sy = _viewport_to_screen(new_page, vx, vy)
            sx = max(10, min(sx, hi.SCREEN_W - 10))
            sy = max(10, min(sy, hi.SCREEN_H - 10))

            # Hover first (human-like)
            hi.human_move_to(sx, sy, duration=random.uniform(0.4, 0.9))
            time.sleep(random.uniform(0.6, 1.5))  # hover dwell

            # Click with human timing
            hi.human_click(sx, sy)
            click_count += 1
            logger.ad(f'   Random click #{click_count} pada elemen iklan ({c["sel"][:30]})')

            # Brief scroll after click (natural browsing behavior)
            if random.random() < 0.5:
                hi.human_scroll(down=True, steps=random.randint(1, 3))

            # Pause between clicks
            time.sleep(random.uniform(1.5, 3.5))

        except Exception as e:
            logger.warn(f'   Random click gagal: {e}')
            continue

    return click_count


# ====================================================================
# v10.11.2 — AD TAB LOAD TIMEOUT HELPER (10s max)
# ====================================================================
AD_TAB_LOAD_TIMEOUT_S = 10  # max seconds to wait for ad tab to load

# URL prefixes that indicate a failed/error page rather than a real ad landing.
_AD_TAB_FAILURE_URL_PREFIXES = (
    'chrome-error://',      # Chromium network error page (DNS fail, conn refused, etc.)
    'about:blank',          # Never navigated away from blank
    'about:neterror',       # Firefox-style net error (rare in Chromium, but safe)
    'data:',                # data: URLs should never be an ad landing
    'blob:',                # blob: URLs are not real ad landings
)


def _wait_for_ad_tab_loaded(new_page, logger, timeout_s=AD_TAB_LOAD_TIMEOUT_S):
    """
    v10.11.2 UPGRADE — AD TAB LOAD TIMEOUT
    Tunggu tab iklan Adsterra sampai benar-benar loaded, maksimal `timeout_s`
    detik. Bila loading gagal (timeout / error page / blank), return False
    agar caller menutup tab dan melanjutkan aktivitas bot di web utama.

    Check yang dilakukan:
      1. URL tidak boleh salah satu failure prefixes (chrome-error://, about:blank, data:, blob:)
      2. wait_for_load_state('domcontentloaded') sukses dalam `timeout_s` detik
      3. Page tidak crash (page.is_closed() == False)

    Args:
        new_page: Playwright Page object dari tab iklan
        logger: logger instance
        timeout_s: max waktu tunggu load (default 10s)

    Returns:
        True bila tab loaded OK, False bila gagal/timeout (caller harus close tab)
    """
    if new_page is None:
        return False

    # ============================================================
    # Cek 1: URL check cepat — skip chrome-error://, data:, blob: immediately.
    # FIX: about:blank is NO LONGER treated as instant failure!
    # Adsterra native ads often open new tabs as about:blank first,
    # then JavaScript in the opener navigates the tab to the real URL.
    # We let Cek 2 (wait_for_load_state) handle the about:blank case:
    # if the tab stays about:blank after the full timeout, it fails;
    # if it navigates to a real URL, it succeeds.
    # ============================================================
    _INSTANT_FAILURE_PREFIXES = (
        'chrome-error://',
        'about:neterror',
        'data:',
        'blob:',
    )
    try:
        current_url = (new_page.url or '').lower()
        for prefix in _INSTANT_FAILURE_PREFIXES:
            if current_url.startswith(prefix):
                logger.warn(
                    f'   Tab iklan URL = {current_url[:80]} (failure page) — '
                    f'load dianggap GAGAL, akan ditutup'
                )
                return False
        # about:blank: don't fail immediately — wait for navigation
        if current_url == 'about:blank':
            logger.info(f'   Tab iklan URL = about:blank (masih loading), menunggu navigasi...')
    except Exception:
        pass

    # ============================================================
    # Cek 2: wait_for_load_state dengan timeout 10s.
    # Bila timeout → loading gagal, return False.
    # ============================================================
    try:
        new_page.wait_for_load_state('domcontentloaded', timeout=timeout_s * 1000)
    except Exception as e:
        err_str = str(e)[:120]
        # Cek apakah page sudah closed saat menunggu
        try:
            if new_page.is_closed():
                logger.warn(f'   Tab iklan ditutup saat loading — load GAGAL')
                return False
        except Exception:
            pass
        logger.warn(
            f'   Tab iklan gagal loading dalam {timeout_s}s ({err_str}) — '
            f'akan ditutup, lanjut di web utama'
        )
        return False

    # ============================================================
    # Cek 3: Post-load URL check — kadang page navigate ke chrome-error://
    # SETELAH domcontentloaded. Cek sekali lagi.
    # FIX: about:blank after load is acceptable (some ad landings are SPA
    # that load content via JS into about:blank). Only fail on real errors.
    # ============================================================
    try:
        post_url = (new_page.url or '').lower()
        for prefix in _INSTANT_FAILURE_PREFIXES:
            if post_url.startswith(prefix):
                logger.warn(
                    f'   Tab iklan navigasi ke failure page: {post_url[:80]} — '
                    f'load dianggap GAGAL, akan ditutup'
                )
                return False
        # about:blank after load: check if page has actual content
        if post_url == 'about:blank':
            try:
                has_content = new_page.evaluate(
                    '() => document.body && document.body.innerHTML.length > 50'
                )
                if has_content:
                    logger.info(f'   Tab iklan about:blank memiliki konten — diterima')
                else:
                    logger.warn(f'   Tab iklan about:blank kosong (tidak ada konten) — GAGAL')
                    return False
            except Exception:
                # Can't evaluate — give benefit of doubt
                logger.info(f'   Tab iklan about:blank (tidak bisa cek konten) — diterima')
    except Exception:
        pass

    # ============================================================
    # Cek 4: Page masih hidup (tidak crash saat loading)
    # ============================================================
    try:
        if new_page.is_closed():
            logger.warn(f'   Tab iklan tertutup saat loading — load GAGAL')
            return False
    except Exception:
        pass

    logger.ok(f'   Tab iklan loaded OK dalam ≤{timeout_s}s')
    return True


# ====================================================================
# v9.4 — WRAPPER: klik native AdSterra + proses tab iklan
# ====================================================================

def click_native_adsterra_and_process(page, ad_entry, logger, ad_index,
                                       context, request_log, source='home_bottom',
                                       deadline=None):
    """
    Klik banner native AdSterra → capture new tab → interaksi human-like
    di halaman iklan (scroll, hover, idle, deep-click) → verify tracking
    beacon → close tab.

    Args:
        page: source page (home / article modal)
        ad_entry: dict dari discover_native_adsterra_ads()
        source: 'home_bottom' / 'article_bottom' (untuk log)

    Returns:
        (success: bool, tracking_fired: bool)
    """
    import human_input as hi

    locator = ad_entry['locator']
    href = ad_entry['href']
    box = ad_entry['box']
    label = ad_entry['label']

    logger.ad(f'[Native AdSterra {source}] Klik AD #{ad_index + 1} ({label} -> {href[:80]})')

    try:
        page.bring_to_front()
        time.sleep(random.uniform(0.2, 0.5))
    except Exception:
        pass

    # Pasang request/response logger
    response_log = []
    request_logger = make_request_logger(logger, request_log, uid=logger.uid)
    response_logger = make_response_logger(logger, response_log, request_log)
    context.on('request', request_logger)
    context.on('response', response_logger)

    new_page = None
    try:
        # Scroll banner into view dulu
        try:
            locator.scroll_into_view_if_needed(timeout=3000)
            time.sleep(random.uniform(0.5, 1.0))
            box = locator.bounding_box() or box
        except Exception:
            pass

        # Klik dengan capture new tab
        # FIX: Record pages BEFORE click so we can detect new ones reliably.
        # The old expect_page + pyautogui approach was unreliable because
        # pyautogui clicks screen coordinates which may not trigger the
        # browser's popup event that Playwright listens for.
        pages_before = set(id(p) for p in context.pages)

        try:
            with context.expect_page(timeout=10000) as new_page_info:
                pyautogui_click_element(box, logger, page=page)
            new_page = new_page_info.value
        except Exception as e:
            logger.warn(f'   expect_page timed out ({str(e)[:80]}), fallback ke .click()')
            try:
                # FIX: Use expect_page with Playwright .click() for reliable tab capture
                with context.expect_page(timeout=8000) as new_page_info2:
                    locator.click(timeout=4000)
                new_page = new_page_info2.value
            except Exception:
                # Last resort: check for new pages that appeared after click
                time.sleep(1.5)
                for p in reversed(context.pages):
                    if id(p) in pages_before:
                        continue
                    if p == page:
                        continue
                    p_url = p.url or ''
                    # FIX: Don't close about:blank immediately — Adsterra native
                    # ads often open as about:blank first then navigate to the
                    # real ad URL. Give it time to resolve.
                    if 'adspower.net' in p_url or 'adspower.com' in p_url:
                        try:
                            p.close()
                        except Exception:
                            pass
                        continue
                    new_page = p
                    break
                if not new_page:
                    logger.warn(f'   Fallback klik juga gagal menemukan tab baru')

        # Bila tidak ada new tab tapi href valid, buka manual
        # FIX: Also handle protocol-relative URLs (href starts with "//")
        # which Adsterra native ads frequently use. Prepend "https:" to
        # make them absolute URLs that context.new_page().goto() can handle.
        if not new_page and href:
            actual_href = href
            if href.startswith('//'):
                actual_href = 'https:' + href
            if actual_href.startswith('http'):
                try:
                    full_referrer = page.url or TARGET_URL
                    logger.ad(f'   Buka tab iklan manual dengan Referer={full_referrer[:60]}')
                    new_page = context.new_page()
                    # v10.11.2 UPGRADE — AD TAB LOAD TIMEOUT: 30s → 10s.
                    # Bila goto timeout dalam 10s, exception akan ditangkap
                    # di blok except di bawah → new_page = None → return False.
                    new_page.goto(actual_href, wait_until='domcontentloaded',
                                  timeout=AD_TAB_LOAD_TIMEOUT_S * 1000,
                                  referer=full_referrer)
                except Exception as e:
                    logger.warn(f'   Buka tab manual gagal dalam {AD_TAB_LOAD_TIMEOUT_S}s: {e}')
                    # v10.11.2: Tutup tab yang setengah-open bila goto gagal
                    if new_page is not None:
                        try:
                            new_page.close()
                        except Exception:
                            pass
                        new_page = None

        if not new_page:
            logger.warn('   Klik native AdSterra tidak membuka tab apa pun')
            return False, False

        # Filter tab AdsPower internal
        # FIX: Do NOT close about:blank tabs immediately! Adsterra native ads
        # often open new tabs as about:blank first, then JavaScript in the
        # opener page navigates the new tab to the real ad URL. The
        # _wait_for_ad_tab_loaded() function below handles the about:blank
        # case properly by waiting for navigation. Only close tabs that are
        # clearly AdsPower internal pages.
        captured_url = new_page.url or ''
        if 'adspower.net' in captured_url or 'adspower.com' in captured_url:
            logger.warn(f'   Tab tertangkap adalah AdsPower internal: {captured_url[:80]}')
            try:
                new_page.close()
            except Exception:
                pass
            return False, False

        logger.ad(f'   Tab iklan tertangkap: {captured_url[:80]}')

        # ============================================================
        # v10.11.2 UPGRADE — AD TAB LOAD TIMEOUT (MAX 10s).
        # Bila tab iklan gagal loading (timeout / chrome-error / about:blank /
        # page crash), tutup tab dan kembalikan kontrol ke caller untuk
        # melanjutkan aktivitas bot di web utama.
        #
        # Ini menghindari bot "menggantung" 30s+ saat iklan Adsterra
        # gagal load (DNS error, koneksi refused, server iklan down, dll).
        # Setelah tab ditutup, page (web utama) tetap aktif dan bot
        # lanjut ke aktivitas berikutnya.
        # ============================================================
        if not _wait_for_ad_tab_loaded(new_page, logger,
                                        timeout_s=AD_TAB_LOAD_TIMEOUT_S):
            # Load gagal — tutup tab dan kembalikan kontrol ke web utama.
            logger.warn(
                f'   Tab iklan gagal loading — ditutup, '
                f'bot lanjut aktivitas di web utama'
            )
            try:
                new_page.close()
            except Exception:
                pass
            # Bersihkan listener (finally block akan handle, tapi kita
            # return di sini agar tracking check & interact dilewati).
            try:
                context.remove_listener('request', request_logger)
            except Exception:
                pass
            try:
                context.remove_listener('response', response_logger)
            except Exception:
                pass
            return False, False

        # v11.0 UPGRADE — MOD 2: Track time spent on ad page for CPM view time
        # FIX: Store as local variable and pass explicitly to _humanlike_interact_with_ad_page
        _ad_page_enter_time = time.time()

        # v12.0 Issue 3 FIX: Pass deadline to _humanlike_interact_with_ad_page
        # so it can bail early if the session is running out of time.
        _humanlike_interact_with_ad_page(
            new_page, logger, context, request_log,
            depth=0, max_depth=1,
            ad_page_enter_time=_ad_page_enter_time,
            deadline=deadline
        )

        # Cek tracking beacon
        rtmark_tracking_requests = [
            r for r in request_log
            if 'rtmark.net' in r['url'].lower() and 'img.gif' in r['url'].lower()
        ]
        tracking_fired = len(rtmark_tracking_requests) > 0
        if tracking_fired:
            logger.ok(f'   TRACKING CONFIRMED native AdSterra: {len(rtmark_tracking_requests)} beacon')
        else:
            # Cek request AdSterra/Monetag/EffectiveCPMNetwork lain
            any_adsterra = [
                r for r in request_log
                if any(d in r['url'].lower() for d in
                       list(MONETAG_DOMAINS) + list(EFFECTIVE_CPM_DOMAINS)
                       + ['adsterra', 'qualitypage', 'effectivecpmnetwork',
                          'cpmnetwork', 'invoke.js'])
            ]
            if any_adsterra:
                logger.ok(f'   Request AdSterra/Monetag/EffectiveCPM terdeteksi: {len(any_adsterra)}')
                tracking_fired = True
            else:
                logger.warn('   Tidak ada beacon AdSterra/Monetag/EffectiveCPM terdeteksi')

        # Tutup tab iklan
        try:
            new_page.close()
            logger.ad('   Tab native AdSterra ditutup')
        except Exception:
            pass

        return True, tracking_fired

    finally:
        try:
            context.remove_listener('request', request_logger)
        except Exception:
            pass
        try:
            context.remove_listener('response', response_logger)
        except Exception:
            pass


# ====================================================================
# Legacy ad discovery (dipertahankan sebagai fallback)
# ====================================================================
def discover_ads(page, max_count=4):
    containers = [
        '#modalAdBanner', '#adBanner', '#footerBanner',
        '.modal-ad-banner-body', '.ad-banner.has-banner',
        '.modal-ad-banner', 'ins.adsbygoogle',
        'div[id*="monetag"]', 'div[id*="propeller"]',
        'iframe[src*="monetag"]', 'iframe[src*="propeller"]',
        'iframe[src*="omg"]',
        # v9.5: EffectiveCPMNetwork patterns (real AdSterra CDN)
        'div[id^="container-"]',
        'div[id*="effectivecpmnetwork"]',
        'div[class*="effectivecpmnetwork"]',
        'iframe[src*="effectivecpmnetwork"]',
        'iframe[src*="cpmnetwork"]',
    ]
    clickables = [
        'a[href][target="_blank"]',
        'a[href]:not([href^="javascript:"]):not([href="#"])',
        'a[href]', 'div[onclick]',
    ]
    found = []
    seen_hrefs = set()
    for csel in containers:
        if len(found) >= max_count:
            break
        try:
            conts = page.locator(csel).all()
            for cont in conts:
                if len(found) >= max_count:
                    break
                try:
                    if not cont.is_visible():
                        continue
                    html = cont.inner_html()
                    if not html or len(html.strip()) < 5:
                        continue
                    if 'Advertisement space available' in html:
                        continue
                    for clksel in clickables:
                        if len(found) >= max_count:
                            break
                        try:
                            clks = cont.locator(clksel).all()
                            for clk in clks:
                                if len(found) >= max_count:
                                    break
                                try:
                                    if not clk.is_visible():
                                        continue
                                    href = clk.get_attribute('href') or ''
                                    if href.startswith('javascript:') or href == '#':
                                        continue
                                    if href in seen_hrefs:
                                        continue
                                    if href:
                                        seen_hrefs.add(href)
                                    box = clk.bounding_box()
                                    found.append({
                                        'locator': clk, 'href': href,
                                        'box': box, 'label': f'{csel} > {clksel}',
                                    })
                                except Exception:
                                    continue
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            continue
    return found

# ====================================================================
# VIEWPORT → SCREEN COORDINATE CONVERSION (sudah diperbaiki)
# ====================================================================
def _get_window_offset_and_dpr(page):
    try:
        dpr = page.evaluate('window.devicePixelRatio')
        if not isinstance(dpr, (int, float)) or dpr <= 0:
            dpr = 1.0
    except Exception:
        dpr = 1.0
    win_x, win_y = 0, 0
    try:
        client = page.context.new_cdp_session(page)
        try:
            target = client.send('Browser.getWindowForTarget', {})
            wid = target.get('windowId')
            if wid:
                bounds = client.send('Browser.getWindowBounds', {'windowId': wid}).get('bounds', {})
                win_x = bounds.get('left', 0)
                win_y = bounds.get('top', 0)
        finally:
            try:
                client.detach()
            except Exception:
                pass
    except Exception:
        pass
    if win_x == 0 and win_y == 0:
        try:
            js_pos = page.evaluate('() => ({x: window.screenX || 0, y: window.screenY || 0})')
            win_x = js_pos.get('x', 0)
            win_y = js_pos.get('y', 0)
        except Exception:
            pass
    return win_x, win_y, dpr

def _viewport_to_screen(page, vx, vy):
    win_x, win_y, dpr = _get_window_offset_and_dpr(page)
    sx = int(win_x + vx * dpr)
    sy = int(win_y + vy * dpr)
    return sx, sy


def _detect_and_set_browser_bounds(page, logger):
    """
    Detect the browser content area on screen and set browser bounds
    in human_input so all pyautogui mouse ops are constrained.

    Uses CDP Browser.getWindowBounds + JS innerWidth/innerHeight to get
    the exact viewport rectangle on screen.
    Falls back to (0, 0, SCREEN_W, SCREEN_H) if detection fails.
    """
    import human_input as hi

    # Default: full screen
    bx, by = 0, 0
    bw, bh = hi.SCREEN_W, hi.SCREEN_H

    try:
        # Get window position from CDP
        win_x, win_y, dpr = _get_window_offset_and_dpr(page)

        # Get viewport (content area) dimensions from JS
        vp_info = page.evaluate('''() => ({
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            screenX: window.screenX || 0,
            screenY: window.screenY || 0,
        })''')

        inner_w = vp_info.get('innerWidth', 0)
        inner_h = vp_info.get('innerHeight', 0)
        outer_w = vp_info.get('outerWidth', 0)
        outer_h = vp_info.get('outerHeight', 0)

        if inner_w > 0 and inner_h > 0:
            # The browser content area starts at (win_x + chrome_left, win_y + chrome_top)
            # where chrome = the browser UI (address bar, tabs, etc.)
            # outerWidth - innerWidth = horizontal chrome (both sides)
            # outerHeight - innerHeight = vertical chrome (top + bottom)
            chrome_w = max(0, outer_w - inner_w)
            chrome_h = max(0, outer_h - inner_h)

            # Chrome offset: typically chrome is at the top and left
            # For most browsers: left border = chrome_w/2, top = chrome_h - small_bottom_bar
            # We approximate: top chrome = outerHeight - innerHeight (address bar + tabs)
            #                 left chrome ≈ 0 (borders are thin)
            chrome_left = chrome_w // 2   # approximate border
            chrome_top = chrome_h - 4     # subtract small bottom border

            # Content area on screen (in CSS pixels, multiply by DPR for screen coords)
            bx = int(win_x + chrome_left * dpr)
            by = int(win_y + chrome_top * dpr)
            bw = int(inner_w * dpr)
            bh = int(inner_h * dpr)

            logger.info(f'Browser bounds detected: pos=({win_x},{win_y}) chrome=({chrome_w},{chrome_h}) '
                        f'viewport=({inner_w},{inner_h}) dpr={dpr} → bounds=({bx},{by},{bw},{bh})')
    except Exception as e:
        logger.warn(f'Browser bounds detection failed, using full screen: {e}')
        bx, by = 0, 0
        bw, bh = hi.SCREEN_W, hi.SCREEN_H

    # Ensure bounds are within screen
    bw = min(bw, hi.SCREEN_W - bx)
    bh = min(bh, hi.SCREEN_H - by)

    hi.set_browser_bounds(bx, by, bw, bh)
    logger.ok(f'Browser bounds set: ({bx},{by}) {bw}x{bh} (screen={hi.SCREEN_W}x{hi.SCREEN_H})')


# ====================================================================
# Click helpers with viewport→screen conversion
# ====================================================================
def pyautogui_click_element(box, logger, page=None):
    import human_input as hi
    if page is None:
        logger.warn('No page provided for coordinate conversion')
        return False
    if not box:
        logger.warn('No bounding box for element')
        return False
    vx = box['x'] + box['width'] * random.uniform(0.25, 0.75)
    vy = box['y'] + box['height'] * random.uniform(0.25, 0.75)
    sx, sy = _viewport_to_screen(page, vx, vy)
    sx = max(10, min(sx, hi.SCREEN_W - 10))
    sy = max(10, min(sy, hi.SCREEN_H - 10))
    logger.info(f'pyautogui click at screen ({sx},{sy})')
    hi.human_click(sx, sy)
    return True

def _process_popunder(popunder_page, context, logger):
    import human_input as hi
    try:
        popunder_page.bring_to_front()
    except Exception:
        pass
    try:
        inject_stealth_to_page(popunder_page)
    except Exception:
        pass
    try:
        ab_result = solve_antibot_if_present(popunder_page, logger=logger, max_total_time_s=20)
        if ab_result['solved']:
            logger.ok('Popunder antibot solved')
    except Exception:
        pass
    hi.human_scroll(down=True, steps=random.randint(2, 5))
    time.sleep(random.uniform(0.5, 1.5))
    for _ in range(random.randint(1, 3)):
        hi.human_move_relative(
            random.randint(-200, 200),
            random.randint(-150, 150),
            duration=random.uniform(0.4, 1.0)
        )
        time.sleep(random.uniform(0.5, 1.0))
    try:
        vp = popunder_page.viewport_size
        if vp:
            vx = random.randint(100, vp['width'] - 100)
            vy = random.randint(100, vp['height'] - 100)
            sx, sy = _viewport_to_screen(popunder_page, vx, vy)
            hi.human_click(sx, sy)
            logger.ad(f'Clicked at ({sx},{sy}) on popunder')
    except Exception:
        pass
    time.sleep(random.uniform(2, 4))
    try:
        popunder_page.close()
        logger.ad('Popunder tab closed')
    except Exception:
        pass

def click_article_with_popunder_handling(page, article, context, logger):
    try:
        article.scroll_into_view_if_needed(timeout=3000)
        time.sleep(0.5)
        box = article.bounding_box()
    except Exception:
        box = None
    popunder_page = None
    try:
        with context.expect_page(timeout=5000) as popup_info:
            if box and pyautogui_click_element(box, logger, page=page):
                pass
            else:
                article.click(timeout=4000)
        popunder_page = popup_info.value
    except Exception as e:
        logger.info(f"No popunder captured: {e}")
        try:
            if box and pyautogui_click_element(box, logger, page=page):
                pass
            else:
                article.click(timeout=4000)
        except Exception as e2:
            logger.warn(f"Article click fallback failed: {e2}")
            return False
    if popunder_page and popunder_page != page:
        logger.ad(f"Popunder captured: {popunder_page.url}")
        _process_popunder(popunder_page, context, logger)
    elif popunder_page:
        logger.info("Captured page is same as main page, ignoring")
    try:
        page.wait_for_selector('#articleModal.open', state='attached', timeout=6000)
        page.wait_for_selector('#articleModal.open', state='visible', timeout=4000)
        return True
    except Exception:
        logger.warn('Modal did not open; skipping article')
        return False

# ====================================================================
# Click AD with new tab capture (tidak diubah, sudah pakai konversi)
# ====================================================================
def click_ad_and_visit_new_tab(page, ad_entry, logger, ad_index, context, request_log):
    import human_input as hi
    locator = ad_entry['locator']
    href = ad_entry['href']
    box = ad_entry['box']
    label = ad_entry['label']
    logger.ad(f'Clicking AD #{ad_index + 1} ({label} -> {href[:80]})')
    try:
        page.bring_to_front()
        time.sleep(0.2)
    except Exception:
        pass
    response_log = []
    request_logger = make_request_logger(logger, request_log, uid=logger.uid)
    response_logger = make_response_logger(logger, response_log, request_log)
    context.on('request', request_logger)
    context.on('response', response_logger)
    tracking_fired = False
    try:
        new_page = _click_ad_inner(page, locator, href, box, logger, ad_index, context, request_log)
        if new_page is None:
            return False, False
        rtmark_tracking_requests = [
            r for r in request_log
            if 'rtmark.net' in r['url'].lower() and 'img.gif' in r['url'].lower()
        ]
        tracking_fired = len(rtmark_tracking_requests) > 0
        return True, tracking_fired
    finally:
        try:
            context.remove_listener('request', request_logger)
        except Exception:
            pass
        try:
            context.remove_listener('response', response_logger)
        except Exception:
            pass

def _click_ad_inner(page, locator, href, box, logger, ad_index, context, request_log):
    import human_input as hi
    new_page = None
    try:
        with context.expect_page(timeout=10000) as new_page_info:
            try:
                locator.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.3)
                box = locator.bounding_box() or box
                pyautogui_click_element(box, logger, page=page)
            except Exception as e:
                logger.warn(f'pyautogui click failed ({e}); fallback to locator.click()')
                try:
                    locator.click(timeout=4000)
                except Exception:
                    pass
        new_page = new_page_info.value
        ADSPIDER_SKIP_URLS = ('adspower.net', 'adspower.com', 'about:blank')
        if new_page:
            captured_url = new_page.url or ''
            if any(skip in captured_url for skip in ADSPIDER_SKIP_URLS):
                logger.warn(f'   expect_page captured AdsPower/blank tab ({captured_url[:80]}), discarding')
                try:
                    new_page.close()
                except Exception:
                    pass
                new_page = None
            else:
                logger.ad('   New tab captured via expect_page()')
    except Exception as e:
        logger.warn(f'expect_page timed out: {str(e)[:120]}')
    time.sleep(1.5)
    if not new_page:
        pages = context.pages
        ADSPIDER_SKIP_URLS = ('adspower.net', 'adspower.com', 'about:blank')
        if len(pages) > 1:
            for p in reversed(pages):
                if p == page:
                    continue
                p_url = p.url or ''
                if any(skip in p_url for skip in ADSPIDER_SKIP_URLS):
                    try:
                        p.close()
                        logger.ad(f'   Closed non-ad tab: {p_url[:80]}')
                    except Exception:
                        pass
                    continue
                new_page = p
                logger.ad(f'   Recovered ad popup from context.pages (count={len(pages)}), URL={p_url[:80]}')
                break
    if new_page:
        new_url = new_page.url or ''
        if any(skip in new_url for skip in ADSPIDER_SKIP_URLS):
            logger.warn(f'   Recovered page is AdsPower/blank ({new_url[:80]}), closing it')
            try:
                new_page.close()
            except Exception:
                pass
            new_page = None
    if not new_page and href and href.startswith('http'):
        full_referrer = page.url or TARGET_URL
        logger.ad(f'   Opening ad manually with Referer={full_referrer[:80]}')
        try:
            new_page = context.new_page()
            new_page.goto(href, wait_until='domcontentloaded', timeout=30000, referer=full_referrer)
        except Exception as e:
            logger.warn(f'   Failed to open new tab: {e}')
    if not new_page:
        logger.warn('AD click did not open a new tab')
        return None
    try:
        inject_stealth_to_page(new_page)
    except Exception as e:
        logger.warn(f'   Stealth injection on new_page failed: {e}')
    try:
        new_page.bring_to_front()
        time.sleep(0.2)
    except Exception:
        pass
    try:
        early_ab = detect_antibot(new_page)
        if early_ab['detected']:
            if early_ab.get('verified'):
                logger.ok(f'   Early antibot already verified')
            else:
                logger.warn(f'   Early antibot detected on ad tab - solving...')
                early_result = solve_antibot_if_present(new_page, logger=logger, max_total_time_s=35)
                if early_result['solved']:
                    logger.ok(f'   Early antibot solved')
                    time.sleep(2.0)
                    try:
                        inject_stealth_to_page(new_page)
                    except Exception:
                        pass
    except Exception:
        pass
    # v11.1 UPGRADE 1 — Age Verification: check for age gate and click Yes/Confirm
    try:
        handle_age_verification(new_page, logger)
    except Exception:
        pass
    try:
        # v10.11.2 UPGRADE — AD TAB LOAD TIMEOUT: 15s → 10s.
        new_page.wait_for_load_state('domcontentloaded',
                                     timeout=AD_TAB_LOAD_TIMEOUT_S * 1000)
    except Exception:
        pass
    time.sleep(4.0)
    try:
        inject_stealth_to_page(new_page)
    except Exception:
        pass
    try:
        for ab_round in range(1, 4):
            ab_result = solve_antibot_if_present(new_page, logger=logger, max_total_time_s=35)
            if not ab_result['detected']:
                if ab_round == 1:
                    logger.ad('   No antibot challenge detected on ad tab')
                else:
                    logger.ok(f'   Antibot cleared after round {ab_round - 1}')
                break
            if ab_result['solved']:
                logger.ok(f'   Antibot round {ab_round}: solved')
                time.sleep(2.5)
                try:
                    inject_stealth_to_page(new_page)
                except Exception:
                    pass
                try:
                    quick_check = detect_antibot(new_page)
                    if quick_check['detected'] and not quick_check.get('verified'):
                        continue
                except Exception:
                    pass
                break
            else:
                logger.warn(f'   Antibot round {ab_round}: detected but NOT solved')
                if ab_round >= 3:
                    break
                time.sleep(2.0)
    except Exception as e:
        logger.warn(f'   Antibot solver error: {e}')
    try:
        landing_check = MonetagVerifier.check_landing_page(new_page, logger=logger)
        if landing_check['rtmarkBeaconFound']:
            logger.ok(f'   rtmark beacon found (userId={landing_check["userId"] or "?"})')
        else:
            logger.warn(f'   rtmark beacon NOT found in HTML')
    except Exception as e:
        logger.warn(f'   Landing page check failed: {e}')
    logger.ad(f'Viewing AD landing: {new_page.url}')
    rtmark_deadline = time.time() + 10.0
    rtmark_seen = False
    while time.time() < rtmark_deadline:
        rtmark_hits = [
            r for r in request_log
            if 'rtmark.net' in r['url'].lower() and 'img.gif' in r['url'].lower()
        ]
        if rtmark_hits:
            rtmark_seen = True
            logger.ok(f'   rtmark.net/img.gif beacon fired! ({len(rtmark_hits)} request(s))')
            break
        time.sleep(0.5)
    if not rtmark_seen:
        any_monetag = [
            r for r in request_log
            if any(d in r['url'].lower() for d in MONETAG_DOMAINS)
        ]
        if any_monetag:
            logger.warn(f'   No rtmark beacon, but {len(any_monetag)} other Monetag requests')
        else:
            logger.warn(f'   rtmark beacon did NOT fire within 10s')
    try:
        time.sleep(random.uniform(1.0, 1.8))
        # v10.11 UPGRADE — MOD 3: persingkat ad-landing browse (dari 5-10s → 2-4s)
        hi.mixed_browse_session(duration_s=random.uniform(2, 4))
        for _ in range(random.randint(1, 3)):
            hi.human_move_relative(
                random.randint(-200, 200),
                random.randint(-150, 150),
                duration=random.uniform(0.5, 1.0),
            )
            time.sleep(random.uniform(0.6, 1.1))
        if random.random() < 0.3:
            hi.random_keystrokes(count=random.randint(1, 2))
    except Exception as e:
        logger.warn(f'Ad landing interaction error: {e}')
    delayed_ab_solved_now = False
    try:
        delayed_ab = solve_antibot_if_present(new_page, logger=logger, max_total_time_s=25)
        if delayed_ab['detected'] and delayed_ab['solved']:
            logger.ok(f'   Delayed antibot solved')
            delayed_ab_solved_now = True
            time.sleep(2.0)
            try:
                inject_stealth_to_page(new_page)
            except Exception:
                pass
    except Exception as e:
        logger.warn(f'   Delayed antibot check error: {e}')
    if not rtmark_seen and delayed_ab_solved_now:
        logger.ad('   Re-polling for rtmark after delayed antibot (8s)...')
        re_poll_deadline = time.time() + 8.0
        while time.time() < re_poll_deadline:
            rtmark_hits = [
                r for r in request_log
                if 'rtmark.net' in r['url'].lower() and 'img.gif' in r['url'].lower()
            ]
            if rtmark_hits:
                rtmark_seen = True
                logger.ok(f'   rtmark fired AFTER delayed antibot!')
                break
            time.sleep(0.5)
    rtmark_tracking_requests = [
        r for r in request_log
        if 'rtmark.net' in r['url'].lower() and 'img.gif' in r['url'].lower()
    ]
    tracking_fired = len(rtmark_tracking_requests) > 0
    if tracking_fired:
        logger.ok(f'   TRACKING CONFIRMED: {len(rtmark_tracking_requests)} beacon(s)')
    else:
        logger.warn('   No Monetag tracking beacon detected')
    try:
        new_page.close()
        logger.ad('Closed AD tab')
    except Exception:
        pass
    return new_page

# ====================================================================
# run_user — dengan perbaikan proxy test dan ignore certificate errors
# ====================================================================
def _push_session_stats(uid, stats):
    """
    v10.3: Push per-session stats to web_preview.state so the dashboard
    updates in real-time during a user's session (not just after it ends).

    The dashboard's /status.json endpoint reads state['current_session_stats'],
    which the frontend can render as "Current user: articles=X ads=Y" while
    the session is still running. This eliminates the previous 30-60s lag
    where the dashboard showed stale data from the previous user.

    Failures here are silently swallowed — dashboard updates are best-effort
    and must never break the bot's main flow.
    """
    try:
        update_state(current_session_stats={
            'uid': uid,
            'articles': stats.get('articles', 0),
            'ads': stats.get('ads', 0),
            'tracking': stats.get('tracking', 0),
            'reached_target': stats.get('reached_target', False),
            'session_active': stats.get('session_active', False),
            'error': stats.get('error'),
        })
    except Exception:
        pass


def run_user(pw, profile, args, ad_manager=None):
    import human_input as hi
    uid = profile['id']
    device = profile['device']
    logger = LiveLogger(uid, device['name'])

    proxy = profile.get('proxy')
    if not proxy:
        logger.error('No proxy assigned to this profile')
        return {'articles': 0, 'ads': 0, 'tracking': 0, 'success': False,
                'error': 'no_proxy', 'duration': 0, 'request_log': [],
                'proxy_failed': True}

    sync_config = profile.get('sync_config', {})

    proxy_info = f" | proxy={proxy['server']}"
    geo_tag = ' | GEO-MATCHED' if profile.get('geo_matched') else ' | geo:fallback-pair'
    # v9.2: include country + device in the banner so the operator can
    # see at a glance which device + geolocation this session targets.
    country_info = ''
    if profile.get('country_code'):
        country_info = f' | geo={profile["country_code"]}'
    elif profile.get('country'):
        country_info = f' | geo={profile["country"][:8]}'
    mode_tag = ' | MODE=ANTIDETECT' if (ad_manager and ad_manager.is_antidetect_mode) else ' | MODE=PATCHRIGHT'
    log.banner(
        f'Starting {uid} | device={device["name"]} | tz={profile["timezone"]}'
        f'{country_info}{proxy_info}{geo_tag}{mode_tag}'
    )
    update_state(current_user=uid, current_device=device['name'])
    set_phase('launching browser')

    logger.start(f'Referer: {profile["referrer"] or "(direct)"}')
    logger.step(f'Target: {profile["target_duration"]}s | max articles: {profile["max_articles"]}')
    logger.info(f'Using proxy: {proxy["server"]}')
    if sync_config:
        logger.info(f'Profile sync: OS={sync_config.get("os", "?")} TTL={sync_config.get("expected_ttl", "?")} Fonts={len(sync_config.get("fonts", []))}')
        logger.info(f'WebGL: {sync_config.get("webgl_renderer", "?")[:50]}...')
        # v9.2: log country + DPR + has_touch so the operator can verify
        # the full synchronization chain (device → OS → UA → WebGL →
        # timezone → country → proxy → AdsPower fingerprint) at startup.
        if sync_config.get('country'):
            logger.info(
                f'Geo: country={sync_config.get("country", "?")} '
                f'({sync_config.get("country_code", "?")}), '
                f'tz={sync_config.get("timezone", "?")}, '
                f'locale={sync_config.get("lan", "?")}'
            )
        logger.info(
            f'Device: mobile={sync_config.get("is_mobile", False)}, '
            f'touch={sync_config.get("has_touch", False)}, '
            f'DPR={sync_config.get("device_scale_factor", "?")}'
        )
        device_os = (device.get('os') or '').strip()
        sync_os   = (sync_config.get('os') or '').strip()
        if device_os and sync_os and device_os != sync_os:
            logger.warn(
                f"DESYNC: device.os={device_os} vs sync_config.os={sync_os} "
                f"— AdsPower/Patchright will use {sync_os} fingerprint "
                f"while the device label says {device.get('name', '?')}."
            )
        sync_ua = sync_config.get('ua', '')
        if sync_ua and sync_ua != profile.get('user_agent'):
            logger.warn(
                f"DESYNC: profile.user_agent != sync_config.ua — "
                f"sync_config wins (UA={sync_ua[:60]}...)"
            )

    started_at = time.time()
    deadline = started_at + min(profile['target_duration'], args.timeout)

    # v12.0 Issue 3 FIX: Helper to check if session deadline is exceeded
    def _check_deadline():
        return time.time() >= deadline

    stats = {'articles': 0, 'ads': 0, 'tracking': 0, 'success': False, 'error': None,
             'reached_target': False, 'session_active': False}
    request_log = []

    # ========== PROXY PRE-FLIGHT YANG DITINGKATKAN ==========
    set_phase('testing proxy')
    ok, dur_ms, err = test_proxy(proxy, timeout=PROXY_PREFLIGHT_TIMEOUT)
    if ok:
        if err:
            logger.ok(f'Proxy pre-flight OK in {dur_ms}ms - {err}')
        else:
            logger.ok(f'Proxy pre-flight OK in {dur_ms}ms')
    else:
        logger.error(f'Proxy pre-flight FAILED in {dur_ms}ms: {err} -> Session aborted')
        stats['error'] = f'proxy_failed: {err}'
        stats['proxy_failed'] = True
        stats['duration'] = int(time.time() - started_at)
        stats['request_log'] = request_log
        return stats

    browser = None
    context = None
    page = None
    session = None

    try:
        set_phase('launching browser')

        if ad_manager and ad_manager.is_antidetect_mode:
            logger.info('Launching anti-detect browser...')
            session = ad_manager.create_and_start(sync_config, pw)
            page = session['page']
            context = session['context']
            browser = session['browser']
            logger.ok(f'Anti-detect browser session started (mode={session["mode"]})')

            # BUG FIX #7: HAPUS blok CDP 'Network.setIgnoreCertificateErrors'.
            # Command ini TIDAK PERNAH ada di Chrome DevTools Protocol —
            # bukti dari log: 'Network.setIgnoreCertificateErrors wasn't found'.
            # ignore_https_errors sudah ditangani via:
            #   1. browser.new_context(ignore_https_errors=True) — di _launch_patchright
            #   2. --ignore-certificate-errors launch arg — di Patchright & AdsPower start_profile
            # Blok ini hanya menghasilkan warning noise setiap run.

            ADSPIDER_SKIP_URLS = ('adspower.net', 'adspower.com', 'about:blank')
            all_pages = context.pages
            if len(all_pages) > 1:
                for extra in all_pages:
                    if extra != page:
                        extra_url = extra.url or ''
                        try:
                            extra.close()
                            logger.info(f'Closed extra tab before navigation: {extra_url[:80]}')
                        except Exception:
                            pass
        else:
            logger.info('Launching Patchright with enhanced stealth...')
            session = _launch_patchright(pw, profile, sync_config, proxy, logger)
            page = session['page']
            context = session['context']
            browser = session['browser']

        # --- v9.1: Detect browser window bounds for mouse constraint ---
        # All pyautogui mouse ops will be clamped to this area, preventing
        # the mouse from escaping to the taskbar or desktop.
        try:
            _detect_and_set_browser_bounds(page, logger)
        except Exception as e:
            logger.warn(f'Browser bounds detection failed: {e} — using full screen')

        set_phase('navigating to site')
        logger.step(f'Navigating to {TARGET_URL}')
        # Coba dengan ignore_https_errors=True (sudah ada di context)
        try:
            page.goto(
                TARGET_URL,
                wait_until='domcontentloaded',
                timeout=30000,
                referer=profile['referrer'] or None,
            )
        except Exception as e:
            err_msg = str(e)[:120]
            logger.warn(f'Initial goto failed ({err_msg}); one short retry...')
            try:
                page.goto(
                    TARGET_URL,
                    wait_until='commit',
                    timeout=20000,
                    referer=profile['referrer'] or None,
                )
                logger.ok('Retry succeeded')
            except Exception as e2:
                raise RuntimeError(f'Failed to load {TARGET_URL}: {str(e2)[:100]}')

        logger.ok(f'Page loaded: {page.url}')

        # v10.3: Mark reached_target AFTER successful page.goto.
        # This is the minimum bar for "partial success" — bot launched browser
        # and the target page actually loaded. Even if article/ads selectors
        # don't match the live site, the session shouldn't be counted as
        # "failed" if it got this far.
        stats['reached_target'] = True
        stats['session_active'] = True
        _push_session_stats(uid, stats)

        try:
            for p in list(context.pages):
                if p != page and ('adspower.net' in (p.url or '') or 'adspower.com' in (p.url or '')):
                    p.close()
                    logger.info(f'Closed auto-reopened AdsPower tab after navigation')
        except Exception:
            pass

        if session.get('mode') != 'antidetect':
            try:
                inject_stealth_to_page(page)
            except Exception:
                pass

        set_phase('waiting for articles')
        logger.step('Waiting for articles to render...')
        try:
            page.wait_for_selector('#articleGrid .article-card, #featuredGrid .article-card',
                                    state='attached', timeout=25000)
            time.sleep(1.2)
            logger.ok('Articles rendered')
        except Exception:
            logger.warn('Articles did not render; reloading...')
            try:
                page.reload(wait_until='domcontentloaded', timeout=25000)
                if session.get('mode') != 'antidetect':
                    inject_stealth_to_page(page)
                page.wait_for_selector('#articleGrid .article-card', state='attached', timeout=15000)
                time.sleep(1.0)
                logger.ok('Articles rendered after reload')
            except Exception:
                logger.warn('Still no articles; continuing anyway')

        set_phase('reading homepage')
        logger.info('pyautogui: brief browse session')
        try:
            page.bring_to_front()
        except Exception:
            pass
        # v10.11 UPGRADE: persingkat homepage browse time (dari 8-15s → 3-5s)
        time.sleep(0.8)
        hi.mixed_browse_session(duration_s=random.uniform(3, 5))
        hi.random_keystrokes(count=random.randint(1, 2))
        hi.random_idle()
        hi.human_move_to(960, 540, duration=0.6)

        articles_visited = 0
        total_ads_clicked = 0
        total_tracking = 0

        # ============================================================
        # v11.0 UPGRADE — MOD 2: Klik MINIMAL 3 iklan Adsterra per session
        # (v12.0: reduced from 4 to 3 — Issue 6 FIX)
        # untuk CPM tinggi. Homepage footer: klik semua banner yang tersedia.
        # ============================================================
        if time.time() < deadline:
            set_phase('footer native banner click')
            try:
                logger.step('Scroll ke footer & cari Adsterra native banner (4 panel)...')
                hi.human_scroll(down=True, steps=random.randint(8, 12))
                time.sleep(random.uniform(0.6, 1.0))
                hi.human_scroll(down=True, steps=random.randint(3, 5))
                time.sleep(random.uniform(0.5, 0.9))

                # v12.0 Issue 5 FIX: Wait for Adsterra scripts to inject content
                # before looking for ads. invoke.js needs time to render.
                logger.ad(f'   Menunggu {ADSTERRA_WAIT_FOR_RENDER_S}s agar Adsterra script render...')
                time.sleep(random.uniform(2, 4))

                # Mouse ke footer ad area
                try:
                    vp = page.viewport_size
                    if vp:
                        footer_x = vp['width'] // 2
                        footer_y = int(vp['height'] * 0.85)
                        sx, sy = _viewport_to_screen(page, footer_x, footer_y)
                        hi.human_move_to(sx, sy, duration=random.uniform(0.6, 1.0))
                        logger.mouse(f'Mouse ke footer ad area ({sx},{sy})')
                        time.sleep(random.uniform(0.4, 0.8))
                except Exception as e:
                    logger.warn(f'Footer mouse move gagal: {e}')

                # v12.0 Issue 5 FIX: Retry loop — if no ads found on first try,
                # wait 3s and try again (up to 2 retries)
                footer_ads = []
                for retry in range(3):
                    footer_ads = discover_native_adsterra_ads(
                        page, max_count=4, location='home_bottom'
                    )
                    if footer_ads:
                        break
                    if retry < 2:
                        logger.ad(f'   Tidak ada ads di footer (retry {retry+1}/2), menunggu 3s...')
                        time.sleep(3)

                if footer_ads:
                    n_panels = len(footer_ads)
                    logger.ok(f'Ditemukan {n_panels} native Adsterra banner di footer')
                    # v11.1 UPGRADE 3: Shuffle footer ads randomly, click ALL (up to 4)
                    # without MIN_AD_CLICKS_PER_SESSION break check
                    random.shuffle(footer_ads)
                    for ad_idx, chosen_ad in enumerate(footer_ads):
                        if _check_deadline():
                            logger.warn('Deadline exceeded during footer banner clicks')
                            break
                        try:
                            success, tracking = click_native_adsterra_and_process(
                                page, chosen_ad, logger, total_ads_clicked,
                                context, request_log, source=f'footer-panel-{ad_idx+1}',
                                deadline=deadline
                            )
                            if success:
                                total_ads_clicked += 1
                                stats['ads'] = total_ads_clicked
                                if tracking:
                                    total_tracking += 1
                                    stats['tracking'] = total_tracking
                                _push_session_stats(uid, stats)
                                logger.ad(f'Footer banner click #{ad_idx+1}/{n_panels} berhasil')
                            else:
                                logger.warn(f'Footer banner click #{ad_idx+1} gagal (load timeout/no tab)')
                        except Exception as e:
                            logger.warn(f'Footer banner click #{ad_idx+1} gagal: {e}')
                        # Jeda antar klik iklan
                        time.sleep(random.uniform(2, 5))
                else:
                    logger.step('Tidak ada native Adsterra banner di footer; lanjut ke artikel.')
                    # v12.0 Issue 5 FIX: Try clicking div[id^="container-"] directly
                    # (EffectiveCPMNetwork container) even if no clickable child found
                    try:
                        containers = page.locator('div[id^="container-"]').all()
                        for cont in containers[:3]:
                            if not cont.is_visible(timeout=1000):
                                continue
                            try:
                                cont.click(timeout=3000)
                                total_ads_clicked += 1
                                stats['ads'] = total_ads_clicked
                                _push_session_stats(uid, stats)
                                logger.ad('EffectiveCPMNetwork container click berhasil (fallback)')
                                time.sleep(random.uniform(2, 4))
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass
            except Exception as e:
                logger.warn(f'Phase footer native-banner error: {e}')
        # =============================================================

        while time.time() < deadline and articles_visited < profile['max_articles']:
            set_phase(f'visiting article {articles_visited + 1}/{profile["max_articles"]}')
            articles = discover_articles(page, max_count=profile['max_articles'] * 2)
            if not articles:
                logger.warn('No article cards found; scrolling...')
                hi.mixed_browse_session(duration_s=random.uniform(3, 6))
                time.sleep(1.2)
                articles = discover_articles(page, max_count=profile['max_articles'] * 2)
                if not articles:
                    logger.warn('Still no articles; ending session')
                    break

            article = random.choice(articles[:8])
            try:
                title = article.locator('.article-title, h3').first.inner_text(timeout=2000)[:70]
            except Exception:
                title = ''
            logger.article(f'Opening article #{articles_visited + 1}: "{title}..."')

            try:
                page.bring_to_front()
            except Exception:
                pass

            set_phase(f'opening article #{articles_visited + 1}')
            # FIX: Article modal click was failing because pyautogui click
            # doesn't trigger Playwright's event listeners. Use Playwright's
            # .click() FIRST (which properly triggers JS event handlers that
            # open the modal), and only fall back to pyautogui if .click()
            # fails. Also add retry logic with scroll-into-view.
            modal_opened = False
            for _click_attempt in range(3):
                try:
                    # Try Playwright click first (reliable for JS-triggered modals)
                    article.scroll_into_view_if_needed(timeout=3000)
                    time.sleep(0.3)
                    article.click(timeout=5000)
                except Exception:
                    # Fallback to pyautogui if Playwright click fails
                    try:
                        box = article.bounding_box()
                        if box:
                            pyautogui_click_element(box, logger, page=page)
                    except Exception:
                        pass
                # Check if modal opened
                try:
                    page.wait_for_selector('#articleModal.open', state='attached', timeout=5000)
                    page.wait_for_selector('#articleModal.open', state='visible', timeout=3000)
                    modal_opened = True
                    break
                except Exception:
                    if _click_attempt < 2:
                        # Scroll a bit and retry with a different article position
                        hi.human_scroll(down=random.random() < 0.5, steps=random.randint(1, 3))
                        time.sleep(0.5)
                        # Try to re-locate the article (DOM might have changed)
                        try:
                            article.scroll_into_view_if_needed(timeout=2000)
                        except Exception:
                            pass
            if not modal_opened:
                logger.warn('Modal did not open after 3 attempts; skipping article')
                continue

            time.sleep(0.4)
            logger.article('Modal opened, reading content...')
            set_phase(f'reading article #{articles_visited + 1}')

            try:
                hi.human_move_to(960, 500, duration=0.5)
                time.sleep(0.2)
                # v10.11 UPGRADE — MOD 3: persingkat article read time
                # (dari random.uniform(6,12) → random.uniform(2,4))
                hi.mixed_browse_session(duration_s=random.uniform(2, 4))
                if random.random() < 0.3:
                    hi.random_keystrokes(count=random.randint(1, 2))
                hi.random_idle()
            except Exception as e:
                logger.warn(f'Modal read error: {e}')

            articles_visited += 1
            stats['articles'] = articles_visited
            _push_session_stats(uid, stats)  # v10.3: real-time dashboard update

            # ============================================================
            # v11.0 UPGRADE — MOD 2: FOOTER BANNER CLICK DI ARTICLE (klik semua)
            # ============================================================
            try:
                set_phase(f'footer banner article #{articles_visited}')
                logger.step(f'Scroll ke footer & cari Adsterra native banner (article #{articles_visited})...')
                hi.human_scroll(down=True, steps=random.randint(6, 9))
                time.sleep(random.uniform(0.4, 0.7))

                try:
                    vp = page.viewport_size
                    if vp:
                        footer_x = vp['width'] // 2
                        footer_y = int(vp['height'] * 0.85)
                        sx, sy = _viewport_to_screen(page, footer_x, footer_y)
                        hi.human_move_to(sx, sy, duration=random.uniform(0.5, 0.8))
                        logger.mouse(f'Mouse ke footer ad area ({sx},{sy})')
                        time.sleep(random.uniform(0.3, 0.6))
                except Exception as e:
                    logger.warn(f'Footer mouse move (article) gagal: {e}')

                footer_article_ads = discover_native_adsterra_ads(
                    page, max_count=4, location='article_bottom'
                )
                # v12.0 Issue 5 FIX: Retry loop for article footer ads too
                if not footer_article_ads:
                    logger.ad('   Tidak ada ads di article footer, retry setelah 3s...')
                    time.sleep(3)
                    footer_article_ads = discover_native_adsterra_ads(
                        page, max_count=4, location='article_bottom'
                    )
                if footer_article_ads:
                    n_panels = len(footer_article_ads)
                    logger.ok(f'Ditemukan {n_panels} native Adsterra banner di article footer')
                    # v11.1 UPGRADE 3: Shuffle article footer ads randomly, click ALL (up to 4)
                    # without MIN_AD_CLICKS_PER_SESSION break check
                    random.shuffle(footer_article_ads)
                    for ad_idx, chosen_ad in enumerate(footer_article_ads):
                        if _check_deadline():
                            logger.warn('Deadline exceeded during article footer banner clicks')
                            break
                        try:
                            success, tracking = click_native_adsterra_and_process(
                                page, chosen_ad, logger, total_ads_clicked,
                                context, request_log, source=f'article-footer-{ad_idx+1}',
                                deadline=deadline
                            )
                            if success:
                                total_ads_clicked += 1
                                stats['ads'] = total_ads_clicked
                                if tracking:
                                    total_tracking += 1
                                    stats['tracking'] = total_tracking
                                _push_session_stats(uid, stats)
                                logger.ad(f'Article footer banner click #{ad_idx+1}/{n_panels} berhasil')
                            else:
                                logger.warn(f'Article footer banner click #{ad_idx+1} gagal')
                        except Exception as e:
                            logger.warn(f'Article footer banner click #{ad_idx+1} gagal: {e}')
                        time.sleep(random.uniform(2, 4))
                else:
                    logger.step('Tidak ada native Adsterra banner di article footer.')
            except Exception as e:
                logger.warn(f'Phase footer-banner article error: {e}')
            # ============================================================

            set_phase(f'closing article #{articles_visited + 1}')
            logger.step('Closing article modal...')
            try:
                hi.escape_key()
                time.sleep(0.3)
                if page.locator('#articleModal.open').count() > 0:
                    page.evaluate('''() => {
                        const m = document.getElementById('articleModal');
                        if (m) m.classList.remove('open');
                        document.body.style.overflow = '';
                    }''')
            except Exception:
                pass
            time.sleep(0.5)

        # ============================================================
        # v10.11 UPGRADE — MOD 2: hapus fallback homepage AD scan.
        # Sebelumnya: jika total_ads_clicked == 0, scan discover_ads &
        # klik 2 homepage ads. Sekarang: footer banner click sudah
        # dilakukan di awal (homepage) & per artikel. Tidak perlu
        # fallback scan generic lagi.
        # ============================================================

        # v11.0 UPGRADE — MOD 2: Catch-up ad clicks jika belum mencapai MIN_AD_CLICKS_PER_SESSION
        if total_ads_clicked < MIN_AD_CLICKS_PER_SESSION and time.time() < deadline:
            set_phase('catch-up ad clicks')
            logger.step(f'Belum mencapai minimum {MIN_AD_CLICKS_PER_SESSION} klik iklan '
                        f'(saat ini: {total_ads_clicked}). Mencoba klik tambahan...')
            for attempt in range(MIN_AD_CLICKS_PER_SESSION - total_ads_clicked + 2):
                if total_ads_clicked >= MIN_AD_CLICKS_PER_SESSION:
                    break
                if _check_deadline():
                    break
                try:
                    # Scroll untuk mencari lebih banyak iklan
                    hi.human_scroll(down=random.random() < 0.7, steps=random.randint(4, 8))
                    time.sleep(random.uniform(0.5, 1.0))

                    # Cari iklan di seluruh halaman (bukan hanya footer)
                    catchup_ads = discover_native_adsterra_ads(
                        page, max_count=4, location='auto'
                    )
                    # v12.0 Issue 5 FIX: Fallback to discover_ads() (legacy) if
                    # discover_native_adsterra_ads() finds nothing
                    if not catchup_ads:
                        catchup_ads = discover_ads(page, max_count=4)
                    if catchup_ads:
                        chosen = random.choice(catchup_ads)
                        success, tracking = click_native_adsterra_and_process(
                            page, chosen, logger, total_ads_clicked,
                            context, request_log, source='catchup',
                            deadline=deadline
                        )
                        if success:
                            total_ads_clicked += 1
                            stats['ads'] = total_ads_clicked
                            if tracking:
                                total_tracking += 1
                                stats['tracking'] = total_tracking
                            _push_session_stats(uid, stats)
                            logger.ad(f'Catch-up ad click berhasil (total: {total_ads_clicked})')
                        time.sleep(random.uniform(3, 6))
                    else:
                        # v12.0 Issue 5 FIX: Try clicking div[id^="container-"] directly
                        # (EffectiveCPMNetwork container) even if no clickable child found
                        try:
                            containers = page.locator('div[id^="container-"]').all()
                            clicked_container = False
                            for cont in containers[:3]:
                                if not cont.is_visible(timeout=1000):
                                    continue
                                try:
                                    cont.click(timeout=3000)
                                    total_ads_clicked += 1
                                    stats['ads'] = total_ads_clicked
                                    _push_session_stats(uid, stats)
                                    logger.ad('EffectiveCPMNetwork container click berhasil (catchup fallback)')
                                    clicked_container = True
                                    time.sleep(random.uniform(2, 4))
                                    break
                                except Exception:
                                    continue
                            if clicked_container:
                                continue
                        except Exception:
                            pass
                        # Jika tidak ada iklan, scroll lagi
                        hi.human_scroll(down=True, steps=random.randint(5, 10))
                        time.sleep(random.uniform(1.0, 2.0))
                except Exception as e:
                    logger.warn(f'Catch-up ad click gagal: {e}')
                    time.sleep(2)

            if total_ads_clicked >= MIN_AD_CLICKS_PER_SESSION:
                logger.ok(f'Target minimum ad clicks tercapai: {total_ads_clicked} klik')
            else:
                logger.warn(f'Hanya {total_ads_clicked} klik iklan tercapai (target: {MIN_AD_CLICKS_PER_SESSION})')

        try:
            if not page.url.startswith(TARGET_URL):
                page.goto(TARGET_URL, wait_until='domcontentloaded', timeout=20000)
            try:
                page.bring_to_front()
            except Exception:
                pass
            # v10.11 UPGRADE — MOD 3: persingkat final browse (dari 5-10s → 2-4s)
            hi.mixed_browse_session(duration_s=random.uniform(2, 4))
            hi.random_idle()
        except Exception:
            pass

        duration = int(time.time() - started_at)
        # v10.3: Lenient success definition.
        #   success = reached target page AND engaged (articles OR ads)
        #   (implicit partial) = reached target but no clicks — caller decides
        #   (implicit failed) = never reached target (e.g. proxy failed mid-goto)
        stats['success'] = (
            stats.get('reached_target', False)
            and (stats['articles'] > 0 or stats['ads'] > 0)
        )
        stats['session_active'] = False
        _push_session_stats(uid, stats)  # final flush for this session
        logger.ok(f'Session done | reached_target={stats["reached_target"]} '
                  f'articles={stats["articles"]} ads={stats["ads"]} '
                  f'tracking={stats["tracking"]} duration={duration}s')
        logger.info(f'Total Monetag requests: {len(request_log)}')

    except Exception as e:
        duration = int(time.time() - started_at)
        stats['error'] = str(e)
        err_lower = str(e).lower()
        stats['proxy_failed'] = (
            'proxy' in err_lower or 'net::' in err_lower or
            'err_proxy' in err_lower or 'timeout' in err_lower or
            'connection' in err_lower or 'failed to load' in err_lower
        )
        stats['session_active'] = False
        _push_session_stats(uid, stats)  # v10.3: flush error state to dashboard
        logger.error(f'Session failed: {e}')
    finally:
        if session and ad_manager and session.get('mode') == 'antidetect':
            ad_manager.close_and_cleanup(session)
        else:
            # v10.4: Patchright fallback cleanup — re-raise TimeoutError so
            # the main-loop watchdog can still interrupt hung close() calls.
            try:
                if context:
                    context.close()
            except TimeoutError:
                raise
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except TimeoutError:
                raise
            except Exception:
                pass
        set_phase('idle')

    stats['duration'] = int(time.time() - started_at)
    stats['request_log'] = request_log
    return stats

# ====================================================================
# _launch_patchright (tidak diubah, sudah pakai ignore_https_errors)
# ====================================================================
def _launch_patchright(pw, profile, sync_config, proxy, logger):
    import human_input as hi

    launch_args = [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        f'--window-size={sync_config.get("screen_width", 1920)},{sync_config.get("screen_height", 1080)}',
        f'--window-position={BROWSER_X},{BROWSER_Y}',
        '--start-maximized',
        '--ignore-certificate-errors',
        '--disable-features=IsolateOrigins,site-per-process',
        f'--lang={profile["locale"]}',
        '--disable-infobars',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-ipc-flooding-protection',
        '--enable-features=NetworkService,NetworkServiceInProcess',
        '--force-color-profile=srgb',
        '--disable-features=MediaRouter,TranslateUI,DnsOverHttps,GlobalMediaControls,OptimizationGuide,SideSearch,ReadAloud,CertificateTransparency,PrivacySandboxSettings4',
        '--enable-features=ScreenCaptureKit',
    ]
    launch_args.extend([
        '--disable-features=WebRTC',
        '--enforce-webrtc-ip-permission-check',
        '--webrtc-ip-handling-policy=disable_non_proxied_udp',
    ])
    launch_args.extend([
        '--disable-features=DnsOverHttps',
    ])

    proxy_username = proxy.get('username')
    proxy_password = proxy.get('password')
    launch_kwargs = {
        'headless': False,
        'args': launch_args,
    }
    if proxy_username:
        launch_kwargs['proxy'] = {
            'server': proxy['server'],
            'username': proxy_username,
            'password': proxy_password or '',
        }
        logger.info(f'Proxy auth: user={proxy_username}')
    else:
        launch_args.append(f'--proxy-server={proxy["server"]}')

    browser = pw.chromium.launch(**launch_kwargs)

    device_vp = profile['device'].get('viewport', {}) or {}
    is_mobile_profile = sync_config.get('is_mobile', False) or sync_config.get('os') == 'Android'
    if is_mobile_profile and device_vp:
        vp_w = device_vp.get('width', sync_config.get('screen_width', 412))
        vp_h = device_vp.get('height', sync_config.get('screen_height', 915))
    else:
        vp_w = sync_config.get('screen_width', profile['device']['viewport']['width'])
        vp_h = sync_config.get('screen_height', profile['device']['viewport']['height'])

    ua = sync_config.get('ua', profile['user_agent'])
    # v9.5: prefer device-level has_touch / device_scale_factor bila ada
    # (dari auto-generated device pool), fallback ke sync_config atau UA-detect.
    device_touch = profile['device'].get('has_touch')
    if device_touch is None:
        has_touch = bool(sync_config.get('has_touch', 'Android' in ua or 'iPhone' in ua))
    else:
        has_touch = bool(device_touch)
    device_dsf = profile['device'].get('device_scale_factor')
    if device_dsf is not None:
        device_scale = float(device_dsf)
    else:
        device_scale = sync_config.get('device_scale_factor', 2.625 if has_touch else 1.0)

    safe_tz = _validate_timezone_for_chromium(profile['timezone'])
    if safe_tz != profile['timezone']:
        logger.warn(f'Timezone {profile["timezone"]!r} not available; using {safe_tz!r}')

    sync_tz = sync_config.get('timezone', profile['timezone'])
    if sync_tz != profile['timezone']:
        logger.warn(
            f"DESYNC: profile.timezone={profile['timezone']!r} but "
            f"sync_config.timezone={sync_tz!r} — using profile.timezone for Chromium."
        )

    ch_ua_headers = derive_ch_ua_headers(ua)

    context = browser.new_context(
        viewport={'width': vp_w, 'height': vp_h},
        locale=profile['locale'],
        timezone_id=safe_tz,
        user_agent=ua,
        ignore_https_errors=True,  # Sudah ada
        java_script_enabled=True,
        has_touch=has_touch,
        device_scale_factor=device_scale,
        is_mobile=has_touch,
        extra_http_headers={
            'Accept-Language': f'{profile["locale"]},en;q=0.9',
            **ch_ua_headers,
        },
    )

    apply_stealth_py(
        context,
        locale=profile['locale'],
        user_agent=ua,
        chrome_version=CHROME_MAJOR_VERSION,
        use_patchright=True,
        profile_config=sync_config,
    )

    page = context.new_page()
    page.set_viewport_size({'width': vp_w, 'height': vp_h})

    return {
        'page': page,
        'context': context,
        'browser': browser,
        'mode': 'patchright',
    }

# ====================================================================
# Main (tidak diubah)
# ====================================================================
def main():
    parser = argparse.ArgumentParser(description='Visit Bot Python v9.0 — Anti-Detect + Profile Sync')
    parser.add_argument('--limit', type=int, default=100, help='Max users to run')
    parser.add_argument('--user', type=int, default=None, help='Run only this user index (1-based)')
    parser.add_argument('--start', type=int, default=1, help='Start from this user index')
    parser.add_argument('--timeout', type=int, default=420, help='Per-user hard timeout (seconds)')
    parser.add_argument('--port', type=int, default=8080, help='Web preview port')
    parser.add_argument('--no-server', action='store_true', help='Disable web preview server')
    parser.add_argument('--clear-profiles', action='store_true', help='Clear saved storage_state profiles')
    parser.add_argument('--no-skip', action='store_true', help='Disable random user skip')
    parser.add_argument('--cooldown-min', type=float, default=None, help='Override min cooldown')
    parser.add_argument('--cooldown-max', type=float, default=None, help='Override max cooldown')
    parser.add_argument('--max-regen', type=int, default=MAX_PROFILE_REGENERATIONS,
                        help=f'Max profile regenerations per slot (default {MAX_PROFILE_REGENERATIONS})')
    parser.add_argument('--mode', type=str, default='antidetect',
                        choices=['antidetect', 'patchright'],
                        help='Browser mode: antidetect (RECOMMENDED) or patchright (fallback)')
    parser.add_argument('--browser-type', type=str, default='adspower',
                        choices=['adspower', 'multilogin', 'dolphin'],
                        help='Anti-detect browser type (default: adspower)')
    args = parser.parse_args()

    if args.clear_profiles and PROFILES_DIR.exists():
        for f in PROFILES_DIR.glob('*.json'):
            try:
                f.unlink()
            except Exception:
                pass
        log.info('', f'Cleared {PROFILES_DIR}/')

    proxies = load_proxies()
    if not proxies:
        log.error('', 'No proxies available. Exiting.')
        sys.exit(1)

    # v11.0 UPGRADE — MOD 3: Gunakan 1000 Android profiles dari file modular
    from android_profiles import generate_android_profiles, ANDROID_PROFILE_COUNT
    pool_size = max(args.limit, 1)
    if args.user:
        # Single-user mode: cukup generate 1 device untuk user ini
        pool_size = 1
    try:
        android_profiles = generate_android_profiles(count=min(pool_size, ANDROID_PROFILE_COUNT))
        set_device_pool(android_profiles[:args.limit])
        log.banner(f'DEVICE POOL: loaded {len(android_profiles[:args.limit])} Android profiles for {pool_size} user(s)')
    except Exception as e:
        log.warn('', f'Android profiles generation failed ({e}); using 4-device fallback')

    if args.mode == 'patchright':
        start_xvfb()
    else:
        log.info('', 'Anti-detect mode: Xvfb not required (browser runs externally)')

    adspower_api_key = os.environ.get('ADSPOWER_API_KEY', '')
    adspower_profile_id = os.environ.get('ADSPOWER_PROFILE_ID', '')
    adspower_group_id = os.environ.get('ADSPOWER_GROUP_ID', '')
    # v9.2: pass the full adspower_config dict to AntiDetectManager
    # instead of just api_key/profile_id/group_id. This ensures
    # base_url and port are also resolved from the API config (not just
    # env vars), and keeps a single source of truth for AdsPower
    # credentials. The constructor of AntiDetectManager accepts an
    # `adspower_config` kwarg that overrides all separate args.
    ad_manager = AntiDetectManager(
        mode=args.mode,
        browser_type=args.browser_type,
        api_key=adspower_api_key,
        profile_id=adspower_profile_id,
        group_id=adspower_group_id,
        adspower_config=_ADSPOWER_CONFIG_RESOLVED or None,
    )
    if ad_manager.is_antidetect_mode:
        log.ok('', f'Anti-detect browser connected: {args.browser_type}')
    else:
        log.warn('', f'Anti-detect browser not available, falling back to Patchright + enhanced stealth')

    if not args.no_server:
        log.banner(f'Starting web preview server on port {args.port}')
        start_server_in_thread(port=args.port)
        time.sleep(1.5)
        log.ok(f'Web preview live at http://localhost:{args.port}')

    mode_str = 'ANTIDETECT' if ad_manager.is_antidetect_mode else 'PATCHRIGHT+FALLBACK'
    log.banner(f'Visit Bot v9.0 — {args.limit} user(s) — target={TARGET_URL} — mode={mode_str}')
    log.info('', f'Mode: {mode_str}')
    if ad_manager.is_antidetect_mode:
        log.info('', f'Anti-detect browser: {args.browser_type}')
    log.info('', f'Profile synchronization: ENABLED')
    log.info('', f'WebRTC leak prevention: ENABLED')
    log.info('', f'DNS leak prevention: ENABLED')
    log.info('', f'Per-user timeout: {args.timeout}s')
    log.info('', f'Proxies available: {len(proxies)}')
    log.info('', f'1:1 Profile-Proxy binding: ENABLED')

    overall_start = time.time()
    # v10.3: 'total' now reflects users ACTUALLY PROCESSED so far
    # (incremented after each session ends), NOT args.limit (planned).
    # 'planned_total' preserves the original target for the progress bar.
    summary = {
        'total': 0,                  # actual users processed so far
        'planned_total': args.limit,  # target count for progress bar
        'success': 0, 'partial': 0, 'failed': 0,
        'reached_target': 0,         # users who loaded the target page
        'skipped': 0,                # anti-burst cooldown slots (NOT failures)
        'articles': 0, 'ads': 0, 'tracking': 0, 'duration': 0,
        'regenerated': 0,
    }

    update_state(progress=f'0 / {args.limit}', stats=summary)

    with sync_playwright() as pw:
        visited = 0
        skipped = 0
        total = args.limit
        profile_counter = args.start
        cd_min = args.cooldown_min if args.cooldown_min is not None else COOLDOWN_MIN
        cd_max = args.cooldown_max if args.cooldown_max is not None else COOLDOWN_MAX

        while visited < total:
            profile = None
            stats = None
            for regen_attempt in range(args.max_regen):
                if args.user:
                    profile = make_user_profile(args.user, proxies)
                else:
                    profile = make_user_profile(profile_counter, proxies)

                if profile is None:
                    log.error('', 'Proxy pool exhausted. Stopping.')
                    break

                update_state(
                    progress=f'{visited + skipped + 1} / {total}',
                    current_user=profile['id'],
                    current_device=profile['device']['name'],
                )
                if regen_attempt > 0:
                    log.banner(f'[{visited + skipped + 1}/{total}] {profile["id"]} (REGEN #{regen_attempt})')
                else:
                    log.banner(f'[{visited + skipped + 1}/{total}] {profile["id"]}')

                # v10.8: HARD WATCHDOG with THREAD-BASED TIMEOUT + ESCALATION.
                #
                # CRITICAL FINDING (v10.8): signal.alarm (SIGALRM) CANNOT
                # interrupt Playwright sync API calls. Playwright's sync
                # wrapper blocks inside C extensions that don't yield to
                # Python's signal handler. So even when the watchdog
                # "fires", the TimeoutError isn't actually raised until the
                # current Playwright call returns — which on a frozen
                # browser never happens.
                #
                # Solution: dual-layer approach.
                #   Layer 1: signal.alarm — sets the budget. When it fires,
                #            the handler runs IF the main thread is in
                #            Python code (between Playwright calls). This
                #            handles the common case.
                #   Layer 2: ESCALATION THREAD — a daemon thread that wakes
                #            30s after the watchdog budget expires and calls
                #            os._exit(2) if the process is still alive AND
                #            the current user hasn't completed. This guarantees
                #            progress even if Layer 1 fails (e.g. main thread
                #            stuck in a Playwright C call). The bot will
                #            restart (needs supervisor: docker restart=always
                #            / systemd Restart=on-failure).
                #
                # The watchdog handler ALSO must NOT call any Playwright
                # operations (no cleanup_all, no context.close) — those
                # would just hang again. Only use AdsPower HTTP API for
                # cleanup (it's signal-interruptible).
                watchdog_budget = int(args.timeout) + 90
                stats = None
                _watchdog_old_handler = signal.SIG_DFL

                # Cancellation flag for the escalation thread.
                # Set to True when run_user returns (normally or via exception).
                _watchdog_cancel = threading.Event()

                def _watchdog_handler(signum, frame):
                    raise TimeoutError(
                        f"run_user watchdog: exceeded {watchdog_budget}s budget — "
                        f"forcing skip to next user"
                    )

                def _escalation_watchdog():
                    """Daemon thread: if process still alive 30s after
                    watchdog budget AND run_user hasn't returned, force-exit.
                    This is the GUARANTEED progress mechanism when
                    signal.alarm can't interrupt a frozen Playwright call."""
                    # Sleep until budget + 30s grace. Use Event.wait so we
                    # can be cancelled cleanly if run_user returns on time.
                    if _watchdog_cancel.wait(timeout=watchdog_budget + 30):
                        # Cancelled — run_user returned, no escalation needed.
                        return
                    # Timer expired AND not cancelled → main thread is wedged.
                    # Force-exit so the supervisor (docker/systemd) can restart.
                    try:
                        sys.stderr.write(
                            f"\n!!! ESCALATION: process still alive {watchdog_budget + 30}s "
                            f"after watchdog budget — Playwright call is wedged. "
                            f"Forcing os._exit(2). Restart bot via supervisor. !!!\n"
                        )
                        sys.stderr.flush()
                    except Exception:
                        pass
                    os._exit(2)

                _escalation_thread = threading.Thread(
                    target=_escalation_watchdog, daemon=True
                )
                _escalation_thread.start()

                try:
                    _watchdog_old_handler = signal.signal(signal.SIGALRM, _watchdog_handler)
                    signal.alarm(watchdog_budget)
                    try:
                        stats = run_user(pw, profile, args, ad_manager=ad_manager)
                    finally:
                        signal.alarm(0)
                        try:
                            signal.signal(signal.SIGALRM, _watchdog_old_handler)
                        except Exception:
                            pass
                except TimeoutError as watchdog_err:
                    log.error('', f'WATCHDOG triggered for {profile["id"]}: {watchdog_err}')
                    # v10.8: CRITICAL — do NOT call ad_manager.cleanup_all()
                    # here. cleanup_all() calls context.close() / browser.close()
                    # which are Playwright operations that ALSO hang on a frozen
                    # browser. The watchdog would fire, the handler would run,
                    # then the bot would hang AGAIN in cleanup — defeating the
                    # watchdog entirely (this was the v10.4-v10.7 bug).
                    #
                    # Instead: only use the AdsPower HTTP API stop_profile,
                    # which is signal-interruptible (it's just requests.post()
                    # with a timeout). Skip ALL Playwright cleanup — the
                    # browser process will be killed when stop_profile hits
                    # the AdsPower API.
                    try:
                        if ad_manager and ad_manager.is_antidetect_mode and ad_manager._client:
                            for sess in list(ad_manager._sessions):
                                pid = sess.get('profile_id')
                                if pid:
                                    try:
                                        # HTTP API only — no Playwright calls.
                                        # This kills the browser process from
                                        # AdsPower's side, releasing the port.
                                        ad_manager._client.stop_profile(pid, silent=True)
                                        log.info('', f'  → stop_profile({pid}) via HTTP API')
                                    except Exception:
                                        pass
                            # Forget the dead sessions — don't try to close them
                            ad_manager._sessions.clear()
                    except Exception:
                        pass
                    log.warn('', f'Force-skipping to next user after watchdog timeout')
                    stats = {
                        'articles': 0, 'ads': 0, 'tracking': 0,
                        'success': False, 'error': 'watchdog_timeout',
                        'duration': watchdog_budget,
                        'request_log': [],
                        'reached_target': False,
                        'session_active': False,
                        'proxy_failed': False,
                    }
                except Exception as user_err:
                    # Catch-all: never let one user's exception kill the loop.
                    log.error('', f'run_user crashed for {profile["id"]}: {user_err}')
                    stats = {
                        'articles': 0, 'ads': 0, 'tracking': 0,
                        'success': False, 'error': f'crash: {user_err}',
                        'duration': 0, 'request_log': [],
                        'reached_target': False,
                        'session_active': False,
                        'proxy_failed': False,
                    }
                    try:
                        signal.alarm(0)
                        signal.signal(signal.SIGALRM, _watchdog_old_handler)
                    except Exception:
                        pass
                finally:
                    # Cancel the escalation thread — run_user has returned
                    # (normally or via exception), so no escalation needed.
                    _watchdog_cancel.set()

                if stats is None:
                    # Should not happen, but defensive — treat as failed
                    stats = {
                        'articles': 0, 'ads': 0, 'tracking': 0,
                        'success': False, 'error': 'no_stats',
                        'duration': 0, 'request_log': [],
                        'reached_target': False, 'session_active': False,
                        'proxy_failed': False,
                    }

                # BUG FIX #5: selalu release proxy setelah run_user selesai,
                # tidak peduli sukses atau gagal. Tanpa ini, proxy pool akan
                # habis setelah 5 user sukses (proxy tetap terkunci di
                # _used_proxy_keys selamanya).
                release_proxy(profile)

                if stats.get('proxy_failed'):
                    log.warn('', f'Proxy failed for {profile["id"]} — regenerating')
                    summary['regenerated'] += 1
                    profile_counter += 1
                    # BUG FIX #2: tambah delay antar regen attempt untuk
                    # menghindari cascade rate-limit dari proxy provider.
                    # Tanpa delay, 5 user x ~700ms = 5 koneksi dalam 3.5
                    # detik → proxy provider rate-limit aktif → semua
                    # ditolak dengan URLError dalam 600-700ms.
                    regen_delay = random.uniform(8.0, 15.0)
                    log.info('', f'⏳ Cooldown {regen_delay:.1f}s sebelum percobaan proxy berikutnya (mencegah rate-limit cascade)...')
                    time.sleep(regen_delay)
                    continue
                else:
                    break

            if profile is None:
                break

            visited += 1
            profile_counter += 1

            # v10.3: Increment 'total' (actual processed count, not planned).
            summary['total'] = visited + skipped

            # v10.3: New 4-way classification.
            #   success  = reached target AND (articles OR ads)
            #   partial  = reached target but no engagement (loaded homepage only)
            #   failed   = didn't reach target (proxy_failed, browser launch error, etc.)
            if stats.get('reached_target'):
                summary['reached_target'] += 1
                if stats.get('success') or stats.get('articles', 0) > 0 or stats.get('ads', 0) > 0:
                    summary['success'] += 1
                else:
                    summary['partial'] += 1
            else:
                summary['failed'] += 1

            summary['articles'] += stats.get('articles', 0)
            summary['ads'] += stats.get('ads', 0)
            summary['tracking'] += stats.get('tracking', 0)
            summary['duration'] += stats.get('duration', 0)
            update_state(stats=summary)

            if visited < total:
                will_skip = (not args.no_skip) and (random.random() < SKIP_PROBABILITY)
                if will_skip:
                    skip_cd = random.uniform(SKIP_EXTRA_COOLDOWN_MIN, SKIP_EXTRA_COOLDOWN_MAX)
                    log.info('', f'Anti-burst: skipping next slot (extended cooldown {skip_cd:.1f}s)')
                    # v10.3: Anti-burst skip is NOT a failed user — it's a planned
                    # cooldown slot with no actual session. Track separately.
                    summary['skipped'] += 1
                    summary['total'] = visited + skipped + 1  # include the skipped slot
                    update_state(stats=summary)
                    time.sleep(skip_cd)
                    skipped += 1
                else:
                    cd = random.uniform(cd_min, cd_max)
                    log.info('', f'Cooldown {cd:.1f}s before next user...')
                    time.sleep(cd)

        if skipped or summary.get('regenerated', 0) > 0:
            log.info('', f'Summary: visited={visited} skipped={skipped} regenerated={summary.get("regenerated", 0)}')

    ad_manager.cleanup_all()

    summary['duration'] = int(time.time() - overall_start)
    summary['total'] = visited + skipped  # v10.3: final actual count
    log.summary(summary)
    update_state(current_phase='done', stats=summary)

if __name__ == '__main__':
    main()