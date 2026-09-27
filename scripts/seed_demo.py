"""Load a known demo dataset into the memory store (respects RECALL_HOME).

Usage: python scripts/seed_demo.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recall.config import data_dir, load_config  # noqa: E402
from recall.memory.activity import ActivityTracker  # noqa: E402
from recall.memory.store import MemoryStore  # noqa: E402

# (minutes ago, app, title, url, source, text)
DEMO = [
    (4, "msedge.exe", "Vector databases explained - The Pragmatic Engineer", "blog.pragmaticengineer.com/vector-databases",
     "text", "A vector database stores embeddings and answers nearest-neighbour queries.\n"
     "HNSW indexes trade a little recall for big speedups; most teams never need more than a few million vectors.\n"
     "For small apps, SQLite with a vector extension is often all you need."),
    (9, "chrome.exe", "Building a second brain - talk (YouTube)", "youtube.com/watch?v=second-brain", "audio",
     "The trick is not capturing everything, it is being able to find it again the moment you need it.\n"
     "Semantic search beats folders because you remember what something was about, not where you put it."),
    (18, "discord.exe", "#general | Hack Atlantic 2026 - Discord", "", "text",
     "Priya: judging starts at 2pm sharp, demos are 3 minutes max\n"
     "Tom: wifi password for the hacker lounge is on the whiteboard by the stage\n"
     "Priya: remember to submit the devpost link before 1:30"),
    (38, "code.exe", "recall/memory/store.py - recall", "vscode://file/C:/dev/recall/recall/memory/store.py:40",
     "text", "Hybrid search: vector and keyword rankings merged by reciprocal rank fusion.\n"
     "RRF_K = 60 keeps one very strong keyword match from drowning the semantic results."),
    (35, "code.exe", "recall/ui/hotkey.py - recall", "vscode://file/C:/dev/recall/recall/ui/hotkey.py:15", "text",
     "Candidate hotkeys are tried in order; the first one no other app holds wins.\n"
     "Ctrl+Shift+Space is often taken, so Win+Alt+Space is the usual fallback."),
    (52, "ms-teams.exe", "Weekly sync | Microsoft Teams", "", "audio",
     "Sarah: the vendor shortlist is Acme, Globex and Initech, we decide by Friday.\n"
     "Dev: Globex was cheapest but their support contract only covers weekdays.\n"
     "Sarah: let's ask Initech for a trial licence before the decision."),
    (70, "notepad.exe", "todo.txt - Notepad", "", "text",
     "Book dentist for next Tuesday morning\nRenew passport before the Lisbon trip in November\n"
     "Send Maya the slides from the design review"),
    (95, "msedge.exe", "Crispy roasted chickpeas recipe", "cookingclassy.example/roasted-chickpeas", "text",
     "Preheat the oven to 220C. Toss chickpeas with olive oil, smoked paprika and garlic powder.\n"
     "Roast for 25 minutes, shaking the tray halfway, until deep golden and crunchy."),
    (180, "msedge.exe", "Flight confirmation - British Airways", "britishairways.example/manage-booking", "text",
     "Flight BA117 London Heathrow to New York JFK, departs 08:40, arrives 11:25.\n"
     "Booking reference QX7L2M. Gate closes 20 minutes before departure."),
    (300, "ms-teams.exe", "Design review | Microsoft Teams", "", "audio",
     "Maya: the onboarding flow drops a third of users at the permissions screen.\n"
     "Let's move the permissions ask until after they have seen one result."),
    (1440, "chrome.exe", "Attention is all you need - explained", "youtube.com/watch?v=attention", "audio",
     "Self attention compares every token with every other token, which is why cost grows quadratically with length."),
    (1500, "discord.exe", "#team-recall | Hack Atlantic 2026 - Discord", "", "text",
     "Alex: can we get per-app audio working? system loopback records Spotify too\n"
     "Sam: Windows 11 has process loopback capture, only the tracked app's audio"),
    (2900, "msedge.exe", "Lease renewal terms - Oakwood Apartments", "oakwood.example/tenants/renewal", "text",
     "Renewal requires 60 days written notice. Rent increases 4 percent from March 1st.\n"
     "Pets allowed with a one-time deposit."),
]


def main() -> int:
    config = load_config()
    store = MemoryStore(data_dir() / "memory.db", config["device_id"])
    tracker = ActivityTracker()
    now = datetime.now()
    records = {}
    for minutes, app, title, url, source, text in sorted(DEMO, key=lambda row: -row[0]):  # oldest first
        e = {"type": "text", "source": source, "app": app, "title": title, "url": url, "text": text,
             "time": (now - timedelta(minutes=minutes)).isoformat(timespec="seconds")}
        record = tracker.add(e)
        records[record["activity_id"]] = record  # the latest state of each activity
    for record in records.values():
        store.upsert(record)
    print(f"seeded {len(records)} demo activities into {data_dir() / 'memory.db'} ({store.count()} total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
