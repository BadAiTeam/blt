"""
Real mouse + scroll + keyboard controller using pyautogui.
OPTIMIZED v9.1 — Browser bounds constraint (mouse cannot leave browser area).

v9.1 changes (addressing mouse escaping browser window):
  1. NEW: Browser bounds constraint system — all mouse movements and clicks
     are automatically clamped to the browser content area on screen.
     This prevents the mouse from moving to/clicking the taskbar, desktop,
     or other areas outside the browser window.
  2. NEW: set_browser_bounds(x, y, w, h) — define the browser content area
     on screen. Called by bot_v6.py after browser launch.
  3. NEW: _safe_moveTo() / _safe_moveRel() — drop-in replacements for
     pyautogui.moveTo() / pyautogui.moveRel() that auto-clamp coordinates
     to browser bounds.
  4. MODIFIED: All pyautogui.moveTo / pyautogui.moveRel calls replaced with
     _safe_moveTo / _safe_moveRel so constraint is enforced globally.

v9.0 changes (addressing issue #5: Kelemahan Simulasi Perilaku Manusia):
  1. FIXED: Static Bezier curve formula — now uses per-session randomized
     control point generation with micro-tremor simulation (human hand tremor)
  2. FIXED: Uniform distribution timing — now uses Gaussian (normal) distribution
     for delays, matching real human behavior (most delays cluster around a mean,
     with occasional long pauses — NOT uniform random)
  3. NEW: Micro-tremor simulation — adds physiological hand tremor to mouse paths
     (8-12 Hz oscillation, matching real human neuromuscular tremor frequency)
  4. NEW: Hesitation pauses — random pauses mid-movement (decision points)
  5. NEW: Overshoot + correction pattern — humans often slightly overshoot
     targets and correct back, especially for fast movements
  6. NEW: Acceleration profile matching Fitts's Law — movement time scales
     logarithmically with distance, matching real human motor control
  7. NEW: Non-uniform click timing — uses beta distribution for press duration
     and gamma distribution for inter-click intervals
  8. NEW: Variable scroll speed with acceleration curves
  9. NEW: Typing rhythm with burst patterns (fast bursts separated by pauses)
     instead of uniform per-key delay

Key insight: Anti-bot ML systems collect telemetry over many sessions.
If your timing always follows uniform random distribution, the statistical
fingerprint is obvious. Real humans have:
  - Lognormal reaction times (not uniform)
  - Gamma-distributed inter-action intervals
  - Burst patterns in typing (fast sequences followed by pauses)
  - Micro-tremor at 8-12 Hz in mouse movements
  - Fitts's Law compliance in target acquisition time
"""

import os
os.environ.setdefault('DISPLAY', ':99')
os.environ.setdefault('XAUTHORITY', '/home/z/.Xauthority')

import pyautogui
import random
import time
import math

# Safety: disable pyautogui's fail-safe (we don't have a real mouse anyway)
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0  # We control all timing ourselves

SCREEN_W, SCREEN_H = pyautogui.size()


# ====================================================================
# Browser bounds constraint — mouse cannot leave browser content area
# ====================================================================
# _browser_bounds = None  →  use full screen (backward compatible)
# _browser_bounds = (x, y, w, h)  →  clamp all mouse ops to this rect

_browser_bounds = None   # (x, y, width, height) in screen coordinates

def set_browser_bounds(x, y, w, h):
    """
    Define the browser content area on the virtual screen.
    All subsequent mouse movements/clicks will be clamped to this area,
    preventing the mouse from escaping to the taskbar or desktop.

    Args:
        x, y: top-left corner of the browser content area (screen coords)
        w, h: width and height of the browser content area (screen coords)
    """
    global _browser_bounds
    _browser_bounds = (int(x), int(y), int(w), int(h))

def get_browser_bounds():
    """Return current browser bounds as (x, y, w, h) or None."""
    return _browser_bounds

def clear_browser_bounds():
    """Reset to full-screen constraint (backward compatible)."""
    global _browser_bounds
    _browser_bounds = None

def _clamp_to_browser(x, y, margin=5):
    """
    Clamp (x, y) to stay within browser bounds (or full screen if unset).
    Margin is subtracted from edges to avoid clicking exactly on borders.
    Returns (clamped_x, clamped_y).
    """
    if _browser_bounds is not None:
        bx, by, bw, bh = _browser_bounds
        x = max(bx + margin, min(x, bx + bw - margin))
        y = max(by + margin, min(y, by + bh - margin))
    else:
        x = max(margin, min(x, SCREEN_W - margin))
        y = max(margin, min(y, SCREEN_H - margin))
    return int(x), int(y)

def _safe_moveTo(x, y, **kwargs):
    """pyautogui.moveTo with automatic browser-bounds clamping."""
    x, y = _clamp_to_browser(x, y)
    pyautogui.moveTo(x, y, **kwargs)

def _safe_moveRel(dx, dy, **kwargs):
    """pyautogui.moveRel with automatic browser-bounds clamping."""
    cx, cy = pyautogui.position()
    nx, ny = _clamp_to_browser(cx + dx, cy + dy)
    # Move to the clamped absolute position instead of relative
    pyautogui.moveTo(nx, ny, **kwargs)


