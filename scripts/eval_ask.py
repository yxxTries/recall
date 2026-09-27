"""Ask eval: questions you'd ask your memory, over one user's two devices, with known answers.

Seeds a throwaway user with three days of understood episodes on a desktop and a laptop (threads, window
spans, device names), asks the questions below, scores each answer, then deletes the user. Seeding uses no
LLM; each question is one Groq call (14 in all), paced for the free tier's tokens per minute.

  python scripts/eval_ask.py                  # seed, ask everything, delete the user
  python scripts/eval_ask.py --only today,lunch
  python scripts/eval_ask.py --keep           # keep the user (e.g. to try the dashboard); prints how to sign in
  python scripts/eval_ask.py --reuse EMAIL:PASSWORD   # ask a kept user again, without seeding
Needs .env with the project's secret key (to create and delete the throwaway user).
"""
import argparse
import json
import secrets
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from recall.sync.cloud import CloudError, CloudSession, request, settings  # noqa: E402
from tests.test_cloud import admin, secret_key  # noqa: E402

URL, _ = settings()
NOW = datetime.now().astimezone().replace(second=0, microsecond=0)
DAY0 = (NOW if NOW.hour >= 5 else NOW - timedelta(days=1)).date()  # a day runs from 5 am, as Ask reads "today"
DEVICES = {"desk": "DESKTOP-4F7Q", "lap": "LAPTOP-9XK2"}
THREADS = {"recall": "Recall hackathon app", "sponsor": "Hackathon sponsors", "landing": "Recall landing page",
           "budget": "Quarterly budget", "home": "Home and family", "demo": "Hackathon demo",
           "vectors": "Vector database research"}

# id, when (days before today and local start time, or a slot today), minutes, device, app, window,
# worked_on, context, important, people, importance, thread
EPISODES = [
    ("d2a", (-2, "10:00"), 90, "desk", "code.exe", "segment.py - recall",
     "Built episode segmentation (CUSUM) for Recall's uploader", "Capture is split into episodes when the topic drifts.",
     ["Episodes now close after 5 idle minutes"], [], 7, "recall"),
    ("d2b", (-2, "13:00"), 60, "desk", "figma.exe", "Landing page - Figma",
     "Designed the landing page hero section in Figma", "Dark theme with a large search box screenshot.", [], [], 5, "landing"),
    ("d2c", (-2, "20:00"), 20, "lap", "chrome.exe", "Oakwood Apartments - Messages",
     "Chatted with the landlord, Tom Okafor, about the rent increase", "Oakwood Apartments proposes a 6% increase from January.",
     ["Rent goes up 6% from January unless renegotiated"], ["Tom Okafor"], 6, "home"),
    ("d1a", (-1, "09:30"), 45, "desk", "excel.exe", "Q3 budget.xlsx - Excel",
     "Reviewed the quarterly budget spreadsheet with Dana from finance", "Went line by line through Q3 spend.",
     ["Marketing spend is 12% over plan", "Dana wants a revised forecast by Friday"], ["Dana"], 7, "budget"),
    ("d1b", (-1, "11:00"), 30, "lap", "ms-teams.exe", "Sponsor sync | Microsoft Teams",
     "Sponsor sync with Priya and Marco: chose Contoso as the hackathon sponsor",
     "Compared Northwind, Contoso and Fabrikam; Contoso's mentors are free on Saturday morning.",
     ["Contoso chosen for Saturday mentoring", "Tell Fabrikam by Thursday", "Priya emails Contoso to confirm the mentor slot"],
     ["Priya", "Marco"], 8, "sponsor"),
    ("d1c", (-1, "14:00"), 40, "lap", "msedge.exe", "OAuth 2.1 server | Supabase Docs",
     "Read the Supabase docs on the OAuth 2.1 server for MCP clients", "Dynamic client registration and a custom consent page.",
     ["The consent page must live on the Site URL"], [], 6, "recall"),
    ("d1d", (-1, "15:00"), 90, "desk", "code.exe", "consent.py - recall",
     "Built the MCP consent page in Recall", "A local page on localhost:8766 approves agents with a per-run nonce.",
     ["Agents get read-only access after Allow"], [], 7, "recall"),
    ("t0", 0, 18, "desk", "code.exe", "mcp/index.ts - recall", "Fixed timezone handling in Recall's MCP server",
     "Times now go out in the user's local time.", ["search_memory understands today and yesterday"], [], 7, "recall"),
    ("t1", 1, 18, "desk", "chrome.exe", "pgvector vs Pinecone - blog", "Read an article comparing vector databases",
     "pgvector, Pinecone and Weaviate compared on HNSW speed and cost.", ["pgvector is cheapest at our scale"], [], 4, "vectors"),
    ("t2", 2, 18, "lap", "outlook.exe", "Venue deposit - Outlook", "Replied to the venue about the hackathon deposit",
     "Sam Lee from the venue asked to confirm the booking.", ["Venue deposit of $500 due October 3"], ["Sam Lee"], 7, "demo"),
    ("t3", 3, 18, "lap", "ms-teams.exe", "Demo rehearsal | Microsoft Teams", "Rehearsed the hackathon demo with Priya and Marco",
     "Ran the 3-minute script twice; the MCP finale worked.", ["Demo slot is Sunday at 2:30 pm", "Cut the audio part if time runs short"],
     ["Priya", "Marco"], 8, "demo"),
    ("t4", 4, 18, "lap", "figma.exe", "Landing page - Figma", "Updated the landing page hero copy after rehearsal feedback",
     "Shorter headline: 'Ask your computer what you did'.", [], [], 5, "landing"),
    ("t5", 5, 18, "desk", "code.exe", "README.md - recall", "Wrote the README setup section for Recall",
     "Setup, sign-in from the tray and MCP for any client.", [], [], 5, "recall"),
    ("t6", 6, 18, "desk", "chrome.exe", "Pull request #42 - GitHub", "Reviewed Dev's pull request on login rate limiting",
     "Asked for a TTL on the rate-limit keys.", ["Dev will add an expiry to the rate-limit keys"], ["Dev"], 6, "recall"),
    ("t7", 7, 18, "lap", "chrome.exe", "Ceramic teapot - Etsy", "Ordered a birthday present for Mum",
     "A ceramic teapot from Etsy.", ["The present arrives Thursday"], [], 3, "home"),
    ("t8", 8, 18, "desk", "powerpnt.exe", "Recall pitch.pptx - PowerPoint", "Updated the pitch deck slides",
     "Added the architecture slide and the privacy close.", [], [], 5, "demo"),
    ("t9", 9, 18, "desk", "msedge.exe", "Postgres HNSW indexing - YouTube", "Watched a video on Postgres HNSW indexing",
     "ef_search trades recall for speed.", [], [], 3, "vectors"),
    ("t10", 10, 18, "lap", "code.exe", "ask.ts - recall", "Improved Ask to answer across devices",
     "Device names and a whole-day overview in the prompt.", [], [], 6, "recall"),
]

