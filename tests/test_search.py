"""Phase 4: the search window's API, filters and helpers."""
import json
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


class FakeCloud:
    signed_in = True

    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def call(self, function, body, headers=None, timeout=60):
        from recall.sync.cloud import CloudError
        if self.fail:
            raise CloudError(0, "offline")
        self.calls.append((function, json.loads(body)))
        return {"results": [{"episode_id": "e1", "worked_on": "Chose Contoso as hackathon sponsor", "important": ["Tell Fabrikam by Thursday"],
                             "evidence": [], "apps": ["ms-teams.exe"], "started": "2026-09-27T13:05:00+00:00",
                             "ended": "2026-09-27T13:19:00+00:00"}]}


def test_all_devices_searches_the_cloud_and_falls_back_offline(api):
    cloud = FakeCloud()
    api._cloud = cloud
    [hit] = api.search("which sponsor did we pick", time_range="any", where="all")
    assert hit == {"title": "Chose Contoso as hackathon sponsor", "text": "Tell Fabrikam by Thursday",
                   "app": "ms-teams.exe", "url": "", "source": "cloud", "start": "2026-09-27T13:05:00+00:00",
                   "time": "2026-09-27T13:19:00+00:00", "link": "https://recall-memory-yxxtries.vercel.app/#e1"}
    assert cloud.calls == [("search", {"query": "which sponsor did we pick", "since": None, "k": 20})]
    assert api.search("which sponsor did we pick", app="code.exe", where="all") == []  # app filter applies
    api._cloud = FakeCloud(fail=True)
    assert api.search("vector database", where="all") == api.search("vector database")  # offline: this device
    api._cloud = None
    assert not api.cloud() and api.search("vector database", where="all") == api.search("vector database")
