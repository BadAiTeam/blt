"""
Anti-bot challenge solver (v8.0 — Off-screen coordinate fix + viewport sanity clamp).

v8.0 changes (Issue 1 fix):
  1. ADDED: scroll_into_view_if_needed() before bounding_box() in
     _find_checkbox_element() and _click_checkbox() to prevent off-screen
     Y coordinates from iframe scroll offsets.
  2. ADDED: Viewport sanity check in _click_at_viewport() and
     _click_checkbox() — if vy > 2000 or vx > 3000, coordinates are
     clearly wrong for a mobile viewport; fall back to Playwright click.
  3. ADDED: Viewport sanity clamp in _viewport_to_screen() — cap vx/vy
     to viewport_width * 2 and viewport_height * 2 as safety margin.
     Log a warning and fall back if coords exceed that.
  4. ADDED: solve_click() now scrolls checkbox into view and re-fetches
     bounding box before clicking.

v7.1 changes:
  1. MODIFIED: All pyautogui mouse operations now respect browser bounds
     (from human_input.get_browser_bounds()) to prevent mouse from
     escaping the browser window area to taskbar/desktop.
  2. MODIFIED: _get_screen_size() replaced with _get_mouse_bounds() that
     returns browser bounds when available, falling back to screen size.
  3. MODIFIED: _pyautogui_drag, _click_checkbox, _click_at_viewport use
     _clamp_to_browser_bounds() for coordinate clamping.

OPTIMIZED v7:
  1. Better Monetag slider detection — handles all known Monetag variants
  2. Improved verification state detection with multiple signals
  3. Faster solving with human-like drag patterns
  4. Handles "already verified" state correctly
  5. Retry with alternative strategies (click, refresh)
  6. Better frame walking for cross-origin iframes

Public API:
  detect_antibot(page)        — returns dict with detected/verified/type/reason
  is_antibot_verified(page)   — returns True if antibot is already solved
  solve_slider(page, ...)     — solve slider CAPTCHA with human-like drag
  solve_click(page, ...)      — solve click/checkbox CAPTCHA
  solve_antibot_if_present(page, ...)
"""

import re
import time
import random
import math


# ====================================================================
# Detection patterns — OPTIMIZED for Monetag
# ====================================================================

ANTIBOT_TEXT_PATTERNS = [
    r'unusual traffic',
    r'slide\s*to\s*verify',
    r'slide\s*to\s*complete',
    r'verify you are human',
    r'verify you\s*re human',
    r'are you a robot',
    r"i'?m\s+not\s+a\s+robot",
    r'human verification',
    r'bot detection',
    r'unusual activity',
    r'please complete the security check',
    r'security verification',
    r'monetag.*verify',
    r'press\s+and\s*hold',
    r'checking your browser',
    r'checking if you.*re connected',
    r'verifying you are human',
    r'just a moment',
    r'attention required',
    r'detected unusual',
    r'not a robot',
    r'please slide',
]
ANTIBOT_TEXT_RE = re.compile('|'.join(ANTIBOT_TEXT_PATTERNS), re.IGNORECASE)

# v7: Enhanced verified text patterns
VERIFIED_TEXT_PATTERNS = [
    r'verification\s+succe',
    r'successfully\s+verif',
    r'you\s+are\s+verified',
    r'you\s+have\s+been\s+verified',
    r'verified\s+successfully',
    r'congratulations',
    r'verification\s+complete',
    r'verification\s+passed',
    r'challenge\s+complete',
    r'challenge\s+passed',
    r'you\s+may\s+proceed',
    r'proceed\s+to',
    r'welcome\s+back',
    r'thank\s+you\s+for\s+verifying',
    r'check\s+passed',
    r'security\s+check\s+passed',
    r'identity\s+verified',
    r'human\s+confirmed',
    r'continue\s+browsing',
    r'access\s+granted',
    r'slider\s+verified',
    r'verified\s+success',
]
VERIFIED_TEXT_RE = re.compile('|'.join(VERIFIED_TEXT_PATTERNS), re.IGNORECASE)

# v7: Monetag-specific selectors
MONETAG_SLIDER_SELECTORS = [
    'div[class*="slider-track"]',
    'div[class*="slide-track"]',
    'div[class*="drag-track"]',
    'div[class*="verify-track"]',
    'div[class*="nc-lang-cnt"]',
    'div[class*="nc-container"]',
    'div[id*="nc_"]',
    'div[class*="slider-bg"]',
    'div[class*="track-bg"]',
    'div[class*="slide-bar"]',
    'div[class*="captcha-track"]',
    'div[class*="slide"]',
    'div[class*="drag"]',
    'div[class*="handler"]',
    'div[class*="verify-bar"]',
    'div[class*="captcha-slider"]',
]

MONETAG_THUMB_SELECTORS = [
    'span[class*="nc_iconfont"]',
    'div[class*="btn_slide"]',
    'span[class*="btn_slide"]',
    'div[class*="slider-btn"]',
    'span[class*="slider-btn"]',
    'div[class*="slider-thumb"]',
    'span[class*="slider-thumb"]',
    'div[class*="drag-btn"]',
    'span[class*="drag-btn"]',
    'div[class*="handler"]',
    'span[class*="handler"]',
    'div[class*="drag-handle"]',
    'span[class*="drag-handle"]',
    'div[class*="slideBlock"]',
    'div[class*="slide_block"]',
    '[role="slider"]',
    '[draggable="true"]',
    'span[class*="iconfont"]',
]

