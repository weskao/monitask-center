#!/opt/homebrew/opt/python@3.13/bin/python3.13
"""誠實版 Monitask 排程控制（pyobjc / AX，無 F13、無合成輸入、無補活動）。

依 schedule.json（early/late/off）判斷當天班別；非該班別日各相位自動略過。

相位子命令（launchd）：
  morning       早班 10:00：每 5 分輪詢，第一次開始計時即止（11:50 前）
  lunch         早班 12:00：午休暫停
  afternoon     早班 13:00：午休結束繼續計時
  evening       早班 20:00：問「關閉 / 繼續」，每 30 分再問，15 分未點 → 自動關閉
  night-start   晚班 17:00：每 5 分輪詢，第一次開始計時即止（19:50 前）
  night-stop    晚班隔日 01:00：昨日為晚班才停止計時
其他：
  watch         常駐：偵測離座彈窗 → 自動點「暫停 / 不計入」（learn 模式，禁點繼續）
  status        印出狀態
  plan          印出今天/昨天班別與各相位動作（不動 Monitask）
  dump-windows  印出目前所有視窗 / 按鈕結構
  ensure-playing / ensure-paused  手動原語（不判斷班表）

明確不做：F13 / 合成輸入 / 補活動 / 離座自動點「繼續」。離座一律讓它停。
AX helper 沿用同事已驗證的實作（處理鎖屏卡死、冷啟動、NSWorkspace 快取），移除補活動相關。
"""

from __future__ import annotations

import fcntl
import functools
import json
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# pyobjc 從解壓的 wheel 目錄載入（launchd 也會帶 PYTHONPATH，這裡雙保險）
sys.path.insert(0, str(Path(__file__).resolve().parent / "pylibs"))

import AppKit
import ApplicationServices as AX
from Foundation import NSDate, NSRunLoop
import Quartz

APP_NAME = "Monitask"
DATA_DIR = Path.home() / "Library/Application Support/MonitaskCenter"
LOCK_FILE = DATA_DIR / "automation.lock"
POPUP_SEEN_FILE = DATA_DIR / "popup-structure.txt"
TG_SEND = Path.home() / ".claude/scripts/tg-send.sh"

AX_API_DISABLED = -25211
AX_TIMEOUT_S = 10.0

EVENING_RECHECK_SECONDS = 1800   # 每 30 分再問一次
EVENING_TIMEOUT_SECONDS = 900    # 彈窗 15 分未點 → 自動關閉
WATCH_INTERVAL_SECONDS = 7

POLL_INTERVAL_SECONDS = 300      # 開始計時輪詢間隔（每 5 分）
EARLY_POLL_MINUTES = 110         # 早班 10:00 起輪詢，11:50 前止（不撞 12:00 午休）
LATE_POLL_MINUTES = 170          # 晚班 17:00 起輪詢，19:50 前止

# 班表：schedule.json 的 days 映射 日期(YYYY-MM-DD) → early / late / off
SCHEDULE_FILE = Path(__file__).resolve().parent / "schedule.json"

# 離座彈窗「該點」的按鈕（誠實方向：暫停 / 不計入）。拿到真實文字後補進來。
PAUSE_KEYWORDS = ("暫停", "停止", "不計入", "捨棄", "pause", "stop", "discard", "no")
# 明確禁點（避免誤點成「繼續計時」）。
FORBID_KEYWORDS = ("繼續", "保留", "計入", "continue", "keep", "resume", "yes")


