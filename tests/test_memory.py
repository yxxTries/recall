"""Phase 3.1: activity memory built by rules, the keyword store, capture-to-searchable latency."""
import time
import uuid
from datetime import datetime, timedelta

import pytest

from recall.capture.text_uia import TextCapture
from recall.memory.activity import MAX_CANDIDATES, ActivityTracker, clean_title, describe
from recall.memory.store import MemoryStore
from recall.memory.worker import MemoryWorker
from recall.watcher import ForegroundWatcher, event
from tests.helpers import EDIT_WINDOW, PYTHONW, edit_controls, focus, open_window, post_text, wait_for

T0 = datetime(2026, 9, 26, 14, 0)


def ev(kind: str, seconds: float, **fields) -> dict:
    return {"type": kind, "time": (T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds"), **fields}


def edit(seconds, file, text, workspace="recall"):
    return ev("text", seconds, app="code.exe", title=f"{file} - {workspace}", text=text,
              url=f"vscode://file/C:/dev/{workspace}/{file}:1")


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.db", device_id="test-device")


def test_clean_title_drops_the_app_name():
    assert clean_title("Weekly sync | Microsoft Teams", "ms-teams.exe") == "Weekly sync"
    assert clean_title("#general | Hack Atlantic 2026 - Discord", "discord.exe") == "#general | Hack Atlantic 2026"
    assert clean_title("Vector DBs - Pragmatic Engineer and 2 more pages - Personal - Microsoft\u200b Edge",
                       "msedge.exe") == "Vector DBs - Pragmatic Engineer"
    assert clean_title("store.py - recall - Visual Studio Code", "code.exe") == "store.py - recall"
    assert clean_title("Daily Mix 1 - Spotify", "spotify.exe") == "Daily Mix 1"


def test_subjects_are_workspaces_sites_and_windows():
    assert describe(edit(0, "recall/ui/search.py", "x")) == ("recall", "search.py")
    chat = ev("text", 0, app="code.exe", title="BUILDPLAN.md - recall - Visual Studio Code", url="", text="x")
    assert describe(chat) == ("recall", "AI chat")  # UIA reads only VS Code's chat panels
    assert describe(ev("session_start", 0, app="code.exe", title="BUILDPLAN.md - recall - Visual Studio Code")) \
        == ("recall", "BUILDPLAN.md")
    page = ev("text", 0, app="msedge.exe", title="Docs - Personal - Microsoft Edge", url="www.example.com/a", text="x")
    assert describe(page) == ("example.com", "Docs")
    pdf = ev("text", 0, app="chrome.exe", title="lease.pdf - Google Chrome", url="C:/Users/me/lease.pdf", text="x")
    assert describe(pdf) == ("lease.pdf", "lease.pdf")  # a local file has no site: each one is its own subject
    assert describe(ev("session_start", 0, app="notepad.exe", title="todo.txt - Notepad")) == ("todo.txt", "todo.txt")


def test_activity_survives_app_switches_and_ends_after_5_quiet_minutes():
    tracker = ActivityTracker()
    first = tracker.add(edit(0, "store.py", "def upsert(self, record): store one activity"))
    tracker.add(ev("text", 60, app="notepad.exe", title="todo.txt - Notepad", text="Book dentist for Tuesday"))
    same = tracker.add(edit(240, "hotkey.py", "Candidate hotkeys are tried in order"))
    assert same["activity_id"] == first["activity_id"]
    assert same["summary"] == "Worked on recall in VS Code (store.py, hotkey.py)"
    assert (same["started"], same["ended"]) == ("2026-09-26T14:00:00", "2026-09-26T14:04:00")
    later = tracker.add(edit(240 + 301, "store.py", "def search(self, query): find by words"))
    assert later["activity_id"] != first["activity_id"]


def test_meeting_names_people_and_keeps_key_lines():
    tracker = ActivityTracker()
    lines = ["Sarah: the vendor shortlist is Acme, Globex and Initech, we decide by Friday.",
             "Dev: Globex was cheapest but their support contract only covers weekdays.",
             "Sarah: let's ask Initech for a trial licence before the decision.",
             "ok", "Dev: sounds good"]
    record = tracker.add(ev("text", 0, app="ms-teams.exe", title="Weekly sync | Microsoft Teams", source="audio",
                            text="\n".join(lines)))
    assert record["summary"] == "Meeting: Weekly sync in Teams with Sarah, Dev"
    assert record["source"] == "audio" and record["people"] == "Sarah, Dev"
    assert "vendor shortlist" in record["key_lines"] and "ok" not in record["key_lines"].splitlines()


def test_a_huge_capture_becomes_one_small_record():
    tracker = ActivityTracker()
    chat = "\n".join(f"Message {i}: discussing the hotkey fallback and vector search plan, step {i}" for i in range(1600))
    record = tracker.add(ev("text", 0, app="code.exe", title="BUILDPLAN.md - recall - Visual Studio Code",
                            url="", text=chat))
    assert record["summary"] == "Worked on recall in VS Code (AI chat)"
    assert len(record["key_lines"].splitlines()) == 5
    assert sum(len(v) for v in record.values()) < 2000
    assert len(tracker._open[("code.exe", "recall")].candidates) == MAX_CANDIDATES


def test_brief_visits_are_not_activities_but_time_spent_is():
    tracker = ActivityTracker()
    player = {"app": "spotify.exe", "title": "Daily Mix 1 - Spotify"}
    assert tracker.add(ev("session_start", 0, **player)) is None
    assert tracker.add(ev("session_end", 3, **player)) is None  # alt-tabbed through
    record = tracker.add(ev("session_end", 40, **player))
    assert record["summary"] == "Used Spotify: Daily Mix 1" and record["key_lines"] == ""


def test_browser_focus_extends_the_site_activity():
    tracker = ActivityTracker()
    page = {"app": "msedge.exe", "title": "Vector DBs - Personal - Microsoft Edge"}
    first = tracker.add(ev("text", 0, url="example.com/vectors", text="A vector database stores embeddings", **page))
    extended = tracker.add(ev("session_end", 180, **page))
    assert extended["activity_id"] == first["activity_id"] and extended["ended"] == "2026-09-26T14:03:00"
    assert tracker.add(ev("session_start", 200, app="msedge.exe", title="Unknown page - Microsoft Edge")) is None


def test_store_updates_activities_in_place_and_finds_them_by_words(store):
    tracker = ActivityTracker()
    store.upsert(tracker.add(ev("text", 0, app="ms-teams.exe", title="Weekly sync | Microsoft Teams",
                                text="Sarah: the vendor shortlist is Acme and Globex")))
    store.db.execute("update activities set synced = 1")
    store.upsert(tracker.add(ev("text", 60, app="ms-teams.exe", title="Weekly sync | Microsoft Teams",
                                text="Dev: Initech offered a trial licence")))
    store.upsert(tracker.add(edit(120, "store.py", "def upsert(self, record): store one activity")))
    assert store.count() == 2
    [meeting] = store.search("Initech trial")
    assert meeting["people"] == "Sarah, Dev" and meeting["synced"] == 0  # changed, so due for sync again
    assert store.search("Sarah")[0]["app"] == "ms-teams.exe"
    assert store.search("Initech", app="code.exe") == []
    assert store.search("Initech", since="2026-09-27T00:00:00") == []
    assert [r["app"] for r in store.recent()] == ["code.exe", "ms-teams.exe"]
    assert store.apps() == ["code.exe", "ms-teams.exe"]
    assert store.search("the of and") == []


@pytest.mark.integration
def test_typed_text_is_searchable_within_5s(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", device_id="test-device")
    worker = MemoryWorker(store)
    capture = TextCapture(worker.submit)

    def on_event(e):
        if e["type"] == "session_start":
            capture.set_session(e)
        elif e["type"] == "session_end":
            capture.set_session(None)
        worker.submit(e)

    watcher = ForegroundWatcher({"pythonw.exe"}, on_event, on_content=capture.content_changed)
    token = uuid.uuid4().hex[:8]
    sentence = f"The submarine cable repair crew sails from Lisbon {token}"
    proc, hwnd = open_window(PYTHONW, "recall-test-memory", EDIT_WINDOW)
    text_box, _ = edit_controls(hwnd)
    worker.start()
    capture.start()
    try:
        focus(hwnd)
        watcher.start()
        time.sleep(2)
        post_text(text_box, sentence)
        typed_at = time.monotonic()

        def found():
            return any(sentence in r["key_lines"] for r in store.search(f"submarine cable {token}"))

        assert wait_for(found, timeout=5), "not searchable within 5 s"
        print(f"capture to searchable: {time.monotonic() - typed_at:.2f} s")
        [record] = store.search(token)
        assert record["summary"] == "Used Pythonw: recall-test-memory"
    finally:
        watcher.stop()
        capture.stop()
        worker.stop()
        proc.kill()
