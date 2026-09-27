"""Sends closed episodes to the cloud: redact, gzip, keep in a local outbox, upload every 60 s.

Batches wait in the outbox (SQLite) until the cloud accepts them, so going offline or quitting
loses nothing; the cloud skips episodes it already has, so a batch sent twice is harmless.
While uploads fail, the wait doubles up to 10 minutes. The outbox is bounded: oldest batches go first.
gzip rather than zstd: it compressed our captures as well (3.9x vs 3.8x), is in Python's standard
library, and Edge Functions decompress it natively.
"""
import gzip
import json
import logging
import sqlite3
import threading
import uuid
from datetime import date, datetime

from recall.sync.cloud import CloudError, CloudSession
from recall.sync.redact import Vault, redact
from recall.sync.segment import Segmenter
from recall.sync.sketch import WordRarity

log = logging.getLogger(__name__)
INTERVAL, MAX_WAIT = 60, 600
MAX_BATCHES = 2000


def aware(t: str) -> str:
    """Device times are local and naive; the cloud needs the offset."""
    return datetime.fromisoformat(t).astimezone().isoformat(timespec="seconds")


def prepare(episode: dict, vault: Vault) -> dict:
    """Redacted, with timezone-aware times: all that leaves the device."""
    spans = [{"app": s["app"], "title": redact(s["title"], vault), "url": redact(s["url"], vault),
              "started": aware(s["started"]), "ended": aware(s["ended"])} for s in episode["spans"]]
    return {"episode_id": episode["episode_id"], "started": aware(episode["started"]),
            "ended": aware(episode["ended"]), "text": redact(episode["text"], vault), "spans": spans}


class Outbox:
    def __init__(self, path) -> None:
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists outbox (seq integer primary key, batch_id text unique, body blob)")

    def put(self, body: bytes) -> None:
        with self._lock, self.db:
            self.db.execute("insert into outbox (batch_id, body) values (?, ?)", (uuid.uuid4().hex, body))
            self.db.execute("delete from outbox where seq <= (select max(seq) from outbox) - ?", (MAX_BATCHES,))

    def oldest(self) -> tuple[int, bytes] | None:
        with self._lock:
            return self.db.execute("select seq, body from outbox order by seq limit 1").fetchone()

    def remove(self, seq: int) -> None:
        with self._lock, self.db:
            self.db.execute("delete from outbox where seq = ?", (seq,))

    def count(self) -> int:
        with self._lock:
            return self.db.execute("select count(*) from outbox").fetchone()[0]


class SyncWorker:
    """Feeds capture events to the segmenter and uploads closed episodes on its own timer thread."""

    def __init__(self, folder, cloud: CloudSession, device_id: str, device_name: str = "") -> None:
        self.cloud, self.device_id, self.device_name = cloud, device_id, device_name
        self.rarity_path = folder / "rarity.npz"
        self.segmenter = Segmenter(WordRarity.load(self.rarity_path), device_id=device_id)
        self.vault = Vault(folder / "vault.db")
        self.outbox = Outbox(folder / "outbox.db")
        self._lock = threading.Lock()  # the segmenter is fed by the memory worker and flushed by this thread
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="sync", daemon=True)
        self._wait = INTERVAL
        self._day = date.today()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        with self._lock:
            self._queue(self.segmenter.close())
            self.segmenter.rarity.save(self.rarity_path)

    def add(self, events: list[dict]) -> None:
        with self._lock:
            for e in events:
                self._queue(self.segmenter.add(e))

    def _queue(self, episodes: list[dict]) -> None:
        if episodes:
            batch = {"device_id": self.device_id, "device_name": self.device_name,
                     "episodes": [prepare(ep, self.vault) for ep in episodes]}
            self.outbox.put(gzip.compress(json.dumps(batch).encode()))
            log.info("queued %d episodes for upload", len(episodes))

    def tick(self) -> bool:
        """Close idle episodes, then upload the outbox oldest first; False if the cloud couldn't be reached."""
        with self._lock:
            self._queue(self.segmenter.flush(datetime.now()))
            if date.today() != self._day:  # word counts halve daily, so the last week decides what's common
                self._day = date.today()
                self.segmenter.rarity.decay()
                self.segmenter.rarity.save(self.rarity_path)
        if not self.cloud.signed_in:
            return True
        while (item := self.outbox.oldest()) is not None:
            seq, body = item
            try:
                result = self.cloud.call("ingest", body, {"x-recall-encoding": "gzip"})
            except CloudError as e:
                if e.status in (0, 401, 408, 429) or e.status >= 500:
                    log.warning("upload failed, will retry: %s", e)
                    return False
                log.error("upload rejected, dropping the batch: %s", e)  # a bad batch mustn't block the rest
            else:
                log.info("uploaded %s episodes", result.get("accepted"))
            self.outbox.remove(seq)
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._wait):
            try:
                ok = self.tick()
            except Exception:
                log.exception("sync failed")
                ok = False
            self._wait = INTERVAL if ok else min(self._wait * 2, MAX_WAIT)
