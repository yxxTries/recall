"""Pre-flight for the 2-minute demo: runs its path for real on a throwaway account and times each step.

An article in Edge (or Chrome), then code written in a separate VS Code window through the Recall Companion
extension, then "send now" (what Ctrl+Alt+Esc does); the cloud must understand the episode, and search by meaning,
MCP and Ask must all find it. Recall's own capture pipeline runs in this process, in a throwaway data folder.
It takes the foreground for about a minute, but only after this PC has been idle for 30 s: don't type meanwhile.
Spends 2 Groq calls. Needs .env with the project's secret key (to create and delete the throwaway account).

  python scripts/demo_check.py            # Edge
  python scripts/demo_check.py --chrome
"""
import argparse
import ctypes
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.helpers import PYTHON, focus, open_window  # noqa: E402
from tests.test_cloud import admin, rest, tool  # noqa: E402

BROWSERS = {"msedge.exe": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            "chrome.exe": r"C:\Program Files\Google\Chrome\Application\chrome.exe"}
CODE = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
TITLE = "Designing idempotent webhook handlers"
ARTICLE = [
    "Webhooks are delivered at least once, so every handler must be idempotent: handling an event twice must leave "
    "the same state as handling it once.",
    "Keep a table keyed on the provider's event id. Insert the id in the same transaction as the side effect and "
    "skip the event when the insert conflicts.",
    "Verify the signature before anything else, and reject events whose timestamp is more than five minutes old.",
    "Return a 2xx within a few seconds and move slow work to a queue, or the provider retries and you create your own "
    "duplicates.",
]
HANDLER = ["import stripe", "from fastapi import FastAPI, Request, Response", "", "app = FastAPI()",
           "processed = set()  # Stripe retries: event ids already handled", "", "@app.post('/webhooks/stripe')",
           "async def stripe_webhook(request: Request):", "    payload = await request.body()",
           "    event = stripe.Webhook.construct_event(payload, request.headers['stripe-signature'], SECRET)",
           "    if event['id'] in processed:", "        return Response(status_code=200)  # a duplicate delivery",
           "    processed.add(event['id'])", "    await queue.put(event)  # acknowledge fast, work later",
           "    return Response(status_code=200)"]
QUERY = "how do I stop the same stripe event from being handled twice"  # a paraphrase: few of the article's words
QUESTION = "What did I do about Stripe sending the same webhook event twice?"

user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
kernel32.GetTickCount.restype = wintypes.DWORD
user32.GetForegroundWindow.restype = wintypes.HWND
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]


def idle_seconds() -> float:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return (kernel32.GetTickCount() - info.dwTime) / 1000


def hold(hwnd) -> None:
    """Bring hwnd to the front and give it the last input event (a zero mouse move), so no app takes it back."""
    focus(hwnd)
    time.sleep(0.05)
    move = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, 0x0001, 0, 0))
    user32.SendInput(1, ctypes.byref(move), ctypes.sizeof(INPUT))