# ====================================================================
# Distribution helpers — replacing uniform random with realistic distributions
# ====================================================================

def _gauss(mean, std, min_val=None, max_val=None):
    """Gaussian (normal) distribution — most human behaviors follow this."""
    val = random.gauss(mean, std)
    if min_val is not None:
        val = max(min_val, val)
    if max_val is not None:
        val = min(max_val, val)
    return val


def _lognormal(mean, sigma, min_val=None, max_val=None):
    """Lognormal distribution — reaction times follow this pattern."""
    val = random.lognormvariate(math.log(mean), sigma)
    if min_val is not None:
        val = max(min_val, val)
    if max_val is not None:
        val = min(max_val, val)
    return val


def _gamma(alpha, beta, min_val=None, max_val=None):
    """Gamma distribution — inter-action intervals follow this pattern."""
    val = random.gammavariate(alpha, beta)
    if min_val is not None:
        val = max(min_val, val)
    if max_val is not None:
        val = min(max_val, val)
    return val


def _beta(alpha, beta_param, min_val=None, max_val=None):
    """Beta distribution — constrained random in [0,1], useful for ratios."""
    val = random.betavariate(alpha, beta_param)
    if min_val is not None:
        val = min_val + val * (max_val - min_val)
    elif max_val is not None:
        val = val * max_val
    return val


# ====================================================================
# Per-session configuration — randomized ONCE per session
# ====================================================================
# This prevents the "static mathematical pattern" detection where
# multiple sessions show identical acceleration/deceleration curves.

_session_config = None

def _get_session_config():
    """Generate per-session movement parameters. Called once per session."""
    global _session_config
    if _session_config is not None:
        return _session_config

    _session_config = {
        # Tremor frequency (Hz) — real human tremor is 8-12 Hz
        'tremor_freq': _gauss(10, 1.5, min_val=7, max_val=13),
        # Tremor amplitude (pixels) — subtle, increases with speed
        'tremor_amplitude': _gauss(1.5, 0.5, min_val=0.5, max_val=3.0),
        # Base speed multiplier (each person has a consistent speed)
        'speed_factor': _gauss(1.0, 0.2, min_val=0.6, max_val=1.5),
        # Overshoot probability (humans overshoot ~20% of fast movements)
        'overshoot_prob': _beta(2, 8, min_val=0.1, max_val=0.35),
        # Hesitation probability (pause mid-movement)
        'hesitation_prob': _beta(3, 15, min_val=0.01, max_val=0.08),
        # Correction amplitude (pixels, for overshoot correction)
        'correction_amplitude': _gauss(5, 2, min_val=2, max_val=10),
        # Acceleration curve shape (0.5 = ease-in-out, varies per person)
        'accel_curve': _beta(2, 2, min_val=0.3, max_val=0.7),
    }
    return _session_config


def reset_session_config():
    """Reset session config for a new user/session."""
    global _session_config
    _session_config = None


# ====================================================================
# Mouse movement — Fitts's Law compliant with micro-tremor
# ====================================================================

def _fitts_duration(distance, target_width=10):
    """
    Calculate movement duration based on Fitts's Law.
    
    Fitts's Law: MT = a + b * log2(2D/W)
    Where D = distance, W = target width
    
    Real humans take longer for smaller/further targets.
    This matches the logarithmic relationship observed in motor control research.
    """
    config = _get_session_config()
    if distance < 1:
        return 0.1
    
    # Fitts's index of difficulty
    id_val = math.log2(2 * distance / target_width)
    
    # Base Fitts's coefficients (empirically derived from motor control research)
    a = 0.1  # Base reaction time
    b = 0.15  # Scaling factor
    
    # Apply session speed factor
    duration = (a + b * id_val) * config['speed_factor']
    
    # Clamp to reasonable range
    return max(0.2, min(duration, 2.5))


def _bezier_point(t, p0, p1, p2, p3):
    """Cubic Bezier curve point."""
    u = 1 - t
    return u*u*u*p0 + 3*u*u*t*p1 + 3*u*t*t*p2 + t*t*t*p3


def _add_tremor(x, y, t, config):
    """
    Add physiological micro-tremor to mouse position.
    
    Real human hands exhibit tremor at 8-12 Hz with amplitude
    of 0.5-3 pixels. This is invisible to the eye but detectable
    in high-resolution mouse tracking data.
    
    The tremor has two components:
    1. Postural tremor (8-12 Hz) — always present during hovering
    2. Kinetic tremor (higher frequency, larger amplitude during movement)
    """
    freq = config['tremor_freq']
    amp = config['tremor_amplitude']
    
    # X tremor (phase-shifted from Y for realistic 2D pattern)
    tx = amp * math.sin(2 * math.pi * freq * t + 0.3)
    # Y tremor (different phase)
    ty = amp * math.sin(2 * math.pi * freq * t * 1.1 + 1.7)
    
    # Add small random component (non-periodic tremor)
    tx += random.gauss(0, amp * 0.3)
    ty += random.gauss(0, amp * 0.3)
    
    return x + tx, y + ty


