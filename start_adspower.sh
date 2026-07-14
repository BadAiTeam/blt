#!/usr/bin/env bash
# =============================================================================
# start_adspower.sh — Linux container launcher for AdsPower Global v8.x
# =============================================================================
#
# WHAT THIS SCRIPT FIXES (vs. the original manual command):
#
#   1. DBus session bus errors ("Failed to connect to socket /dev/null"):
#      Root cause: `DBUS_SESSION_BUS_ADDRESS="unix:path=/dev/null"` is NOT a
#      valid DBus socket — /dev/null is a char device, not a Unix socket.
#      Chromium-based apps try connect() to it and get ECONNREFUSED on every
#      D-Bus call (Notifications, Settings, Secret Service, etc.).
#
#      Fix: start a REAL private DBus session bus via `dbus-run-session`
#      (from dbus-x11 package). The bus is isolated to this script's lifetime,
#      so it's safe in containers. If dbus-run-session is unavailable, fall
#      back to launching dbus-daemon manually.
#
#   2. DBus system bus noise ("Failed to connect to socket /run/dbus/system_bus_socket"):
#      Chromium also tries to connect to the SYSTEM bus at
#      /run/dbus/system_bus_socket — which doesn't exist in most containers.
#      These errors are NON-FATAL (session bus already works, AdsPower starts
#      fine), but they pollute the console.
#
#      Fix: start a SECOND dbus-daemon in "system" mode with a generated
#      config in a writable temp dir, and export DBUS_SYSTEM_BUS_ADDRESS
#      so Chromium finds our fake system bus instead of /run/dbus/.
#
#   3. Port mismatch (AdsPower reports http://local.adspower.net:5032 but
#      bot_v6.py / antidetect_browser.py default to http://127.0.0.1:50325):
#      Fix: antidetect_browser.py has built-in port auto-detection.
#
#   4. IPv6 binding (local.adspower.net resolves to ::1, not 127.0.0.1):
#      Fix: antidetect_browser.py changed DEFAULT_LOCAL_BASE from
#      'http://127.0.0.1' to 'http://localhost'.
#
#   5. Container /dev/shm too small (causes Chromium crashes):
#      Fix: pass --disable-dev-shm-usage to AdsPower (propagated to Chromium)
#      and --disable-software-rasterizer for headless GPU-less rendering.
#
#   6. [v2.1 NEW] Harmless "Failed to set fd limit to 65536: Operation not
#      permitted" warning from dbus-daemon in containers without
#      CAP_SYS_RESOURCE. Non-fatal but confusing.
#      Fix: (a) pre-raise `ulimit -n` to the hard limit when possible,
#      (b) filter the residual warning line from AdsPower's stderr stream.
#
#   7. [v2.1 NEW] "ERROR - 4012 / The package you are using expires" — the
#      AdsPower server-side license check rejects the API key because the
#      subscription expired. This is NOT a script bug; the script worked
#      correctly up to and including the license check. However, the original
#      script let the error scroll past silently and exited 0, leaving the
#      operator with no actionable guidance.
#
#      Fix: capture AdsPower stdout+stderr to a log file AND display it live.
#      After AdsPower exits, scan the log for known error codes (4012 / 4013 /
#      4014) and print a clear, actionable remediation message. Exit with a
#      distinct code per error class so callers (run_bot_supervised.sh, docker
#      restart policies, etc.) can branch on the failure mode.
#
# USAGE:
#   ./start_adspower.sh [extra adspower_global flags...]
#
# ENVIRONMENT VARIABLES:
#   ADSPOWER_BIN          Path to adspower_global binary
#                         (default: /opt/AdsPower Global/adspower_global)
#   ADSPOWER_API_KEY      AdsPower API key (required)
#   ADSPOWER_PORT         Optional explicit port (skips auto-detect)
#   XVFB_SCREEN           Xvfb screen spec (default: 1920x1080x24)
#   SKIP_DBUS             If set to "1", do not start any dbus (you'll see noise)
#   SKIP_SYSTEM_BUS       If set to "1", start only session bus (default: 0).
#   ADSPOWER_LOG          Path to AdsPower stdout/stderr capture log
#                         (default: /tmp/adspower.log)
#   ADSPOWER_API_PORT     Local API port AdsPower should bind
#                         (default: 50325) — used for readiness poll
#   ADSPOWER_START_TIMEOUT Seconds to wait for API port before considering
#                         startup failed (default: 30)
#
# EXIT CODES:
#   0                     AdsPower exited cleanly
#   1                     Setup error (missing binary, missing API key, etc.)
#   2                     Xvfb failed to start
#   7                     AdsPower license expired (ERROR - 4012)
#   8                     AdsPower API key invalid (ERROR - 4013)
#   9                     AdsPower account frozen (ERROR - 4014)
#   10                    AdsPower exited non-zero with unknown error
# =============================================================================