MONETAG_CHECKBOX_SELECTORS = [
    '#recaptcha-anchor',
    '.recaptcha-checkbox',
    'div[class*="rc-anchor"]',
    'div[class*="rc-checkbox"]',
    'div[class*="recaptcha"]',
    '[role="checkbox"]',
    'input[type="checkbox"]',
    '.cb-lb',
    'label.cb-lb',
    '#anchor',
    'div[role="button"][aria-checked]',
    'button[role="checkbox"]',
    'div[class*="check"][class*="box"]',
    'div[class*="checkbox"]',
    'span[class*="checkbox"]',
]


# ====================================================================
# Frame walking
# ====================================================================

def _iter_frames(page):
    """Yield (frame, depth) for page.main_frame and all descendant frames."""
    if page is None:
        return
    try:
        main = page.main_frame
    except Exception:
        return
    seen = set()
    stack = [(main, 0)]
    while stack:
        frame, depth = stack.pop(0)
        if frame is None:
            continue
        if id(frame) in seen:
            continue
        seen.add(id(frame))
        yield frame, depth
        try:
            for ch in list(frame.child_frames):
                stack.append((ch, depth + 1))
        except Exception:
            pass


def _safe_inner_text(frame):
    try:
        return frame.evaluate('() => (document.body && document.body.innerText) || ""') or ''
    except Exception:
        return ''


def _safe_locator_all(frame, selector):
    try:
        return list(frame.locator(selector).all())
    except Exception:
        return []


def _safe_is_visible(loc):
    try:
        return loc.is_visible()
    except Exception:
        return False


def _safe_bounding_box(loc):
    try:
        b = loc.bounding_box()
        if b and b.get('width', 0) > 0 and b.get('height', 0) > 0:
            return b
    except Exception:
        pass
    return None


# ====================================================================
# v7: Enhanced verification state detection
# ====================================================================