def _ease_in_out(t, curve_shape=0.5):
    """
    Custom easing function with configurable curve shape.
    
    curve_shape near 0 = more ease-in (slow start, fast end)
    curve_shape near 1 = more ease-out (fast start, slow end)
    curve_shape 0.5 = symmetric ease-in-out
    """
    if t < curve_shape:
        # Ease-in phase
        return (t / curve_shape) ** 2 * curve_shape
    else:
        # Ease-out phase
        progress = (t - curve_shape) / (1 - curve_shape)
        return curve_shape + (1 - curve_shape) * (1 - (1 - progress) ** 2)


def human_move_to(x, y, duration=None, noise_scale=5):
    """
    Move mouse to (x,y) with human-like movement.
    
    v9.0 improvements:
      - Duration calculated via Fitts's Law (distance-dependent)
      - Bezier control points randomized per movement (not static formula)
      - Micro-tremor added at physiological frequency (8-12 Hz)
      - Hesitation pauses mid-movement (decision points)
      - Overshoot + correction pattern for fast movements
      - Gaussian-distributed timing instead of uniform
    """
    config = _get_session_config()
    
    # Get current position
    cx, cy = pyautogui.position()
    
    # Calculate distance for Fitts's Law
    distance = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    
    # Calculate duration via Fitts's Law if not specified
    if duration is None:
        duration = _fitts_duration(distance, noise_scale * 2)
    
    # Add noise to target (human never hits exact pixel)
    jx = x + int(_gauss(0, noise_scale * 0.4, min_val=-noise_scale, max_val=noise_scale))
    jy = y + int(_gauss(0, noise_scale * 0.4, min_val=-noise_scale, max_val=noise_scale))
    
    # Clamp to screen
    jx = max(10, min(jx, SCREEN_W - 10))
    jy = max(10, min(jy, SCREEN_H - 10))
    
    # Generate Bezier control points with PER-MOVEMENT randomization
    # (not the static formula from v3 that produces identical curve shapes)
    spread = distance * 0.3
    cp1x = cx + (jx - cx) * _beta(2, 3, min_val=0.15, max_val=0.45) + _gauss(0, spread * 0.3)
    cp1y = cy + (jy - cy) * _beta(2, 3, min_val=0.25, max_val=0.55) + _gauss(0, spread * 0.25)
    cp2x = cx + (jx - cx) * _beta(2, 3, min_val=0.55, max_val=0.85) + _gauss(0, spread * 0.3)
    cp2y = cy + (jy - cy) * _beta(2, 3, min_val=0.55, max_val=0.85) + _gauss(0, spread * 0.25)
    
    # Movement steps — more steps for smoother curves
    steps = max(25, int(duration * 70))  # ~70 steps per second
    
    start_time = time.time()
    hesitated = False
    
    for i in range(1, steps + 1):
        # Raw progress [0, 1]
        raw_t = i / steps
        
        # Apply custom easing curve
        t = _ease_in_out(raw_t, config['accel_curve'])
        
        # Bezier interpolation
        px = _bezier_point(t, cx, cp1x, cp2x, jx)
        py = _bezier_point(t, cy, cp1y, cp2y, jy)
        
        # Add micro-tremor (physiological tremor)
        elapsed = time.time() - start_time
        px, py = _add_tremor(px, py, elapsed, config)
        
        _safe_moveTo(int(px), int(py), duration=0)
        
        # Hesitation pause (decision point mid-movement)
        if not hesitated and raw_t > 0.4 and raw_t < 0.7:
            if random.random() < config['hesitation_prob']:
                pause = _lognormal(0.08, 0.5, min_val=0.03, max_val=0.2)
                time.sleep(pause)
                hesitated = True
        
        # Variable step timing — Gaussian distribution, NOT uniform
        step_delay = _gauss(0.012, 0.004, min_val=0.005, max_val=0.030)
        time.sleep(step_delay)
    
    # Overshoot + correction (humans often overshoot targets)
    if distance > 100 and random.random() < config['overshoot_prob']:
        # Overshoot in the direction of movement
        dx = jx - cx
        dy = jy - cy
        dist = math.sqrt(dx*dx + dy*dy) or 1
        overshoot_dist = _gauss(config['correction_amplitude'], 2, min_val=2, max_val=15)
        ox = jx + (dx / dist) * overshoot_dist + _gauss(0, 2)
        oy = jy + (dy / dist) * overshoot_dist + _gauss(0, 2)
        
        # Quick move to overshoot position
        _safe_moveTo(int(ox), int(oy), duration=0)
        time.sleep(_gauss(0.05, 0.02, min_val=0.02, max_val=0.12))
        
        # Correction back to target (slower, more precise)
        correction_steps = random.randint(3, 6)
        for s in range(correction_steps):
            ct = (s + 1) / correction_steps
            correction_x = ox + (jx - ox) * ct + _gauss(0, 1)
            correction_y = oy + (jy - oy) * ct + _gauss(0, 1)
            _safe_moveTo(int(correction_x), int(correction_y), duration=0)
            time.sleep(_gauss(0.02, 0.01, min_val=0.01, max_val=0.05))
    
    # Final micro-adjustment (human always has slight drift)
    time.sleep(_gauss(0.08, 0.04, min_val=0.03, max_val=0.18))
    final_jitter_x = jx + int(_gauss(0, 1.5))
    final_jitter_y = jy + int(_gauss(0, 1.5))
    _safe_moveTo(final_jitter_x, final_jitter_y, duration=0)


