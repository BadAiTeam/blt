#!/usr/bin/env python3
"""
Android Console Profile Pool — v11.0 UPGRADE (1000 Modular Profiles)
======================================================================

v11.0 UPGRADE — MOD 3: Tambah jumlah profile console Android sebanyak 1000.
  - File terpisah (android_profiles.py) agar lebih modular.
  - 1000 profil Android yang unik (smartphone + tablet).
  - Setiap profil: nama device, viewport, user-agent, OS, touch support,
    device scale factor, mobile model, Chrome version, locale, timezone.
  - Profil di-generate secara deterministik dari template + variasi sehingga
    konsisten antar run namun cukup beragam untuk fingerprint diversity.

Usage in bot_v6.py:
    from android_profiles import generate_android_profiles, ANDROID_PROFILE_COUNT

    # Generate 1000 profiles
    profiles = generate_android_profiles(count=1000)

    # Or use with device pool
    devices = profiles  # each entry is compatible with DEVICES format
"""

import random
import hashlib
from typing import List, Dict, Any


# ====================================================================
# Android Smartphone Templates (64 models)
# ====================================================================

SMARTPHONE_TEMPLATES = [
    # Google Pixel family
    {'name': 'Pixel 4',       'viewport': {'width': 353, 'height': 753},
     'mobile_model': 'Pixel 4',       'device_scale_factor': 2.625},
    {'name': 'Pixel 4a',      'viewport': {'width': 353, 'height': 753},
     'mobile_model': 'Pixel 4a',      'device_scale_factor': 2.625},
    {'name': 'Pixel 5',       'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'Pixel 5',       'device_scale_factor': 2.625},
    {'name': 'Pixel 5a',      'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'Pixel 5a',      'device_scale_factor': 2.625},
    {'name': 'Pixel 6',       'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'Pixel 6',       'device_scale_factor': 2.625},
    {'name': 'Pixel 6a',      'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'Pixel 6a',      'device_scale_factor': 2.625},
    {'name': 'Pixel 6 Pro',   'viewport': {'width': 412, 'height': 892},
     'mobile_model': 'Pixel 6 Pro',   'device_scale_factor': 3.5},
    {'name': 'Pixel 7',       'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'Pixel 7',       'device_scale_factor': 2.625},
    {'name': 'Pixel 7 Pro',   'viewport': {'width': 412, 'height': 892},
     'mobile_model': 'Pixel 7 Pro',   'device_scale_factor': 3.5},
    {'name': 'Pixel 7a',      'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'Pixel 7a',      'device_scale_factor': 2.625},
    {'name': 'Pixel 8',       'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'Pixel 8',       'device_scale_factor': 2.625},
    {'name': 'Pixel 8 Pro',   'viewport': {'width': 412, 'height': 892},
     'mobile_model': 'Pixel 8 Pro',   'device_scale_factor': 3.5},
    {'name': 'Pixel 9',       'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'Pixel 9',       'device_scale_factor': 2.625},
    {'name': 'Pixel 9 Pro',   'viewport': {'width': 412, 'height': 892},
     'mobile_model': 'Pixel 9 Pro',   'device_scale_factor': 3.5},

    # Samsung Galaxy S series
    {'name': 'Galaxy S21',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-G991B',      'device_scale_factor': 3.0},
    {'name': 'Galaxy S21 Ultra', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-G998B',      'device_scale_factor': 3.75},
    {'name': 'Galaxy S22',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-S901B',      'device_scale_factor': 3.0},
    {'name': 'Galaxy S22 Ultra', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-S908B',      'device_scale_factor': 3.75},
    {'name': 'Galaxy S23',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-S911B',      'device_scale_factor': 3.0},
    {'name': 'Galaxy S23 Ultra', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-S918B',      'device_scale_factor': 3.75},
    {'name': 'Galaxy S24',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-S921B',      'device_scale_factor': 3.0},
    {'name': 'Galaxy S24 Ultra', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-S928B',      'device_scale_factor': 3.75},
    {'name': 'Galaxy S25',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-S931B',      'device_scale_factor': 3.0},
    {'name': 'Galaxy S25 Ultra', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-S938B',      'device_scale_factor': 3.75},

    # Samsung Galaxy A series (mid-range — very common)
    {'name': 'Galaxy A14',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-A145F',      'device_scale_factor': 2.0},
    {'name': 'Galaxy A15',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-A155F',      'device_scale_factor': 2.0},
    {'name': 'Galaxy A23',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-A235F',      'device_scale_factor': 2.0},
    {'name': 'Galaxy A24',    'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'SM-A245F',      'device_scale_factor': 2.0},
    {'name': 'Galaxy A34',    'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'SM-A346B',      'device_scale_factor': 2.625},
    {'name': 'Galaxy A35',    'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'SM-A356B',      'device_scale_factor': 2.625},
    {'name': 'Galaxy A54',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-A546B',      'device_scale_factor': 2.625},
    {'name': 'Galaxy A55',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'SM-A556B',      'device_scale_factor': 2.625},

    # Xiaomi family
    {'name': 'Xiaomi 12',     'viewport': {'width': 393, 'height': 851},
     'mobile_model': '2201123G',      'device_scale_factor': 2.625},
    {'name': 'Xiaomi 12T',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': '22081212UG',    'device_scale_factor': 2.625},
    {'name': 'Xiaomi 13',     'viewport': {'width': 412, 'height': 915},
     'mobile_model': '2211133G',      'device_scale_factor': 2.625},
    {'name': 'Xiaomi 13 Pro', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': '2210132G',      'device_scale_factor': 2.625},
    {'name': 'Xiaomi 14',     'viewport': {'width': 412, 'height': 915},
     'mobile_model': '23127PN0CG',    'device_scale_factor': 2.625},
    {'name': 'Xiaomi 14 Pro', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': '23116PN5BC',    'device_scale_factor': 2.625},
    {'name': 'Redmi Note 12', 'viewport': {'width': 393, 'height': 851},
     'mobile_model': '23021RAAEG',    'device_scale_factor': 2.625},
    {'name': 'Redmi Note 13', 'viewport': {'width': 393, 'height': 851},
     'mobile_model': '23106RA0GE',    'device_scale_factor': 2.625},
    {'name': 'Redmi Note 13 Pro', 'viewport': {'width': 393, 'height': 851},
     'mobile_model': '2312ERA50G',    'device_scale_factor': 2.625},
    {'name': 'POCO X5 Pro',   'viewport': {'width': 393, 'height': 851},
     'mobile_model': '2210132G',      'device_scale_factor': 2.625},
    {'name': 'POCO X6 Pro',   'viewport': {'width': 412, 'height': 915},
     'mobile_model': '2311DRK50G',    'device_scale_factor': 2.625},

    # OnePlus family
    {'name': 'OnePlus 10 Pro', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'NE2210',       'device_scale_factor': 3.5},
    {'name': 'OnePlus 11',     'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'CPH2449',      'device_scale_factor': 2.625},
    {'name': 'OnePlus 12',     'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'CPH2581',      'device_scale_factor': 2.625},
    {'name': 'OnePlus Nord 3', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'CPH2487',      'device_scale_factor': 2.625},
    {'name': 'OnePlus Nord CE 3', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'CPH2599',      'device_scale_factor': 2.625},

    # Motorola
    {'name': 'Moto G84',       'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'XT2347-2',     'device_scale_factor': 2.625},
    {'name': 'Moto G54',       'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'XT2343-3',     'device_scale_factor': 2.0},
    {'name': 'Edge 40',        'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'XT2303-2',     'device_scale_factor': 2.625},

    # Oppo / Realme
    {'name': 'OPPO Find X5',   'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'CPH2305',      'device_scale_factor': 2.625},
    {'name': 'OPPO Reno 10',   'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'CPH2531',      'device_scale_factor': 2.0},
    {'name': 'Realme 11 Pro',  'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'RMX3770',      'device_scale_factor': 2.0},
    {'name': 'Realme GT 3',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'RMX3809',      'device_scale_factor': 2.625},

    # Vivo / iQOO
    {'name': 'Vivo X90',       'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'V2219',        'device_scale_factor': 2.625},
    {'name': 'Vivo V29',       'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'V2306',        'device_scale_factor': 2.0},
    {'name': 'iQOO 11',        'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'I2211',        'device_scale_factor': 2.625},

    # Huawei
    {'name': 'Huawei P60 Pro', 'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'MNA-AL00',     'device_scale_factor': 2.625},
    {'name': 'Huawei Nova 11', 'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'FOA-AL00',     'device_scale_factor': 2.0},

    # Sony Xperia
    {'name': 'Xperia 1 V',     'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'XQ-EC72',      'device_scale_factor': 2.625},
    {'name': 'Xperia 5 V',     'viewport': {'width': 393, 'height': 851},
     'mobile_model': 'XQ-DE72',      'device_scale_factor': 2.0},

    # Asus ROG
    {'name': 'ROG Phone 7',    'viewport': {'width': 412, 'height': 915},
     'mobile_model': 'AI2205_F',     'device_scale_factor': 2.625},

    # Nokia
    {'name': 'Nokia G42',      'viewport': {'width': 360, 'height': 780},
     'mobile_model': 'TA-1585',      'device_scale_factor': 2.0},
]

# ====================================================================
# Android Tablet Templates (8 models)
# ====================================================================
TABLET_TEMPLATES = [
    {'name': 'Galaxy Tab S9',       'viewport': {'width': 800, 'height': 1280},
     'mobile_model': 'SM-X716B',    'device_scale_factor': 2.0},
    {'name': 'Galaxy Tab S9+',      'viewport': {'width': 853, 'height': 1280},
     'mobile_model': 'SM-X816B',    'device_scale_factor': 2.0},
    {'name': 'Galaxy Tab S9 Ultra', 'viewport': {'width': 960, 'height': 1424},
     'mobile_model': 'SM-X916B',    'device_scale_factor': 2.0},
    {'name': 'Galaxy Tab S8',       'viewport': {'width': 800, 'height': 1280},
     'mobile_model': 'SM-X706B',    'device_scale_factor': 2.0},
    {'name': 'Galaxy Tab S8+',      'viewport': {'width': 853, 'height': 1280},
     'mobile_model': 'SM-X806B',    'device_scale_factor': 2.0},
    {'name': 'Galaxy Tab A9+',      'viewport': {'width': 800, 'height': 1280},
     'mobile_model': 'SM-X216B',    'device_scale_factor': 1.5},
    {'name': 'Xiaomi Pad 6',       'viewport': {'width': 820, 'height': 1280},
     'mobile_model': '23046RP50C',  'device_scale_factor': 2.0},
    {'name': 'Lenovo Tab P12',     'viewport': {'width': 800, 'height': 1280},
     'mobile_model': 'TB370FU',     'device_scale_factor': 1.5},
]

# ====================================================================
# Android OS version pool
# ====================================================================
ANDROID_VERSIONS = ['10', '11', '12', '12L', '13', '14', '15']

# ====================================================================
# Chrome major version pool
# ====================================================================
CHROME_VERSION_POOL = ['120', '121', '122', '123', '124', '125', '126',
                       '127', '128', '129', '130', '131', '132', '133',
                       '134', '135', '136', '137', '138', '139', '140', '141']

# ====================================================================
# Locale & Timezone pools
# ====================================================================
LOCALE_POOL = [
    'en-US', 'en-GB', 'en-AU', 'en-CA', 'en-IE', 'en-SG', 'en-IN',
    'id-ID', 'ms-MY', 'th-TH', 'vi-VN', 'fil-PH', 'zh-CN', 'zh-TW',
    'ja-JP', 'ko-KR', 'pt-BR', 'es-ES', 'de-DE', 'fr-FR', 'it-IT',
    'ru-RU', 'ar-SA', 'tr-TR', 'pl-PL', 'nl-NL',
]

TIMEZONE_POOL = [
    'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Phoenix', 'America/Anchorage', 'Pacific/Honolulu',
    'America/Toronto', 'America/Vancouver', 'America/Edmonton',
    'Europe/London', 'Europe/Dublin', 'Europe/Berlin', 'Europe/Paris',
    'Europe/Amsterdam', 'Europe/Moscow', 'Europe/Istanbul',
    'Asia/Singapore', 'Asia/Kolkata', 'Asia/Jakarta', 'Asia/Bangkok',
    'Asia/Ho_Chi_Minh', 'Asia/Manila', 'Asia/Shanghai', 'Asia/Taipei',
    'Asia/Tokyo', 'Asia/Seoul', 'Asia/Karachi', 'Asia/Dhaka',
    'Australia/Sydney', 'Australia/Melbourne', 'Australia/Perth',
    'Pacific/Auckland', 'America/Sao_Paulo', 'America/Mexico_City',
    'Africa/Lagos', 'Africa/Johannesburg', 'Africa/Cairo',
    'Asia/Riyadh', 'Asia/Dubai',
]

ANDROID_PROFILE_COUNT = 1000


def _build_android_ua(model, android_version, chrome_major, is_tablet=False):
    """Build User-Agent string for Android device."""
    if is_tablet:
        return (f'Mozilla/5.0 (Linux; Android {android_version}; {model}) '
                f'AppleWebKit/537.36 (KHTML, like Gecko) '
                f'Chrome/{chrome_major}.0.0.0 Safari/537.36')
    else:
        return (f'Mozilla/5.0 (Linux; Android {android_version}; {model}) '
                f'AppleWebKit/537.36 (KHTML, like Gecko) '
                f'Chrome/{chrome_major}.0.0.0 Mobile Safari/537.36')


def _deterministic_hash_index(seed_str, modulo):
    """Deterministic hash-based index selection for reproducibility."""
    h = hashlib.sha256(seed_str.encode('utf-8')).hexdigest()
    return int(h, 16) % modulo


def generate_android_profiles(count=ANDROID_PROFILE_COUNT):
    """
    Generate `count` unique Android console profiles.

    Strategy:
      1. Combine 64 smartphone + 8 tablet templates = 72 base.
      2. Multiply by Android version (7) × Chrome version (22) = 11,088
         theoretical combinations — more than enough for 1000.
      3. Select entries deterministically.
      4. Each profile: name, viewport, ua, os, has_touch,
         device_scale_factor, mobile_model, chrome_version, android_version,
         locale, timezone.
    """
    all_templates = []
    for tmpl in SMARTPHONE_TEMPLATES:
        all_templates.append(('smartphone', tmpl))
    for tmpl in TABLET_TEMPLATES:
        all_templates.append(('tablet', tmpl))

    profiles = []
    seen_signatures = set()
    profile_idx = 0
    round_num = 0

    while len(profiles) < count:
        for tmpl_type, tmpl in all_templates:
            if len(profiles) >= count:
                break
            is_tablet = (tmpl_type == 'tablet')
            for android_ver in ANDROID_VERSIONS:
                if len(profiles) >= count:
                    break
                for chrome_major in CHROME_VERSION_POOL:
                    if len(profiles) >= count:
                        break
                    sig = f"{tmpl['name']}|A{android_ver}|C{chrome_major}|R{round_num}"
                    if sig in seen_signatures:
                        continue
                    seen_signatures.add(sig)

                    vp = dict(tmpl['viewport'])
                    if round_num > 0:
                        rng = random.Random(profile_idx * 7 + 42)
                        vp['width']  = max(320, vp['width']  + rng.randint(-2, 2))
                        vp['height'] = max(400, vp['height'] + rng.randint(-2, 2))

                    locale_idx  = _deterministic_hash_index(f"locale-{profile_idx}", len(LOCALE_POOL))
                    tz_idx      = _deterministic_hash_index(f"tz-{profile_idx}", len(TIMEZONE_POOL))
                    locale  = LOCALE_POOL[locale_idx]
                    timezone = TIMEZONE_POOL[tz_idx]

                    ua = _build_android_ua(tmpl['mobile_model'], android_ver,
                                           chrome_major, is_tablet=is_tablet)

                    name_suffix = f" [A{android_ver} C{chrome_major}]"
                    if round_num > 0:
                        name_suffix += f"#{round_num}"

                    entry = {
                        'name': f"{tmpl['name']}{name_suffix}",
                        'viewport': vp,
                        'ua': ua,
                        'os': 'Android',
                        'has_touch': True,
                        'device_scale_factor': tmpl['device_scale_factor'],
                        'mobile_model': tmpl['mobile_model'],
                        'ua_platform': 'android_tab' if is_tablet else 'android',
                        'chrome_version': chrome_major,
                        'android_version': android_ver,
                        'locale': locale,
                        'timezone': timezone,
                        'is_tablet': is_tablet,
                    }
                    profiles.append(entry)
                    profile_idx += 1
        round_num += 1
        if round_num > 20:
            break

    rng = random.Random(0xAD570000)
    rng.shuffle(profiles)
    return profiles[:count]


def get_profile_by_index(index, count=ANDROID_PROFILE_COUNT):
    """Get a single profile by index."""
    if not hasattr(get_profile_by_index, '_cache'):
        get_profile_by_index._cache = generate_android_profiles(count)
    return get_profile_by_index._cache[index % len(get_profile_by_index._cache)]


def get_profiles_batch(start_index, batch_size, count=ANDROID_PROFILE_COUNT):
    """Get a batch of profiles starting from start_index."""
    if not hasattr(get_profiles_batch, '_cache'):
        get_profiles_batch._cache = generate_android_profiles(count)
    return get_profiles_batch._cache[start_index:start_index + batch_size]


if __name__ == '__main__':
    import sys
    profiles = generate_android_profiles(1000)
    print(f"Generated {len(profiles)} Android profiles")
    uas = set(p['ua'] for p in profiles)
    names = set(p['name'] for p in profiles)
    print(f"  Unique UAs:   {len(uas)}")
    print(f"  Unique names: {len(names)}")
    non_android = [p for p in profiles if p['os'] != 'Android']
    if non_android:
        print(f"  ERROR: {len(non_android)} non-Android profiles!")
        sys.exit(1)
    else:
        print(f"  All {len(profiles)} profiles are Android — OK")
    smartphones = [p for p in profiles if not p.get('is_tablet')]
    tablets = [p for p in profiles if p.get('is_tablet')]
    print(f"  Smartphones: {len(smartphones)}")
    print(f"  Tablets:     {len(tablets)}")