set -euo pipefail

# ---- Config from env or defaults -------------------------------------------
ADSPOWER_BIN="${ADSPOWER_BIN:-/opt/AdsPower Global/adspower_global}"
ADSPOWER_API_KEY="${ADSPOWER_API_KEY:-}"
XVFB_SCREEN="${XVFB_SCREEN:-1920x1080x24}"
SKIP_DBUS="${SKIP_DBUS:-0}"
SKIP_SYSTEM_BUS="${SKIP_SYSTEM_BUS:-0}"
ADSPOWER_LOG="${ADSPOWER_LOG:-/tmp/adspower.log}"
ADSPOWER_API_PORT="${ADSPOWER_API_PORT:-50325}"
ADSPOWER_START_TIMEOUT="${ADSPOWER_START_TIMEOUT:-30}"

if [[ -z "$ADSPOWER_API_KEY" ]]; then
    echo "ERROR: ADSPOWER_API_KEY environment variable is required." >&2
    echo "       Example: ADSPOWER_API_KEY=ed64f37f... ./start_adspower.sh" >&2
    exit 1
fi

if [[ ! -x "$ADSPOWER_BIN" ]]; then
    echo "ERROR: AdsPower binary not found or not executable: $ADSPOWER_BIN" >&2
    echo "       Set ADSPOWER_BIN to the correct path." >&2
    exit 1
fi

# ---- Helper: check command availability ------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

# ---- Try to pre-raise the open-files limit ---------------------------------
# In containers without CAP_SYS_RESOURCE, dbus-daemon prints a harmless
# "Failed to set fd limit to 65536: Operation not permitted" warning. We
# try to raise the soft limit to the hard limit ourselves; if that succeeds,
# dbus-daemon won't need to try. If it fails (no permission), we silently
# move on — the residual warning is filtered later in the launch pipeline.
ulimit -n 65536 2>/dev/null || ulimit -n "$(ulimit -Hn)" 2>/dev/null || true

# ---- PIDs / temp dirs that need cleanup on exit ----------------------------
# All set as global so the unified cleanup function can see them.
XVFB_PID=""
SESSION_DBUS_PID=""
SESSION_DBUS_DIR=""
SYSTEM_DBUS_PID=""
SYSTEM_DBUS_DIR=""
ADSPOWER_PID=""
TEE_PID=""
WATCHER_PID=""

# ---- Unified cleanup (single EXIT trap) ------------------------------------
cleanup_all() {
    # Stop the readiness watcher first so it doesn't fire after teardown
    if [[ -n "$WATCHER_PID" ]] && kill -0 "$WATCHER_PID" 2>/dev/null; then
        kill "$WATCHER_PID" 2>/dev/null || true
    fi
    # AdsPower
    if [[ -n "$ADSPOWER_PID" ]] && kill -0 "$ADSPOWER_PID" 2>/dev/null; then
        kill -TERM "$ADSPOWER_PID" 2>/dev/null || true
        # Give it a moment, then force-kill if still alive
        for _ in 1 2 3 4 5; do
            kill -0 "$ADSPOWER_PID" 2>/dev/null || break
            sleep 0.2
        done
        kill -KILL "$ADSPOWER_PID" 2>/dev/null || true
        wait "$ADSPOWER_PID" 2>/dev/null || true
    fi
    # tee pipeline
    if [[ -n "$TEE_PID" ]] && kill -0 "$TEE_PID" 2>/dev/null; then
        kill "$TEE_PID" 2>/dev/null || true
    fi
    # Xvfb
    if [[ -n "$XVFB_PID" ]] && kill -0 "$XVFB_PID" 2>/dev/null; then
        echo "[xvfb] Stopping Xvfb (PID=$XVFB_PID)..."
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    fi
    # Session bus (only if we started it manually — dbus-run-session auto-cleans)
    if [[ -n "$SESSION_DBUS_PID" ]] && kill -0 "$SESSION_DBUS_PID" 2>/dev/null; then
        kill "$SESSION_DBUS_PID" 2>/dev/null || true
    fi
    if [[ -n "$SESSION_DBUS_DIR" ]]; then
        rm -rf "$SESSION_DBUS_DIR" 2>/dev/null || true
    fi
    # System bus (fake one we started)
    if [[ -n "$SYSTEM_DBUS_PID" ]] && kill -0 "$SYSTEM_DBUS_PID" 2>/dev/null; then
        kill "$SYSTEM_DBUS_PID" 2>/dev/null || true
    fi
    if [[ -n "$SYSTEM_DBUS_DIR" ]]; then
        rm -rf "$SYSTEM_DBUS_DIR" 2>/dev/null || true
    fi
}
trap cleanup_all EXIT