def human_move_relative(dx, dy, duration=None):
    if duration is None:
        distance = math.sqrt(dx*dx + dy*dy)
        duration = _fitts_duration(distance)
    cx, cy = pyautogui.position()
    human_move_to(cx + dx, cy + dy, duration=duration, noise_scale=3)


def human_click(x=None, y=None, button='left', duration=None):
    """
    Move to (x,y) with bezier path then click with natural timing.
    
    v9.0 improvements:
      - Click press duration follows beta distribution (not uniform)
      - Post-click dwell follows gamma distribution (not uniform)
      - Hover pause before click follows lognormal distribution
    """
    if x is not None and y is not None:
        human_move_to(x, y, duration=duration)
        
        # Hover pause before clicking (decision time) — lognormal distribution
        hover_pause = _lognormal(0.15, 0.5, min_val=0.06, max_val=0.35)
        time.sleep(hover_pause)
        
        # Small hover jitter (repositioning before click)
        if random.random() < 0.25:
            jitter_x = int(_gauss(0, 2))
            jitter_y = int(_gauss(0, 2))
            _safe_moveRel(jitter_x, jitter_y, duration=0)
            time.sleep(_gauss(0.04, 0.02, min_val=0.02, max_val=0.10))
    
    # Press with variable duration — beta distribution models the
    # asymmetry of human press (quick press = short, deliberate press = longer)
    press_duration = _beta(2, 5, min_val=0.04, max_val=0.18)
    pyautogui.mouseDown(button=button)
    time.sleep(press_duration)
    pyautogui.mouseUp(button=button)
    
    # Post-click dwell — gamma distribution (exponential-like with a mode)
    dwell = _gamma(2, 0.08, min_val=0.1, max_val=0.5)
    time.sleep(dwell)
    
    # Post-click micro-movement (human relaxes hand after clicking)
    if random.random() < 0.15:
        _safe_moveRel(
            int(_gauss(0, 2)),
            int(_gauss(0, 1.5)),
            duration=0
        )


def human_double_click(x=None, y=None, duration=None):
    if x is not None and y is not None:
        human_move_to(x, y, duration=duration)
        time.sleep(_gauss(0.08, 0.04, min_val=0.04, max_val=0.20))
    # Double click interval — lognormal (humans vary this a lot)
    interval = _lognormal(0.1, 0.4, min_val=0.05, max_val=0.22)
    pyautogui.click(clicks=2, interval=interval)


def human_scroll(down=True, steps=None, pause_per_step=None):
    """
    Scroll the mouse wheel like a human.
    
    v9.0 improvements:
      - Scroll speed follows an acceleration curve (starts slow, speeds up)
      - Pause between steps follows gamma distribution
      - Occasional direction reversal (overshoot)
    """
    if steps is None:
        steps = max(1, int(_gamma(3, 1.5, min_val=2, max_val=8)))
    
    direction = -1 if down else 1
    
    for i in range(steps):
        # Acceleration curve: start slow, speed up, then decelerate
        progress = i / max(steps - 1, 1)
        if progress < 0.3:
            base_amount = random.randint(1, 3)  # Start scrolling slowly
        elif progress < 0.7:
            base_amount = random.randint(3, 5)  # Speed up
        else:
            base_amount = random.randint(2, 4)  # Decelerate
        
        # Occasional reverse scroll (human overshoot)
        if random.random() < 0.10 and i > 1:
            amt = -direction * random.randint(1, 2)
        else:
            amt = direction * base_amount
        
        pyautogui.scroll(amt)
        
        # Variable pause — gamma distribution (NOT uniform)
        pause = _gamma(2, 0.08, min_val=0.06, max_val=0.4)
        
        # Micro-movement during pause
        if random.random() < 0.12:
            _safe_moveRel(
                int(_gauss(0, 3)),
                int(_gauss(0, 2)),
                duration=0
            )
        time.sleep(pause)