# name, question, earlier turns, groups of words (each group: any one word must appear), how many groups must
# match (None: all), words that must not appear, episodes the answer must cite
QUESTIONS = [
    ("today", "What did I do today?", [],
     [["timezone", "time zone"], ["vector", "pgvector"], ["deposit", "venue"], ["rehears"], ["landing"], ["readme"],
      ["pull request", "rate limit", "rate-limit"], ["teapot", "present", "mum"], ["pitch", "deck", "slide"], ["ask"]], 6, [], []),
    ("laptop", "What did I do on my laptop yesterday?", [], [["sponsor", "contoso"], ["oauth", "supabase"]], None,
     ["budget", "localhost", "readme", "pitch"], ["d1b"]),  # desktop-only work
    ("desktop_pm", "What was I doing on my desktop yesterday afternoon?", [], [["consent"]], None, ["contoso", "budget", "supabase", "oauth"], ["d1d"]),
    ("sponsor", "Which sponsor did we pick for the hackathon?", [], [["contoso"]], None, [], ["d1b"]),
    ("vscode", "How long was I in VS Code today?", [], [["54"]], None, [], []),
    ("budget_who", "Who did I go through the budget with?", [], [["dana"]], None, [], ["d1a"]),
    ("demo_when", "When is the demo?", [], [["2:30", "14:30"], ["sunday"]], None, [], ["t3"]),
    ("projects", "What projects have I been working on this week?", [],
     [["recall"], ["landing"], ["sponsor"], ["budget"], ["demo"]], 3, [], []),
    ("follow_up", "What do I need to follow up on?", [],
     [["fabrikam"], ["forecast"], ["deposit"], ["expiry", "ttl"]], 2, [], []),
    ("deadlines", "What deadlines are coming up?", [],
     [["october 3", "oct 3", "3 october"], ["friday"], ["thursday"]], 2, [], []),
    ("lunch", "What did I have for lunch today?", [],
     [["no ", "not ", "don't", "doesn't", "nothing", "isn't"]], None, [], []),
    ("started", "When did I start on Recall's episode segmentation?", [],
     [[(DAY0 - timedelta(days=2)).strftime("%A").lower(), "two days ago", "2 days ago",
       f"{(DAY0 - timedelta(days=2)).day}"]], None, [], ["d2a"]),
    ("rent", "How much is my rent going up?", [], [["6%", "6 %", "6 percent", "six percent"]], None, [], ["d2c"]),
    ("who_else", "Who was in that call?", [("What did we decide in the sponsor sync?",
                                            "You chose Contoso as the hackathon sponsor, for their Saturday mentoring.")],
     [["priya"], ["marco"]], None, [], []),
]


