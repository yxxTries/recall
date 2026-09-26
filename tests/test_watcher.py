"""Phase 1 gate: tracking events from real windows and a real (silent) audio stream.

These tests briefly take the foreground; don't type while they run.
"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

from recall.watcher import AudioWatcher, ForegroundWatcher

user32 = ctypes.WinDLL("user32")
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002

# Tracked vs untracked test windows differ only by interpreter: pythonw.exe vs python.exe.
PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
PYTHON = sys.executable
WINDOW = "import sys, tkinter; r = tkinter.Tk(); r.title(sys.argv[1]); r.geometry('320x120'); r.mainloop()"
SILENCE = (
    "import io, wave, winsound; b = io.BytesIO(); w = wave.open(b, 'wb'); "
    "w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(64000)); w.close(); "
    "winsound.PlaySound(b.getvalue(), winsound.SND_MEMORY)"
)


def open_window(exe: str, title: str):
    proc = subprocess.Popen([exe, "-c", WINDOW, title])
    for _ in range(100):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return proc, hwnd
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"window {title!r} never appeared")


def focus(hwnd) -> None:
    # Windows only lets the foreground process move the foreground; a synthetic Alt tap lifts that lock.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def wait_for(predicate, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_sessions_follow_the_foreground():
    events = []
    tracked_proc, tracked = open_window(PYTHONW, "recall-test-tracked")
    other_proc, other = open_window(PYTHON, "recall-test-other")
    watcher = ForegroundWatcher({"pythonw.exe"}, events.append)
    try:
        focus(other)
        time.sleep(0.5)
        watcher.start()
        for _ in range(5):
            focus(tracked)
            time.sleep(0.5)
            focus(other)
            time.sleep(0.5)
    finally:
        watcher.stop()
        tracked_proc.kill()
        other_proc.kill()

    got = [(e["type"], e["app"], e["title"]) for e in events]
    expected = [
        ("session_start", "pythonw.exe", "recall-test-tracked"),
        ("session_end", "pythonw.exe", "recall-test-tracked"),
    ] * 5
    assert got == expected


def test_pausing_ends_the_live_session():
    events = []
    proc, hwnd = open_window(PYTHONW, "recall-test-pause")
    watcher = ForegroundWatcher({"pythonw.exe"}, events.append)
    try:
        focus(hwnd)
        time.sleep(0.5)
        watcher.start()
        watcher.set_tracked(set())  # what the tray's Pause does
    finally:
        watcher.stop()
        proc.kill()
    assert [e["type"] for e in events] == ["session_start", "session_end"]


def test_audio_start_and_stop_for_tracked_app():
    events = []
    watcher = AudioWatcher({"pythonw.exe"}, events.append)
    watcher.start()
    try:
        player = subprocess.Popen([PYTHONW, "-c", SILENCE])  # 2 s of silence: inaudible, but a real stream
        player.wait(timeout=20)
        wait_for(lambda: any(e["type"] == "audio_stop" for e in events), timeout=10)
    finally:
        watcher.stop()
    assert [(e["type"], e["app"]) for e in events] == [
        ("audio_start", "pythonw.exe"),
        ("audio_stop", "pythonw.exe"),
    ]


def test_untracked_audio_is_ignored():
    events = []
    watcher = AudioWatcher({"notepad.exe"}, events.append)
    watcher.start()
    try:
        subprocess.run([PYTHONW, "-c", SILENCE], timeout=20)
        time.sleep(1)
    finally:
        watcher.stop()
    assert events == []