def is_antibot_verified(page):
    """
    v7: Enhanced check if the antibot challenge is in VERIFIED state.
    Uses multiple signals: text, DOM attributes, class changes, thumb position.
    """
    if page is None:
        return True

    checks_passed = 0
    details = []

    # CHECK 1: Verified text on page
    for frame, _depth in _iter_frames(page):
        try:
            body_text = _safe_inner_text(frame)
            if body_text and VERIFIED_TEXT_RE.search(body_text):
                checks_passed += 1
                details.append('verified_text')
                break
        except Exception:
            pass

    # CHECK 2: JavaScript DOM scan for verified/success elements
    for frame, _depth in _iter_frames(page):
        try:
            js_result = frame.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const cls = (el.className || '').toString().toLowerCase();
                    const dataStatus = (el.getAttribute('data-status') || '').toLowerCase();
                    const dataVerified = el.getAttribute('data-verified');
                    const dataState = (el.getAttribute('data-state') || '').toLowerCase();
                    const ariaChecked = el.getAttribute('aria-checked');
                    const role = (el.getAttribute('role') || '').toLowerCase();
                    
                    if (dataStatus === 'success' || dataStatus === 'verified' || 
                        dataStatus === 'pass' || dataStatus === 'done' ||
                        dataStatus === 'completed' || dataStatus === 'ok') {
                        return {verified: true, reason: 'data-status=' + dataStatus};
                    }
                    if (dataVerified === 'true' || dataVerified === '1') {
                        return {verified: true, reason: 'data-verified=true'};
                    }
                    if (dataState === 'verified' || dataState === 'success' || dataState === 'done') {
                        return {verified: true, reason: 'data-state=' + dataState};
                    }
                    if (role === 'checkbox' && ariaChecked === 'true') {
                        return {verified: true, reason: 'aria-checked=true'};
                    }
                    if (cls) {
                        if (/verified|succe|done|passed|completed|checked|valid|confirmed|finish|solved/.test(cls) &&
                            !/unverified|not-verified/.test(cls)) {
                            return {verified: true, reason: 'class=' + cls.split(' ').find(c => /verified|succe|done|passed|completed/.test(c))};
                        }
                    }
                }
                return {verified: false, reason: ''};
            }""")
            if js_result and js_result.get('verified'):
                checks_passed += 1
                details.append(f'js_dom:{js_result.get("reason","")}')
                break
        except Exception:
            pass

    # CHECK 3: Slider thumb position (at right end = verified)
    thumb_box, track_box, _ = _find_slider_elements(page)
    if thumb_box and track_box:
        thumb_center_x = thumb_box['x'] + thumb_box['width'] / 2
        track_right = track_box['x'] + track_box['width']
        track_start = track_box['x']
        track_width = track_box['width']
        if track_width > 0:
            thumb_progress = (thumb_center_x - track_start) / track_width
            if thumb_progress > 0.85:
                checks_passed += 1
                details.append(f'slider_thumb_at_end:{thumb_progress:.0%}')

    # CHECK 4: Monetag-specific success class on slider container
    for frame, _depth in _iter_frames(page):
        try:
            success_check = frame.evaluate("""() => {
                const selectors = ['div[class*="slider"]', 'div[class*="slide"]', 'div[class*="drag"]'];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const cls = (el.className || '').toString().toLowerCase();
                        if (/success|verified|done|pass/.test(cls) && !/unverified/.test(cls)) {
                            return {verified: true, reason: 'monetag_success_' + cls.split(' ').find(c => /success|verified|done|pass/.test(c))};
                        }
                    }
                }
                return {verified: false};
            }""")
            if success_check and success_check.get('verified'):
                checks_passed += 1
                details.append(f'monetag_success')
                break
        except Exception:
            pass

    # Decision: verified if at least 1 strong check passes
    return checks_passed > 0


def _is_challenge_gone(page):
    """Check if the antibot challenge has disappeared OR is verified."""
    if page is None:
        return True
    try:
        if is_antibot_verified(page):
            return True
    except Exception:
        pass
    try:
        det = detect_antibot(page)
        if not det['detected']:
            return True
        if det.get('verified'):
            return True
    except Exception:
        return True
    return False


def _bring_to_front(page):
    try:
        page.bring_to_front()
        time.sleep(0.2)
    except Exception:
        pass


# ====================================================================
# Viewport -> screen coordinate conversion
# ====================================================================

def _get_window_offset_and_dpr(page):
    """Return (window_x, window_y, dpr) for converting viewport coords to screen coords."""
    try:
        dpr = page.evaluate('window.devicePixelRatio')
        if not isinstance(dpr, (int, float)) or dpr <= 0:
            dpr = 1
    except Exception:
        dpr = 1
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
    return win_x, win_y, dpr


def _viewport_to_screen(page, vx, vy, win_x=None, win_y=None, dpr=None):
    if win_x is None or win_y is None or dpr is None:
        win_x, win_y, dpr = _get_window_offset_and_dpr(page)
    # v8.0: Viewport sanity clamp — viewport coords should never be
    # massively larger than the actual viewport.  For mobile viewports
    # (e.g. Pixel 4 at 412×915 with DPR 3), a viewport Y of 4831
    # (915*3+scroll) produces screen Y of 14493 — WAY off-screen.
    # Cap vx/vy to 2× viewport size as safety margin.
    try:
        vp = page.viewport_size
        if vp:
            max_vx = vp['width'] * 2
            max_vy = vp['height'] * 2
            if vx > max_vx or vy > max_vy:
                # Coordinates are clearly wrong (off-screen for mobile viewport)
                # Clamp to safe maximum and warn
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    f'antibot: viewport coords ({int(vx)},{int(vy)}) exceed '
                    f'viewport limit ({max_vx},{max_vy}) — clamping'
                )
                vx = min(vx, max_vx)
                vy = min(vy, max_vy)
    except Exception:
        pass
    return int(win_x + vx * dpr), int(win_y + vy * dpr)


def _get_screen_size():
    """Legacy: returns full screen size. Prefer _get_mouse_bounds()."""
    try:
        import human_input as hi
        return hi.SCREEN_W, hi.SCREEN_H
    except Exception:
        return 1920, 1080


def _get_mouse_bounds():
    """
    Return the constrained mouse area as (x, y, w, h).
    If browser bounds are set in human_input, use those.
    Otherwise fall back to full screen.
    """
    try:
        import human_input as hi
        bb = hi.get_browser_bounds()
        if bb is not None:
            return bb  # (x, y, w, h)
        return (0, 0, hi.SCREEN_W, hi.SCREEN_H)
    except Exception:
        return (0, 0, 1920, 1080)


def _clamp_to_browser_bounds(x, y, margin=10):
    """Clamp (x, y) to stay within browser bounds (or full screen)."""
    bx, by, bw, bh = _get_mouse_bounds()
    x = max(bx + margin, min(x, bx + bw - margin))
    y = max(by + margin, min(y, by + bh - margin))
    return int(x), int(y)


# ====================================================================
# Drag primitives — OPTIMIZED for human-like movement
# ====================================================================

def _playwright_drag(page, start_x, start_y, end_x, end_y, logger=None):
    """Drag using Playwright's page.mouse API with improved human simulation."""
    try:
        # Add small random offset to start
        offset_x = random.uniform(-3, 3)
        offset_y = random.uniform(-2, 2)
        page.mouse.move(start_x + offset_x, start_y + offset_y, steps=random.randint(10, 18))
        time.sleep(random.uniform(0.15, 0.4))

        page.mouse.down()
        time.sleep(random.uniform(0.06, 0.15))

        # Human-like drag with variable speed and overshoot
        steps = random.randint(18, 30)
        for i in range(1, steps + 1):
            t = i / steps
            # Easing: start slow, speed up, slow down at end
            if t < 0.3:
                ease = 2 * t * t
            elif t < 0.7:
                ease = 0.18 + 0.64 * (t - 0.3)
            else:
                ease = 1 - 2 * (1 - t) * (1 - t)
            
            cx = start_x + (end_x - start_x) * ease + random.uniform(-2, 2)
            wobble = math.sin(t * math.pi * random.uniform(1.5, 3.5)) * random.uniform(0.3, 1.5)
            cy = start_y + (end_y - start_y) * ease + wobble
            page.mouse.move(cx, cy, steps=1)
            
            # Variable timing
            if 0.2 < t < 0.8:
                time.sleep(random.uniform(0.01, 0.03))
            else:
                time.sleep(random.uniform(0.03, 0.08))

        # Overshoot and correct (human behavior)
        overshoot = random.uniform(2, 8)
        page.mouse.move(end_x + overshoot, end_y + random.uniform(-2, 2), steps=1)
        time.sleep(random.uniform(0.06, 0.15))
        page.mouse.move(end_x, end_y, steps=1)
        time.sleep(random.uniform(0.08, 0.18))

        page.mouse.up()
        time.sleep(random.uniform(0.05, 0.12))
        
        # Post-drag micro-movement
        page.mouse.move(end_x + random.uniform(-4, 4), end_y + random.uniform(-4, 4), steps=random.randint(2, 4))
        return True
    except Exception as e:
        if logger:
            logger.warn(f'  Playwright drag failed: {e}')
        return False


