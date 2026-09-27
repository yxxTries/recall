"""Phase 4: the search window's API, filters and helpers."""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from recall.memory.store import MemoryStore
from recall.ui.search import SearchApi, openable, since_for

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    subprocess.run([sys.executable, str(REPO / "scripts" / "seed_demo.py")], check=True,
                   env={**os.environ, "RECALL_HOME": str(home)})
    store = MemoryStore(home / "memory.db", device_id="test-device")
    return SearchApi(store)


def test_words_find_the_demo_activity(api):
    top = api.search("vector database")[0]
    assert top["app"] == "msedge.exe" and "vector database" in top["text"]
    assert top["title"].startswith("Read blog.pragmaticengineer.com in Edge")
    assert set(top) == {"title", "text", "app", "url", "source", "start", "time"}


def test_heard_meeting_is_searchable_by_person(api):
    top = api.search("Sarah vendor shortlist")[0]
    assert top["source"] == "audio" and "Globex" in top["text"]
    assert top["title"] == "Meeting: Weekly sync in Teams with Sarah, Dev"


def test_editor_work_is_one_activity_per_workspace(api):
    [work] = [r for r in api.search("") if r["app"] == "code.exe"]
    assert work["title"] == "Worked on recall in VS Code (store.py, hotkey.py)"
    assert work["url"].startswith("vscode://file/") and work["start"] < work["time"]


def test_filters(api):
    assert all(r["app"] == "discord.exe" for r in api.search("password", app="discord.exe"))
    assert not any("oakwood" in r["title"] for r in api.search("rent increases", time_range="today"))
    assert "discord.exe" in api.apps()


def test_empty_query_lists_recent_first(api):
    recent = api.search("")
    assert recent[0]["title"].startswith("Read blog.pragmaticengineer.com")
    assert [r["time"] for r in recent] == sorted((r["time"] for r in recent), reverse=True)


def test_since_for():
    now = datetime(2026, 9, 26, 15, 30)
    assert since_for("today", now) == "2026-09-26T00:00:00"
    assert since_for("week", now) == "2026-09-19T15:30:00"
    assert since_for("any", now) is None


def test_openable_urls():
    assert openable("youtube.com/watch?v=x") == "https://youtube.com/watch?v=x"
    assert openable("https://example.com") == "https://example.com"
    assert openable("C:/Users/me/page.html") == "file:///C:/Users/me/page.html"
    assert openable("vscode://file/C:/dev/recall/store.py:40") == "vscode://file/C:/dev/recall/store.py:40"
