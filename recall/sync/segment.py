"""Episode segmentation: split the capture stream where the task changes (no models).

Events are grouped into 30-s windows. Each window becomes a 512-slot vector of its words (hashed,
weighted by rarity) and window-title words, plus its app and subject (workspace, site, chat). Drift
from the open episode feeds a CUSUM change detector: drift must stay high for about a minute before
an episode ends, so an alt-tab doesn't split it, and the episode is cut where the drift began,
unless what came before is under 2 minutes (a search before the page it finds joins that task).
Episodes also end after 5 idle minutes (while a tracked window stays in front, reading or watching
without new text isn't idle, up to 20 minutes) and at 20 minutes, so the cloud gets bounded input on time.
Each closed episode carries its exact app and window spans and its most distinctive new lines.
Events must arrive in time order.
"""
import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from recall.memory.activity import clean_title, describe
from recall.sync.sketch import WORD, SeenLines, WordRarity, hash64

WINDOW = timedelta(seconds=30)
IDLE = timedelta(minutes=5)
MAX_EPISODE = timedelta(minutes=20)
MIN_EPISODE = timedelta(minutes=2)
DIM = 512
TEXT_WEIGHT, APP_WEIGHT, SUBJECT_WEIGHT, TITLE_WEIGHT = 0.6, 0.4, 0.6, 1.0
SLACK, THRESHOLD = 0.5, 0.8  # CUSUM: drift below SLACK is normal; the sum must pass THRESHOLD to cut
MAX_CHARS = 12_000  # ~3k tokens of episode text, so a whole request fits Groq's 8K tokens per minute
MIN_LINE = 3
NUMBERED = re.compile(r"[\w./:\\-]*\d[\w./:\\-]*")  # tokens with a digit: counters, ids, paths, times


def when(e: dict) -> datetime:
    return datetime.fromisoformat(e["time"])


def slot(feature: str) -> int:
    return hash64(feature) % DIM


@dataclass
class Window:
    start: datetime
    events: list[dict] = field(default_factory=list)
    vector: np.ndarray | None = None

    @property
    def last(self) -> datetime:
        return when(self.events[-1])