def _pyautogui_drag(page, start_vx, start_vy, end_vx, end_vy,
                    screen_w, screen_h, logger=None):
    """Fallback drag via pyautogui with proper viewport->screen conversion."""
    import pyautogui
    win_x, win_y, dpr = _get_window_offset_and_dpr(page)

    sx1, sy1 = _viewport_to_screen(page, start_vx, start_vy, win_x, win_y, dpr)
    sx2, sy2 = _viewport_to_screen(page, end_vx, end_vy, win_x, win_y, dpr)
    # Clamp to browser bounds (not just screen)
    sx1, sy1 = _clamp_to_browser_bounds(sx1, sy1)
    sx2, sy2 = _clamp_to_browser_bounds(sx2, sy2)

    try:
        try:
            import human_input as hi
            hi.human_move_to(sx1, sy1, duration=random.uniform(0.3, 0.7))
        except Exception:
            sx1, sy1 = _clamp_to_browser_bounds(sx1, sy1)
            pyautogui.moveTo(sx1, sy1, duration=random.uniform(0.3, 0.7))
        time.sleep(random.uniform(0.12, 0.3))
        pyautogui.mouseDown(button='left')
        time.sleep(random.uniform(0.05, 0.12))
        steps = random.randint(12, 20)
        for i in range(1, steps + 1):
            t = i / steps
            ease = t * t * (3 - 2 * t)
            cx = int(sx1 + (sx2 - sx1) * ease)
            cy = int(sy1 + (sy2 - sy1) * ease + random.randint(-3, 3))
            cx, cy = _clamp_to_browser_bounds(cx, cy)
            try:
                import human_input as hi
                hi._safe_moveTo(cx, cy, duration=random.uniform(0.03, 0.08))
            except Exception:
                cx, cy = _clamp_to_browser_bounds(cx, cy)
                pyautogui.moveTo(cx, cy, duration=random.uniform(0.03, 0.08))
            time.sleep(random.uniform(0.02, 0.06))
        try:
            ox, oy = _clamp_to_browser_bounds(sx2 + random.randint(2, 5), sy2)
            import human_input as hi
            hi._safe_moveTo(ox, oy, duration=0.06)
        except Exception:
            pass
        time.sleep(random.uniform(0.06, 0.15))
        try:
            sx2c, sy2c = _clamp_to_browser_bounds(sx2, sy2)
            import human_input as hi
            hi._safe_moveTo(sx2c, sy2c, duration=0.05)
        except Exception:
            pass
        time.sleep(random.uniform(0.08, 0.18))
        pyautogui.mouseUp(button='left')
        return True
    except Exception as e:
        if logger:
            logger.warn(f'  pyautogui drag failed: {e}')
        return False


# ====================================================================
# Click primitive
# ====================================================================

def _click_checkbox(page, logger=None, label='checkbox'):
    """Click a checkbox-style element. Tries both pyautogui and Playwright.
    v8.0: Scrolls element into view before getting bounding box, and falls
    back to Playwright click if viewport coordinates are off-screen.
    """
    frame, loc, box = _find_checkbox_element(page)
    if loc is None:
        if logger:
            logger.warn(f'  No {label} element found in any frame')
        return False

    # v8.0: Scroll into view before clicking to prevent off-screen coords
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
        time.sleep(0.2)
        # Re-fetch bounding box after scroll
        box = _safe_bounding_box(loc) or box
    except Exception:
        pass

    vx = box['x'] + box['width'] * random.uniform(0.4, 0.6)
    vy = box['y'] + box['height'] * random.uniform(0.4, 0.6)

    # v8.0: Sanity check — if viewport coords are off-screen, fall back
    # to Playwright click directly (e.g. iframe scroll offset bug produces
    # Y values like 14493 on a 1080px screen)
    if vy > 2000 or vx > 3000:
        if logger:
            logger.warn(f'  Viewport coords ({int(vx)},{int(vy)}) off-screen — '
                        f'falling back to Playwright click for {label}')
        _bring_to_front(page)
        try:
            loc.click(timeout=3000)
            return True
        except Exception as e:
            if logger:
                logger.warn(f'  Playwright fallback click also failed: {e}')
            return False

    win_x, win_y, dpr = _get_window_offset_and_dpr(page)
    sx, sy = _viewport_to_screen(page, vx, vy, win_x, win_y, dpr)
    # Clamp to browser bounds (not just screen)
    sx, sy = _clamp_to_browser_bounds(sx, sy)

    _bring_to_front(page)

    if logger:
        logger.info(f'  Clicking {label}: viewport=({int(vx)},{int(vy)}) -> screen=({int(sx)},{int(sy)})')

    try:
        import pyautogui
        try:
            import human_input as hi
            hi.human_move_to(sx, sy, duration=random.uniform(0.3, 0.6))
        except Exception:
            sx, sy = _clamp_to_browser_bounds(sx, sy)
            pyautogui.moveTo(sx, sy, duration=random.uniform(0.3, 0.6))
        time.sleep(random.uniform(0.08, 0.2))
        pyautogui.mouseDown(button='left')
        time.sleep(random.uniform(0.04, 0.10))
        pyautogui.mouseUp(button='left')
        time.sleep(random.uniform(0.15, 0.4))
        return True
    except Exception as e:
        if logger:
            logger.warn(f'  pyautogui click failed: {e}; trying Playwright click')
    try:
        loc.click(timeout=2000)
        return True
    except Exception as e:
        if logger:
            logger.warn(f'  Playwright click also failed: {e}')
        return False


