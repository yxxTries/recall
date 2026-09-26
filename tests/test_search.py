"""Phase 4: the search window's API, filters and helpers."""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from recall.memory.embed import Embedder
from recall.memory.store import MemoryStore
from recall.ui.search import SearchApi, openable, since_for

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    subprocess.run([sys.executable, str(REPO / "scripts" / "seed_demo.py")], check=True,
                   env={**os.environ, "RECALL_HOME": str(home)})
    store = MemoryStore(home / "memory.db", device_id="test-device")
    return SearchApi(store, Embedder())


def test_paraphrase_finds_the_demo_memory(api):
    top = api.search("that article about storing embeddings for similarity search")[0]
    assert top["app"] == "msedge.exe" and "vector database" in top["text"]
    assert set(top) == {"text", "app", "title", "url", "source", "time"}


def test_heard_audio_is_searchable(api):
    top = api.search("which suppliers are we choosing between")[0]
    assert top["source"] == "audio" and "Globex" in top["text"]


def test_filters(api):
    assert all(r["app"] == "discord.exe" for r in api.search("password", app="discord.exe"))
    assert not any("Lease" in r["title"] for r in api.search("rent increase", time_range="today"))
    assert "discord.exe" in api.apps()


def test_empty_query_lists_recent_first(api):
    recent = api.search("")
    assert recent[0]["title"].startswith("Vector databases")
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