def when(slot) -> tuple[datetime, int]:
    """The local start of an episode: a day and time, or one of today's 21-minute slots ending just before now."""
    if isinstance(slot, int):
        return NOW - timedelta(minutes=4 * 60 - 21 * slot), 0
    days, clock = slot
    hour, minute = map(int, clock.split(":"))
    return datetime.combine(DAY0 + timedelta(days=days), datetime.min.time()).replace(hour=hour, minute=minute).astimezone(), days


def seed(user: str) -> None:
    key = secret_key()
    service = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Prefer": "return=representation"}
    offset = round(NOW.utcoffset().total_seconds() / 60)
    rest = lambda table, rows: request("POST", f"{URL}/rest/v1/{table}", service, json.dumps(rows).encode())
    rest("devices", [{"user_id": user, "device_id": d, "name": name, "utc_offset_minutes": offset,
                      "last_seen": NOW.isoformat()} for d, name in DEVICES.items()])
    thread_ids = {}
    for key_, title in THREADS.items():
        mine = [e for e in EPISODES if e[11] == key_]
        starts = [when(e[1])[0] for e in mine]
        ends = [s + timedelta(minutes=e[2]) for s, e in zip(starts, mine)]
        [row] = rest("threads", {"user_id": user, "title": title, "summary": mine[0][6], "started": min(starts).isoformat(),
                                 "ended": max(ends).isoformat(), "episodes": len(mine)})
        thread_ids[key_] = row["id"]
    episodes, spans = [], []
    for eid, slot, minutes, device, app, window, worked_on, context, important, people, importance, thread in EPISODES:
        start = when(slot)[0]
        end = start + timedelta(minutes=minutes)
        episodes.append({"user_id": user, "episode_id": eid, "device_id": device, "started": start.isoformat(),
                         "ended": end.isoformat(), "apps": [app], "worked_on": worked_on, "context": context,
                         "important": important, "people": people, "importance": importance,
                         "thread_id": thread_ids[thread], "topics": [THREADS[thread].lower()]})
        spans.append({"user_id": user, "episode_id": eid, "device_id": device, "app": app, "title": window,
                      "started": start.isoformat(), "ended": end.isoformat()})
    rest("episodes", episodes)
    rest("timeline_spans", spans)
    while request("POST", f"{URL}/functions/v1/understand", service, b'{"task": "embed"}', timeout=120)["embedded"]:
        pass


def ask(cloud: CloudSession, question: str, history: list) -> tuple[dict, float]:
    body = {"question": question, "utc_offset_minutes": round(NOW.utcoffset().total_seconds() / 60),
            "history": [{"question": q, "answer": a} for q, a in history]}
    for attempt in range(3):
        started = time.monotonic()
        try:
            return cloud.call("ask", json.dumps(body).encode(), timeout=90), time.monotonic() - started
        except CloudError as e:
            if e.status not in (429, 503) or attempt == 2:
                raise
            print(f"  busy ({e.status}), waiting 60 s")
            time.sleep(60)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # answers carry characters the Windows console codepage lacks
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma-separated question names")
    parser.add_argument("--keep", action="store_true", help="keep the throwaway user")
    parser.add_argument("--reuse", default="", help="EMAIL:PASSWORD of a kept user")
    parser.add_argument("--pause", type=float, default=25, help="seconds between questions (tokens per minute)")
    args = parser.parse_args()
    if args.reuse:
        email, password = args.reuse.split(":", 1)
        user = None
    else:
        email, password = f"recall-ask-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(18)
        user = admin("POST", "users", {"email": email, "password": password, "email_confirm": True})["id"]
    try:
        if user:
            seed(user)
            print(f"seeded {len(EPISODES)} episodes on {len(DEVICES)} devices")
        cloud = CloudSession(Path(tempfile.mkdtemp()) / "cloud.json")
        cloud.sign_in(email, password)
        only = set(filter(None, args.only.split(",")))
        passed, total = 0, 0
        for i, (name, question, history, groups, need, forbidden, cites) in enumerate(QUESTIONS):
            if only and name not in only:
                continue
            if total:
                time.sleep(args.pause)
            reply, seconds = ask(cloud, question, history)
            text = " ".join(reply.get("answer", reply.get("error", "")).lower().replace("’", "'").split())  # narrow spaces, curly apostrophes -> plain
            hits = sum(any(w in text for w in group) for group in groups)
            ok = hits >= (need or len(groups)) and not any(w in text for w in forbidden) \
                and set(cites) <= set(reply.get("cited", []))
            if name == "lunch":
                ok = ok and not reply.get("cited")
            passed, total = passed + ok, total + 1
            print(f"{'PASS' if ok else 'FAIL'} {name} ({seconds:.1f} s, {hits}/{len(groups)}, cited {reply.get('cited')})\n"
                  f"  Q: {question}\n  A: {reply.get('answer', reply)}")
        print(f"\nAsk: {passed}/{total}")
    finally:
        if args.keep and user:
            print(f"kept: --reuse {email}:{password}")
        elif user:
            admin("DELETE", f"users/{user}")


if __name__ == "__main__":
    main()
