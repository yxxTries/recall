"""Activity memory: what you were doing, in which app and when, built by rules (no models).

Capture events about the same subject (a VS Code workspace, a website, a chat or meeting
window) merge into one activity until that subject has been quiet for GAP; switching to other
apps in between doesn't split it. Each activity keeps a templated summary, the files or pages
seen, people named, frequent terms and a few key lines. All other captured text is dropped.
Search by meaning happens in the cloud (Phase 7). Events must arrive in time order.
"""
import hashlib
import re
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import PurePath
from urllib.parse import urlparse

from recall.memory.store import STOPWORDS

GAP = timedelta(minutes=5)
MIN_SECONDS = 10  # a window passed through while alt-tabbing is not an activity
MAX_ITEMS, MAX_SHOWN_ITEMS, MAX_PEOPLE, MAX_TERMS, MAX_KEY_LINES = 8, 3, 6, 12, 5
MAX_CANDIDATES = 300  # recent lines held in memory (never stored) to pick key lines from
KEY_LINE_CHARS = (20, 200)

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe", "firefox.exe"}
CHAT_APPS = {"ms-teams.exe", "teams.exe", "discord.exe", "slack.exe", "zoom.exe", "whatsapp.exe", "telegram.exe"}
APP_NAMES = {"code.exe": "VS Code", "msedge.exe": "Edge", "chrome.exe": "Chrome", "firefox.exe": "Firefox",
             "ms-teams.exe": "Teams", "teams.exe": "Teams", "discord.exe": "Discord", "slack.exe": "Slack",
             "zoom.exe": "Zoom", "notepad.exe": "Notepad"}
# Trailing window-title parts that name the app, not what you were doing in it.
APP_SUFFIXES = {"visual studio code", "microsoft edge", "personal", "work", "google chrome", "mozilla firefox",
                "microsoft teams", "discord", "slack", "zoom", "notepad"}
SEPARATOR = re.compile(r"\s+[-|–—]\s+")
EDGE_PROFILE = re.compile(r"profile \d+")  # Edge names an unnamed profile "Profile 1" in the title
MORE_PAGES = re.compile(r"\s+and \d+ more pages?$")
SPEAKER = re.compile(r"^([A-Z][a-z]+(?: [A-Z][a-z]+)?):\s+\S")  # "Priya: judging starts at 2pm"
WITH_PERSON = re.compile(r"\bwith ([A-Z][a-z]+(?: [A-Z][a-z]+)?)")  # "Call with Sarah Lee"
LOCAL_FILE = re.compile(r"^(?:file:|[a-zA-Z]:[\\/])")  # a browser showing a file on this PC ("C:/Users/me/lease.pdf")
NOT_PEOPLE = {"Note", "Notes", "Error", "Warning", "Tip", "Step", "Example", "Usage", "Todo", "Update", "Re",
              "Fwd", "Subject", "From", "To", "Date", "Time", "Link", "Source", "Question", "Answer"}
WORD = re.compile(r"[a-z][a-z0-9]{2,}")
TERM_STOPWORDS = STOPWORDS | set(
    "not can will just should would could our their they them there then than into out also more some any all "
    "one get got like yes now here been being had him her his she only very too each other such own same over "
    "under again once because while after before until these those let use used using new "
    "self def return import class none true false elif else const var function async await null undefined "
    "public private static void int str dict list len print".split()
)


def clean_title(title: str, app: str) -> str:
    """The window title without the trailing app name ("Weekly sync | Microsoft Teams" -> "Weekly sync")."""
    title = title.replace("​", "").strip()
    stem = app.removesuffix(".exe").lower()
    while True:
        parts = list(SEPARATOR.finditer(title))
        tail = title[parts[-1].end():].strip().lower() if parts else ""
        if not parts or (tail not in APP_SUFFIXES and tail != stem and not EDGE_PROFILE.fullmatch(tail)):
            break
        title = title[:parts[-1].start()]
    return MORE_PAGES.sub("", title).strip()


def describe(e: dict) -> tuple[str, str]:
    """(subject, item): what an event's activity is about, and the file, page or window within it."""
    app, url = e.get("app", ""), e.get("url", "")
    title = clean_title(e.get("title", ""), app)
    if app == "code.exe":
        file, _, workspace = title.rpartition(" - ")  # "recall/ui/search.py - recall"
        if e["type"] == "text" and not url.startswith("vscode://"):
            return workspace, "AI chat"  # UIA only reads VS Code's chat panels; editor text has vscode:// links
        return workspace, PurePath(file).name if file else ""
    if app in BROWSERS and url and not LOCAL_FILE.match(url):
        host = urlparse(url if "://" in url else "https://" + url).hostname or ""
        return host.removeprefix("www.") or title, title
    return title, title


