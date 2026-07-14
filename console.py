"""
Console emulator — pretty colored logging with emoji for the bot.

v11.0 UPGRADE — MOD 1+2: Added tab activity + CPM tracking log helpers.
  - tab(uid, msg): log untuk aktivitas di tab baru (iklan)
  - cpm(uid, msg): log untuk CPM view time tracking
  - summary() now shows CPM stats (total_ad_view_time, avg_ad_view_time)

v9.3 FIX: ts() sebelumnya memanggil datetime.now() DUA kali — sekali untuk
strftime, sekali untuk microseconds. Pada kasus edge (pertengahan detik),
bisa menghasilkan timestamp inkonsisten seperti "12:00:00.999" diikuti
"12:00:01.000" untuk dua baris yang diprint bersamaan. Sekarang
datetime.now() dipanggil SEKALI saja.
"""
import sys
import time
from datetime import datetime

# ANSI color codes
class C:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    GRAY = '\033[90m'
    # v11.0: additional colors for tab activity & CPM tracking
    ORANGE = '\033[38;5;208m'   # Tab activity
    LIME = '\033[38;5;118m'     # CPM tracking


def ts():
    # v9.3: panggil datetime.now() sekali saja — hindari race milisecond
    # antara strftime dan .microsecond.
    now = datetime.now()
    return now.strftime('%H:%M:%S.') + f'{now.microsecond // 1000:03d}'


def _line(user_id, emoji, msg, color=C.RESET):
    tag = f'{C.GRAY}[{user_id}]{C.RESET} ' if user_id else ''
    print(f'{C.GRAY}{ts()}{C.RESET} {tag}{color}{emoji}  {msg}{C.RESET}', flush=True)

def banner(msg):
    print(f'{C.GRAY}{ts()}{C.RESET} {C.BOLD}{C.CYAN}━━━ {msg} ━━━{C.RESET}', flush=True)


def start(uid='', msg=''): _line(uid, '🚀', msg, C.CYAN)
def info(uid='', msg=''):  _line(uid, 'ℹ️', msg, C.BLUE)
def ok(uid='', msg=''):    _line(uid, '✅', msg, C.GREEN)
def warn(uid='', msg=''):  _line(uid, '⚠️', msg, C.YELLOW)
def error(uid='', msg=''): _line(uid, '❌', msg, C.RED)
def step(uid='', msg=''):  _line(uid, '🔹', msg, C.MAGENTA)
def ad(uid='', msg=''):    _line(uid, '🎯', msg, C.MAGENTA)
def article(uid='', msg=''): _line(uid, '📰', msg, C.BLUE)
def scroll(uid='', msg=''): _line(uid, '🖱️', msg, C.GRAY)
def idle(uid='', msg=''):  _line(uid, '⏳', msg, C.DIM)
def mouse(uid='', msg=''): _line(uid, '🖱️', msg, C.GRAY)

# v11.0 UPGRADE — MOD 1+2: Tab activity & CPM view time log helpers
def tab(uid='', msg=''):   _line(uid, '🪟', msg, C.ORANGE)   # Tab baru/iklan activity
def cpm(uid='', msg=''):   _line(uid, '💰', msg, C.LIME)     # CPM view time tracking


def summary(stats):
    print('\n' + C.BOLD + C.CYAN + '═' * 50 + C.RESET, flush=True)
    print(C.BOLD + '  RUN SUMMARY' + C.RESET, flush=True)
    print(C.CYAN + '═' * 50 + C.RESET, flush=True)
    # v10.3: 'total' is now actual processed count; show planned too if available
    planned = stats.get('planned_total')
    if planned and planned != stats['total']:
        print(f'  Total users     : {stats["total"]} / {planned} (planned)')
    else:
        print(f'  Total users     : {stats["total"]}')
    print(f'  {C.GREEN}Successful      : {stats["success"]}{C.RESET}')
    print(f'  {C.YELLOW}Partial         : {stats["partial"]}{C.RESET}')
    print(f'  {C.RED}Failed          : {stats["failed"]}{C.RESET}')
    # v10.3: new optional fields
    if 'reached_target' in stats:
        print(f'  Reached target  : {stats["reached_target"]}')
    if 'skipped' in stats and stats['skipped']:
        print(f'  Anti-burst skip : {stats["skipped"]}')
    print(f'  Articles viewed : {stats["articles"]}')
    print(f'  Ads clicked     : {stats["ads"]}')
    print(f'  Tracking fired  : {stats.get("tracking", 0)}')
    # v11.0 UPGRADE — MOD 2: CPM view time stats
    total_ad_view_time = stats.get('total_ad_view_time', 0)
    avg_ad_view_time = stats.get('avg_ad_view_time', 0)
    if total_ad_view_time > 0:
        print(f'  {C.LIME}Total ad view time : {total_ad_view_time:.1f}s{C.RESET}')
        print(f'  {C.LIME}Avg ad view time   : {avg_ad_view_time:.1f}s{C.RESET}')
    print(f'  Total duration  : {stats["duration"]}s')
    print(C.CYAN + '═' * 50 + C.RESET + '\n', flush=True)
