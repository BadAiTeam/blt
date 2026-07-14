"""
Web preview server — streams the Xvfb screen as MJPEG so the user can
watch the bot simulation live in their browser.

v9.3 FIX: Critical bug — `Image.new()` dipakai di error path capture_screen()
tapi `Image` tidak pernah di-import (hanya `ImageGrab`). Akibatnya, setiap
kali screen capture gagal (mis. Xvfb belum start), server mengeluarkan
NameError dan stream mati total. Sekarang kedua nama di-import.

Endpoints:
  GET /            → HTML page with embedded MJPEG + control panel
  GET /stream      → MJPEG stream of the Xvfb screen
  GET /status.json → current bot status JSON
  GET /log         → recent log lines (SSE-like)
"""

import io
import threading
import time
import base64
from flask import Flask, Response, jsonify, render_template_string, request
# v9.3: FIX NameError — sebelumnya hanya ImageGrab yang di-import, tapi
# capture_screen() error path memanggil Image.new(). Import both.
from PIL import Image, ImageGrab
import os

# Force DISPLAY for X11 screen capture
os.environ.setdefault('DISPLAY', ':99')

app = Flask(__name__)

# Shared state updated by the bot
state = {
    'current_user': '-',
    'current_device': '-',
    'current_phase': 'idle',
    'progress': '0 / 0',
    'stats': {
        'total': 0, 'planned_total': 0,
        'success': 0, 'partial': 0, 'failed': 0,
        'reached_target': 0, 'skipped': 0,
        'articles': 0, 'ads': 0, 'tracking': 0,
    },
    # v10.3: per-session real-time stats for the currently-running user
    'current_session_stats': {
        'uid': '-',
        'articles': 0, 'ads': 0, 'tracking': 0,
        'reached_target': False, 'session_active': False,
        'error': None,
    },
    'log_lines': [],
    'started_at': time.time(),
}
state_lock = threading.Lock()


def update_state(**kwargs):
    with state_lock:
        for k, v in kwargs.items():
            state[k] = v


def push_log(line):
    with state_lock:
        state['log_lines'].append(line)
        # Keep last 200 lines
        if len(state['log_lines']) > 200:
            state['log_lines'] = state['log_lines'][-200:]


def capture_screen():
    """Capture Xvfb screen as JPEG bytes."""
    try:
        img = ImageGrab.grab()  # Uses DISPLAY env
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=70)
        return buf.getvalue()
    except Exception as e:
        # Return a small placeholder image
        img = Image.new('RGB', (800, 600), color=(40, 40, 40))
        buf = io.BytesIO()
        img.save(buf, format='JPEG')
        return buf.getvalue()


def mjpeg_generator():
    """Generate MJPEG frames forever."""
    boundary = b'--frame\r\n'
    last_capture = 0
    while True:
        now = time.time()
        if now - last_capture < 0.25:  # 4 fps
            time.sleep(0.05)
            continue
        last_capture = now
        frame = capture_screen()
        yield (
            boundary
            + b'Content-Type: image/jpeg\r\n'
            + b'Content-Length: ' + str(len(frame)).encode() + b'\r\n\r\n'
            + frame + b'\r\n'
        )


