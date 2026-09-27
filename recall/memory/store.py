"""The local memory: one SQLite file of activity records with a keyword index (FTS5).

Search by meaning happens in the cloud (Phase 7); locally, activities are found by their words.
"""
import re
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
create table if not exists activities (
    id integer primary key,
    activity_id text not null unique,
    app text not null,
    subject text not null,
    summary text not null,
    items text not null default '',
    people text not null default '',
    terms text not null default '',
    key_lines text not null default '',
    url text not null default '',
    source text not null,
    started text not null,
    ended text not null,
    device_id text not null,
    synced integer not null default 0
);
create index if not exists activities_ended on activities(ended);
create virtual table if not exists activity_words using fts5(
    summary, items, people, terms, key_lines, tokenize='porter unicode61'
);
"""
FIELDS = ("activity_id", "app", "subject", "summary", "items", "people", "terms", "key_lines", "url", "source",
          "started", "ended")
WORD_FIELDS = ("summary", "items", "people", "terms", "key_lines")
STOPWORDS = set(
    "a an and are as at be but by for from has have i in is it its me my of on or so that the this to was "
    "were what when where which who why with you your about did do does how".split()
)


def fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w+", text.lower()) if w not in STOPWORDS]
    return " OR ".join(f'"{w}"' for w in words)


class MemoryStore:
    def __init__(self, path: Path, device_id: str) -> None:
        self.device_id = device_id
        self._lock = threading.Lock()  # one connection shared by the worker and the search UI
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)

    def upsert(self, record: dict) -> None:
        """Store an activity, or update it as it grows; it's searchable at once and due for sync again."""
        values = {key: record[key] for key in FIELDS} | {"device_id": record.get("device_id", self.device_id)}
        with self._lock, self.db:
            rowid = self.db.execute(
                f"insert into activities ({', '.join(values)}) values ({', '.join(':' + k for k in values)}) "
                f"on conflict(activity_id) do update set {', '.join(f'{k} = excluded.{k}' for k in FIELDS[1:])}, "
                "synced = 0 returning id",
                values,
            ).fetchone()[0]
            self.db.execute("delete from activity_words where rowid = ?", (rowid,))
            self.db.execute(f"insert into activity_words (rowid, {', '.join(WORD_FIELDS)}) values (?, ?, ?, ?, ?, ?)",
                            (rowid, *(values[k] for k in WORD_FIELDS)))

    def count(self) -> int:
        with self._lock:
            return self.db.execute("select count(*) from activities").fetchone()[0]

    def apps(self) -> list[str]:
        with self._lock:
            return [row[0] for row in self.db.execute("select distinct app from activities order by app")]

    def recent(self, k: int = 20, app: str | None = None, since: str | None = None) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "select * from activities where (:app is null or app = :app) and (:since is null or ended >= :since) "
                "order by ended desc, id desc limit :k",
                {"app": app, "since": since, "k": k},
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, query: str, k: int = 20, app: str | None = None, since: str | None = None) -> list[dict]:
        """Activities matching any of the query's words, best BM25 match first."""
        words = fts_query(query)
        if not words:
            return []
        with self._lock:
            rows = self.db.execute(
                "select a.* from activity_words join activities a on a.id = activity_words.rowid "
                "where activity_words match :words and (:app is null or a.app = :app) "
                "and (:since is null or a.ended >= :since) order by bm25(activity_words) limit :k",
                {"words": words, "app": app, "since": since, "k": k},
            ).fetchall()
        return [dict(row) for row in rows]