def find_window(part: str, timeout: float = 60) -> int:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = []

        @WNDENUMPROC
        def check(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            if n and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if part in buf.value:
                    found.append(hwnd)
            return True

        user32.EnumWindows(check, 0)
        if found:
            return found[0]
        time.sleep(0.3)
    raise RuntimeError(f"no window titled {part!r}")


def kill(marker: str) -> None:
    """Every process started with this profile folder: the throwaway browser and VS Code."""
    for p in psutil.process_iter(["cmdline"]):
        try:
            if any(marker in a for a in p.info["cmdline"] or []):
                p.kill()
        except psutil.Error:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", action="store_true", help="read the article in Chrome instead of Edge")
    args = parser.parse_args()
    browser = "chrome.exe" if args.chrome else "msedge.exe"
    root = Path(tempfile.mkdtemp(prefix="recall-demo-check-"))
    home = root / "home"
    home.mkdir()
    os.environ["RECALL_HOME"] = str(home)  # Recall and the VS Code extension both use this throwaway folder
    (home / "config.json").write_text(json.dumps({"tracked_apps": [browser, "code.exe"], "device_id": "demo-check"}))

    from recall.config import load_config
    from recall.main import TrayApp
    from recall.sync.cloud import CloudSession

    email, password = f"recall-demo-check-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(18)
    user_id = admin("POST", "users", {"email": email, "password": password, "email_confirm": True})["id"]
    CloudSession(home / "cloud.json").sign_in(email, password)
    steps, idle_proc, app = [], None, None
    t0 = time.monotonic()

    def step(name: str, ok: bool, detail: str = "") -> bool:
        steps.append((round(time.monotonic() - t0, 1), name, ok, detail))
        print(f"{steps[-1][0]:6.1f} s  {'ok  ' if ok else 'FAIL'} {name}  {detail}", flush=True)
        return ok

    try:
        page = root / "article.html"
        page.write_text(f"<!doctype html><meta charset='utf-8'><title>{TITLE}</title><h1>{TITLE}</h1>"
                        + "".join(f"<p>{p}</p>" for p in ARTICLE), encoding="utf-8")
        subprocess.Popen([BROWSERS[browser], f"--user-data-dir={root / 'browser'}", "--no-first-run",
                          "--no-default-browser-check", "--disable-sync", "--disable-extensions",  # else Edge signs in
                          "--new-window", page.as_uri()])  # and a synced extension opens its own tab
        project = root / "shop-api"
        project.mkdir()
        handler = project / "webhooks.py"
        handler.write_text("# Stripe webhooks\n")
        subprocess.Popen(["cmd", "/c", str(CODE), "--user-data-dir", str(root / "vscode"), "--disable-workspace-trust",
                          "--skip-welcome", "--new-window", str(project), str(handler)])
        w_article, w_code = find_window(TITLE), find_window("webhooks.py")
        idle_proc, w_idle = open_window(PYTHON, "recall-demo-check")  # untracked
        print("waiting until this PC has been idle for 30 s...", flush=True)
        while idle_seconds() < 30:
            time.sleep(1)
        hold(w_idle)

        app = TrayApp(load_config())  # the tray app's capture and sync, without the tray or the search window
        for part in (app.memory, app.sync, app.ingest, app.text, app.foreground, app.audio):
            part.start()
        t0 = time.monotonic()
        hold(w_article)
        time.sleep(20)
        hold(w_code)
        for i in range(0, len(HANDLER), 4):  # written in bursts; the extension sends what's on screen
            handler.write_text("# Stripe webhooks\n" + "\n".join(HANDLER[:i + 4]) + "\n")
            time.sleep(5)
        time.sleep(2)
        hold(w_idle)

        sent = []
        app.sync.send_now(lambda reached, episodes: sent.append((reached, episodes)))  # Ctrl+Alt+Esc
        sent_at = time.monotonic()
        while not sent and time.monotonic() - sent_at < 30:
            time.sleep(0.1)
        if not step("send now: uploaded", bool(sent) and sent[0][0] and sent[0][1] >= 1, f"{sent}"):
            return 1
        cloud = app.sync.cloud
        understood = False
        while not understood and time.monotonic() - sent_at < 120:
            time.sleep(1)
            raw = rest(cloud, "raw_episodes", "select=understood")
            understood = bool(raw) and all(r["understood"] for r in raw)
        episodes = rest(cloud, "episodes", "select=episode_id,worked_on,apps")
        step("understood in the cloud", understood and bool(episodes),
             f"{time.monotonic() - sent_at:.1f} s after send: {episodes[0]['worked_on'] if episodes else ''}")
        ids = {e["episode_id"] for e in episodes}
        both = bool(episodes) and {browser, "code.exe"} <= set(episodes[0]["apps"])
        step(f"{browser} and VS Code both in the episode", both, f"{episodes[0]['apps'] if episodes else []}")
        hits = cloud.call("search", json.dumps({"query": QUERY, "k": 5}).encode())["results"]
        step("search by meaning (a paraphrase)", bool(hits) and hits[0]["episode_id"] in ids, f"{len(hits)} hit(s)")
        found = tool(cloud, "search_memory", {"query": "stripe webhook duplicate events"})
        step("MCP search_memory", bool(found), f"{len(found)} result(s)")
        reply = cloud.call("ask", json.dumps({"question": QUESTION}).encode(), timeout=90)
        step("Ask answers, citing the episode", bool(set(reply.get("cited") or []) & ids), reply.get("answer", "")[:120])
        titles = {s["title"] for s in rest(cloud, "timeline_spans", "select=title")}
        foreign = [t for t in titles if TITLE not in t and "webhooks.py" not in t]
        step("only the demo's windows were captured", not foreign,
             f"another window came to the front (was this PC used meanwhile?); run it again: {foreign}" if foreign else "")
    finally:
        if app:
            app.quit(SimpleNamespace(stop=lambda: None), None)
        if idle_proc:
            idle_proc.kill()
        kill(str(root / "browser"))
        kill(str(root / "vscode"))
        admin("DELETE", f"users/{user_id}")
    passed = all(ok for _, _, ok, _ in steps)
    print(f"\n{'PASS' if passed else 'FAIL'}: {sum(ok for _, _, ok, _ in steps)}/{len(steps)} steps, "
          f"{steps[-1][0] if steps else 0:.0f} s from the first window to the last check")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