def log(message: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    with (DATA_DIR / "monitask.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


SCRIPT_DIR = Path(__file__).resolve().parent


def run(command: list[str], timeout: int = 30, cwd=None) -> subprocess.CompletedProcess:
    return subprocess.run(command, text=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=timeout, check=False, cwd=cwd)


@functools.cache
def host_name() -> str:
    try:
        r = subprocess.run(["/usr/sbin/scutil", "--get", "ComputerName"],
                           text=True, capture_output=True, timeout=5, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return socket.gethostname() or "unknown-host"


def macos_notification(message: str, title: str = "Monitask") -> None:
    run(["/usr/bin/osascript", "-e",
         f'display notification {message!r} with title {title!r}'], timeout=10)


def telegram(command: list[str], timeout: int) -> bool:
    if not TG_SEND.is_file():
        return False
    try:
        # tg-send.sh 開頭用 git rev-parse 定位 .env，非 git 目錄會 set -e 而死；
        # 故 cwd 固定在本專案（已 git init）。TG_BOT_TOKEN/TG_CHAT_ID 由 run.sh 載入 .env 提供。
        if run(["/bin/bash", str(TG_SEND), "preflight"], timeout=12, cwd=SCRIPT_DIR).returncode != 0:
            return False
        return run(["/bin/bash", str(TG_SEND), *command], timeout=timeout, cwd=SCRIPT_DIR).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def notify(message: str) -> None:
    delivered = False
    try:
        delivered = telegram(["send", f"{message}\n🖥️ {host_name()}"], timeout=45)
    except Exception as exc:
        log(f"telegram error: {exc}")
    if not delivered:
        macos_notification(message)


def gui_session_available() -> bool:
    session = Quartz.CGSessionCopyCurrentDictionary()
    if not session:
        return False
    return bool(session.get("kCGSSessionOnConsoleKey")) and not bool(
        session.get("CGSSessionScreenIsLocked", False))


def refresh_running_applications() -> None:
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))


def running_app():
    refresh_running_applications()
    return next((a for a in AppKit.NSWorkspace.sharedWorkspace().runningApplications()
                 if a.localizedName() == APP_NAME), None)


def ax_value(element, attribute: str):
    error, value = AX.AXUIElementCopyAttributeValue(element, attribute, None)
    if error == AX_API_DISABLED:
        raise RuntimeError("Accessibility 權限未授予給 "
                           f"{os.path.realpath(sys.executable)}")
    if error != 0:
        return None
    return value


def ax_app_element(app):
    element = AX.AXUIElementCreateApplication(app.processIdentifier())
    AX.AXUIElementSetMessagingTimeout(element, AX_TIMEOUT_S)
    return element


def _collect(element, role: str, out: list) -> None:
    if ax_value(element, "AXRole") == role:
        out.append(element)
    error, children = AX.AXUIElementCopyAttributeValue(element, "AXChildren", None)
    if error == 0 and children:
        for c in children:
            _collect(c, role, out)


def windows(app) -> list:
    return list(ax_value(ax_app_element(app), "AXWindows") or [])


def checkboxes(app) -> list:
    out: list = []
    for w in windows(app):
        _collect(w, "AXCheckBox", out)
    return out


def unminimize(app) -> None:
    for w in windows(app):
        AX.AXUIElementSetAttributeValue(w, "AXMinimized", False)


def is_playing() -> bool:
    app = running_app()
    if app is None:
        return False
    boxes = checkboxes(app)
    if len(boxes) < 2:
        raise RuntimeError(f"預期 2 個 Monitask checkbox，實得 {len(boxes)}")
    return bool(ax_value(boxes[0], "AXValue"))


def press_play_pause(playing: bool, action: str) -> bool:
    app = running_app()
    if app is None:
        return False
    boxes = checkboxes(app)
    if len(boxes) < 2:
        raise RuntimeError(f"預期 2 個 Monitask checkbox，實得 {len(boxes)}")
    AX.AXUIElementPerformAction(boxes[0 if playing else 1], "AXPress")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if is_playing() == playing:
                return True
        except RuntimeError as exc:
            log(f"{action} 狀態讀取重試：{exc}")
        time.sleep(1)
    return False


def ensure_window(app) -> bool:
    """Monitask 計時中會把視窗收起（AXWindows 為空）；點 Dock tile 叫回才有 checkbox 可讀/按。"""
    if windows(app):
        return True
    run(["/usr/bin/osascript", "-e",
         'tell application "System Events" to tell process "Dock" to '
         'click (first UI element of list 1 whose name is "Monitask")'], timeout=8)
    for _ in range(10):
        time.sleep(0.5)
        if windows(app):
            return True
    return bool(windows(app))


def ensure_app_running() -> bool:
    if running_app() is not None:
        return True
    log("Monitask 未執行，open -a 啟動")
    run(["/usr/bin/open", "-a", APP_NAME], timeout=10)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        app = running_app()
        if app is not None:
            try:
                if len(checkboxes(app)) >= 2:
                    return True
            except RuntimeError:
                pass
        time.sleep(1)
    return running_app() is not None


# ---------- 班表 ----------

def load_schedule() -> dict:
    """讀 schedule.json 的 days 映射；讀不到回空 dict。"""
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        days = data.get("days", data)  # 容忍無外層 days 的舊格式
        return days if isinstance(days, dict) else {}
    except FileNotFoundError:
        log(f"schedule.json 不存在：{SCHEDULE_FILE}"); return {}
    except Exception as exc:
        log(f"讀 schedule.json 失敗：{exc}"); return {}


def shift_for(d: date) -> str:
    """回傳該日班別 early / late / off / unknown。"""
    return load_schedule().get(d.isoformat(), "unknown")


# ---------- 計時原語（帶動作標籤） ----------

def _ensure_playing_once() -> str:
    """單次確保計時中。回傳 playing / started / locked / fail。不發通知。"""
    if not gui_session_available():
        return "locked"
    if not ensure_app_running():
        return "fail"
    if not ensure_window(running_app()):
        return "fail"
    try:
        if is_playing():
            return "playing"
    except RuntimeError as exc:
        log(f"讀取狀態失敗：{exc}"); return "fail"
    return "started" if press_play_pause(True, "Play") else "fail"


def _ensure_playing(label: str) -> int:
    """單次：確保計時中並發通知（13:00 繼續 / 手動用）。"""
    r = _ensure_playing_once()
    if r == "locked":
        log(f"{label}：螢幕鎖定/無 console，略過"); return 0
    if r == "playing":
        log(f"{label}：已在計時，跳過"); notify(f"☑️ Monitask 已在計時中（{label}，無需動作）"); return 0
    if r == "started":
        log(f"{label}：已開始計時"); notify(f"✅ Monitask 已自動{label}"); return 0
    log(f"{label}：失敗"); notify(f"❌ Monitask {label}失敗，請手動處理"); return 1


def _ensure_playing_poll(max_minutes: int, label: str) -> int:
    """輪詢：每 5 分確保「第一次」開始計時，成功即止；到時限仍未成功則告警。
    誠實原則：只負責把第一次啟動點成，成功即退出，不會在你之後離座 idle 暫停時再補點。"""
    deadline = time.monotonic() + max_minutes * 60
    warned_locked = False
    while True:
        r = _ensure_playing_once()
        if r == "playing":
            log(f"{label}：已在計時"); notify(f"☑️ Monitask 已在計時中（{label}，無需動作）"); return 0
        if r == "started":
            log(f"{label}：已開始計時"); notify(f"✅ Monitask 已自動{label}"); return 0
        # locked（未解鎖/電腦沒開）或 fail：等下一輪再試，直到時限
        if r == "locked" and not warned_locked:
            log(f"{label}：螢幕鎖定/電腦未開，每 5 分續試至時限"); warned_locked = True
        else:
            log(f"{label}：{r}，5 分後重試")
        if time.monotonic() + POLL_INTERVAL_SECONDS >= deadline:
            log(f"{label}：到時限仍未能開始"); notify(f"⚠️ Monitask 到時限仍未{label}（電腦未開/未解鎖？）"); return 1
        time.sleep(POLL_INTERVAL_SECONDS)


def _ensure_paused(label: str) -> int:
    """單次：確保暫停並發通知。"""
    if not gui_session_available():
        log(f"{label}：螢幕鎖定/無 console，略過"); return 0
    if running_app() is None:
        log(f"{label}：Monitask 未執行，無需暫停"); return 0
    if not ensure_window(running_app()):
        log("無法取得 Monitask 視窗"); notify("⚠️ 無法取得 Monitask 視窗"); return 1
    try:
        if not is_playing():
            log(f"{label}：已暫停，跳過"); notify(f"☑️ Monitask 已暫停（{label}，無需動作）"); return 0
    except RuntimeError as exc:
        log(f"讀取狀態失敗：{exc}"); notify(f"⚠️ 讀取 Monitask 狀態失敗：{exc}"); return 1
    if press_play_pause(False, "Pause"):
        log(f"✅ 已暫停（{label}）"); notify(f"✅ Monitask 已{label}"); return 0
    log(f"❌ {label}失敗"); notify(f"❌ Monitask {label}失敗"); return 1


# 手動/相容用原語命令
def cmd_ensure_playing() -> int:
    return _ensure_playing("開始計時")


def cmd_ensure_paused() -> int:
    return _ensure_paused("暫停")


# ---------- 依班表的每日相位命令（launchd 呼叫） ----------

def cmd_morning() -> int:
    s = shift_for(date.today())
    if s == "unknown":
        notify("⚠️ schedule.json 沒有今天的班別，未動作。請重生班表。"); return 0
    if s != "early":
        log(f"morning：今日={s}，非早班，略過"); return 0
    return _ensure_playing_poll(EARLY_POLL_MINUTES, "早班開始計時")


def cmd_lunch() -> int:
    if shift_for(date.today()) != "early":
        log("lunch：非早班，略過"); return 0
    return _ensure_paused("午休暫停")


def cmd_afternoon() -> int:
    if shift_for(date.today()) != "early":
        log("afternoon：非早班，略過"); return 0
    return _ensure_playing("午休結束繼續計時")


def cmd_night_start() -> int:
    if shift_for(date.today()) != "late":
        log("night-start：今日非晚班，略過"); return 0
    return _ensure_playing_poll(LATE_POLL_MINUTES, "晚班開始計時")


def cmd_night_stop() -> int:
    # 晚班 17:00→隔日 01:00；01:00 當下的「昨天」才是那個晚班日
    if shift_for(date.today() - timedelta(days=1)) != "late":
        log("night-stop：昨日非晚班，略過"); return 0
    return _ensure_paused("晚班下班停止")


def cmd_plan() -> int:
    """只印今天/昨天班別與各相位會做什麼，不動 Monitask（煙霧測試用）。"""
    today, yday = date.today(), date.today() - timedelta(days=1)
    ts, ys = shift_for(today), shift_for(yday)
    print(f"schedule_file={SCHEDULE_FILE} exists={SCHEDULE_FILE.exists()}")
    print(f"today={today.isoformat()} shift={ts}")
    print(f"yesterday={yday.isoformat()} shift={ys}")
    print(f"  morning(10:00)   -> {'早班輪詢開始' if ts=='early' else '略過'}")
    print(f"  lunch(12:00)     -> {'午休暫停' if ts=='early' else '略過'}")
    print(f"  afternoon(13:00) -> {'午休結束繼續' if ts=='early' else '略過'}")
    print(f"  evening(20:00)   -> {'早班下班詢問' if ts=='early' else '略過'}")
    print(f"  night-start(17:00)-> {'晚班輪詢開始' if ts=='late' else '略過'}")
    print(f"  night-stop(01:00)-> {'晚班下班停止' if ys=='late' else '略過'}")
    return 0


def _dialog(prompt: str, buttons: tuple[str, str], default: str, timeout: int):
    """回傳 (button_returned, gave_up)。gave_up=True 代表逾時未點。"""
    b = "{" + ", ".join(f'"{x}"' for x in buttons) + "}"
    script = (f'tell application "System Events"\n'
              f'activate\n'
              f'set d to display dialog {prompt!r} buttons {b} '
              f'default button "{default}" with title "Monitask" giving up after {timeout}\n'
              f'return (button returned of d) & "|" & (gave up of d)\n'
              f'end tell')
    r = subprocess.run(["/usr/bin/osascript", "-e", script],
                       text=True, capture_output=True, timeout=timeout + 30, check=False)
    out = (r.stdout or "").strip()
    if "|" not in out:
        return ("", True)
    btn, gave = out.rsplit("|", 1)
    return (btn, gave.strip().lower() == "true")


def cmd_evening() -> int:
    """早班下班（20:00）：僅早班日執行。發 TG 提醒 + 彈對話框問關閉/繼續，15 分未點自動關。"""
    if shift_for(date.today()) != "early":
        log("evening：非早班，略過"); return 0
    return _evening_dialog()


def _evening_dialog() -> int:
    if not gui_session_available():
        log("螢幕鎖定，evening 略過"); return 0
    if running_app() is None:
        log("Monitask 未執行，evening 無需處理"); return 0
    if not ensure_window(running_app()):
        log("無法取得 Monitask 視窗"); return 1
    try:
        if not is_playing():
            log("下班時已暫停，無需詢問"); notify("✅ Monitask 下班時已停止，無需處理"); return 0
    except RuntimeError as exc:
        log(f"evening 讀取狀態失敗：{exc}"); return 1

    notify("🕗 20:00 到，Monitask 還在計時。畫面上已跳關閉/繼續視窗，15 分沒點會自動停。")

    while True:
        btn, gave_up = _dialog("Monitask 還在計時。要關閉還是繼續?",
                               ("關閉監控", "繼續監控"), "繼續監控", EVENING_TIMEOUT_SECONDS)
        if btn == "繼續監控" and not gave_up:
            log(f"使用者選擇繼續，{EVENING_RECHECK_SECONDS // 60} 分後再問")
            time.sleep(EVENING_RECHECK_SECONDS)
            if running_app() is None:
                log("Monitask 已關閉，evening 結束"); return 0
            try:
                if not is_playing():
                    log("已暫停，evening 結束"); return 0
            except RuntimeError:
                pass
            continue
        why = "逾時 15 分未點，自動關閉" if gave_up else "使用者選擇關閉"
        log(f"{why} → 暫停計時")
        if press_play_pause(False, "Pause"):
            notify(f"🌙 Monitask 已關閉計時（{why}）"); return 0
        notify(f"❌ Monitask 關閉失敗（{why}），請手動暫停"); return 1


def _button_label(element) -> str:
    for attr in ("AXTitle", "AXDescription", "AXValue"):
        v = ax_value(element, attr)
        if v:
            return str(v)
    return ""


def _handle_popup(w) -> None:
    title = ax_value(w, "AXTitle") or ""
    buttons: list = []
    _collect(w, "AXButton", buttons)
    labels = [_button_label(b) for b in buttons]
    labels = [x for x in labels if x]

    if not POPUP_SEEN_FILE.exists():
        with POPUP_SEEN_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"=== {datetime.now()} popup title={title!r} ===\n")
            fh.write("\n".join(labels) + "\n")
        log(f"偵測到疑似彈窗 title={title!r} 按鈕={labels}，已記錄")
        notify(f"🔎 偵測到 Monitask 彈窗「{title}」按鈕：{'/'.join(labels)}。已記錄，請確認要點哪顆。")

    target = None
    for b, label in zip(buttons, labels):
        low = label.lower()
        if any(k in low for k in FORBID_KEYWORDS):
            continue
        if any(k in low for k in PAUSE_KEYWORDS):
            target = (b, label); break
    if target is not None:
        AX.AXUIElementPerformAction(target[0], "AXPress")
        log(f"彈窗自動點擊：{target[1]}")
        notify(f"🟡 離座彈窗自動點「{target[1]}」（暫停/不計入）")
    else:
        log(f"彈窗找不到安全暫停按鈕，未動作（按鈕：{labels}）")