def human_scroll_to_read(max_steps=None):
    """
    Simulate reading: scroll down with long pauses, occasional re-reading.
    
    v9.0 improvements:
      - Reading pause follows lognormal distribution (heavy right tail)
      - "Getting absorbed" probability with very long pause
      - Re-read probability with scroll-up
    """
    if max_steps is None:
        max_steps = max(1, int(_gamma(4, 2, min_val=2, max_steps=12)))
    
    for i in range(max_steps):
        # 85% scroll down, 15% scroll up (re-read)
        scroll_down = random.random() < 0.85
        steps = max(1, int(_gamma(2, 1, min_val=1, max_val=4)))
        human_scroll(down=scroll_down, steps=steps)
        
        # Reading pause — lognormal distribution (long right tail)
        # Most pauses are short, but occasionally you get "absorbed" in content
        pause = _lognormal(1.5, 0.6, min_val=0.5, max_val=6.0)
        
        # "Getting absorbed" — rare very long pause
        if random.random() < 0.12:
            pause += _gamma(3, 1.0, min_val=1.0, max_val=5.0)
        
        time.sleep(pause)
        
        # Occasional mouse drift during reading
        if random.random() < 0.25:
            human_move_relative(
                int(_gauss(0, 60, min_val=-100, max_val=100)),
                int(_gauss(0, 30, min_val=-50, max_val=50)),
                duration=_gauss(0.5, 0.2, min_val=0.3, max_val=1.0),
            )


def random_idle(duration_s=None):
    """Simulate user distraction / pause with micro-movements."""
    if duration_s is None:
        duration_s = _lognormal(3.0, 0.6, min_val=1.0, max_val=8.0)
    
    start_time = time.time()
    while time.time() - start_time < duration_s:
        remaining = duration_s - (time.time() - start_time)
        if remaining <= 0:
            break
        
        # Sometimes mouse drifts during idle
        if random.random() < 0.04:
            human_move_relative(
                int(_gauss(0, 50, min_val=-100, max_val=100)),
                int(_gauss(0, 30, min_val=-60, max_val=60)),
                duration=_gauss(0.4, 0.2, min_val=0.2, max_val=0.8),
            )
        
        # Micro-movement (breathing/fidgeting)
        if random.random() < 0.02:
            _safe_moveRel(
                int(_gauss(0, 1)),
                int(_gauss(0, 1)),
                duration=0
            )
        
        # Idle step delay — lognormal (NOT uniform)
        time.sleep(_lognormal(1.0, 0.5, min_val=0.3, max_val=2.0))


def get_cursor_pos():
    return pyautogui.position()


def screenshot():
    """Take a screenshot of the Xvfb screen."""
    return pyautogui.screenshot()


# ====================================================================
# KEYBOARD INTERACTION — burst typing pattern
# ====================================================================

# Common keys that real users press while browsing
BROWSING_KEYS = [
    'space', 'tab', 'down', 'up', 'pagedown', 'pageup',
    'left', 'right', 'home', 'end',
]

# Keys for navigation
NAVIGATION_KEYS = ['alt+left', 'alt+right', 'f5', 'ctrl+r']

# Keys for scrolling
SCROLL_KEYS = ['space', 'pagedown', 'pageup', 'down', 'up', 'end', 'home']


def random_keystrokes(count=None):
    """
    Generate random keyboard events that a real user would produce.
    
    v9.0 improvements:
      - Key press duration follows lognormal distribution
      - Inter-key interval follows gamma distribution
      - Occasional modifier key holds with realistic timing
    """
    if count is None:
        count = max(1, int(_gamma(2, 0.8, min_val=1, max_val=4)))

    for _ in range(count):
        key = random.choice(BROWSING_KEYS)
        
        # Sometimes hold modifier keys
        if random.random() < 0.08:
            mod = random.choice(['ctrl', 'shift', 'alt'])
            pyautogui.keyDown(mod)
            time.sleep(_gauss(0.03, 0.01, min_val=0.01, max_val=0.06))
            pyautogui.keyDown(key)
            # Key hold duration — lognormal
            time.sleep(_lognormal(0.07, 0.4, min_val=0.03, max_val=0.15))
            pyautogui.keyUp(key)
            time.sleep(_gauss(0.02, 0.01, min_val=0.01, max_val=0.05))
            pyautogui.keyUp(mod)
        else:
            pyautogui.keyDown(key)
            time.sleep(_lognormal(0.07, 0.4, min_val=0.03, max_val=0.15))
            pyautogui.keyUp(key)
        
        # Post-key pause — gamma distribution (NOT uniform)
        time.sleep(_gamma(2, 0.1, min_val=0.1, max_val=0.8))