def _click_at_viewport(page, vx, vy, logger=None):
    """Click at specific viewport coordinates using pyautogui or Playwright.
    v8.0: Falls back to Playwright page.mouse.click if viewport coords are
    off-screen (vy > 2000 or vx > 3000).
    """
    # v8.0: Sanity check — off-screen viewport coords (e.g. from iframe
    # bounding_box() that includes scroll offset) produce massive screen
    # coordinates like y=14493. Fall back to Playwright mouse click.
    if vy > 2000 or vx > 3000:
        if logger:
            logger.warn(f'  Viewport coords ({int(vx)},{int(vy)}) off-screen — '
                        f'falling back to Playwright mouse.click')
        _bring_to_front(page)
        try:
            page.mouse.click(min(vx, 500), min(vy, 1000))
            return True
        except Exception as e:
            if logger:
                logger.warn(f'  Playwright mouse.click fallback also failed: {e}')
            return False

    win_x, win_y, dpr = _get_window_offset_and_dpr(page)
    sx, sy = _viewport_to_screen(page, vx, vy, win_x, win_y, dpr)
    # Clamp to browser bounds (not just screen)
    sx, sy = _clamp_to_browser_bounds(sx, sy)

    _bring_to_front(page)

    try:
        import pyautogui
        try:
            import human_input as hi
            hi.human_move_to(sx, sy, duration=random.uniform(0.3, 0.6))
        except Exception:
            sx, sy = _clamp_to_browser_bounds(sx, sy)
            pyautogui.moveTo(sx, sy, duration=random.uniform(0.3, 0.6))
        time.sleep(random.uniform(0.08, 0.2))
        pyautogui.mouseDown(button='left')
        time.sleep(random.uniform(0.04, 0.10))
        pyautogui.mouseUp(button='left')
        time.sleep(random.uniform(0.15, 0.4))
        return True
    except Exception as e:
        if logger:
            logger.warn(f'  pyautogui click_at failed: {e}')
    try:
        page.mouse.click(vx, vy)
        return True
    except Exception as e:
        if logger:
            logger.warn(f'  Playwright click_at failed: {e}')
        return False


# ====================================================================
# Element finders
# ====================================================================

def _find_slider_elements(page):
    """Aggressively search for slider track + thumb elements."""
    best_track = None
    best_thumb = None
    best_frame = None
    
    for frame, _depth in _iter_frames(page):
        for sel in MONETAG_SLIDER_SELECTORS:
            for loc in _safe_locator_all(frame, sel):
                if not _safe_is_visible(loc):
                    continue
                box = _safe_bounding_box(loc)
                if not box:
                    continue
                w, h = box['width'], box['height']
                if w < 80 or h > 80:
                    continue
                aspect = w / max(h, 1)
                if aspect < 2.0:
                    continue
                if best_track is None or w > best_track['width']:
                    best_track = box
                    best_frame = frame
        
        if not best_track:
            continue
            
        for sel in MONETAG_THUMB_SELECTORS:
            for loc in _safe_locator_all(frame, sel):
                if not _safe_is_visible(loc):
                    continue
                tbox = _safe_bounding_box(loc)
                if not tbox or tbox['width'] < 8 or tbox['height'] < 8:
                    continue
                tb = best_track
                if (tbox['x'] >= tb['x'] - 10 and
                    tbox['y'] >= tb['y'] - 20 and
                    tbox['x'] + tbox['width'] <= tb['x'] + tb['width'] + 10 and
                    tbox['y'] + tbox['height'] <= tb['y'] + tb['height'] + 20):
                    if tbox['width'] <= 70 and tbox['height'] <= 70:
                        if best_thumb is None or tbox['width'] > best_thumb['width']:
                            best_thumb = tbox
                            best_frame = frame
        
        if best_track and not best_thumb:
            try:
                thumb_data = frame.evaluate("""() => {
                    const track = arguments[0];
                    const all = document.querySelectorAll('*');
                    let best = null;
                    for (const el of all) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 8 || rect.height < 8) continue;
                        if (rect.width > 70 || rect.height > 70) continue;
                        if (rect.x >= track.x - 5 && rect.x <= track.x + track.width * 0.3 &&
                            rect.y >= track.y - 15 && rect.y <= track.y + track.height + 5) {
                            const style = window.getComputedStyle(el);
                            const cursor = style.cursor || '';
                            const draggable = el.getAttribute('draggable');
                            const role = el.getAttribute('role') || '';
                            const cls = (el.className || '').toString().toLowerCase();
                            if (cursor.includes('pointer') || cursor.includes('grab') ||
                                draggable === 'true' || role === 'slider' ||
                                cls.includes('thumb') || cls.includes('btn') ||
                                cls.includes('handle') || cls.includes('drag') ||
                                cls.includes('slide') || cls.includes('icon')) {
                                if (!best || rect.width > best.width) {
                                    best = {x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                                }
                            }
                        }
                    }
                    return best;
                }""", best_track)
                if thumb_data:
                    best_thumb = thumb_data
            except Exception:
                pass
    
    return best_thumb, best_track, best_frame


def _find_checkbox_element(page):
    """Aggressively search for a checkbox/verify element.
    v8.0: Scrolls each candidate into view before getting bounding box
    to prevent off-screen Y coordinates from iframe scroll offsets.
    """
    for frame, _depth in _iter_frames(page):
        for sel in MONETAG_CHECKBOX_SELECTORS:
            for loc in _safe_locator_all(frame, sel):
                if not _safe_is_visible(loc):
                    continue
                # v8.0: Scroll into view before getting bounding box
                try:
                    loc.scroll_into_view_if_needed(timeout=3000)
                    time.sleep(0.1)
                except Exception:
                    pass
                box = _safe_bounding_box(loc)
                if not box:
                    continue
                if box['width'] < 10 or box['height'] < 10:
                    continue
                return frame, loc, box
    
    for frame, _depth in _iter_frames(page):
        try:
            checkbox_data = frame.evaluate("""() => {
                const all = document.querySelectorAll('*');
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 18 || rect.height < 18) continue;
                    if (rect.width > 80 || rect.height > 80) continue;
                    const ratio = rect.width / Math.max(rect.height, 1);
                    if (ratio < 0.5 || ratio > 2.0) continue;
                    
                    const style = window.getComputedStyle(el);
                    const cursor = style.cursor || '';
                    const role = el.getAttribute('role') || '';
                    const type = (el.getAttribute('type') || '').toLowerCase();
                    const cls = (el.className || '').toString().toLowerCase();
                    const id = (el.id || '').toLowerCase();
                    
                    if (type === 'checkbox' || role === 'checkbox' ||
                        cls.includes('recaptcha') || cls.includes('hcaptcha') ||
                        cls.includes('turnstile') || cls.includes('cf-turnstile') ||
                        id.includes('recaptcha') || id.includes('hcaptcha') ||
                        id.includes('checkbox') || id.includes('cf-chl')) {
                        return {x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                                tag: el.tagName, cls: cls, id: id};
                    }
                }
                return null;
            }""")
            if checkbox_data:
                cls = checkbox_data.get('cls', '')
                tag = checkbox_data.get('tag', '').lower()
                eid = checkbox_data.get('id', '')
                
                locators_to_try = []
                if eid:
                    locators_to_try.append(f'#{eid}')
                if cls and cls.split():
                    locators_to_try.append(f'{tag}.{cls.split()[0]}')
                locators_to_try.append(f'{tag}[role="checkbox"]')
                
                for sel in locators_to_try:
                    for loc in _safe_locator_all(frame, sel):
                        if _safe_is_visible(loc):
                            box = _safe_bounding_box(loc)
                            if box:
                                return frame, loc, box
        except Exception:
            pass
    
    return None, None, None