@app.route('/')
def index():
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Bot Simulation Preview</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0d1117; color: #c9d1d9; font-family: 'SF Mono', 'Consolas', monospace;
    padding: 20px; min-height: 100vh;
  }
  h1 { color: #58a6ff; margin-bottom: 16px; font-size: 20px; }
  .container { display: grid; grid-template-columns: 1fr 360px; gap: 20px; max-width: 1600px; margin: 0 auto; }
  @media (max-width: 1100px) { .container { grid-template-columns: 1fr; } }
  .preview {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 12px;
  }
  .preview img {
    width: 100%; height: auto; display: block; border-radius: 4px;
    background: #000; min-height: 400px;
  }
  .preview-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;
    font-size: 12px; color: #8b949e;
  }
  .live-dot {
    display: inline-block; width: 8px; height: 8px; background: #f85149;
    border-radius: 50%; animation: pulse 1.5s infinite; margin-right: 6px;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .sidebar { display: flex; flex-direction: column; gap: 16px; }
  .panel {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px;
  }
  .panel h2 { font-size: 13px; color: #58a6ff; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 13px; }
  .stat-row .label { color: #8b949e; }
  .stat-row .value { color: #c9d1d9; font-weight: bold; }
  .stat-row.success .value { color: #3fb950; }
  .stat-row.failed .value { color: #f85149; }
  .stat-row.partial .value { color: #d29922; }
  .log {
    background: #0d1117; border: 1px solid #30363d; border-radius: 4px;
    padding: 10px; height: 280px; overflow-y: auto; font-size: 11px;
    line-height: 1.5; font-family: 'SF Mono', monospace;
  }
  .log-line { white-space: pre-wrap; word-break: break-all; }
  .log-line.error { color: #f85149; }
  .log-line.warn { color: #d29922; }
  .log-line.ok { color: #3fb950; }
  .log-line.info { color: #58a6ff; }
  .controls { display: flex; gap: 8px; margin-top: 10px; }
  .btn {
    background: #238636; color: white; border: none; padding: 8px 14px;
    border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600;
  }
  .btn:hover { background: #2ea043; }
  .btn.danger { background: #da3633; }
  .btn.danger:hover { background: #f85149; }
  .progress {
    width: 100%; height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; margin-top: 8px;
  }
  .progress-bar {
    height: 100%; background: linear-gradient(90deg, #58a6ff, #3fb950);
    transition: width 0.5s; width: 0%;
  }
</style>
</head>
<body>
  <h1>🌐 Visit Bot Simulation — Live Preview</h1>
  <div class="container">
    <div class="preview">
      <div class="preview-header">
        <span><span class="live-dot"></span>LIVE — Xvfb :99 (1920×1080)</span>
        <span id="fps">— fps</span>
      </div>
      <img src="/stream" alt="Bot preview" id="preview">
    </div>
    <div class="sidebar">
      <div class="panel">
        <h2>📊 Current Session</h2>
        <div class="stat-row"><span class="label">User</span><span class="value" id="cur-user">-</span></div>
        <div class="stat-row"><span class="label">Device</span><span class="value" id="cur-device">-</span></div>
        <div class="stat-row"><span class="label">Phase</span><span class="value" id="cur-phase">-</span></div>
        <div class="stat-row"><span class="label">Progress</span><span class="value" id="cur-progress">-</span></div>
        <div class="progress"><div class="progress-bar" id="progress-bar"></div></div>
        <div style="margin-top:10px;padding-top:8px;border-top:1px solid #30363d;">
          <div style="font-size:11px;color:#8b949e;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">Live session (current user)</div>
          <div class="stat-row"><span class="label">Reached target</span><span class="value" id="cs-reached">—</span></div>
          <div class="stat-row"><span class="label">Articles (this user)</span><span class="value" id="cs-articles">0</span></div>
          <div class="stat-row"><span class="label">Ads (this user)</span><span class="value" id="cs-ads">0</span></div>
          <div class="stat-row"><span class="label">Tracking (this user)</span><span class="value" id="cs-tracking">0</span></div>
        </div>
      </div>
      <div class="panel">
        <h2>📈 Run Totals</h2>
        <div class="stat-row"><span class="label">Total processed</span><span class="value" id="s-total">0</span></div>
        <div class="stat-row"><span class="label">Planned total</span><span class="value" id="s-planned">0</span></div>
        <div class="stat-row success"><span class="label">Successful</span><span class="value" id="s-success">0</span></div>
        <div class="stat-row partial"><span class="label">Partial (homepage only)</span><span class="value" id="s-partial">0</span></div>
        <div class="stat-row failed"><span class="label">Failed</span><span class="value" id="s-failed">0</span></div>
        <div class="stat-row"><span class="label">Reached target</span><span class="value" id="s-reached">0</span></div>
        <div class="stat-row"><span class="label">Anti-burst skipped</span><span class="value" id="s-skipped">0</span></div>
        <div class="stat-row"><span class="label">Articles viewed</span><span class="value" id="s-articles">0</span></div>
        <div class="stat-row"><span class="label">Ads clicked</span><span class="value" id="s-ads">0</span></div>
        <div class="stat-row success"><span class="label">Tracking fired</span><span class="value" id="s-tracking">0</span></div>
      </div>
      <div class="panel">
        <h2>📜 Live Log</h2>
        <div class="log" id="log"></div>
      </div>
    </div>
  </div>
<script>
  let lastUpdate = performance.now();
  let frameCount = 0;
  const img = document.getElementById('preview');
  img.onload = () => {
    frameCount++;
    const now = performance.now();
    if (now - lastUpdate > 1000) {
      document.getElementById('fps').textContent = frameCount + ' fps';
      frameCount = 0; lastUpdate = now;
    }
  };

  async function pollStatus() {
    try {
      const r = await fetch('/status.json');
      const s = await r.json();
      document.getElementById('cur-user').textContent = s.current_user;
      document.getElementById('cur-device').textContent = s.current_device;
      document.getElementById('cur-phase').textContent = s.current_phase;
      document.getElementById('cur-progress').textContent = s.progress;
      const parts = (s.progress || '').split('/').map(x => parseInt(x.trim()) || 0);
      const cur = parts[0] || 0, total = parts[1] || 0;
      const pct = total > 0 ? (cur/total*100) : 0;
      document.getElementById('progress-bar').style.width = pct + '%';

      // v10.3: Run Totals — defensive .toString() with fallback to '0' for missing fields
      const st = s.stats || {};
      const safe = (k, dflt) => (st[k] !== undefined && st[k] !== null) ? st[k] : dflt;
      document.getElementById('s-total').textContent    = safe('total', 0);
      document.getElementById('s-planned').textContent  = safe('planned_total', 0);
      document.getElementById('s-success').textContent  = safe('success', 0);
      document.getElementById('s-partial').textContent  = safe('partial', 0);
      document.getElementById('s-failed').textContent   = safe('failed', 0);
      document.getElementById('s-reached').textContent  = safe('reached_target', 0);
      document.getElementById('s-skipped').textContent  = safe('skipped', 0);
      document.getElementById('s-articles').textContent = safe('articles', 0);
      document.getElementById('s-ads').textContent      = safe('ads', 0);
      document.getElementById('s-tracking').textContent = safe('tracking', 0);

      // v10.3: Live per-session stats for the currently-running user
      const cs = s.current_session_stats || {};
      const csSafe = (k, dflt) => (cs[k] !== undefined && cs[k] !== null) ? cs[k] : dflt;
      const reached = csSafe('reached_target', false);
      const active = csSafe('session_active', false);
      const reachedTag = reached ? '✓ yes' : (active ? '… loading' : '—');
      document.getElementById('cs-reached').textContent  = reachedTag;
      document.getElementById('cs-articles').textContent = csSafe('articles', 0);
      document.getElementById('cs-ads').textContent      = csSafe('ads', 0);
      document.getElementById('cs-tracking').textContent = csSafe('tracking', 0);
    } catch(e) {}
  }
  async function pollLog() {
    try {
      const r = await fetch('/log?since=' + (window._logOffset || 0));
      const data = await r.json();
      window._logOffset = data.offset;
      const logEl = document.getElementById('log');
      data.lines.forEach(line => {
        const div = document.createElement('div');
        div.className = 'log-line ' + (line.level || '');
        div.textContent = line.text;
        logEl.appendChild(div);
      });
      // Keep last 100 lines visible
      while (logEl.children.length > 100) logEl.removeChild(logEl.firstChild);
      logEl.scrollTop = logEl.scrollHeight;
    } catch(e) {}
  }
  setInterval(pollStatus, 1000);
  setInterval(pollLog, 500);
  pollStatus(); pollLog();
</script>
</body>
</html>'''
    return html


@app.route('/stream')
def stream():
    return Response(
        mjpeg_generator(),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/status.json')
def status_json():
    with state_lock:
        return jsonify(state)


@app.route('/log')
def log_json():
    since = int(request.args.get('since', 0))
    with state_lock:
        lines = state['log_lines'][since:]
        # parse level from text
        parsed = []
        for line in lines:
            level = ''
            if '❌' in line or 'ERROR' in line.upper(): level = 'error'
            elif '⚠️' in line: level = 'warn'
            elif '✅' in line: level = 'ok'
            elif 'ℹ️' in line or '🔹' in line: level = 'info'
            parsed.append({'text': line, 'level': level})
        return jsonify({
            'lines': parsed,
            'offset': len(state['log_lines']),
        })


def run_server(host='0.0.0.0', port=8080):
    """Run the Flask server in the current thread (blocking)."""
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)


def start_server_in_thread(host='0.0.0.0', port=8080):
    """Run the Flask server in a daemon thread (non-blocking)."""
    t = threading.Thread(target=run_server, args=(host, port), daemon=True)
    t.start()
    return t