def human_type_text(text, min_delay=0.04, max_delay=0.15):
    """
    Type text character-by-character with realistic per-key timing.
    
    v9.0 improvements:
      - Typing rhythm follows burst pattern (fast sequences + pauses)
      - Common bigrams typed faster (muscle memory effect)
      - Error + correction with realistic timing
      - Inter-key delay follows gamma distribution (NOT uniform)
    """
    # Typing speed varies per person — set once per session
    base_speed = _gauss(0.08, 0.02, min_val=0.04, max_delay=0.15)
    
    # Burst typing: accumulate characters into bursts separated by pauses
    burst_length = 0
    max_burst = max(1, int(_gamma(5, 2, min_val=3, max_val=15)))
    
    for i, char in enumerate(text):
        # Base delay — gamma distribution (NOT uniform)
        delay = _gamma(2, base_speed / 2, min_val=0.02, max_val=max_delay)
        
        # Common bigrams are typed faster (muscle memory)
        if i > 0:
            prev = text[i-1]
            common_pairs = ['th', 'he', 'in', 'er', 'an', 're', 'on', 'at',
                          'en', 'nd', 'ti', 'es', 'or', 'te', 'of', 'ed',
                          'is', 'it', 'al', 'ar', 'st', 'to', 'nt', 'ng']
            if (prev + char).lower() in common_pairs:
                delay *= 0.65  # Faster for muscle-memory bigrams
        
        # Burst pattern: after a certain number of chars, add a longer pause
        burst_length += 1
        if burst_length >= max_burst:
            delay += _gamma(2, 0.15, min_val=0.2, max_val=0.8)  # Inter-burst pause
            burst_length = 0
            max_burst = max(1, int(_gamma(5, 2, min_val=3, max_val=15)))
        
        # Occasional thinking pause
        if random.random() < 0.015:
            delay += _lognormal(0.5, 0.6, min_val=0.2, max_val=1.5)
        
        # Occasional typo + backspace (human error)
        if random.random() < 0.012 and i > 2:
            wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
            pyautogui.write(wrong_char, interval=0)
            time.sleep(_gauss(0.08, 0.03, min_val=0.04, max_val=0.15))  # Reaction to error
            pyautogui.press('backspace')
            time.sleep(_gauss(0.06, 0.02, min_val=0.03, max_val=0.12))  # Correction delay
        
        pyautogui.write(char, interval=0)
        time.sleep(delay)


def search_bar_interaction(search_text=None):
    """Simulate a user interacting with search bar."""
    # Tab to a focusable element
    pyautogui.press('tab')
    time.sleep(_gauss(0.4, 0.15, min_val=0.2, max_val=0.8))
    
    # Sometimes use Ctrl+F
    if random.random() < 0.25:
        pyautogui.hotkey('ctrl', 'f')
        time.sleep(_gauss(0.4, 0.15, min_val=0.2, max_val=0.7))
    
    if search_text:
        human_type_text(search_text[:20])
        time.sleep(_gauss(0.6, 0.2, min_val=0.3, max_val=1.2))
        pyautogui.press('escape')
        time.sleep(_gauss(0.3, 0.1, min_val=0.15, max_val=0.6))


def human_scroll_with_keyboard():
    """Simulate scrolling using keyboard (Space, PageDown, Arrow keys)."""
    method = random.choice(['space', 'pagedown', 'down', 'pagedown', 'space'])
    
    if method == 'space':
        pyautogui.press('space')
        time.sleep(_gauss(0.5, 0.2, min_val=0.2, max_val=1.0))
        # Sometimes Space again quickly (human double-tap)
        if random.random() < 0.2:
            time.sleep(_lognormal(0.1, 0.3, min_val=0.04, max_val=0.2))
            pyautogui.press('space')
            time.sleep(_gauss(0.3, 0.15, min_val=0.15, max_val=0.6))
    elif method == 'pagedown':
        pyautogui.press('pagedown')
        time.sleep(_gauss(0.6, 0.2, min_val=0.3, max_val=1.2))
    elif method == 'down':
        num_presses = max(1, int(_gamma(3, 1.5, min_val=2, max_val=7)))
        for _ in range(num_presses):
            pyautogui.press('down')
            time.sleep(_gauss(0.12, 0.06, min_val=0.05, max_val=0.3))