class Segmenter:
    def __init__(self, rarity: WordRarity | None = None, seen: SeenLines | None = None, device_id: str = "") -> None:
        self.rarity = rarity or WordRarity()
        self.seen = seen or SeenLines()
        self.device_id = device_id
        self.windows: list[Window] = []  # closed windows of the open episode
        self.current: Window | None = None
        self.cusum = 0.0
        self.rise: int | None = None  # index of the window where the CUSUM sum left zero
        self.in_session = False  # a tracked window is in front (its session started and hasn't ended)

    def add(self, e: dict) -> list[dict]:
        """Take one capture or session event; returns the episodes it closed (usually none)."""
        t = when(e)
        closed = self.flush(t)
        if self.current and t >= self.current.start + WINDOW:
            closed += self._close_window()
        if self.current is None:
            self.current = Window(t)
        self.current.events.append(e)
        if e["type"] == "text":
            self.rarity.add(e.get("text", ""))
        elif e["type"] in ("session_start", "session_end"):
            self.in_session = e["type"] == "session_start"
        return useful(closed)

    def flush(self, now: datetime) -> list[dict]:
        """Close the episode if nothing happened for IDLE; call this on a timer too."""
        last = self.current.last if self.current else (self.windows[-1].last if self.windows else None)
        if last is None or now - last <= (MAX_EPISODE if self.in_session else IDLE):
            return []
        return self.close()

    def close(self) -> list[dict]:
        """Close whatever is open (idle, or Recall is quitting)."""
        closed = self._close_window() if self.current else []
        if self.windows:
            closed.append(self._episode(self.windows))
        self.windows, self.cusum, self.rise = [], 0.0, None
        return useful(closed)

    def _vector(self, window: Window) -> np.ndarray:
        text, context = np.zeros(DIM), np.zeros(DIM)
        for e in window.events:
            subject, _ = describe(e)
            context[slot("app:" + e.get("app", ""))] += APP_WEIGHT
            context[slot("subject:" + subject)] += SUBJECT_WEIGHT
            for word in WORD.findall(e.get("text", "").lower()):
                text[slot(word)] += self.rarity.idf(word)
            for word in set(WORD.findall(clean_title(e.get("title", ""), e.get("app", "")).lower())):
                text[slot("title:" + word)] += TITLE_WEIGHT * self.rarity.idf(word)
        context /= len(window.events)
        norm = np.linalg.norm(text)
        vector = context + (TEXT_WEIGHT * text / norm if norm else 0)
        return vector / np.linalg.norm(vector)

    def _close_window(self) -> list[dict]:
        window, self.current = self.current, None
        window.vector = self._vector(window)
        closed = []
        if self.windows:
            centroid = sum(w.vector for w in self.windows[:self.rise])  # windows since the rise may be the next task
            drift = 1 - float(window.vector @ centroid) / np.linalg.norm(centroid)
            self.cusum = max(0.0, self.cusum + drift - SLACK)
            if self.cusum == 0:
                self.rise = None
            elif self.rise is None:
                self.rise = len(self.windows)
            if self.cusum > THRESHOLD:
                head = self.windows[:self.rise]
                if head[-1].last - head[0].start >= MIN_EPISODE:
                    closed.append(self._episode(head))
                    self.windows = self.windows[self.rise:]
                self.cusum, self.rise = 0.0, None  # a lead-in under MIN_EPISODE joins the new task
        self.windows.append(window)
        if self.windows[-1].last - self.windows[0].start >= MAX_EPISODE:
            closed.append(self._episode(self.windows))
            self.windows, self.cusum, self.rise = [], 0.0, None
        return closed

    def _episode(self, windows: list[Window]) -> dict:
        events = [e for w in windows for e in w.events]
        started = when(events[0]).isoformat(timespec="seconds")
        return {
            "episode_id": hashlib.sha1(f"{self.device_id}|{started}".encode()).hexdigest()[:32],
            "device_id": self.device_id,
            "started": started,
            "ended": max(when(e) for e in events).isoformat(timespec="seconds"),
            "spans": spans(events),
            "text": self._text(events),
        }

    def _text(self, events: list[dict]) -> str:
        """The episode's new lines, headed by app and window; if too long, the most distinctive ones.

        Runs of lines that differ only in numbers or ids (test output, logs, tool calls) keep their first
        and last line. When over budget, prose outranks symbol-heavy lines like commands and JSON.
        """
        lines = []  # (score, header, line)
        templates: dict[str, list[int]] = {}  # line with numbers masked -> indexes into lines
        for e in events:
            if e["type"] != "text":
                continue
            header = f"## {e.get('app', '')} · {clean_title(e.get('title', ''), e.get('app', ''))}"
            for line in e.get("text", "").splitlines():
                line = line.strip()
                if len(line) < MIN_LINE or self.seen.seen(line):
                    continue
                words = WORD.findall(line.lower())
                score = sum(self.rarity.idf(w) for w in words) / math.sqrt(len(words) + 1)
                symbols = sum(not (c.isalnum() or c.isspace()) for c in line) / len(line)
                templates.setdefault(NUMBERED.sub("#", " ".join(line.split())), []).append(len(lines))
                lines.append((score * (1 - min(2 * symbols, 0.8)), header, line))
        repeats = {i for run in templates.values() for i in run[1:-1]}
        keep, size = set(), 0
        for i in sorted(range(len(lines)), key=lambda i: -lines[i][0]):
            if i in repeats or size + len(lines[i][2]) + 1 > MAX_CHARS:
                continue
            keep.add(i)
            size += len(lines[i][2]) + 1
        out, last = [], None
        for i, (_, header, line) in enumerate(lines):
            if i in keep:
                if header != last:
                    out.append(header)
                    last = header
                out.append(line)
        return "\n".join(out)


def useful(episodes: list[dict]) -> list[dict]:
    """Drops episodes with nothing in them (only a session's end after an idle cut)."""
    return [ep for ep in episodes if ep["spans"] or ep["text"]]


def spans(events: list[dict]) -> list[dict]:
    """Exact app and window spans: consecutive events in the same window merge into one."""
    out: list[dict] = []
    for e in events:
        app, title, t = e.get("app", ""), clean_title(e.get("title", ""), e.get("app", "")), e["time"]
        if out and (out[-1]["app"], out[-1]["title"]) == (app, title):
            out[-1]["ended"] = t
            out[-1]["url"] = e.get("url") or out[-1]["url"]
        elif e["type"] != "session_end":
            out.append({"app": app, "title": title, "url": e.get("url", ""), "started": t, "ended": t})
    return out
