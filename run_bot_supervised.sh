#!/usr/bin/env bash
# =============================================================================
# run_bot_supervised.sh — Auto-restart wrapper for bot_v6.py
# =============================================================================
#
# WHY THIS EXISTS:
#   v10.8 added an ESCALATION THREAD that calls os._exit(2) when the bot
#   is wedged beyond recovery (Playwright call stuck, signal.alarm can't
#   interrupt it). This guarantees progress: the bot dies, and a
#   supervisor restarts it so the next run begins fresh.
#
#   Without this wrapper, os._exit(2) would just kill the bot and you'd
#   have to restart manually. With this wrapper, the bot auto-restarts
#   within 2 seconds, picking up where it left off (next user in queue).
#
# USAGE:
#   ./run_bot_supervised.sh [any bot_v6.py arguments...]
#
#   Example:
#     ./run_bot_supervised.sh --limit 100 --mode antidetect --browser-type adspower
#
# ENVIRONMENT VARIABLES (passed through to bot):
#   ADSPOWER_API_KEY, ADSPOWER_PORT, ADSPOWER_PROFILE_ID, etc.
#
# BEHAVIOR:
#   - If bot exits with code 2 (ESCALATION), restart immediately (2s delay)
#   - If bot exits with code 0 (normal completion), exit 0
#   - If bot exits with any other code, wait 10s and restart (crash loop
#     protection — 5 consecutive non-escalation crashes → give up)
#   - Ctrl-C / SIGTERM: propagate to bot, then exit
# =============================================================================

set -uo pipefail

RESTART_DELAY_ESCALATION=2    # after os._exit(2) — fast restart
RESTART_DELAY_CRASH=10        # after unexpected crash — slow restart
MAX_CONSECUTIVE_CRASHES=5     # give up after this many non-escalation crashes

consecutive_crashes=0
iteration=0

# Propagate signals to the child bot process
child_pid=""
forward_signal() {
    if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
        kill "-$1" "$child_pid" 2>/dev/null || true
    fi
}
trap 'forward_signal TERM; exit 130' TERM
trap 'forward_signal INT; exit 130' INT
trap 'forward_signal HUP; exit 129' HUP

echo "[supervisor] Starting bot_v6.py with auto-restart on escalation (os._exit 2)"
echo "[supervisor] Max consecutive non-escalation crashes: $MAX_CONSECUTIVE_CRASHES"
echo "[supervisor] Pass-through args: $*"
echo "================================================================"

while true; do
    iteration=$((iteration + 1))
    echo "[supervisor] === Iteration $iteration ==="

    # Run bot in foreground, capture exit code
    python3 bot_v6.py "$@" &
    child_pid=$!
    wait "$child_pid" 2>/dev/null
    exit_code=$?
    child_pid=""

    case "$exit_code" in
        0)
            echo "[supervisor] Bot exited normally (code 0). Done."
            exit 0
            ;;
        2)
            # ESCALATION — Playwright was wedged, os._exit(2) called.
            # Restart fast — the bot's state is gone but the queue continues.
            consecutive_crashes=0  # escalation is not a "crash"
            echo "[supervisor] Bot escalated (code 2 = Playwright wedged). Restarting in ${RESTART_DELAY_ESCALATION}s..."
            sleep "$RESTART_DELAY_ESCALATION"
            ;;
        130|129)
            # Signal-induced exit (SIGINT/SIGTERM/SIGHUP) — don't restart
            echo "[supervisor] Bot killed by signal (code $exit_code). Exiting."
            exit "$exit_code"
            ;;
        *)
            consecutive_crashes=$((consecutive_crashes + 1))
            echo "[supervisor] Bot crashed (code $exit_code). Consecutive crashes: $consecutive_crashes/$MAX_CONSECUTIVE_CRASHES" >&2
            if [[ "$consecutive_crashes" -ge "$MAX_CONSECUTIVE_CRASHES" ]]; then
                echo "[supervisor] Giving up after $consecutive_crashes consecutive crashes." >&2
                exit 1
            fi
            echo "[supervisor] Restarting in ${RESTART_DELAY_CRASH}s..." >&2
            sleep "$RESTART_DELAY_CRASH"
            ;;
    esac
done