# ====================================================================
# detect_antibot — v7 enhanced
# ====================================================================

def detect_antibot(page):
    """
    v7: Scan the page for an anti-bot interstitial.
    Distinguishes between "unsolved" and "already verified" states.
    """
    result = {
        'detected': False,
        'verified': False,
        'type': None,
        'reason': '',
        'frame': None,
        'thumb_box': None,
        'track_box': None,
        'click_target': None,
        'click_locator': None,
        'text_match': None,
        'selector': None,
        'extra': {},
    }
    if page is None:
        return result

    # First check if already verified
    already_verified = False
    try:
        already_verified = is_antibot_verified(page)
    except Exception:
        pass

    # ============================================================
    # PASS 1: Text pattern detection
    # ============================================================
    for frame, _depth in _iter_frames(page):
        body_text = _safe_inner_text(frame)
        if body_text:
            m = ANTIBOT_TEXT_RE.search(body_text)
            if m:
                result['detected'] = True
                result['verified'] = already_verified
                result['text_match'] = m.group(0)
                result['frame'] = frame
                result['reason'] = f'text="{m.group(0)}"'
                low = m.group(0).lower()
                if 'slide' in low:
                    result['type'] = 'slider'
                elif 'click' in low or 'tap' in low:
                    result['type'] = 'click'
                elif 'verify' in low or 'verification' in low:
                    result['type'] = 'click'
                elif 'moment' in low or 'checking' in low or 'attention' in low:
                    result['type'] = 'turnstile'
                else:
                    result['type'] = 'unknown'
                break

    # ============================================================
    # PASS 2: Slider element detection
    # ============================================================
    thumb_box, track_box, slider_frame = _find_slider_elements(page)
    if thumb_box and track_box:
        result['detected'] = True
        result['verified'] = already_verified
        result['type'] = 'slider'
        result['thumb_box'] = thumb_box
        result['track_box'] = track_box
        result['frame'] = slider_frame
        if not result['reason']:
            result['reason'] = f'slider detected (thumb {int(thumb_box["width"])}x{int(thumb_box["height"])})'
        result['type'] = 'slider'
    elif track_box and not thumb_box:
        result['detected'] = True
        result['verified'] = already_verified
        result['type'] = 'slider'
        result['track_box'] = track_box
        result['frame'] = slider_frame
        if not result['reason']:
            result['reason'] = f'slider track detected'

    # ============================================================
    # PASS 3: Checkbox/verify element detection
    # ============================================================
    if not result.get('thumb_box'):
        cb_frame, cb_loc, cb_box = _find_checkbox_element(page)
        if cb_frame and cb_loc and cb_box:
            if not result['detected']:
                result['detected'] = True
                result['verified'] = already_verified
                result['type'] = 'click'
                result['frame'] = cb_frame
                result['click_target'] = cb_box
                result['click_locator'] = cb_loc
                if not result['reason']:
                    result['reason'] = f'checkbox detected'
            elif result['type'] in ('unknown', 'click'):
                result['type'] = 'click'
                result['click_target'] = cb_box
                result['click_locator'] = cb_loc

    # Refine unknown type
    if result['type'] == 'unknown' and result['detected']:
        if result.get('thumb_box') or result.get('track_box'):
            result['type'] = 'slider'
        elif result.get('click_target') or result.get('click_locator'):
            result['type'] = 'click'

    return result


# ====================================================================
# Slider solver — v7 optimized
# ====================================================================