def new_tab_human_activity(page, duration_s=None, max_scrolls=8, max_clicks=3):
    """
    v11.0 UPGRADE — MOD 1: Human-like activity dalam tab baru (iklan).

    Melakukan scroll + klik random yang terlihat seperti aktivitas
    manusia saat membuka tab iklan baru. Dipanggil saat tab baru
    aktif (ad landing page).

    Alur:
      1. Brief idle (user registers new tab)
      2. Multiple scroll segments with variable speed
      3. Random mouse movements with hesitation
      4. Click on random visible elements
      5. More scroll + idle between clicks
      6. Final idle before returning

    Args:
        page: Playwright Page object (the new/ad tab)
        duration_s: total duration (default: 30-60s for CPM)
        max_scrolls: max scroll segments (default 8)
        max_clicks: max random clicks (default 3)

    Returns:
        dict with keys: clicks_done, scrolls_done, duration_actual
    """
    import time as _time

    if duration_s is None:
        duration_s = _lognormal(3.5, 0.4, min_val=25.0, max_val=65.0)

    start_time = _time.time()
    clicks_done = 0
    scrolls_done = 0

    # 1. Brief idle — user registers the new tab opened
    _time.sleep(_lognormal(1.5, 0.5, min_val=1.0, max_val=3.5))

    # 2. Scroll segments with variable speed
    remaining = duration_s - (_time.time() - start_time)
    while remaining > 5 and scrolls_done < max_scrolls:
        # Scroll down 85% of the time, up 15%
        scroll_down = random.random() < 0.85
        steps = max(1, int(_gamma(2, 1.5, min_val=1, max_val=5)))
        human_scroll(down=scroll_down, steps=steps)
        scrolls_done += 1

        # Reading pause
        read_pause = _lognormal(2.0, 0.6, min_val=1.5, max_val=8.0)
        _time.sleep(read_pause)

        # Mouse drift during reading
        if random.random() < 0.3:
            human_move_relative(
                int(_gauss(0, 80, min_val=-150, max_val=150)),
                int(_gauss(0, 40, min_val=-80, max_val=80)),
                duration=_gauss(0.6, 0.2, min_val=0.3, max_val=1.0),
            )

        remaining = duration_s - (_time.time() - start_time)

    # 3. Random mouse movements with hesitation
    for _ in range(random.randint(2, 5)):
        if _time.time() - start_time >= duration_s:
            break
        try:
            dx = int(_gauss(0, 200, min_val=-400, max_val=400))
            dy = int(_gauss(0, 150, min_val=-300, max_val=300))
            human_move_relative(dx, dy, duration=random.uniform(0.5, 1.2))
            _time.sleep(_lognormal(0.8, 0.4, min_val=0.5, max_val=3.0))
        except Exception:
            pass

    # 4. Click on random visible elements via Playwright (not pyautogui)
    # We use page.evaluate to find clickable elements and page.mouse to click
    if max_clicks > 0 and page is not None:
        try:
            clickable_elements = page.evaluate('''() => {
                const selectors = [
                    'a[href]:not([href^="javascript:"]):not([href="#"])',
                    'button:not([disabled])',
                    'div[onclick]',
                    'div[role="button"]',
                    'input[type="submit"]:not([disabled])',
                    'input[type="button"]:not([disabled])',
                ];
                const results = [];
                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 10 && rect.height > 10 &&
                            rect.top < window.innerHeight && rect.bottom > 0 &&
                            rect.left < window.innerWidth && rect.right > 0) {
                            results.push({
                                x: rect.left + rect.width * (0.3 + Math.random() * 0.4),
                                y: rect.top + rect.height * (0.3 + Math.random() * 0.4),
                                tag: el.tagName,
                                href: el.getAttribute('href') || '',
                            });
                        }
                    }
                }
                // Shuffle
                for (let i = results.length - 1; i > 0; i--) {
                    const j = Math.floor(Math.random() * (i + 1));
                    [results[i], results[j]] = [results[j], results[i]];
                }
                return results.slice(0, ''' + str(max_clicks) + ''');
            }''')

            for elem in (clickable_elements or []):
                if clicks_done >= max_clicks:
                    break
                if _time.time() - start_time >= duration_s:
                    break
                try:
                    ex, ey = elem['x'], elem['y']
                    # Move mouse to element position on screen
                    sx, sy = _viewport_to_screen_coords(ex, ey, page)
                    if sx and sy:
                        human_move_to(sx, sy, duration=random.uniform(0.4, 0.9))
                        _time.sleep(random.uniform(0.6, 1.5))  # hover dwell
                        human_click(sx, sy)
                        clicks_done += 1
                    else:
                        # Fallback: use Playwright mouse.click directly
                        page.mouse.click(ex, ey, delay=random.randint(50, 150))
                        clicks_done += 1

                    _time.sleep(random.uniform(1.5, 4.0))  # post-click dwell

                    # Small scroll after click
                    if random.random() < 0.4:
                        human_scroll(down=True, steps=random.randint(1, 2))

                except Exception:
                    continue
        except Exception:
            pass

    # 5. More scroll + idle to fill remaining time
    remaining = duration_s - (_time.time() - start_time)
    while remaining > 3:
        chunk = min(remaining, _lognormal(2.0, 0.5, min_val=1.5, max_val=6.0))
        _time.sleep(chunk)
        remaining = duration_s - (_time.time() - start_time)
        if remaining > 3 and random.random() < 0.4:
            human_scroll(down=random.random() < 0.8, steps=random.randint(1, 2))
            scrolls_done += 1

    # 6. Final idle
    _time.sleep(_lognormal(1.5, 0.5, min_val=0.8, max_val=4.0))

    duration_actual = _time.time() - start_time
    return {
        'clicks_done': clicks_done,
        'scrolls_done': scrolls_done,
        'duration_actual': round(duration_actual, 1),
    }


def _viewport_to_screen_coords(vx, vy, page):
    """
    Convert viewport coordinates to screen coordinates for pyautogui.
    Returns (screen_x, screen_y) or (None, None) on failure.
    """
    try:
        bounds = get_browser_bounds()
        if not bounds:
            return None, None
        bx, by, bw, bh = bounds
        # Get page viewport size
        vp = page.viewport_size
        if not vp:
            return None, None
        vp_w, vp_h = vp['width'], vp['height']
        # Scale viewport coords to browser window coords
        sx = int(bx + (vx / vp_w) * bw)
        sy = int(by + (vy / vp_h) * bh)
        # Clamp to browser bounds
        sx = max(bx + 5, min(sx, bx + bw - 5))
        sy = max(by + 5, min(sy, by + bh - 5))
        return sx, sy
    except Exception:
        return None, None


def tab_navigation(count=None):
    """Simulate Tab key navigation between elements."""
    if count is None:
        count = max(1, int(_gamma(2, 0.8, min_val=1, max_val=4)))
    for _ in range(count):
        pyautogui.press('tab')
        time.sleep(_gauss(0.25, 0.1, min_val=0.1, max_val=0.5))


