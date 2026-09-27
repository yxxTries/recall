"""Phase 7: episode segmentation on scripted event streams with known task switches (boundary F1)."""
import random
from datetime import datetime, timedelta

from recall.sync.segment import MAX_CHARS, MAX_EPISODE, Segmenter

T0 = datetime(2026, 9, 26, 14, 0)
COMMON = "the and to of a in is it that for on with this file line page next open".split()
TASKS = [
    dict(app="code.exe", title="segment.py - recall - Visual Studio Code", url="vscode://file/C:/dev/recall/segment.py:1",
         words="episode window cusum drift vector rarity sketch numpy segment boundary threshold slack centroid"),
    dict(app="code.exe", title="ledger.py - budget-app - Visual Studio Code", url="vscode://file/C:/dev/budget-app/ledger.py:1",
         words="invoice expense category ledger total monthly csv parse balance account payee receipt"),
    dict(app="msedge.exe", title="Vector DBs - Personal - Microsoft Edge", url="newsletter.pragmaticengineer.com/p/vectors",
         words="vector database embeddings index hnsw similarity retrieval latency pgvector postgres ann"),
    dict(app="ms-teams.exe", title="Weekly sync | Microsoft Teams", url="",
         words="vendor shortlist acme globex initech trial licence decision friday support contract budget"),
    dict(app="discord.exe", title="#general | Hack Atlantic 2026 - Discord", url="",
         words="judging team submission deadline demo pitch sponsors prizes devpost track mentors"),
    dict(app="msedge.exe", title="Groq docs - Personal - Microsoft Edge", url="console.groq.com/docs/rate-limits",
         words="groq rate limits tokens minute requests day model llama openai compatible structured outputs"),
]
DETOURS = [dict(app="notepad.exe", title="todo.txt - Notepad", url="", words="buy milk dentist tuesday call mum"),
           dict(app="spotify.exe", title="Daily Mix 1 - Spotify", url="", words="")]


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def text(rng, task) -> str:
    vocab = task["words"].split()
    return "\n".join(" ".join(rng.choice(vocab if rng.random() < 0.6 else COMMON) for _ in range(rng.randint(6, 10)))
                     for _ in range(rng.randint(2, 4)))


def stream(seed: int, tasks: int = 7) -> tuple[list[dict], list[float]]:
    """Events for `tasks` back-to-back tasks of 3-12 min with 10-s alt-tab detours; returns (events, switch times)."""
    rng, t, events, switches, previous = random.Random(seed), 0.0, [], [], None
    for n in range(tasks):
        task = rng.choice([x for x in TASKS if x is not previous])
        previous = task
        if n:
            switches.append(t)
        end = t + rng.uniform(180, 720)
        while t < end:
            if rng.random() < 0.1:
                detour = rng.choice(DETOURS)
                events.append({"type": "session_start", "time": at(t), **{k: detour[k] for k in ("app", "title")}})
                if detour["words"]:
                    events.append({"type": "text", "time": at(t + 2), "app": detour["app"], "title": detour["title"],
                                   "url": "", "text": text(rng, detour)})
                t += rng.uniform(5, 15)
            events.append({"type": "text", "time": at(t), "app": task["app"], "title": task["title"],
                           "url": task["url"], "text": text(rng, task)})
            t += rng.uniform(10, 25)
    return events, switches


def boundaries(events: list[dict]) -> list[float]:
    segmenter, episodes = Segmenter(), []
    for e in events:
        episodes += segmenter.add(e)
    episodes += segmenter.close()
    return [(datetime.fromisoformat(ep["started"]) - T0).total_seconds() for ep in episodes[1:]]


def f1(found: list[float], truth: list[float], tolerance: float = 60) -> tuple[float, float, float]:
    unmatched, hits = list(truth), 0
    for b in found:
        match = next((s for s in unmatched if abs(s - b) <= tolerance), None)
        if match is not None:
            unmatched.remove(match)
            hits += 1
    precision, recall = hits / max(len(found), 1), hits / max(len(truth), 1)
    return precision, recall, 2 * precision * recall / max(precision + recall, 1e-9)


def test_boundary_f1_on_scripted_streams():
    found, truth = [], []
    for seed in range(8):
        events, switches = stream(seed)
        offset = len(truth) and max(truth + found) + 3600  # keep streams apart when pooling
        found += [b + offset for b in boundaries(events)]
        truth += [s + offset for s in switches]
    precision, recall, score = f1(found, truth)
    print(f"segmentation boundary precision {precision:.2f}, recall {recall:.2f}, F1 {score:.2f} "
          f"({len(truth)} switches, ±60 s)")
    assert score >= 0.85


def test_a_detour_does_not_split_an_episode():
    events, _ = stream(seed=1, tasks=1)
    assert boundaries(events) == []


def test_idle_and_long_episodes_close():
    segmenter = Segmenter()
    task = TASKS[0]
    make = lambda s: {"type": "text", "time": at(s), "app": task["app"], "title": task["title"],
                      "url": task["url"], "text": f"cusum drift window {s}"}
    assert segmenter.add(make(0)) == [] and segmenter.add(make(40)) == []
    assert segmenter.flush(T0 + timedelta(minutes=3)) == []
    [idle] = segmenter.flush(T0 + timedelta(minutes=6))
    assert (idle["started"], idle["ended"]) == (at(0), at(40))
    closed = []
    for s in range(1000, 1000 + int(MAX_EPISODE.total_seconds()) + 120, 20):
        closed += segmenter.add(make(s))
    assert len(closed) == 1 and closed[0]["started"] == at(1000)


def test_episode_has_spans_and_only_new_distinctive_text():
    segmenter = Segmenter(device_id="dev-a")
    code, edge = TASKS[0], TASKS[2]
    chat = "\n".join(f"line {i}: the cusum threshold decides where episode {i} ends" for i in range(2000))
    segmenter.add({"type": "text", "time": at(0), "app": code["app"], "title": code["title"], "url": code["url"],
                   "text": "the drift window\nthe drift window"})
    segmenter.add({"type": "text", "time": at(20), "app": edge["app"], "title": edge["title"], "url": edge["url"],
                   "text": chat})
    segmenter.add({"type": "session_end", "time": at(50), "app": edge["app"], "title": edge["title"]})
    [episode] = segmenter.close()
    assert episode["device_id"] == "dev-a" and len(episode["episode_id"]) == 32
    assert [(s["app"], s["title"], s["started"], s["ended"]) for s in episode["spans"]] == [
        ("code.exe", "segment.py - recall", at(0), at(0)), ("msedge.exe", "Vector DBs", at(20), at(50))]
    lines = episode["text"].splitlines()
    assert lines[:3] == ["## code.exe · segment.py - recall", "the drift window", "## msedge.exe · Vector DBs"]
    assert len(episode["text"]) <= MAX_CHARS + 100
    segmenter.add({"type": "text", "time": at(60), "app": code["app"], "title": code["title"], "url": code["url"],
                   "text": "the drift window\nsomething new about slack"})
    [later] = segmenter.close()
    assert later["text"] == "## code.exe · segment.py - recall\nsomething new about slack"  # sent lines aren't resent