# ---- Step 1: Ensure Xvfb is available --------------------------------------
if ! have xvfb-run && ! have Xvfb; then
    echo "ERROR: Xvfb is required for headless operation but not installed." >&2
    echo "       Install with: apt-get install -y xvfb" >&2
    echo "                     or:  dnf install -y xorg-x11-server-Xvfb" >&2
    exit 1
fi

# ---- Step 2: Start a private DBus SESSION bus ------------------------------
# Chromium needs a real session bus for SecretService, Notifications, Settings.
# We use `dbus-run-session` (from dbus-x11) which starts an isolated bus for
# only this script's child processes. If unavailable, fall back to manual
# dbus-daemon; if neither, warn and continue (errors are non-fatal).

DBUS_WRAPPER=()
if [[ "$SKIP_DBUS" == "1" ]]; then
    echo "[dbus] SKIP_DBUS=1, not starting a session bus (you may see DBus warnings)."
elif have dbus-run-session; then
    DBUS_WRAPPER=(dbus-run-session --)
    echo "[dbus] Will use dbus-run-session for an isolated session bus."
elif have dbus-daemon; then
    # Manual fallback: launch dbus-daemon with a generated config + address.
    SESSION_DBUS_DIR="$(mktemp -d --tmpdir adspower-dbus.XXXXXX)"
    SESSION_DBUS_PIDFILE="$SESSION_DBUS_DIR/pid"
    SESSION_DBUS_ADDRFILE="$SESSION_DBUS_DIR/addr"
    dbus-daemon \
        --session \
        --fork \
        --print-address=8 \
        --print-pid=9 \
        8>"$SESSION_DBUS_ADDRFILE" \
        9>"$SESSION_DBUS_PIDFILE"
    DBUS_SESSION_BUS_ADDRESS="$(cat "$SESSION_DBUS_ADDRFILE")"
    export DBUS_SESSION_BUS_ADDRESS
    SESSION_DBUS_PID="$(cat "$SESSION_DBUS_PIDFILE")"
    echo "[dbus] Started session dbus-daemon (PID=$SESSION_DBUS_PID)"
else
    echo "[dbus] WARNING: neither dbus-run-session nor dbus-daemon available." >&2
    echo "[dbus] AdsPower will likely emit 'Failed to connect to the bus' warnings." >&2
    echo "[dbus] Install: apt-get install -y dbus-x11  (or: dnf install -y dbus-daemon)" >&2
    echo "[dbus] Continuing anyway — these warnings are non-fatal." >&2
fi

# ---- Step 3: Start a fake DBus SYSTEM bus (noise suppressor) ---------------
# Chromium also tries /run/dbus/system_bus_socket on startup. In containers
# that socket doesn't exist → Chromium prints "Failed to connect to the bus"
# noise. These errors are non-fatal but confusing.
#
# Fix: start a second dbus-daemon in system mode at a writable temp path
# and export DBUS_SYSTEM_BUS_ADDRESS so Chromium finds it.
if [[ "$SKIP_DBUS" == "1" || "$SKIP_SYSTEM_BUS" == "1" ]]; then
    : # Skip — user requested no system bus
elif have dbus-daemon; then
    SYSTEM_DBUS_DIR="$(mktemp -d --tmpdir adspower-sysbus.XXXXXX)"
    SYSTEM_DBUS_CONFIG="$SYSTEM_DBUS_DIR/system.conf"
    SYSTEM_DBUS_PIDFILE="$SYSTEM_DBUS_DIR/pid"
    SYSTEM_DBUS_SOCKET="$SYSTEM_DBUS_DIR/system_bus_socket"
    cat > "$SYSTEM_DBUS_CONFIG" <<EOF
