"""Phase 2 gate: text typed into a tracked native window is captured once; passwords never are."""
import subprocess
import time
import uuid
from pathlib import Path

import psutil
import pytest

from recall.capture import text_uia
from recall.capture.text_uia import TAIL_BUDGET_S, TAIL_CHARS, TextCapture, WindowReader, clean_lines
from recall.watcher import ForegroundWatcher
from tests.helpers import EDIT_WINDOW, PYTHONW, edit_controls, focus, open_window, post_text, user32, wait_for

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def test_clean_lines_normalises_and_dedupes():
    text = "  Hello   world \n￼\n\n--\nHello world\nx\nSecond line!"
    assert clean_lines(text) == ["Hello world", "Second line!"]


class FakeReader:
    def __init__(self, text: str) -> None:
        self.text = text

    def read(self, hwnd, app):
        return self.text, ""


def test_first_read_of_a_chat_sends_only_its_latest_lines():
    history = "\n".join(f"message {i} about the release" for i in range(500))
    for app, expected in (("ms-teams.exe", range(420, 500)), ("chrome.exe", range(500))):
        texts = []
        capture = TextCapture(texts.append)
        session = {"hwnd": 1, "app": app, "title": "Release planning"}
        capture._snapshot(FakeReader(history), session)
        assert texts[0]["text"].splitlines() == [f"message {i} about the release" for i in expected], app
        capture._snapshot(FakeReader(history + "\nmessage 500 about the release"), session)
        assert texts[1]["text"] == "message 500 about the release"  # the skipped history counts as seen


class LiveReader:
    """A window whose text the test changes; counts reads."""

    pages: dict[int, str] = {}
    reads: list[int] = []

    def read(self, hwnd, app):
        LiveReader.reads.append(hwnd)
        return LiveReader.pages[hwnd], ""


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(text_uia, "WindowReader", LiveReader)
    monkeypatch.setattr(text_uia, "DEBOUNCE_S", 0.3)
    LiveReader.pages, LiveReader.reads = {1: "Dana: the upgrade is on Saturday", 2: "Budget sheet"}, []
    texts = []
    capture = TextCapture(texts.append)
    capture.start()
    yield capture, texts
    capture.stop()


def test_leaving_a_window_reads_what_changed_since_its_last_read(live):
    capture, texts = live
    chat = {"hwnd": 1, "app": "slack.exe", "title": "#platform"}
    capture.set_session(chat)
    assert wait_for(lambda: texts, timeout=2)
    LiveReader.pages[1] += "\nYou: I'll review the runbook Thursday"
    capture.content_changed(chat)
    capture.set_session(None)  # switched away before the debounce ran out: the message you just sent is still read
    assert wait_for(lambda: len(texts) == 2, timeout=0.2)
    assert texts[1]["text"] == "You: I'll review the runbook Thursday"


def test_windows_passed_through_are_not_read(live):
    capture, texts = live
    capture.set_session({"hwnd": 1, "app": "slack.exe", "title": "#platform"})
    capture.set_session({"hwnd": 2, "app": "excel.exe", "title": "Budget"})  # alt-tabbed through the chat
    assert wait_for(lambda: texts, timeout=2)
    time.sleep(0.5)
    assert LiveReader.reads == [2]


@pytest.mark.integration
@pytest.mark.skipif(not CHROME.exists(), reason="needs Chrome")
def test_a_long_chat_is_read_from_its_end_in_small_steps(tmp_path):
    """A chat over 500K characters (a long Claude Code day): the newest messages are at the end, past any cap
    read from the top. Chromium renders VS Code's chat panels, so Chrome stands in for them."""
    page = tmp_path / "chat.html"
    page.write_text("<title>recall-test-long-chat</title>" + "".join(
        f"<p>message {i} about the <b>release</b> plan, see <code>deploy_{i}.py</code></p>" for i in range(15_000)),
        encoding="utf-8")
    profile = str(tmp_path / "profile")
    subprocess.Popen([str(CHROME), f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                      "--new-window", page.as_uri()])
    try:
        assert wait_for(lambda: user32.FindWindowW(None, "recall-test-long-chat - Google Chrome"), timeout=20)
        reader = WindowReader()
        root = reader.uia.ElementFromHandle(user32.FindWindowW(None, "recall-test-long-chat - Google Chrome"))
        assert wait_for(lambda: bool(root.FindFirst(reader.U.TreeScope_Descendants, reader.document)), timeout=20)
        doc = root.FindFirst(reader.U.TreeScope_Descendants, reader.document)
        pattern = doc.GetCurrentPattern(reader.U.UIA_TextPatternId).QueryInterface(reader.U.IUIAutomationTextPattern)
        assert wait_for(lambda: "message 14999" in pattern.DocumentRange.GetText(-1)[-100:], timeout=20)

        started = time.perf_counter()
        tail = clean_lines(reader._tail(doc, pattern))
        elapsed = time.perf_counter() - started
    finally:
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if p.info["name"] == "chrome.exe" and any(profile in a for a in p.info["cmdline"] or []):
                    p.kill()
            except psutil.Error:
                pass
    assert tail[-2:] == ["message 14998 about the release plan, see deploy_14998.py",
                         "message 14999 about the release plan, see deploy_14999.py"]  # whole lines, bold and code kept
    assert len(tail) >= 50 and sum(len(line) for line in tail) < 2 * TAIL_CHARS
    assert elapsed < TAIL_BUDGET_S + 0.5, f"took {elapsed:.2f} s"
    print(f"tail read: {len(tail)} lines in {elapsed * 1000:.0f} ms")


EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


@pytest.mark.integration
@pytest.mark.skipif(not EDGE.exists(), reason="needs Edge")
def test_a_browser_is_read_for_its_page_never_its_own_ui(tmp_path):
    """Before a browser's first read builds its page tree, only its own UI is there: a fresh Edge profile shows its
    sync dialog (with the signed-in email) and, without any document, the tree holds tabs and toolbar buttons."""
    page = tmp_path / "article.html"
    page.write_text("<title>recall-test-article</title><h1>Idempotent webhooks</h1>" +
                    "".join(f"<p>Note {i}: record each event id and skip ones already seen.</p>" for i in range(20)),
                    encoding="utf-8")
    profile = str(tmp_path / "profile")
    subprocess.Popen([str(EDGE), f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                      "--new-window", page.as_uri()])
    reads = []
    try:
        assert wait_for(lambda: user32.FindWindowW(None, "recall-test-article - Profile 1 - Microsoft\u200b Edge") or
                        user32.FindWindowW(None, "recall-test-article - Microsoft\u200b Edge"), timeout=20)
        hwnd = user32.FindWindowW(None, "recall-test-article - Profile 1 - Microsoft\u200b Edge") or \
            user32.FindWindowW(None, "recall-test-article - Microsoft\u200b Edge")
        time.sleep(2)
        reader = WindowReader()
        for _ in range(4):  # the first read builds the page tree; the events that follow bring the next read
            reads.append(clean_lines(reader.read(hwnd, "msedge.exe")[0]))
            time.sleep(1.5)
    finally:
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                if p.info["name"] == "msedge.exe" and any(profile in a for a in p.info["cmdline"] or []):
                    p.kill()
            except psutil.Error:
                pass
    for lines in reads:
        assert not lines or lines[0] == "Idempotent webhooks", lines[:5]  # the page, or nothing yet
    assert "Note 19: record each event id and skip ones already seen." in reads[-1]


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
