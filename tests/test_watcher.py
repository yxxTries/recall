"""Phase 1 gate: tracking events from real windows and a real (silent) audio stream.

These tests briefly take the foreground; don't type while they run.
"""
import subprocess
import time

import pytest

from recall.watcher import AudioWatcher, ForegroundWatcher
from tests.helpers import PYTHON, PYTHONW, focus, open_window, wait_for

pytestmark = pytest.mark.integration

SILENCE = (
    "import io, wave, winsound; b = io.BytesIO(); w = wave.open(b, 'wb'); "
    "w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(64000)); w.close(); "
    "winsound.PlaySound(b.getvalue(), winsound.SND_MEMORY)"
)


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