<!DOCTYPE busconfig PUBLIC
 "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>system</type>
  <listen>unix:path=$SYSTEM_DBUS_SOCKET</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow user="*"/>
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
EOF
    if dbus-daemon \
        --config-file="$SYSTEM_DBUS_CONFIG" \
        --fork \
        --print-pid=1 \
        1>"$SYSTEM_DBUS_PIDFILE" 2>/dev/null; then
        SYSTEM_DBUS_PID="$(cat "$SYSTEM_DBUS_PIDFILE" 2>/dev/null || true)"
        if [[ -n "$SYSTEM_DBUS_PID" ]] && kill -0 "$SYSTEM_DBUS_PID" 2>/dev/null; then
            export DBUS_SYSTEM_BUS_ADDRESS="unix:path=$SYSTEM_DBUS_SOCKET"
            echo "[dbus] Fake system bus started (PID=$SYSTEM_DBUS_PID) — /run/dbus noise suppressed"
        else
            echo "[dbus] WARNING: system bus daemon exited immediately — non-fatal, will see /run/dbus noise." >&2
            rm -rf "$SYSTEM_DBUS_DIR"
            SYSTEM_DBUS_DIR=""
        fi
    else
        echo "[dbus] WARNING: could not start fake system bus — non-fatal, will see /run/dbus noise." >&2
        rm -rf "$SYSTEM_DBUS_DIR"
        SYSTEM_DBUS_DIR=""
    fi
else
    echo "[dbus] NOTE: dbus-daemon not available, cannot start fake system bus." >&2
    echo "[dbus]       Chromium will print 'Failed to connect to /run/dbus/system_bus_socket' noise." >&2
    echo "[dbus]       These errors are non-fatal — session bus still works." >&2
fi

# ---- Step 4: Build AdsPower CLI flags --------------------------------------
# --headless=true           : run without UI (mandatory in container)
# --no-sandbox              : required when running as root in container
# --disable-gpu             : no GPU available in headless container
# --disable-dev-shm-usage   : CRITICAL — prevents crashes when /dev/shm is
#                             small (default 64MB in Docker). Uses /tmp instead.
# --disable-software-rasterizer : avoid SwiftShader init failures in containers
ADSPOWER_FLAGS=(
    --headless=true
    --no-sandbox
    --disable-gpu
    --disable-dev-shm-usage
    --disable-software-rasterizer
    --api-key="$ADSPOWER_API_KEY"
)

# Append any extra flags passed by the user.
ADSPOWER_FLAGS+=("$@")

# ---- Step 5: Launch AdsPower under Xvfb (and dbus-run-session if available) -
XVFB_DISPLAY=":99"

if [[ -n "${DISPLAY:-}" ]]; then
    echo "[xvfb] Using existing DISPLAY=$DISPLAY"
else
    if have Xvfb; then
        echo "[xvfb] Starting Xvfb on $XVFB_DISPLAY ($XVFB_SCREEN)..."
        Xvfb "$XVFB_DISPLAY" -screen 0 "$XVFB_SCREEN" -ac -nolisten tcp \
            >/tmp/adspower_xvfb.log 2>&1 &
        XVFB_PID=$!
        export DISPLAY="$XVFB_DISPLAY"
        sleep 1
        if ! kill -0 "$XVFB_PID" 2>/dev/null; then
            echo "[xvfb] Xvfb failed to start. Log:" >&2
            cat /tmp/adspower_xvfb.log >&2 || true
            exit 2
        fi
        echo "[xvfb] Xvfb running (PID=$XVFB_PID) on DISPLAY=$DISPLAY"
    else
        echo "[xvfb] Xvfb binary not found — falling back to xvfb-run wrapper." >&2
        DBUS_WRAPPER=(xvfb-run --server-args="-screen 0 $XVFB_SCREEN" "${DBUS_WRAPPER[@]}")
    fi
fi

