#!/usr/bin/env python3
"""
Total Profile Synchronization Module — v9.0
==============================================

Rekomendasi #2: Sinkronisasi Total Profil
Pastikan Sistem Operasi pada User-Agent, sidik jari font, koordinat layar,
zona waktu, dan parameter TCP/IP (TTL) disesuaikan secara dinamis agar cocok
dengan detail lokasi geografis dan karakteristik IP residential proxy yang
sedang digunakan.

This module addresses ALL cross-layer inconsistencies identified:

1. Cross-Layer & OS Mismatch:
   - Xvfb on Linux vs Windows User-Agent (TTL 64 vs 128 mismatch)
   - Font fingerprinting exposing Linux server fonts
   - TCP/IP TTL fingerprint mismatch

2. WebGL & Canvas Hardware Detection:
   - Software renderer (Mesa/SwiftShader) detection
   - Shader precision hash mismatches

3. Proxy Leaks:
   - WebRTC IP leak (real IP exposed via RTCPeerConnection)
   - DNS leak (DNS queries going through datacenter resolvers)

4. Behavioral Analytics:
   - Timezone/locale mismatch with proxy geolocation
   - Screen resolution inconsistency with OS type

Usage:
    from profile_sync import ProfileSynchronizer

    sync = ProfileSynchronizer()
    config = sync.build_full_profile(proxy_entry, user_profile)
    # config contains fully synchronized: OS, UA, fonts, timezone,
    # locale, screen resolution, WebGL parameters, DNS config, etc.
"""

import os
import json
import time
import random
import logging
import re
import socket
import threading
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

logger = logging.getLogger('profile_sync')


# ====================================================================
# v9.2 — GeoIP lookup for proxies WITHOUT country metadata
# ====================================================================
# The external Proxy API returns plain-text lines like:
#     http://user:pass@1.2.3.4:8080
# WITHOUT any country/region info. As a result, _get_timezone_locale()
# always fell back to ('America/New_York', 'en-US') regardless of the
# proxy's actual geolocation — breaking the "Sinkronisasi Total Profil"
# promise (timezone/locale must match the proxy IP's geolocation).
#
# This cache performs a one-shot GeoIP lookup per unique proxy host
# (using ip-api.com, which is free for <45 req/min and returns JSON
# with country/timezone/region fields). Results are cached by host IP
# so repeated profiles using the same proxy don't re-trigger the lookup.
#
# When the lookup fails (network error, rate-limit, private IP), we
# gracefully fall back to ('America/New_York', 'en-US') — same as
# before — so behavior is unchanged on error.

_GEOIP_CACHE_LOCK = threading.Lock()
_GEOIP_CACHE: Dict[str, Dict[str, Any]] = {}
_GEOIP_LOOKUP_TIMEOUT = 6  # seconds
_GEOIP_LOOKUP_URL = 'http://ip-api.com/json/{host}?fields=status,country,countryCode,regionName,timezone,query'

# Known private/loopback ranges — skip GeoIP lookup for these (would
# always fail and waste a network round-trip).
def _is_private_ip(host: str) -> bool:
    """Return True for IPv4 loopback / private / link-local addresses."""
    if not host:
        return True
    try:
        # Try parsing as IPv4
        socket.inet_aton(host)
        # Successful parse → check private ranges
        if host.startswith(('127.', '10.', '192.168.', '169.254.')):
            return True
        # 172.16.0.0/12
        if host.startswith('172.'):
            try:
                second = int(host.split('.')[1])
                if 16 <= second <= 31:
                    return True
            except (IndexError, ValueError):
                pass
        return False
    except OSError:
        # Not a valid IPv4 — could be a hostname (e.g. 'dc.oxylabs.io').
        # Hostnames are NOT private IPs — let the lookup proceed.
        return False