def escape_key():
    """Press Escape — used to close modals, popups, overlays."""
    pyautogui.press('escape')
    time.sleep(_gauss(0.2, 0.08, min_val=0.1, max_val=0.4))


def ctrl_click(x, y):
    """Ctrl+Click to open link in new tab (common user behavior)."""
    human_move_to(x, y, duration=_gauss(0.4, 0.15, min_val=0.2, max_val=0.7))
    time.sleep(_gauss(0.1, 0.04, min_val=0.05, max_val=0.2))
    pyautogui.keyDown('ctrl')
    time.sleep(_gauss(0.03, 0.01, min_val=0.01, max_val=0.06))
    pyautogui.mouseDown(button='left')
    time.sleep(_beta(2, 5, min_val=0.04, max_val=0.12))
    pyautogui.mouseUp(button='left')
    time.sleep(_gauss(0.03, 0.01, min_val=0.01, max_val=0.06))
    pyautogui.keyUp('ctrl')
    time.sleep(_gauss(0.4, 0.15, min_val=0.2, max_val=0.8))


def mixed_browse_session(duration_s=30):
    """
    Simulate a mixed browsing session with both mouse and keyboard input.
    This is the most human-like pattern.
    
    v9.0 improvements:
      - All timing uses realistic distributions (NOT uniform)
      - Action weights more closely match real browsing behavior
      - Session config is reset per session for uniqueness
    """
    # Reset session config for each browsing session
    reset_session_config()
    
    end_time = time.time() + duration_s
    
    # Track if we've done certain actions
    has_keyboard = False
    has_mouse_scroll = False

    while time.time() < end_time:
        # Weighted random actions — weights match real user behavior analytics
        action = random.choices(
            ['mouse_scroll', 'keyboard_scroll', 'mouse_move',
             'keystroke', 'idle', 'mixed_scroll', 'micro_movement'],
            weights=[22, 18, 16, 10, 15, 12, 7],
            k=1
        )[0]

        if action == 'mouse_scroll':
            steps = max(1, int(_gamma(3, 1, min_val=2, max_val=6)))
            human_scroll(down=(random.random() < 0.85), steps=steps)
            time.sleep(_gauss(0.8, 0.3, min_val=0.3, max_val=1.5))
            has_mouse_scroll = True

        elif action == 'keyboard_scroll':
            human_scroll_with_keyboard()
            time.sleep(_gauss(0.6, 0.3, min_val=0.2, max_val=1.5))
            has_keyboard = True

        elif action == 'mouse_move':
            human_move_relative(
                int(_gauss(0, 120, min_val=-250, max_val=250)),
                int(_gauss(0, 70, min_val=-150, max_val=150)),
                duration=_gauss(0.7, 0.25, min_val=0.3, max_val=1.3),
            )
            time.sleep(_gauss(0.4, 0.15, min_val=0.15, max_val=0.8))

        elif action == 'keystroke':
            count = max(1, int(_gamma(2, 0.7, min_val=1, max_val=3)))
            random_keystrokes(count=count)
            time.sleep(_gauss(0.3, 0.12, min_val=0.15, max_val=0.6))
            has_keyboard = True

        elif action == 'idle':
            idle_duration = _lognormal(2.0, 0.5, min_val=0.8, max_val=4.0)
            random_idle(duration_s=idle_duration)

        elif action == 'mixed_scroll':
            # Mix of mouse and keyboard in sequence
            human_scroll(down=True, steps=max(1, random.randint(1, 3)))
            time.sleep(_gauss(0.4, 0.15, min_val=0.2, max_val=0.8))
            pyautogui.press('space')
            time.sleep(_gauss(0.4, 0.2, min_val=0.2, max_val=1.0))
            if random.random() < 0.3:
                pyautogui.press('down')
                time.sleep(_gauss(0.12, 0.06, min_val=0.06, max_val=0.3))

        elif action == 'micro_movement':
            # Tiny mouse movements (human "breathing" / fidgeting)
            _safe_moveRel(
                int(_gauss(0, 1.5)),
                int(_gauss(0, 1)),
                duration=0
            )
            time.sleep(_gauss(0.15, 0.06, min_val=0.05, max_val=0.3))

    # Ensure at least some keyboard events occurred
    if not has_keyboard:
        count = max(1, int(_gamma(2, 0.8, min_val=2, max_val=5)))
        random_keystrokes(count=count)
    if not has_mouse_scroll:
        steps = max(1, int(_gamma(2, 1, min_val=2, max_val=5)))
        human_scroll(down=True, steps=steps)


def click_with_jitter(x, y, button='left'):
    """Click with natural jitter."""
    jx = x + int(_gauss(0, 5, min_val=-8, max_val=8))
    jy = y + int(_gauss(0, 4, min_val=-5, max_val=5))
    jx = max(10, min(jx, SCREEN_W - 10))
    jy = max(10, min(jy, SCREEN_H - 10))
    human_click(jx, jy, button=button)