class Activity:
    def __init__(self, app: str, subject: str, time: datetime) -> None:
        self.app, self.subject = app, subject
        self.started = self.ended = time
        self.items: Counter[str] = Counter()
        self.people: Counter[str] = Counter()
        self.terms: Counter[str] = Counter()
        self.candidates: deque[str] = deque(maxlen=MAX_CANDIDATES)
        self.url = ""
        self.has_text = self.heard = False

    def update(self, e: dict, item: str, time: datetime) -> None:
        self.ended = max(self.ended, time)
        if item:
            self.items[item] += 1
        self.url = e.get("url") or self.url
        self.heard |= e.get("source") == "audio"
        if self.app in CHAT_APPS:
            self.people.update(name for name in WITH_PERSON.findall(e.get("title", "")) if name not in NOT_PEOPLE)
        if e["type"] != "text":
            return
        self.has_text = True
        for line in e.get("text", "").splitlines():
            line = line.strip()
            speaker = SPEAKER.match(line)
            if speaker and self.app != "code.exe" and speaker[1] not in NOT_PEOPLE:
                self.people[speaker[1]] += 1
            self.terms.update(w for w in WORD.findall(line.lower()) if w not in TERM_STOPWORDS)
            if KEY_LINE_CHARS[0] <= len(line) <= KEY_LINE_CHARS[1]:
                self.candidates.append(line)

    def key_lines(self) -> list[str]:
        """The lines that carry most of the activity's frequent terms, in the order they appeared."""
        top = {term for term, _ in self.terms.most_common(MAX_TERMS)}
        scored = [(len(top & set(WORD.findall(line.lower()))), i) for i, line in enumerate(self.candidates)]
        best = sorted(i for score, i in sorted(scored, reverse=True)[:MAX_KEY_LINES] if score)
        return [self.candidates[i] for i in best]

    def summary(self) -> str:
        name = APP_NAMES.get(self.app, self.app.removesuffix(".exe").capitalize())
        if self.app == "code.exe":
            text = f"Worked on {self.subject} in {name}"
        elif self.app in BROWSERS:
            text = f"{'Listened to' if self.heard else 'Read'} {self.subject} in {name}"
        elif self.app in CHAT_APPS:
            text = f"{'Meeting' if self.heard else 'Chat'}: {self.subject} in {name}"
        else:
            text = f"Used {name}: {self.subject}"
        items = [item for item, _ in self.items.most_common(MAX_ITEMS) if item != self.subject][:MAX_SHOWN_ITEMS]
        if items:
            text += f" ({', '.join(items)})"
        if self.people:
            text += " with " + ", ".join(name for name, _ in self.people.most_common(MAX_PEOPLE))
        return text

    def worth_storing(self) -> bool:
        return self.has_text or (self.ended - self.started).total_seconds() >= MIN_SECONDS

    def record(self) -> dict:
        started = self.started.isoformat(timespec="seconds")
        return {
            "activity_id": hashlib.sha1(f"{self.app}|{self.subject}|{started}".encode()).hexdigest()[:32],
            "app": self.app, "subject": self.subject, "summary": self.summary(),
            "items": "\n".join(item for item, _ in self.items.most_common(MAX_ITEMS)),
            "people": ", ".join(name for name, _ in self.people.most_common(MAX_PEOPLE)),
            "terms": " ".join(term for term, _ in self.terms.most_common(MAX_TERMS)),
            "key_lines": "\n".join(self.key_lines()), "url": self.url,
            "source": "audio" if self.heard else "text",
            "started": started, "ended": self.ended.isoformat(timespec="seconds"),
        }


class ActivityTracker:
    """Folds capture and session events into the activities they belong to."""

    def __init__(self) -> None:
        self._open: dict[tuple[str, str], Activity] = {}

    def add(self, e: dict) -> dict | None:
        """The updated activity's record, or None when there's nothing worth storing yet."""
        time = datetime.fromisoformat(e["time"])
        for key, activity in list(self._open.items()):  # activities quiet for GAP have ended
            if time - activity.ended > GAP:
                del self._open[key]
        subject, item = describe(e)
        if not subject:
            return None
        key = (e["app"], subject)
        activity = self._open.get(key)
        if activity is None and e["type"] != "text" and e["app"] in BROWSERS:
            # Browser activities are keyed by site, which only text events carry: extend the one showing this page.
            activity = next((a for a in self._open.values() if a.app == e["app"] and item in a.items), None)
            if activity is None:
                return None
        if activity is None:
            activity = self._open[key] = Activity(e["app"], subject, time)
        activity.update(e, item, time)
        return activity.record() if activity.worth_storing() else None