def cmd_watch() -> int:
    log(f"watch 啟動（間隔 {WATCH_INTERVAL_SECONDS}s）")
    while True:
        try:
            if gui_session_available():
                app = running_app()
                if app is not None:
                    wins = windows(app)
                    # 主計時視窗 = 含 >=2 checkbox 的視窗；其餘視為彈窗
                    for w in wins:
                        boxes: list = []
                        _collect(w, "AXCheckBox", boxes)
                        if len(boxes) >= 2:
                            continue
                        # 只有含按鈕的視窗才當彈窗處理
                        btns: list = []
                        _collect(w, "AXButton", btns)
                        if btns:
                            _handle_popup(w)
        except Exception as exc:
            log(f"watch 迴圈錯誤：{exc}")
        time.sleep(WATCH_INTERVAL_SECONDS)


def cmd_status() -> int:
    print(f"host={host_name()}")
    print(f"accessibility_trusted={bool(AX.AXIsProcessTrusted())}")
    app = running_app()
    print(f"running={app is not None}")
    if app is not None:
        ensure_window(app)
    try:
        print(f"playing={is_playing()}")
    except Exception as exc:
        print(f"playing=unknown ({exc})")
    print(f"gui_session_available={gui_session_available()}")
    return 0


def cmd_dump_windows() -> int:
    app = running_app()
    if app is None:
        print("Monitask 未執行"); return 0
    ensure_window(app)
    for i, w in enumerate(windows(app)):
        title = ax_value(w, "AXTitle")
        subrole = ax_value(w, "AXSubrole")
        print(f"WINDOW[{i}] title={title!r} subrole={subrole!r}")
        boxes: list = []; _collect(w, "AXCheckBox", boxes)
        btns: list = []; _collect(w, "AXButton", btns)
        print(f"  checkboxes={len(boxes)} buttons={[_button_label(b) for b in btns]}")
    return 0


COMMANDS = {
    # 依班表的每日相位（launchd 用）
    "morning": cmd_morning,
    "lunch": cmd_lunch,
    "afternoon": cmd_afternoon,
    "evening": cmd_evening,
    "night-start": cmd_night_start,
    "night-stop": cmd_night_stop,
    # 常駐 / 手動 / 除錯
    "watch": cmd_watch,
    "status": cmd_status,
    "plan": cmd_plan,
    "dump-windows": cmd_dump_windows,
    "ensure-playing": cmd_ensure_playing,
    "ensure-paused": cmd_ensure_paused,
}

# 不搶互斥鎖的命令（唯讀 / 自己常駐）
NO_LOCK = {"watch", "status", "plan", "dump-windows"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: {sys.argv[0]} {{{'|'.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    # 單一鎖：相位/ensure 互斥；watch/status/plan/dump 不搶鎖
    if cmd not in NO_LOCK:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        lock = LOCK_FILE.open("w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
    return COMMANDS[cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