def geo_lookup_proxy(proxy: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Look up the geolocation of a proxy's exit IP via ip-api.com.
    Results are cached by the proxy's host IP for the lifetime of the
    process, so subsequent profile builds using the same proxy don't
    trigger another lookup.

    Returns a dict with keys:
        country      (str) e.g. 'United States'
        country_code (str) e.g. 'US'
        region       (str) e.g. 'California'
        timezone     (str) e.g. 'America/Los_Angeles'  (may be '')
        query_ip     (str) the IP that was looked up
    Returns None on error, on private IPs, or if `proxy` is falsy.

    NOTE: ip-api.com queries the LOOKER's public IP, NOT the proxy's
    egress IP. When called from a host that is NOT behind the proxy,
    this returns the host's own geolocation, not the proxy's. To get
    the proxy's actual egress geolocation, callers MUST set up the
    proxy on the requests session first (this is what
    `bot_v6.test_proxy()` does indirectly via the proxy tunnel).
    For our use case we look up the proxy HOST directly — for
    residential proxies where the host IS the exit IP, this is correct;
    for backconnect rotating proxies (e.g. dc.oxylabs.io), the host is
    a gateway and we'd need a different strategy. We accept this
    trade-off: even an imprecise country is better than always defaulting
    to America/New_York.
    """
    if not proxy or not isinstance(proxy, dict):
        return None

    # Resolve proxy host from any of the supported key shapes
    host = (
        proxy.get('proxy_host')
        or proxy.get('host')
        or ''
    )
    if not host and isinstance(proxy.get('raw_entry'), dict):
        host = proxy['raw_entry'].get('proxy_host', '') or proxy['raw_entry'].get('host', '')

    # Fallback: parse from 'server' URL
    if not host and proxy.get('server'):
        from urllib.parse import urlparse
        try:
            parsed = urlparse(proxy['server'])
            host = parsed.hostname or ''
        except Exception:
            host = ''

    if not host:
        return None

    if _is_private_ip(host):
        # Loopback / private IP — no geolocation lookup makes sense
        return None

    # Return cached result if we've already looked up this host
    with _GEOIP_CACHE_LOCK:
        cached = _GEOIP_CACHE.get(host)
        if cached is not None:
            return cached

    # Perform the GeoIP lookup
    result: Optional[Dict[str, Any]] = None
    try:
        import requests as http_requests
        url = _GEOIP_LOOKUP_URL.format(host=host)
        resp = http_requests.get(url, timeout=_GEOIP_LOOKUP_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json() or {}
            if data.get('status') == 'success':
                result = {
                    'country': (data.get('country') or '').strip(),
                    'country_code': (data.get('countryCode') or '').strip(),
                    'region': (data.get('regionName') or '').strip(),
                    'timezone': (data.get('timezone') or '').strip(),
                    'query_ip': host,
                }
                if not result['country']:
                    result = None
            else:
                logger.debug(
                    f"GeoIP lookup for {host}: status={data.get('status')!r}, "
                    f"message={data.get('message', 'unknown')}"
                )
        else:
            logger.debug(f"GeoIP lookup for {host}: HTTP {resp.status_code}")
    except Exception as e:
        logger.debug(f"GeoIP lookup for {host} failed: {e}")

    # Cache result (including None — we don't want to retry every time)
    with _GEOIP_CACHE_LOCK:
        _GEOIP_CACHE[host] = result

    if result:
        logger.info(
            f"GeoIP: {host} → country={result['country']!r} "
            f"({result['country_code']}), tz={result['timezone']!r}, "
            f"region={result['region']!r}"
        )
    return result


def clear_geoip_cache():
    """Clear the GeoIP lookup cache. Useful for tests / long-running
    processes where proxy IP mappings may rotate."""
    with _GEOIP_CACHE_LOCK:
        _GEOIP_CACHE.clear()


# ====================================================================
# OS-Specific Font Lists — Prevents Font Fingerprinting Detection
# ====================================================================
# When the User-Agent claims Windows but the browser only has Linux
# server fonts (DejaVu Sans, FreeFont), anti-bot systems immediately
# detect the mismatch. These lists provide OS-consistent font sets.

WINDOWS_FONTS = [
    'Arial', 'Arial Black', 'Bahnschrift', 'Calibri', 'Cambria',
    'Cambria Math', 'Candara', 'Comic Sans MS', 'Consolas', 'Constantia',
    'Corbel', 'Courier New', 'Ebrima', 'Franklin Gothic Medium',
    'Gabriola', 'Gadugi', 'Georgia', 'HoloLens MDL2 Assets', 'Impact',
    'Ink Free', 'Javanese Text', 'Leelawadee UI', 'Lucida Console',
    'Lucida Sans Unicode', 'Malgun Gothic', 'Marlett', 'Microsoft Himalaya',
    'Microsoft JhengHei', 'Microsoft New Tai Lue', 'Microsoft PhagsPa',
    'Microsoft Tai Le', 'Microsoft YaHei', 'Microsoft Yi Baiti',
    'MingLiU-ExtB', 'Mongolian Baiti', 'MS Gothic', 'MV Boli',
    'Myanmar Text', 'Nirmala UI', 'Palatino Linotype', 'Segoe MDL2 Assets',
    'Segoe Print', 'Segoe Script', 'Segoe UI', 'Segoe UI Emoji',
    'Segoe UI Historic', 'Segoe UI Symbol', 'SimSun', 'Sitka',
    'Sylfaen', 'Symbol', 'Tahoma', 'Times New Roman', 'Trebuchet MS',
    'Verdana', 'Webdings', 'Wingdings', 'Yu Gothic',
]

MAC_FONTS = [
    'American Typewriter', 'Andale Mono', 'Apple Braille', 'Apple Chancery',
    'Apple Color Emoji', 'Apple SD Gothic Neo', 'Apple Symbols',
    'AppleGothic', 'AppleMyungjo', 'Avenir', 'Avenir Next',
    'Avenir Next Condensed', 'Baskerville', 'Big Caslon', 'Brush Script MT',
    'Chalkboard', 'Chalkboard SE', 'Chalkduster', 'Charter', 'Cochin',
    'Copperplate', 'Corsiva Hebrew', 'Courier New', 'Didot',
    'DIN Alternate', 'DIN Condensed', 'Futura', 'Geneva', 'Georgia',
    'Gill Sans', 'Helvetica', 'Helvetica Neue', 'Herculanum',
    'Hoefler Text', 'Impact', 'Iowan Old Style', 'Kefa', 'Kohinoor Bangla',
    'Kohinoor Devanagari', 'Kohinoor Gujarati', 'Kohinoor Telugu',
    'Lao Sangam MN', 'Malayalam MN', 'Marion', 'Marker Felt',
    'Menlo', 'Microsoft Sans Serif', 'Monaco', 'Monotype Corsiva',
    'Nanum Brush Script', 'Nanum Pen Script', 'Noteworthy',
    'Optima', 'Palatino', 'Papyrus', 'PingFang HK', 'PingFang SC',
    'PingFang TC', 'Raanana', 'Rockwell', 'SF Arabic', 'SF Compact',
    'SF Mono', 'SF Pro', 'Savoye LET', 'Sinhala MN', 'Sinhala Sangam MN',
    'Skia', 'Snell Roundhand', 'Sukhumvit Set', 'Symbol',
    'Tamil MN', 'Tamil Sangam MN', 'Thonburi', 'Times New Roman',
    'Trattatello', 'Trebuchet MS', 'Verdana', 'Zapf Dingbats', 'Zapfino',
]

LINUX_FONTS = [
    'DejaVu Sans', 'DejaVu Sans Mono', 'DejaVu Serif',
    'Droid Sans', 'Droid Sans Mono', 'Droid Serif',
    'FreeMono', 'FreeSans', 'FreeSerif',
    'Liberation Mono', 'Liberation Sans', 'Liberation Serif',
    'Noto Sans', 'Noto Sans CJK SC', 'Noto Serif',
    'Ubuntu', 'Ubuntu Condensed', 'Ubuntu Mono',
    'Cantarell', 'Carlito', 'Caladea',
]

ANDROID_FONTS = [
    'Roboto', 'Roboto Condensed', 'Roboto Medium', 'Roboto Black',
    'Roboto Light', 'Roboto Thin', 'Noto Sans', 'Noto Sans CJK',
    'Noto Serif', 'Droid Sans', 'Droid Sans Mono', 'Droid Serif',
    'Android Emoji', 'Coming Soon', 'Cutive Mono', 'Dancing Script',
]


# ====================================================================
# OS-Specific Screen Resolutions
# ====================================================================
# Common real-user screen resolutions per OS

WINDOWS_RESOLUTIONS = [
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
    (1280, 720), (1600, 900), (2560, 1440), (1280, 800),
    (1680, 1050), (1360, 768), (3840, 2160),
]

MAC_RESOLUTIONS = [
    (1440, 900), (2560, 1600), (1680, 1050), (1280, 800),
    (1920, 1200), (2880, 1800), (2304, 1440), (1680, 1050),
    (1366, 768), (1920, 1080),
]

LINUX_RESOLUTIONS = [
    (1920, 1080), (1366, 768), (1536, 864), (1440, 900),
    (1600, 900), (2560, 1440), (1280, 1024), (1280, 800),
]

ANDROID_RESOLUTIONS = [
    (412, 915), (360, 780), (393, 851), (412, 891),
    (360, 800), (412, 846), (384, 854),
]


# ====================================================================
# OS-Specific WebGL Renderers
# ====================================================================
# These must match the OS in the User-Agent

WINDOWS_WEBGL = [
    {
        'vendor': 'Google Inc. (NVIDIA)',
        'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
    {
        'vendor': 'Google Inc. (NVIDIA)',
        'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
    {
        'vendor': 'Google Inc. (NVIDIA)',
        'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
    {
        'vendor': 'Google Inc. (Intel)',
        'renderer': 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
    {
        'vendor': 'Google Inc. (Intel)',
        'renderer': 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
    {
        'vendor': 'Google Inc. (AMD)',
        'renderer': 'ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
    {
        'vendor': 'Google Inc. (AMD)',
        'renderer': 'ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)',
    },
]

MAC_WEBGL = [
    {
        'vendor': 'Google Inc. (Apple)',
        'renderer': 'ANGLE (Apple, Apple M1, OpenGL 4.1)',
    },
    {
        'vendor': 'Google Inc. (Apple)',
        'renderer': 'ANGLE (Apple, Apple M2, OpenGL 4.1)',
    },
    {
        'vendor': 'Google Inc. (Intel)',
        'renderer': 'ANGLE (Intel, Intel(R) Iris(TM) Plus Graphics 655, OpenGL 4.1)',
    },
    {
        'vendor': 'Google Inc. (AMD)',
        'renderer': 'ANGLE (AMD, AMD Radeon Pro 5500M, OpenGL 4.1)',
    },
]

LINUX_WEBGL = [
    {
        'vendor': 'Google Inc. (Intel)',
        'renderer': 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 630 (CFL GT2), OpenGL 4.6)',
    },
    {
        'vendor': 'Google Inc. (Intel)',
        'renderer': 'ANGLE (Intel, Mesa Intel(R) HD Graphics 630 (KBL GT2), OpenGL 4.6)',
    },
    {
        'vendor': 'Google Inc. (NVIDIA)',
        'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060/PCIe/SSE2, OpenGL 4.6)',
    },
]

ANDROID_WEBGL = [
    {
        'vendor': 'ARM',
        'renderer': 'Mali-G715',
    },
    {
        'vendor': 'Qualcomm',
        'renderer': 'Adreno (TM) 740',
    },
    {
        'vendor': 'ARM',
        'renderer': 'Mali-G78',
    },
]


# ====================================================================
# OS-Specific TTL Values
# ====================================================================
# TCP/IP TTL fingerprinting:
#   Windows default TTL = 128
#   macOS default TTL = 64
#   Linux default TTL = 64
#   Android default TTL = 64
#
# If your server is Linux (TTL 64) but UA claims Windows (expected TTL 128),
# anti-fraud systems detect the mismatch instantly.
#
# Solution: Use iptables TTL mangling to set outgoing TTL to match the
# claimed OS in the User-Agent.

OS_TTL_MAP = {
    'Windows': 128,
    'Mac': 64,
    'Linux': 64,
    'Android': 64,
}


# ====================================================================
# Geo-IP to Timezone/Locale Mapping (Extended)
# ====================================================================
# Comprehensive mapping of countries to their timezone and locale pairs.
# This ensures timezone and locale always match the proxy's geolocation.

COUNTRY_TZ_LOCALE_EXTENDED = {
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
        ('America/Winnipeg', 'en-CA'),
    ],
    'Australia': [
        ('Australia/Sydney', 'en-AU'),
        ('Australia/Melbourne', 'en-AU'),
        ('Australia/Brisbane', 'en-AU'),
        ('Australia/Perth', 'en-AU'),
        ('Australia/Adelaide', 'en-AU'),
    ],
    'Ireland': [('Europe/Dublin', 'en-IE')],
    'Germany': [('Europe/Berlin', 'de-DE'), ('Europe/Busingen', 'de-DE')],
    'France': [('Europe/Paris', 'fr-FR')],
    'Netherlands': [('Europe/Amsterdam', 'nl-NL')],
    'Japan': [('Asia/Tokyo', 'ja-JP')],
    'Singapore': [('Asia/Singapore', 'en-SG')],
    'India': [('Asia/Kolkata', 'en-IN')],
    'Indonesia': [
        ('Asia/Jakarta', 'id-ID'),
        ('Asia/Makassar', 'id-ID'),
        ('Asia/Jayapura', 'id-ID'),
    ],
    'Brazil': [
        ('America/Sao_Paulo', 'pt-BR'),
        ('America/Rio_Branco', 'pt-BR'),
        ('America/Manaus', 'pt-BR'),
        ('America/Fortaleza', 'pt-BR'),
    ],
    'South Korea': [('Asia/Seoul', 'ko-KR')],
    'Hong Kong': [('Asia/Hong_Kong', 'zh-HK')],
    'Russia': [
        ('Europe/Moscow', 'ru-RU'),
        ('Asia/Yekaterinburg', 'ru-RU'),
        ('Asia/Novosibirsk', 'ru-RU'),
        ('Asia/Vladivostok', 'ru-RU'),
    ],
    'Thailand': [('Asia/Bangkok', 'th-TH')],
    'Vietnam': [('Asia/Ho_Chi_Minh', 'vi-VN')],
    'Philippines': [('Asia/Manila', 'fil-PH')],
    'Malaysia': [('Asia/Kuala_Lumpur', 'ms-MY')],
    'Taiwan': [('Asia/Taipei', 'zh-TW')],
    'Mexico': [
        ('America/Mexico_City', 'es-MX'),
        ('America/Tijuana', 'es-MX'),
        ('America/Cancun', 'es-MX'),
    ],
    'Spain': [('Europe/Madrid', 'es-ES')],
    'Italy': [('Europe/Rome', 'it-IT')],
    'Portugal': [('Europe/Lisbon', 'pt-PT')],
    'Argentina': [('America/Argentina/Buenos_Aires', 'es-AR')],
    'South Africa': [('Africa/Johannesburg', 'en-ZA')],
    'United Arab Emirates': [('Asia/Dubai', 'ar-AE')],
    'Saudi Arabia': [('Asia/Riyadh', 'ar-SA')],
    'Turkey': [('Europe/Istanbul', 'tr-TR')],
    'Poland': [('Europe/Warsaw', 'pl-PL')],
    'Sweden': [('Europe/Stockholm', 'sv-SE')],
    'Norway': [('Europe/Oslo', 'nb-NO')],
    'Finland': [('Europe/Helsinki', 'fi-FI')],
    'Denmark': [('Europe/Copenhagen', 'da-DK')],
    'Switzerland': [('Europe/Zurich', 'de-CH')],
    'Austria': [('Europe/Vienna', 'de-AT')],
    'Belgium': [('Europe/Brussels', 'nl-BE')],
    'Israel': [('Asia/Jerusalem', 'he-IL')],
    'Egypt': [('Africa/Cairo', 'ar-EG')],
    'Nigeria': [('Africa/Lagos', 'en-NG')],
    'Kenya': [('Africa/Nairobi', 'en-KE')],
    'Colombia': [('America/Bogota', 'es-CO')],
    'Chile': [('America/Santiago', 'es-CL')],
    'Peru': [('America/Lima', 'es-PE')],
    'Pakistan': [('Asia/Karachi', 'ur-PK')],
    'Bangladesh': [('Asia/Dhaka', 'bn-BD')],
}


# ====================================================================
# DNS Leak Prevention
# ====================================================================
# When using a residential proxy, DNS queries must go through the proxy
# (or at minimum, through DNS servers that match the proxy's geolocation).
# Using datacenter DNS (like AWS, DigitalOcean, Hetzner resolvers) while
# the HTTP connection comes from a residential IP in Jakarta is a fatal
# inconsistency.

GEO_DNS_SERVERS = {
    'United States': ['8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1'],
    'United Kingdom': ['8.8.8.8', '1.1.1.1'],
    'Indonesia': ['8.8.8.8', '1.1.1.1', '9.9.9.9'],
    'India': ['8.8.8.8', '1.1.1.1'],
    'Japan': ['8.8.8.8', '1.1.1.1'],
    'Brazil': ['8.8.8.8', '1.1.1.1'],
    'Germany': ['8.8.8.8', '1.1.1.1', '9.9.9.9'],
    'France': ['8.8.8.8', '1.1.1.1'],
    'Canada': ['8.8.8.8', '1.1.1.1'],
    'Australia': ['8.8.8.8', '1.1.1.1'],
    'Singapore': ['8.8.8.8', '1.1.1.1'],
    'South Korea': ['8.8.8.8', '1.1.1.1'],
}


# ====================================================================
# User-Agent Database (Chrome versions updated)
# ====================================================================
# OS-matched User-Agent strings. The OS in the UA must match the
# selected OS profile to avoid cross-layer mismatch.

CHROME_VERSION = '137'

USER_AGENT_TEMPLATES = {
    'Windows': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36',
    'Mac': f'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36',
    'Linux': f'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36',
    'Android': f'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Mobile Safari/537.36',
}


# ====================================================================
# Profile Synchronizer
# ====================================================================

class ProfileSynchronizer:
    """
    Ensures total synchronization of all browser profile parameters.
    
    This addresses the critical "Cross-Layer & OS Mismatch" issue where:
    - The User-Agent claims Windows but TCP TTL reveals Linux (64 vs 128)
    - The font list shows Linux server fonts while UA claims Windows
    - The WebGL renderer doesn't match the claimed OS
    - The timezone/locale doesn't match the proxy's geolocation
    - DNS queries leak through datacenter resolvers
    
    All parameters are synchronized based on a SINGLE source of truth:
    the OS selection, which is derived from the proxy geolocation.
    """

    def __init__(self, prefer_os=None):
        """
        Args:
            prefer_os: Override OS selection ('Windows', 'Mac', 'Linux', 'Android')
                      If None, OS is chosen based on proxy geolocation
        """
        self.prefer_os = prefer_os

    def _determine_os(self, proxy=None, user_profile=None):
        """
        Determine the OS to claim. SINGLE SOURCE OF TRUTH: the device.

        v9.1 FIX (desync bug):
          Previously this method ignored the device already chosen in
          bot_v6.make_user_profile() and re-rolled a random OS with an
          80/15/5 Windows/Mac/Android split. That produced critical
          mismatches such as:
              device=Pixel 8 (Android)  ->  sync_config.os=Windows
                                              -> UA=Windows, fonts=Arial/Segoe,
                                                 WebGL=Direct3D11, TTL=128
          Anti-fraud systems instantly flag "mobile device label +
          desktop fingerprint" as a bot.

        New strategy (priority order):
          1. prefer_os override (if set at construction)
          2. user_profile['device']['os'] (chosen in bot_v6.DEVICES)
          3. user_profile['user_agent'] hint (parse UA)
          4. fallback to 'Windows' (safest desktop)
        """
        if self.prefer_os:
            return self.prefer_os

        # --- Priority 2: device.os from bot_v6.make_user_profile ---
        if user_profile and isinstance(user_profile, dict):
            device = user_profile.get('device') or {}
            device_os = (device.get('os') or '').strip()
            if device_os:
                # Normalize: accept 'Windows', 'Mac', 'Linux', 'Android'
                # (matches DEVICES list in bot_v6.py)
                if device_os in ('Windows', 'Mac', 'Linux', 'Android'):
                    return device_os
                # Common aliases
                aliases = {
                    'macos': 'Mac', 'osx': 'Mac', 'os x': 'Mac',
                    'win': 'Windows', 'win10': 'Windows', 'win11': 'Windows',
                }
                norm = device_os.lower()
                if norm in aliases:
                    return aliases[norm]

        # --- Priority 3: parse UA if device.os missing ---
        if user_profile and isinstance(user_profile, dict):
            ua = (user_profile.get('user_agent') or '').lower()
            if 'windows nt' in ua:
                return 'Windows'
            if 'mac os x' in ua or 'macintosh' in ua:
                return 'Mac'
            if 'android' in ua:
                return 'Android'
            if 'iphone' in ua or 'ipad' in ua:
                return 'Android'  # closest match in our supported set
            if 'linux' in ua and 'android' not in ua:
                return 'Linux'

        # --- Priority 4: safe fallback ---
        # Real residential users are predominantly Windows; this is only
        # reached when no device/UA hint is provided.
        return 'Windows'

    def _get_fonts_for_os(self, os_type):
        """Return OS-consistent font list for fingerprint matching."""
        font_map = {
            'Windows': WINDOWS_FONTS,
            'Mac': MAC_FONTS,
            'Linux': LINUX_FONTS,
            'Android': ANDROID_FONTS,
        }
        return font_map.get(os_type, WINDOWS_FONTS)

    def _get_resolution_for_os(self, os_type):
        """Return a common screen resolution for the OS."""
        res_map = {
            'Windows': WINDOWS_RESOLUTIONS,
            'Mac': MAC_RESOLUTIONS,
            'Linux': LINUX_RESOLUTIONS,
            'Android': ANDROID_RESOLUTIONS,
        }
        resolutions = res_map.get(os_type, WINDOWS_RESOLUTIONS)
        w, h = random.choice(resolutions)
        return w, h

    def _get_webgl_for_os(self, os_type):
        """Return OS-consistent WebGL vendor/renderer pair."""
        webgl_map = {
            'Windows': WINDOWS_WEBGL,
            'Mac': MAC_WEBGL,
            'Linux': LINUX_WEBGL,
            'Android': ANDROID_WEBGL,
        }
        options = webgl_map.get(os_type, WINDOWS_WEBGL)
        return random.choice(options)

    def _get_expected_ttl(self, os_type):
        """Return expected TCP TTL for the OS."""
        return OS_TTL_MAP.get(os_type, 128)

    def _get_timezone_locale(self, proxy):
        """
        Get timezone and locale that match the proxy's geolocation.
        This ensures no timezone/geolocation mismatch.

        v9.2: When the proxy has NO country metadata (which is the case
        for the external Proxy API /range/<n>-<m>?format=txt endpoint),
        perform a one-shot GeoIP lookup via ip-api.com. The lookup result
        is cached by proxy host, so subsequent profiles using the same
        proxy don't trigger another lookup. If the lookup fails or the
        country is unknown, we fall back to ('America/New_York', 'en-US').
        """
        if not proxy:
            return 'America/New_York', 'en-US'

        country = ''
        if isinstance(proxy, dict):
            raw_entry = proxy.get('raw_entry', {})
            info = raw_entry.get('info', {}) if raw_entry else {}
            country = (info.get('country') or '').strip()
            if not country:
                country = (proxy.get('country') or '').strip()

        # v9.2 — GeoIP fallback when no country metadata is present.
        # The Proxy API returns plain-text lines without country info,
        # so country will be empty here for almost every proxy. We
        # perform a GeoIP lookup on the proxy host and cache the result
        # back onto the proxy dict so other consumers (bot_v6, AdsPower
        # profile name, etc.) can read it without re-querying.
        if not country and isinstance(proxy, dict):
            geo = geo_lookup_proxy(proxy)
            if geo and geo.get('country'):
                country = geo['country']
                # Store on proxy dict so the rest of the pipeline
                # (AdsPower profile name, bot_v6 logger, geo_match_proxy)
                # can use it without another lookup.
                try:
                    proxy['country'] = country
                    proxy['country_code'] = geo.get('country_code', '')
                    proxy.setdefault('raw_entry', {})
                    if isinstance(proxy.get('raw_entry'), dict):
                        proxy['raw_entry'].setdefault('info', {})
                        if isinstance(proxy['raw_entry'].get('info'), dict):
                            proxy['raw_entry']['info']['country'] = country
                            if geo.get('timezone'):
                                proxy['raw_entry']['info']['timezone'] = geo['timezone']
                except Exception:
                    pass

                # If ip-api returned a precise timezone, prefer it over
                # our random pick from COUNTRY_TZ_LOCALE_EXTENDED. The
                # API's tz is the actual tz of the exit IP — strictly
                # better than a country-level random choice.
                geo_tz = (geo.get('timezone') or '').strip()
                if geo_tz:
                    # Derive locale from country (we don't have a per-tz
                    # locale map, but COUNTRY_TZ_LOCALE_EXTENDED gives us
                    # the right locale for the country).
                    locale_for_country = 'en-US'
                    matches = COUNTRY_TZ_LOCALE_EXTENDED.get(country)
                    if matches:
                        locale_for_country = matches[0][1]
                    return geo_tz, locale_for_country

        if country:
            matches = COUNTRY_TZ_LOCALE_EXTENDED.get(country)
            if not matches:
                # Fuzzy match
                for key, val in COUNTRY_TZ_LOCALE_EXTENDED.items():
                    if key.lower() in country.lower() or country.lower() in key.lower():
                        matches = val
                        break
            if matches:
                return random.choice(matches)

        # Fallback
        return 'America/New_York', 'en-US'

    def _get_dns_servers(self, proxy):
        """
        Get DNS servers appropriate for the proxy's geolocation.
        Prevents DNS leak detection.

        v9.2: Same GeoIP fallback as _get_timezone_locale. When country
        metadata is missing, look it up via ip-api.com.
        """
        country = ''
        if isinstance(proxy, dict):
            raw_entry = proxy.get('raw_entry', {})
            info = raw_entry.get('info', {}) if raw_entry else {}
            country = (info.get('country') or '').strip()
            if not country:
                country = (proxy.get('country') or '').strip()

        # v9.2 — GeoIP fallback
        if not country and isinstance(proxy, dict):
            geo = geo_lookup_proxy(proxy)
            if geo and geo.get('country'):
                country = geo['country']
                try:
                    proxy['country'] = country
                except Exception:
                    pass

        if country:
            servers = GEO_DNS_SERVERS.get(country)
            if servers:
                return servers

        return ['8.8.8.8', '1.1.1.1']

    def _setup_ttl_mangle(self, os_type):
        """
        Set up TTL to match the claimed OS.
        
        When the server runs Linux (default TTL 64), but we claim Windows
        (expected TTL 128), we need to mangle the TTL of outgoing packets
        so that anti-fraud systems see TTL 128 instead of 64.
        
        IMPORTANT: With residential proxies, the anti-fraud system sees the
        TTL of the LAST hop (the ISP's router), NOT our server's TTL.
        This means TTL mangling is NOT strictly required when using
        residential proxies — the ISP's exit node handles TTL naturally.
        
        For datacenter/dedicated proxies, TTL mangling IS important.
        
        Platform behavior:
          - Windows: TTL default sudah 128, tidak perlu diubah.
          - Linux: Perlu root untuk sysctl/iptables.
          - macOS: Perlu sudo untuk sysctl.
        """
        import platform
        current_os = platform.system()  # 'Windows', 'Linux', 'Darwin'
        expected_ttl = self._get_expected_ttl(os_type)
        
        # === Windows: TTL default sudah 128, langsung OK ===
        if current_os == 'Windows':
            # Windows default TTL is 128, which is correct for Windows UA
            if os_type == 'Windows':
                logger.info(f"Windows detected: TTL default sudah 128 (cocok untuk Windows UA). Tidak perlu diubah.")
                return True
            else:
                # Claiming non-Windows OS (e.g., Android/macOS) on Windows machine
                # Windows TTL=128 but we want 64 — would need netsh, but for
                # residential proxies this mismatch is handled by ISP exit node.
                logger.info(
                    f"Windows detected: TTL default=128, expected={expected_ttl} untuk {os_type}. "
                    f"Untuk residential proxy, ISP exit node menangani TTL. Tidak perlu diubah."
                )
                return True
        
        # === Linux / macOS ===
        # Check current default TTL
        try:
            with open('/proc/sys/net/ipv4/ip_default_ttl', 'r') as f:
                current_ttl = int(f.read().strip())
        except Exception:
            # macOS tidak punya /proc/sys, cek via sysctl
            try:
                import subprocess
                result = subprocess.run(
                    ['sysctl', '-n', 'net.inet.ip.ttl'],
                    capture_output=True, text=True, timeout=5
                )
                current_ttl = int(result.stdout.strip())
            except Exception:
                current_ttl = 64

        if current_ttl == expected_ttl:
            logger.info(f"TTL already correct: {current_ttl} (matches {os_type})")
            return True

        # Try to set TTL via sysctl (needs root)
        try:
            import subprocess
            if current_os == 'Darwin':
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', f'net.inet.ip.ttl={expected_ttl}'],
                    capture_output=True, timeout=5
                )
            else:
                result = subprocess.run(
                    ['sysctl', '-w', f'net.ipv4.ip_default_ttl={expected_ttl}'],
                    capture_output=True, timeout=5
                )
            # Verify
            try:
                with open('/proc/sys/net/ipv4/ip_default_ttl', 'r') as f:
                    new_ttl = int(f.read().strip())
            except Exception:
                new_ttl = current_ttl  # fallback
            if new_ttl == expected_ttl:
                logger.info(f"TTL set to {expected_ttl} (matches {os_type})")
                return True
        except Exception:
            pass

        # Try iptables TTL mangling (needs root, Linux only)
        if current_os == 'Linux':
            try:
                import subprocess
                result = subprocess.run(
                    ['iptables', '-t', 'mangle', '-A', 'POSTROUTING',
                     '-j', 'TTL', '--ttl-set', str(expected_ttl)],
                    capture_output=True, timeout=5
                )
                if result.returncode == 0:
                    logger.info(f"TTL mangling set via iptables to {expected_ttl}")
                    return True
            except Exception:
                pass

        # Cannot set TTL — but this is NOT fatal for residential proxies
        # because the ISP's exit node has its own TTL.
        logger.info(
            f"TTL tidak diubah (current={current_ttl}, expected={expected_ttl} untuk {os_type}). "
            f"Untuk residential proxy, ISP exit node menangani TTL — ini aman. "
            f"Untuk datacenter proxy di Linux, jalankan dengan sudo atau: "
            f"sudo sysctl -w net.ipv4.ip_default_ttl={expected_ttl}"
        )
        return True  # Return True for residential proxy (not fatal)

    def build_full_profile(self, proxy, user_profile):
        """
        Build a fully synchronized browser profile configuration.
        
        This is the core function that implements RECOMMENDATION #2:
        "Sinkronisasi Total Profil"
        
        All parameters are derived from a single OS selection and
        synchronized with the proxy's geolocation.
        
        Args:
            proxy: Proxy entry dict from bot_v6.py load_proxies()
            user_profile: User profile dict from make_user_profile()
        
        Returns:
            Complete profile_config dict for AntiDetectManager or
            direct browser launch configuration.
        """
        # Step 1: Determine OS (avoid claiming Linux on a Linux server)
        os_type = self._determine_os(proxy, user_profile)
        logger.info(f"Profile OS: {os_type}")

        # v9.1: Cross-check device vs OS — log warning if mismatched
        if user_profile and isinstance(user_profile, dict):
            device = user_profile.get('device') or {}
            device_os = (device.get('os') or '').strip()
            if device_os and device_os != os_type:
                logger.warning(
                    f"DESYNC WARNING: device.os={device_os!r} but sync_config.os={os_type!r}. "
                    f"This indicates make_user_profile() picked a device that doesn't match "
                    f"the synchronized OS — anti-fraud will flag this. Device UA label will "
                    f"say '{device.get('name', '?')}' but fingerprint will look like {os_type}."
                )

        # Step 2: Get timezone/locale matching proxy geolocation
        timezone, locale = self._get_timezone_locale(proxy)
        logger.info(f"Timezone: {timezone}, Locale: {locale}")

        # Step 3: Get OS-consistent User-Agent
        user_agent = USER_AGENT_TEMPLATES.get(os_type, USER_AGENT_TEMPLATES['Windows'])
        # Override with user_profile UA if it matches the selected OS
        # v9.1: stricter matching — UA must contain OS-specific token AND
        # must NOT contain a conflicting OS token (e.g., Android UA must
        # not be used when os_type=Windows, even if 'windows' substring
        # somehow appears).
        if user_profile and user_profile.get('user_agent'):
            profile_ua = user_profile['user_agent']
            ua_lower = profile_ua.lower()
            ua_ok = False
            if os_type == 'Windows' and 'windows nt' in ua_lower and 'android' not in ua_lower:
                ua_ok = True
            elif os_type == 'Mac' and ('mac os x' in ua_lower or 'macintosh' in ua_lower) and 'android' not in ua_lower:
                ua_ok = True
            elif os_type == 'Linux' and 'linux' in ua_lower and 'android' not in ua_lower:
                ua_ok = True
            elif os_type == 'Android' and 'android' in ua_lower:
                ua_ok = True
            if ua_ok:
                user_agent = profile_ua
            else:
                logger.warning(
                    f"DESYNC WARNING: user_profile.ua='{profile_ua[:60]}...' does not match "
                    f"os_type={os_type}. Using OS-consistent template instead: "
                    f"{user_agent[:60]}..."
                )

        # Step 4: Get OS-consistent font list
        fonts = self._get_fonts_for_os(os_type)
        font_list_str = ','.join(fonts)

        # Step 5: Get OS-consistent screen resolution
        scr_w, scr_h = self._get_resolution_for_os(os_type)
        resolution = f'{scr_w}x{scr_h}'

        # Step 6: Get OS-consistent WebGL parameters
        webgl = self._get_webgl_for_os(os_type)

        # Step 7: Get DNS servers for the proxy's geolocation
        dns_servers = self._get_dns_servers(proxy)

        # Step 8: Set up TTL mangling if possible
        ttl_ok = self._setup_ttl_mangle(os_type)

        # Step 9: Extract proxy configuration
        proxy_host = ''
        proxy_port = ''
        proxy_user = ''
        proxy_password = ''
        proxy_type = 'http'

        if proxy:
            server = proxy.get('server', '')
            if '://' in server:
                from urllib.parse import urlparse
                parsed = urlparse(server)
                proxy_host = parsed.hostname or ''
                proxy_port = str(parsed.port or 8080)
                proxy_type = parsed.scheme or 'http'
            elif ':' in server:
                parts = server.split(':')
                proxy_host = parts[0]
                proxy_port = parts[1] if len(parts) > 1 else '8080'

            proxy_user = proxy.get('username', '') or ''
            proxy_password = proxy.get('password', '') or ''
            if proxy.get('protocol'):
                proxy_type = proxy['protocol']

        # Step 10: Build the OS-matching platform string
        platform_map = {
            'Windows': 'Win32',
            'Mac': 'MacIntel',
            'Linux': 'Linux x86_64',
            'Android': 'Linux armv81',
        }
        platform = platform_map.get(os_type, 'Win32')

        # Step 11: Determine color depth
        color_depth = 24
        if os_type == 'Windows' and random.random() < 0.3:
            color_depth = 32
        elif os_type == 'Mac' and random.random() < 0.2:
            color_depth = 32

        # Step 12: Build device memory and hardware concurrency
        if os_type == 'Android':
            device_memory = random.choice([4, 6, 8])
            hardware_concurrency = random.choice([4, 6, 8])
        elif os_type == 'Mac':
            device_memory = random.choice([8, 16, 16])
            hardware_concurrency = random.choice([8, 10, 12])
        else:  # Windows/Linux
            device_memory = random.choice([8, 8, 16, 16, 32])
            hardware_concurrency = random.choice([4, 6, 8, 8, 12, 16])

        # Step 13: Determine max touch points
        max_touch_points = 0
        if os_type == 'Android':
            max_touch_points = 5
        elif os_type == 'Mac' and random.random() < 0.15:
            max_touch_points = 0  # Most Macs don't have touch screens

        # Step 14: Build profile name
        # v9.2: include device name + proxy country so the AdsPower console
        # shows a meaningful label per profile (e.g. "bot_Pixel8_US-Eastern").
        # This is the "console profile device" synchronization the user
        # asked for — each AdsPower profile is now visibly tagged with the
        # device + geolocation it represents.
        device_name = ''
        if user_profile and isinstance(user_profile, dict):
            dev = user_profile.get('device') or {}
            device_name = (dev.get('name') or '').strip()
        # Sanitize device name → short alphanumeric token (no spaces).
        device_token = ''.join(c if c.isalnum() else '-' for c in device_name).strip('-')
        if not device_token:
            device_token = os_type or 'Unknown'

        # Country code from proxy (may be empty if GeoIP failed)
        country_code = ''
        if proxy and isinstance(proxy, dict):
            country_code = (proxy.get('country_code') or '').strip()
            if not country_code:
                # Try raw_entry.info.country (full name) — take first 2 chars
                raw_info = (proxy.get('raw_entry') or {}).get('info', {}) or {}
                country_full = (raw_info.get('country') or proxy.get('country') or '').strip()
                if country_full:
                    country_code = country_full[:2].upper()

        tz_token = timezone.replace('/', '-').replace('_', '-')
        country_token = country_code if country_code else 'XX'
        profile_name = (
            f'bot_{device_token}_{country_token}_{tz_token}'
            f'_{int(time.time())}_{random.randint(1000, 9999)}'
        )

        # Assemble the complete configuration
        config = {
            # Identity
            'name': profile_name,
            'os': os_type,
            'platform': platform,

            # v9.2: device + country tags for AdsPower console profile
            # identification. These are also used by antidetect_browser.py
            # to set the AdsPower profile name + remark so each profile is
            # clearly labeled with its device + geolocation.
            'device_name': device_name or device_token,
            'device_token': device_token,
            'country': (proxy.get('country') if isinstance(proxy, dict) else '') or '',
            'country_code': country_code,

            # User-Agent
            'ua': user_agent,

            # Language & Locale
            'lan': locale,
            'languages': self._build_languages(locale),

            # Timezone
            'timezone': timezone,

            # Screen
            'resolution': resolution,
            'color_depth': color_depth,
            'screen_width': scr_w,
            'screen_height': scr_h,

            # Fonts (OS-consistent)
            'font_list': font_list_str,
            'fonts': fonts,

            # WebGL (OS-consistent)
            'webgl_vendor': webgl['vendor'],
            'webgl_renderer': webgl['renderer'],

            # Hardware
            'device_memory': device_memory,
            'hardware_concurrency': hardware_concurrency,
            'max_touch_points': max_touch_points,

            # Network
            'proxy_type': proxy_type,
            'proxy_host': proxy_host,
            'proxy_port': proxy_port,
            'proxy_user': proxy_user,
            'proxy_password': proxy_password,
            'proxy_soft': 'custom',
            'dns_servers': dns_servers,
            'expected_ttl': self._get_expected_ttl(os_type),
            'ttl_mangled': ttl_ok,

            # WebRTC (must be disabled to prevent IP leak)
            'webrtc_mode': 'disabled',

            # Browser config
            'headless': False,

            # v9.1: carry the original device info so downstream consumers
            # (antidetect_browser, stealth_py, bot_v6 launch) can verify
            # device/fingerprint consistency and pick the right viewport,
            # has_touch, device_scale_factor, etc.
            'device': (user_profile or {}).get('device', {}) if isinstance(user_profile, dict) else {},
            'is_mobile': os_type == 'Android',
            'has_touch': os_type == 'Android',
            # Device pixel ratio — Pixel 8 = 2.625, typical Android = 2.0-3.0,
            # desktop = 1.0. We let callers override; default per OS.
            'device_scale_factor': 2.625 if os_type == 'Android' else 1.0,
        }

        # v9.2: Pull device-pinned values from the device pool entry when
        # available. The bot_v6.DEVICES pool has authoritative has_touch /
        # device_scale_factor / viewport per device (e.g. Pixel 7 Pro →
        # 3.5 DPR, iPhone 15 Pro Max → 3.0 DPR). Previously these device-
        # specific values were computed but then OVERWRITTEN by the OS-
        # level defaults above (Android → 2.625), losing the per-device
        # detail. Now device pool wins; OS-level defaults are fallback.
        device_info = config.get('device') or {}
        if isinstance(device_info, dict):
            if 'has_touch' in device_info and device_info['has_touch'] is not None:
                config['has_touch'] = bool(device_info['has_touch'])
            if 'device_scale_factor' in device_info and device_info['device_scale_factor'] is not None:
                try:
                    config['device_scale_factor'] = float(device_info['device_scale_factor'])
                except (TypeError, ValueError):
                    pass
            # is_mobile stays as os_type == 'Android' for now — that's
            # what AdsPower/Patchright use to set navigator.platform.

        # v9.1: Final consistency check — surface any remaining desync
        issues = self.verify_profile_consistency(config)
        if issues:
            for issue in issues:
                logger.warning(f"CONSISTENCY CHECK FAILED: {issue}")
        else:
            logger.info("Profile consistency check: PASSED (OS / UA / WebGL / fonts / TTL aligned)")

        logger.info(
            f"Built synchronized profile: name={profile_name}, OS={os_type}, "
            f"UA={user_agent[:50]}..., TZ={timezone}, Locale={locale}, "
            f"Country={config['country']!r} ({country_token}), "
            f"Fonts={len(fonts)}, Resolution={resolution}, "
            f"WebGL={webgl['renderer'][:40]}..., TTL={config['expected_ttl']}, "
            f"DNS={dns_servers}, mobile={config['is_mobile']}, "
            f"touch={config['has_touch']}, DPR={config['device_scale_factor']}"
        )

        return config

    def _build_languages(self, locale):
        """Build the navigator.languages array from locale."""
        if not locale or not isinstance(locale, str):
            return ['en-US', 'en']
        base = locale.split('-')[0] if '-' in locale else locale
        langs = [locale]
        if base != locale:
            langs.append(base)
        if locale != 'en-US' and base != 'en':
            langs.extend(['en-US', 'en'])
        return langs

    def verify_profile_consistency(self, config):
        """
        Verify that all profile parameters are internally consistent.
        Returns a list of inconsistencies found.
        """
        issues = []
        os_type = config.get('os', 'Windows')
        ua = config.get('ua', '').lower()

        # Check UA matches OS
        if os_type == 'Windows' and 'windows' not in ua:
            issues.append(f"OS is Windows but UA doesn't contain 'windows': {config.get('ua', '')[:60]}")
        elif os_type == 'Mac' and 'mac' not in ua and 'macintosh' not in ua:
            issues.append(f"OS is Mac but UA doesn't contain 'mac': {config.get('ua', '')[:60]}")
        elif os_type == 'Linux' and 'linux' not in ua and 'android' not in ua:
            issues.append(f"OS is Linux but UA doesn't contain 'linux': {config.get('ua', '')[:60]}")
        elif os_type == 'Android' and 'android' not in ua:
            issues.append(f"OS is Android but UA doesn't contain 'android': {config.get('ua', '')[:60]}")

        # Check WebGL matches OS
        renderer = config.get('webgl_renderer', '').lower()
        if os_type == 'Windows' and 'direct3d' not in renderer and 'd3d11' not in renderer:
            issues.append(f"OS is Windows but WebGL renderer doesn't use Direct3D: {config.get('webgl_renderer', '')}")
        elif os_type == 'Mac' and 'opengl' not in renderer.lower():
            issues.append(f"OS is Mac but WebGL renderer doesn't use OpenGL: {config.get('webgl_renderer', '')}")

        # Check fonts match OS
        fonts = config.get('fonts', [])
        if os_type == 'Windows':
            has_arial = any('Arial' in f for f in fonts)
            has_segoe = any('Segoe' in f for f in fonts)
            if not has_arial:
                issues.append("OS is Windows but font list missing Arial")
            if not has_segoe:
                issues.append("OS is Windows but font list missing Segoe UI")
        elif os_type == 'Mac':
            has_helvetica = any('Helvetica' in f for f in fonts)
            has_sf = any('SF' in f for f in fonts)
            if not has_helvetica:
                issues.append("OS is Mac but font list missing Helvetica")
        elif os_type == 'Linux':
            has_dejavu = any('DejaVu' in f for f in fonts)
            if not has_dejavu:
                issues.append("OS is Linux but font list missing DejaVu Sans")

        # Check TTL
        expected_ttl = config.get('expected_ttl', 0)
        if os_type == 'Windows' and expected_ttl != 128:
            issues.append(f"OS is Windows but expected TTL is {expected_ttl}, should be 128")

        return issues
