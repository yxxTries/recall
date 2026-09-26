"""The local memory: one SQLite file with vectors (sqlite-vec) and keywords (FTS5)."""
import re
import sqlite3
import threading
from pathlib import Path

import sqlite_vec

from recall.memory.embed import DIMENSIONS

SCHEMA = f"""
create table if not exists chunks (
    id integer primary key,
    chunk_id text not null unique,
    text text not null,
    app text not null default '',
    title text not null default '',
    url text not null default '',
    source text not null,
    time text not null,
    device_id text not null,
    synced integer not null default 0
);
create index if not exists chunks_time on chunks(time);
create virtual table if not exists chunk_vectors using vec0(embedding float[{DIMENSIONS}] distance_metric=cosine);
create virtual table if not exists chunk_words using fts5(text, tokenize='porter unicode61');
"""
CANDIDATES = 50  # per retriever, before fusion
RRF_K = 60
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
        self.db.enable_load_extension(True)
        sqlite_vec.load(self.db)
        self.db.enable_load_extension(False)
        self.db.execute("pragma journal_mode=wal")
        self.db.executescript(SCHEMA)

    def new_chunks(self, chunks: list[dict]) -> list[dict]:
        """The chunks not stored yet (checked before spending time embedding them)."""
        unique = list({c["chunk_id"]: c for c in chunks}.values())
        with self._lock:
            known = {
                row[0] for row in self.db.execute(
                    f"select chunk_id from chunks where chunk_id in ({','.join('?' * len(unique))})",
                    [c["chunk_id"] for c in unique],
                )
            } if unique else set()
        return [c for c in unique if c["chunk_id"] not in known]

    def add(self, chunks: list[dict], vectors) -> int:
        added = 0
        with self._lock, self.db:
            for chunk, vector in zip(chunks, vectors):
                cursor = self.db.execute(
                    "insert or ignore into chunks (chunk_id, text, app, title, url, source, time, device_id) "
                    "values (:chunk_id, :text, :app, :title, :url, :source, :time, :device_id)",
                    {**chunk, "device_id": chunk.get("device_id", self.device_id)},
                )
                if not cursor.rowcount:
                    continue
                rowid = cursor.lastrowid
                self.db.execute("insert into chunk_vectors (rowid, embedding) values (?, ?)",
                                (rowid, sqlite_vec.serialize_float32(list(map(float, vector)))))
                self.db.execute("insert into chunk_words (rowid, text) values (?, ?)", (rowid, chunk["text"]))
                added += 1
        return added

    def count(self) -> int:
        with self._lock:
            return self.db.execute("select count(*) from chunks").fetchone()[0]

    def apps(self) -> list[str]:
        with self._lock:
            return [row[0] for row in self.db.execute("select distinct app from chunks where app != '' order by app")]

    def recent(self, k: int = 20, app: str | None = None, since: str | None = None) -> list[dict]:
        with self._lock:
            rows = self.db.execute(
                "select * from chunks where (:app is null or app = :app) and (:since is null or time >= :since) "
                "order by time desc, id desc limit :k",
                {"app": app, "since": since, "k": k},
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, query_vector, query_text: str, k: int = 10, app: str | None = None,
               since: str | None = None) -> list[dict]:
        """Hybrid search: vector and keyword rankings merged by reciprocal rank fusion."""
        scores: dict[int, float] = {}
        with self._lock:
            vector_ids = [row[0] for row in self.db.execute(
                "select rowid from chunk_vectors where embedding match ? and k = ? order by distance",
                (sqlite_vec.serialize_float32(list(map(float, query_vector))), CANDIDATES),
            )]
            words = fts_query(query_text)
            word_ids = [row[0] for row in self.db.execute(
                "select rowid from chunk_words where chunk_words match ? order by rank limit ?", (words, CANDIDATES)
            )] if words else []
            for ranking in (vector_ids, word_ids):
                for rank, rowid in enumerate(ranking):
                    scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (RRF_K + rank + 1)
            if not scores:
                return []
            rows = self.db.execute(
                f"select * from chunks where id in ({','.join('?' * len(scores))})", list(scores)
            ).fetchall()
        results = [
            {**dict(row), "score": scores[row["id"]]} for row in rows
            if (app is None or row["app"] == app) and (since is None or row["time"] >= since)
        ]
        return sorted(results, key=lambda r: r["score"], reverse=True)[:k]