def solve_slider(page, logger=None, thumb_box=None, track_box=None,
                 max_attempts=5):
    """v7: Solve a slider anti-bot with human-like drag."""
    if page is None:
        return False

    for attempt in range(1, max_attempts + 1):
        # Check if already verified
        try:
            if is_antibot_verified(page):
                if logger:
                    logger.ok('Antibot slider already verified')
                return True
        except Exception:
            pass

        # Re-detect each attempt
        if not thumb_box or not track_box:
            det = detect_antibot(page)
            if det.get('verified'):
                if logger:
                    logger.ok('Antibot slider already verified (detection)')
                return True
            thumb_box = thumb_box or det.get('thumb_box')
            track_box = track_box or det.get('track_box')
        else:
            try:
                det = detect_antibot(page)
                if det.get('verified'):
                    if logger:
                        logger.ok('Antibot slider already verified')
                    return True
                if det.get('thumb_box'):
                    thumb_box = det['thumb_box']
                if det.get('track_box'):
                    track_box = det['track_box']
            except Exception:
                pass

        if not thumb_box or not track_box:
            if _is_challenge_gone(page):
                if logger:
                    logger.ok('Antibot slider gone (already solved)')
                return True
            if logger:
                logger.info(f'  Slider attempt {attempt}: searching for elements...')
            time.sleep(1.0)
            thumb_box, track_box, _ = _find_slider_elements(page)
            if not thumb_box or not track_box:
                if logger:
                    logger.warn(f'  Slider attempt {attempt}: thumb/track not found')
                continue

        if logger:
            logger.info(f'Slider attempt {attempt}/{max_attempts} (thumb at x={int(thumb_box["x"])})')

        _bring_to_front(page)

        # Calculate drag coordinates with slight offset variation
        start_x = thumb_box['x'] + thumb_box['width'] / 2 + random.uniform(-2, 2)
        start_y = thumb_box['y'] + thumb_box['height'] / 2 + random.uniform(-1, 1)
        end_x = track_box['x'] + track_box['width'] - thumb_box['width'] / 2 - random.uniform(2, 6)
        end_y = start_y + random.uniform(-3, 3)

        if logger:
            logger.info(f'  Dragging viewport ({int(start_x)},{int(start_y)}) -> ({int(end_x)},{int(end_y)})')

        # PRIMARY: Playwright mouse API
        dragged = _playwright_drag(page, start_x, start_y, end_x, end_y, logger=logger)

        # FALLBACK: pyautogui
        if not dragged:
            screen_w, screen_h = _get_screen_size()
            dragged = _pyautogui_drag(page, start_x, start_y, end_x, end_y, screen_w, screen_h, logger=logger)

        if not dragged:
            time.sleep(0.5)
            continue

        # Wait for verification
        time.sleep(random.uniform(1.5, 3.0))

        # Check if solved
        try:
            if is_antibot_verified(page):
                if logger:
                    logger.ok('Antibot slider solved (verified state)')
                return True
        except Exception:
            pass

        if _is_challenge_gone(page):
            if logger:
                logger.ok('Antibot slider solved (challenge gone)')
            return True

        # Re-detect and check
        try:
            new_det = detect_antibot(page)
            if not new_det['detected']:
                if logger:
                    logger.ok('Antibot slider solved (disappeared)')
                return True
            if new_det.get('verified'):
                if logger:
                    logger.ok('Antibot slider solved (verified flag)')
                return True
            new_thumb = new_det.get('thumb_box')
            if new_thumb and new_thumb['x'] > track_box['x'] + track_box['width'] * 0.8:
                if logger:
                    logger.ok('Antibot slider solved (thumb at end)')
                return True
        except Exception:
            pass

        # Try refresh link for failed sliders
        try:
            for sel in ['a:has-text("refresh")', 'a:has-text("Refresh")',
                        'div:has-text("Click to refresh")', 'span:has-text("refresh")']:
                try:
                    rl = page.locator(sel).first
                    if _safe_is_visible(rl):
                        rl.click(timeout=2000)
                        time.sleep(random.uniform(0.8, 1.5))
                        break
                except Exception:
                    continue
        except Exception:
            pass

        time.sleep(random.uniform(1.0, 2.0))
        thumb_box = None
        track_box = None

    if logger:
        logger.warn(f'Antibot slider NOT solved after {max_attempts} attempts')
    return False


# ====================================================================
# Click solver — v7 optimized
# ====================================================================

def solve_click(page, logger=None, max_attempts=5):
    """v7: Click solver for checkbox-style challenges."""
    if page is None:
        return False

    for attempt in range(1, max_attempts + 1):
        try:
            if is_antibot_verified(page):
                if logger:
                    logger.ok('Antibot click already verified')
                return True
        except Exception:
            pass

        if _is_challenge_gone(page):
            if logger:
                logger.ok('Antibot gone (already solved)')
            return True

        if logger:
            logger.info(f'Click attempt {attempt}/{max_attempts}')

        clicked = False
        
        # Strategy 1: Find and click checkbox
        cb_frame, cb_loc, cb_box = _find_checkbox_element(page)
        if cb_loc and cb_box:
            # v8.0: Scroll checkbox into view and re-fetch bounding box
            try:
                cb_loc.scroll_into_view_if_needed(timeout=3000)
                time.sleep(0.2)
                new_box = _safe_bounding_box(cb_loc)
                if new_box:
                    cb_box = new_box
            except Exception:
                pass
            vx = cb_box['x'] + cb_box['width'] * random.uniform(0.4, 0.6)
            vy = cb_box['y'] + cb_box['height'] * random.uniform(0.4, 0.6)
            if logger:
                logger.info(f'  Clicking checkbox at ({int(vx)},{int(vy)})')
            _bring_to_front(page)
            clicked = _click_at_viewport(page, vx, vy, logger=logger)
            if not clicked:
                try:
                    cb_loc.click(timeout=3000)
                    clicked = True
                except Exception:
                    pass
        
        # Strategy 2: Click any "Verify" text element
        if not clicked:
            try:
                for frame, _depth in _iter_frames(page):
                    for sel in ['div:has-text("Verify"):visible',
                                'span:has-text("Verify"):visible',
                                'button:has-text("Verify"):visible']:
                        for loc in _safe_locator_all(frame, sel):
                            if _safe_is_visible(loc):
                                box = _safe_bounding_box(loc)
                                if box and 30 <= box['width'] <= 300 and 20 <= box['height'] <= 80:
                                    vx = box['x'] + box['width'] * 0.2
                                    vy = box['y'] + box['height'] / 2
                                    if logger:
                                        logger.info(f'  Clicking "Verify" element')
                                    _bring_to_front(page)
                                    clicked = _click_at_viewport(page, vx, vy, logger=logger)
                                    if clicked:
                                        break
                        if clicked:
                            break
                    if clicked:
                        break
            except Exception:
                pass

        if not clicked:
            time.sleep(0.8)
            continue

        time.sleep(random.uniform(1.5, 3.0))

        try:
            if is_antibot_verified(page):
                if logger:
                    logger.ok('Click solved (verified state)')
                return True
        except Exception:
            pass

        if _is_challenge_gone(page):
            if logger:
                logger.ok('Click solved')
            return True

        time.sleep(random.uniform(1.0, 2.0))

    if logger:
        logger.warn(f'Click NOT solved after {max_attempts} attempts')
    return False


