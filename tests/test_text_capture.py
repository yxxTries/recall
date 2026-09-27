"""Phase 2 gate: text typed into a tracked native window is captured once; passwords never are."""
import time
import uuid

import pytest

from recall.capture.text_uia import TextCapture, clean_lines
from recall.watcher import ForegroundWatcher
from tests.helpers import EDIT_WINDOW, PYTHONW, edit_controls, focus, open_window, post_text, wait_for


def test_clean_lines_normalises_and_dedupes():
    text = "  Hello   world \n￼\n\n--\nHello world\nx\nSecond line!"
    assert clean_lines(text) == ["Hello world", "Second line!"]


class Pipeline:
    """ForegroundWatcher feeding TextCapture, as main.py wires them."""

    def __init__(self, tracked) -> None:
        self.texts = []
        self.capture = TextCapture(self.texts.append)
        self.watcher = ForegroundWatcher(tracked, self.on_event, on_content=self.capture.content_changed)

    def on_event(self, e) -> None:
        if e["type"] == "session_start":
            self.capture.set_session(e)
        elif e["type"] == "session_end":
            self.capture.set_session(None)

    def __enter__(self):
        self.capture.start()
        self.watcher.start()
        return self

    def __exit__(self, *exc):
        self.watcher.stop()
        self.capture.stop()

    def captured(self, needle: str) -> list[dict]:
        return [t for t in self.texts if needle in t["text"]]


@pytest.mark.integration
def test_typed_text_is_captured_once_within_3s():
    first = f"The launch codename is {uuid.uuid4().hex[:8]}"
    second = f"Budget review moved to {uuid.uuid4().hex[:8]}"
    proc, hwnd = open_window(PYTHONW, "recall-test-typing", EDIT_WINDOW)
    text_box, _ = edit_controls(hwnd)
    try:
        focus(hwnd)
        with Pipeline({"pythonw.exe"}) as p:
            time.sleep(2)  # let the session's first read happen
            post_text(text_box, first + "\r\n")
            typed_at = time.monotonic()
            assert wait_for(lambda: p.captured(first), timeout=3), "not captured within 3 s"
            latency = time.monotonic() - typed_at

            post_text(text_box, second + "\r\n")  # more edits must not re-emit the first line
            assert wait_for(lambda: p.captured(second), timeout=3)
    finally:
        proc.kill()

    assert len(p.captured(first)) == 1
    event = p.captured(first)[0]
    assert event["app"] == "pythonw.exe" and event["title"] == "recall-test-typing"
    print(f"capture latency {latency:.2f} s, read {event['read_ms']} ms")


@pytest.mark.integration
def test_password_text_is_never_captured():
    secret = f"hunter2-{uuid.uuid4().hex[:8]}"
    visible = f"Visible note {uuid.uuid4().hex[:8]}"
    proc, hwnd = open_window(PYTHONW, "recall-test-password", EDIT_WINDOW)
    text_box, password_box = edit_controls(hwnd)
    try:
        focus(hwnd)
        with Pipeline({"pythonw.exe"}) as p:
            post_text(password_box, secret)
            post_text(text_box, visible)
            assert wait_for(lambda: p.captured(visible), timeout=4)
    finally:
        proc.kill()
    assert not any(secret in t["text"] for t in p.texts)


@pytest.mark.integration
def test_untracked_window_is_never_read():
    note = f"Untracked note {uuid.uuid4().hex[:8]}"
    proc, hwnd = open_window(PYTHONW, "recall-test-untracked", EDIT_WINDOW)
    text_box, _ = edit_controls(hwnd)
    try:
        focus(hwnd)
        with Pipeline({"notepad.exe"}) as p:
            post_text(text_box, note)
            time.sleep(2.5)
    finally:
        proc.kill()
    assert p.texts == []
