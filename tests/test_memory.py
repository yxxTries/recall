"""Phase 3 gate: chunking, the store, golden semantic queries, capture-to-searchable latency."""
import time
import uuid

import numpy as np
import pytest

from recall.capture.text_uia import TextCapture
from recall.memory.chunker import chunk_event, chunk_text
from recall.memory.embed import DIMENSIONS, Embedder
from recall.memory.store import MemoryStore
from recall.memory.worker import MemoryWorker
from recall.watcher import ForegroundWatcher, event
from tests.helpers import EDIT_WINDOW, PYTHONW, edit_controls, focus, open_window, post_text, wait_for


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.db", device_id="test-device")


def one_hot(i: int) -> np.ndarray:
    v = np.zeros(DIMENSIONS, dtype=np.float32)
    v[i] = 1.0
    return v


def test_chunks_split_on_lines_with_one_line_overlap():
    lines = [f"line {i} " + "x" * 90 for i in range(30)]  # ~100 chars each
    chunks = chunk_text("\n".join(lines), max_chars=500)
    assert all(len(c) <= 600 for c in chunks)
    assert chunks[1].splitlines()[0] == chunks[0].splitlines()[-1]
    assert chunk_text("word " * 1000, max_chars=500)[0].startswith("word")  # one huge line gets split too


def test_chunk_event_carries_metadata():
    e = event("text", app="msedge.exe", title="Docs", url="example.com", text="Hello world")
    [c] = chunk_event(e)
    assert (c["app"], c["title"], c["url"], c["source"], c["text"]) == ("msedge.exe", "Docs", "example.com", "text", "Hello world")


def test_store_dedupes_and_ranks_by_vector_and_keyword(store):
    chunks = [chunk_event(event("text", app="a.exe", text=t))[0] for t in ("alpha report", "beta notes", "gamma plan")]
    assert store.add(chunks, [one_hot(0), one_hot(1), one_hot(2)]) == 3
    assert store.new_chunks(chunks) == []
    assert store.add(chunks, [one_hot(0), one_hot(1), one_hot(2)]) == 0
    assert store.count() == 3
    assert store.search(one_hot(1), "")[0]["text"] == "beta notes"  # vector only
    assert store.search(one_hot(0), "gamma")[0]["text"] in ("alpha report", "gamma plan")
    assert store.search(one_hot(0), "gamma", app="b.exe") == []


GOLDEN = [
    ("The quarterly sales review moved to Thursday at 3pm in the Oak conference room.",
     "when is the meeting about revenue numbers"),
    ("Preheat the oven to 220C, roast the chickpeas with smoked paprika for 25 minutes.",
     "recipe for crispy baked legumes"),
    ("Flight BA117 from Heathrow to JFK departs at 08:40, gate closes 20 minutes earlier.",
     "what time does my plane to New York leave"),
    ("The React useEffect hook runs after render; return a cleanup function to unsubscribe.",
     "how to clean up side effects in a frontend component"),
    ("Our Postgres replica lagged 40 seconds behind the primary during the backup window.",
     "database replication delay incident"),
    ("Maya prefers async standups posted in the team channel before 10am.",
     "how does my colleague like to do daily status updates"),
    ("The lease renewal requires 60 days notice and the rent increases by 4 percent.",
     "apartment contract terms and price rise"),
    ("Photosynthesis converts light energy into chemical energy stored as glucose.",
     "how plants make food from sunlight"),
    ("Run pytest with -k to select tests whose names match an expression.",
     "filter which unit tests execute by name"),
    ("The hackathon judging criteria are impact, technical depth, and demo quality.",
     "how will the competition projects be scored"),
    ("Replace the air filter in the car every 15,000 miles or once a year.",
     "vehicle maintenance schedule for filters"),
    ("Invoice 4471 for the design agency is due on the 30th, net 30 terms.",
     "when do we need to pay the creative contractor"),
    ("Rust's borrow checker prevents data races by enforcing ownership rules at compile time.",
     "memory safety guarantees in a systems programming language"),
    ("The museum is closed on Mondays; tickets are cheaper after 5pm on Fridays.",
     "discounted entry times for the gallery"),
    ("Mom's birthday dinner is at Luigi's on Saturday, bring the photo album.",
     "family celebration plans this weekend"),
    ("Transformer attention scales quadratically with sequence length.",
     "why long inputs are expensive for language models"),
    ("The gym offers a free trial week and yoga classes on Tuesday evenings.",
     "fitness center stretching sessions schedule"),
    ("Set the thermostat to 19 degrees at night to cut the heating bill.",
     "saving money on home energy costs"),
    ("Kubernetes restarts a pod when its liveness probe fails three times in a row.",
     "what happens when a container health check keeps failing"),
    ("The novel follows a lighthouse keeper who finds letters from a shipwreck.",
     "book plot about a coastal tower and old messages"),
]


def test_golden_paraphrased_queries_hit_top_3(store, embedder):
    events = [event("text", app="notes.exe", title="Notes", text=snippet) for snippet, _ in GOLDEN]
    chunks = [c for e in events for c in chunk_event(e)]
    store.add(chunks, embedder.passages([f"{c['title']}\n{c['text']}" for c in chunks]))
    misses = []
    for snippet, query in GOLDEN:
        top3 = [r["text"] for r in store.search(embedder.query(query), query, k=3)]
        if snippet not in top3:
            misses.append(query)
    print(f"golden queries: {len(GOLDEN) - len(misses)}/{len(GOLDEN)} in top 3; misses: {misses}")
    assert len(GOLDEN) - len(misses) >= 18


def test_typed_text_is_searchable_within_5s(tmp_path, embedder):
    store = MemoryStore(tmp_path / "memory.db", device_id="test-device")
    worker = MemoryWorker(store, embedder)
    capture = TextCapture(worker.submit)

    def on_event(e):
        if e["type"] == "session_start":
            capture.set_session(e)
        elif e["type"] == "session_end":
            capture.set_session(None)

    watcher = ForegroundWatcher({"pythonw.exe"}, on_event, on_content=capture.content_changed)
    sentence = f"The submarine cable repair crew sails from Lisbon {uuid.uuid4().hex[:6]}"
    embedder.passages(["warm up"])  # model load is a one-time startup cost, not capture latency
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
        query = "undersea internet line maintenance ship"

        def found():
            return any(sentence in r["text"] for r in store.search(embedder.query(query), query, k=3))

        assert wait_for(found, timeout=5), "not searchable within 5 s"
        print(f"capture to searchable: {time.monotonic() - typed_at:.2f} s")
    finally:
        watcher.stop()
        capture.stop()
        worker.stop()
        proc.kill()