# ====================================================================
# reCAPTCHA v2 solver
# ====================================================================

def solve_recaptcha_v2(page, logger=None, max_attempts=3):
    """Solve Google reCAPTCHA v2 checkbox."""
    if page is None:
        return False

    for attempt in range(1, max_attempts + 1):
        if logger:
            logger.info(f'reCAPTCHA v2: attempt {attempt}/{max_attempts}')

        try:
            if is_antibot_verified(page):
                if logger:
                    logger.ok('reCAPTCHA already verified')
                return True
        except Exception:
            pass

        if _is_challenge_gone(page):
            if logger:
                logger.ok('reCAPTCHA gone')
            return True

        anchor_loc = None
        for frame, _depth in _iter_frames(page):
            for loc in _safe_locator_all(frame, '#recaptcha-anchor'):
                if _safe_is_visible(loc):
                    anchor_loc = loc
                    break
            if anchor_loc:
                break

        if anchor_loc:
            try:
                if anchor_loc.get_attribute('aria-checked') == 'true':
                    if logger:
                        logger.ok('reCAPTCHA already checked')
                    return True
            except Exception:
                pass

        clicked = _click_checkbox(page, logger=logger, label='reCAPTCHA')
        if not clicked:
            time.sleep(0.8)
            continue

        verified = False
        for _ in range(20):
            time.sleep(0.5)
            if anchor_loc is None:
                for frame, _depth in _iter_frames(page):
                    for loc in _safe_locator_all(frame, '#recaptcha-anchor'):
                        if _safe_is_visible(loc):
                            anchor_loc = loc
                            break
                    if anchor_loc:
                        break
            if anchor_loc:
                try:
                    state = anchor_loc.get_attribute('aria-checked')
                    if state == 'true':
                        verified = True
                        break
                except Exception:
                    verified = True
                    break
            else:
                if _is_challenge_gone(page):
                    verified = True
                    break

        if verified:
            if logger:
                logger.ok('reCAPTCHA v2 verified')
            time.sleep(random.uniform(0.8, 1.5))
            return True

        if logger:
            logger.warn('reCAPTCHA did not verify; may require image challenge')
        break

    if logger:
        logger.warn(f'reCAPTCHA v2 NOT solved after {max_attempts} attempts')
    return False


# ====================================================================
# Top-level entry
# ====================================================================

def solve_antibot_if_present(page, logger=None, max_total_time_s=45):
    """
    v7: Detect & solve any anti-bot interstitial on the page.
    Returns: {'detected': bool, 'solved': bool, 'type': str, 'reason': str, 'verified': bool}
    """
    out = {'detected': False, 'solved': False, 'type': None, 'reason': '', 'verified': False}

    if page is None:
        return out

    try:
        det = detect_antibot(page)
    except Exception as e:
        if logger:
            logger.warn(f'Antibot detection error: {e}')
        return out

    if not det['detected']:
        return out

    if det.get('verified'):
        out['detected'] = True
        out['solved'] = True
        out['type'] = det['type']
        out['reason'] = det['reason']
        out['verified'] = True
        if logger:
            logger.ok(f'Antibot already verified: type={det["type"]}')
        return out

    out['detected'] = True
    out['type'] = det['type']
    out['reason'] = det['reason']
    if logger:
        logger.warn(f'Antibot challenge detected: type={det["type"]}')

    deadline = time.time() + max_total_time_s

    # Build solver chain
    primary = {
        'slider': solve_slider,
        'click': solve_click,
    }.get(det['type'])

    if primary:
        attempts = [primary, solve_click, solve_slider, solve_recaptcha_v2]
    else:
        attempts = [solve_slider, solve_click, solve_recaptcha_v2]

    for solver in attempts:
        if time.time() > deadline:
            break
        try:
            if is_antibot_verified(page):
                out['solved'] = True
                out['verified'] = True
                if logger:
                    logger.ok('Antibot verified (pre-solver)')
                return out
        except Exception:
            pass

        try:
            if solver == solve_slider and det.get('thumb_box'):
                ok = solver(page, logger=logger,
                           thumb_box=det.get('thumb_box'),
                           track_box=det.get('track_box'))
            else:
                ok = solver(page, logger=logger)
        except Exception as e:
            if logger:
                logger.warn(f'Solver {solver.__name__} error: {e}')
            ok = False

        if ok:
            out['solved'] = True
            if logger:
                logger.ok(f'Antibot solved via {solver.__name__}')
            return out

        try:
            if is_antibot_verified(page):
                out['solved'] = True
                out['verified'] = True
                if logger:
                    logger.ok('Antibot verified after solver')
                return out
            new_det = detect_antibot(page)
            if not new_det['detected']:
                out['solved'] = True
                if logger:
                    logger.ok('Antibot disappeared')
                return out
            if new_det.get('verified'):
                out['solved'] = True
                out['verified'] = True
                if logger:
                    logger.ok('Antibot verified (detection)')
                return out
            det = new_det
        except Exception:
            pass

    if logger:
        logger.error('Antibot challenge NOT solved')
    return out