# ---- Step 6: Print launch info ----------------------------------------------
echo "================================================================"
echo " Launching AdsPower Global"
echo "   binary    : $ADSPOWER_BIN"
echo "   display   : ${DISPLAY:-<inherited>}"
echo "   dbus wrap : ${DBUS_WRAPPER[*]:-<none>}"
echo "   system bus: ${DBUS_SYSTEM_BUS_ADDRESS:-<none, /run/dbus noise expected>}"
echo "   flags     : ${ADSPOWER_FLAGS[*]}"
echo "   log file  : $ADSPOWER_LOG"
echo "   api port  : $ADSPOWER_API_PORT (readiness poll, ${ADSPOWER_START_TIMEOUT}s timeout)"
echo "================================================================"

# ---- Step 7: Truncate log & launch AdsPower with stderr/stdout capture -----
: > "$ADSPOWER_LOG"

# Launch AdsPower. We capture stdout+stderr to $ADSPOWER_LOG via `tee` AND
# display live on the terminal. We also filter out the harmless dbus
# "Failed to set fd limit to 65536: Operation not permitted" warning so
# the operator doesn't see noise that suggests a setup problem.
#
# Pipeline (stderr side):
#   AdsPower stderr  ->  grep -v "Failed to set fd limit"  ->  tee -a $LOG  ->  terminal stderr
# Pipeline (stdout side):
#   AdsPower stdout  ->  tee -a $LOG  ->  terminal stdout
#
# Process substitution keeps the file descriptors separate so the trap
# can still `wait` on the AdsPower PID directly.
"${DBUS_WRAPPER[@]}" "$ADSPOWER_BIN" "${ADSPOWER_FLAGS[@]}" \
    2> >(grep -v --line-buffered 'Failed to set fd limit' \
            | tee -a "$ADSPOWER_LOG" >&2) \
    > >(tee -a "$ADSPOWER_LOG") &
ADSPOWER_PID=$!

# Capture the PID of the last background `tee` so cleanup can reap it.
TEE_PID=$!

# ---- Step 8: Forward signals to AdsPower -----------------------------------
# Without this, killing this script (Ctrl-C / docker stop) would leave
# AdsPower running until the container itself is killed.
forward_signal() {
    local sig="$1"
    if [[ -n "$ADSPOWER_PID" ]] && kill -0 "$ADSPOWER_PID" 2>/dev/null; then
        kill "-$sig" "$ADSPOWER_PID" 2>/dev/null || true
    fi
}
trap 'forward_signal TERM' TERM
trap 'forward_signal INT'  INT
trap 'forward_signal HUP'  HUP

# ---- Step 9: Background readiness watcher ----------------------------------
# Polls the AdsPower local API port every 1s. If the port comes up, the
# app is running normally and the watcher exits quietly. If AdsPower exits
# BEFORE the port opens (license failure, missing libs, etc.), the watcher
# also exits quietly. Either way, the foreground `wait` below is what
# actually blocks until AdsPower dies.
(
    for _ in $(seq 1 "$ADSPOWER_START_TIMEOUT"); do
        if ! kill -0 "$ADSPOWER_PID" 2>/dev/null; then
            exit 0   # AdsPower died — nothing to wait for
        fi
        # Try IPv4 then IPv6 loopback (AdsPower binds to local.adspower.net
        # which resolves to both 127.0.0.1 and ::1).
        if curl -sS --max-time 1 \
               "http://127.0.0.1:$ADSPOWER_API_PORT/status" \
               >/dev/null 2>&1 \
           || curl -sS --max-time 1 \
               "http://[::1]:$ADSPOWER_API_PORT/status" \
               >/dev/null 2>&1 \
           || curl -sS --max-time 1 \
               "http://localhost:$ADSPOWER_API_PORT/status" \
               >/dev/null 2>&1; then
            echo "[adspower] API ready on port $ADSPOWER_API_PORT"
            exit 0
        fi
        sleep 1
    done
    # Timed out — don't kill AdsPower, just warn (could be a slow boot)
    echo "[adspower] WARNING: API port $ADSPOWER_API_PORT not responding after ${ADSPOWER_START_TIMEOUT}s" >&2
    echo "[adspower]          Check $ADSPOWER_LOG for details." >&2
) &
WATCHER_PID=$!

# ---- Step 10: Wait for AdsPower to exit ------------------------------------
# Disable `set -e` for the wait so we can capture the exit code.
set +e
wait "$ADSPOWER_PID" 2>/dev/null
ADSPOWER_EXIT_CODE=$?
set -e

# Stop the watcher if it's still running
if [[ -n "$WATCHER_PID" ]] && kill -0 "$WATCHER_PID" 2>/dev/null; then
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
fi

# ---- Step 11: Post-exit error detection (the main fix for ERROR - 4012) ----
# Scan the captured log for known AdsPower error codes and print a clear,
# actionable message. This is the primary user-facing fix: instead of
# letting "ERROR - 4012 / The package you are using expires" scroll past
# silently, we explain what it means and what to do next.
scan_known_errors() {
    local log="$1"
    if [[ ! -f "$log" ]]; then
        return "$ADSPOWER_EXIT_CODE"
    fi

    # 4012 — subscription / package expired
    if grep -qE 'ERROR - 4012|package you are using expires' "$log"; then
        cat >&2 <<EOF

====================================================================
 FATAL: AdsPower subscription expired (error code 4012)
====================================================================

 Root cause:
   AdsPower launched correctly (Xvfb, dbus, binary — all OK), but the
   server-side license check rejected the API key because the package /
   subscription tied to it has EXPIRED on AdsPower's side.

   This is NOT a bug in start_adspower.sh — the script did its job.
   The license itself needs to be renewed or replaced.

 Remediation (in order of preference):

   1. Renew your AdsPower subscription
      -> Log in at https://adspro.com  ->  Billing / Subscription
      -> After renewal, just re-run this script (no code change needed).

   2. Switch to a different / fresh API key
      -> export ADSPOWER_API_KEY='<new-key-here>'
      -> ./start_adspower.sh

   3. If you already renewed but still see this error, force AdsPower
      to re-fetch the license by clearing its local cache:
      -> rm -rf ~/.adspower_global/cache
      -> ./start_adspower.sh

 Captured AdsPower output saved to:
   $ADSPOWER_LOG

 Exit code: 7 (license expired)
====================================================================
EOF
        return 7
    fi

    # 4013 — API key invalid / malformed
    if grep -qE 'ERROR - 4013|API key.{0,30}invalid|api_key.{0,30}invalid' "$log"; then
        cat >&2 <<EOF

====================================================================
 FATAL: AdsPower API key rejected (error code 4013)
====================================================================

 Root cause:
   AdsPower reports the API key is invalid, malformed, or belongs to a
   different account.

 Remediation:
   1. Verify the key at https://adspro.com  ->  Account  ->  API
   2. Re-export and restart:
        export ADSPOWER_API_KEY='<correct-key>'
        ./start_adspower.sh

 Captured log: $ADSPOWER_LOG
 Exit code: 8 (API key invalid)
====================================================================
EOF
        return 8
    fi

    # 4014 — account frozen / banned
    if grep -qE 'ERROR - 4014|account.{0,20}frozen|account.{0,20}banned' "$log"; then
        cat >&2 <<EOF

====================================================================
 FATAL: AdsPower account frozen (error code 4014)
====================================================================

 Root cause:
   AdsPower reports this account has been frozen or banned.

 Remediation:
   -> Contact AdsPower support: support@adspower.com
   -> Provide them your API key prefix: ${ADSPOWER_API_KEY:0:8}...

 Captured log: $ADSPOWER_LOG
 Exit code: 9 (account frozen)
====================================================================
EOF
        return 9
    fi

    # Generic non-zero exit
    if [[ "$ADSPOWER_EXIT_CODE" -ne 0 ]]; then
        cat >&2 <<EOF

====================================================================
 AdsPower exited with code $ADSPOWER_EXIT_CODE
====================================================================

 No known error code was detected in the output. Inspect the full log:
   $ADSPOWER_LOG

 Common causes for non-zero exit in containers:
   - Missing shared libraries (run: ldd "$ADSPOWER_BIN")
   - Wrong architecture (run: file "$ADSPOWER_BIN")
   - Insufficient disk space in /tmp or /dev/shm
   - Stale lock file (try: rm -f ~/.adspower_global/*.lock)

 Exit code: 10 (unknown AdsPower failure)
====================================================================
EOF
        return 10
    fi

    return 0
}

scan_known_errors "$ADSPOWER_LOG"
SCAN_EXIT=$?

# If scan returned a non-zero error class, propagate it. Otherwise return
# AdsPower's own exit code (which should be 0 here).
if [[ "$SCAN_EXIT" -ne 0 ]]; then
    exit "$SCAN_EXIT"
fi
exit "$ADSPOWER_EXIT_CODE"
