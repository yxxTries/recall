"""Phase 7 gate: the deployed cloud end to end, with two throwaway users. Run with --cloud.

Creating and deleting the users needs the project's secret key from .env; nothing else here uses it.
Deleting a user deletes everything they stored (foreign keys cascade).
"""
import gzip
import json
import secrets
import time
import urllib.request
from datetime import datetime, timedelta

import pytest

from recall.sync.cloud import ENV_FILE, CloudSession, request, settings
from recall.sync.uploader import SyncWorker

pytestmark = pytest.mark.cloud
URL, PUBLISHABLE = settings()
UNDERSTOOD_WITHIN = 120  # seconds from upload to an understood episode (gate M7)
MEETING = [
    "Priya: for the hackathon we have three sponsors left to pick from: Northwind, Contoso and Fabrikam.",
    "Marco: Fabrikam offers the biggest cloud credit but wants their logo on the demo slides.",
    "Priya: Contoso's mentors are free on Saturday morning, which matters more to us than credits.",
    "Marco: then we go with Contoso and tell Fabrikam by Thursday.",
    "Priya: I'll email ⟨EMAIL:1⟩ at Contoso tonight to confirm the mentor slot.",
]


def secret_key() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SUPABASE_SERVICE_ROLE_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY missing from .env")


def admin(method: str, path: str, body: dict | None = None) -> dict:
    key = secret_key()
    return request(method, f"{URL}/auth/v1/admin/{path}",
                   {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   json.dumps(body).encode() if body else None)


def rest(session: CloudSession, table: str, query: str = "select=*") -> list:
    return request("GET", f"{URL}/rest/v1/{table}?{query}",
                   {"apikey": PUBLISHABLE, "Authorization": f"Bearer {session.token()}"})


def mcp(session: CloudSession, method: str, params: dict) -> dict:
    """One JSON-RPC call over Streamable HTTP; the reply may come as JSON or as one SSE event."""
    req = urllib.request.Request(f"{URL}/functions/v1/mcp", method="POST", data=json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(), headers={
        "Authorization": f"Bearer {session.token()}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
    with urllib.request.urlopen(req, timeout=60) as res:
        text = res.read().decode()
    data = "\n".join(line[5:].strip() for line in text.splitlines() if line.startswith("data:")) or text
    return json.loads(data)


def tool(session: CloudSession, name: str, arguments: dict):
    result = mcp(session, "tools/call", {"name": name, "arguments": arguments})["result"]
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])["data"]


@pytest.fixture(scope="module")
def users(tmp_path_factory):
    """Two users; user A signed in on two devices. Yields (A on device 1, A on device 2, B)."""
    made, sessions = [], []
    for name in ("a", "a2", "b"):
        if name != "a2":
            email = f"recall-test-{name}-{secrets.token_hex(4)}@example.com"
            password = secrets.token_urlsafe(18)
            made.append(admin("POST", "users", {"email": email, "password": password, "email_confirm": True})["id"])
        session = CloudSession(tmp_path_factory.mktemp(name) / "cloud.json")
        session.sign_in(email, password)
        sessions.append(session)
    yield sessions
    for user_id in made:
        admin("DELETE", f"users/{user_id}")


def meeting_events() -> list[dict]:
    start = datetime.now() - timedelta(minutes=8)
    at = lambda s: (start + timedelta(seconds=s)).isoformat(timespec="seconds")
    title = "Sponsor sync | Microsoft Teams"
    events = [{"type": "session_start", "time": at(0), "app": "ms-teams.exe", "title": title}]
    events += [{"type": "text", "time": at(20 + 40 * i), "app": "ms-teams.exe", "title": title, "url": "",
                "source": "audio", "text": line} for i, line in enumerate(MEETING)]
    return events + [{"type": "session_end", "time": at(300), "app": "ms-teams.exe", "title": title}]


def test_device_b_finds_by_meaning_what_device_a_captured(users, tmp_path):
    device_1, device_2, _ = users
    sync = SyncWorker(tmp_path, device_1, device_id="device-1", device_name="test laptop")
    sync.add(meeting_events())
    sync.stop()  # closes the episode
    uploaded = time.monotonic()
    assert sync.tick() and sync.outbox.count() == 0

    episode = None
    while time.monotonic() - uploaded < UNDERSTOOD_WITHIN + 30 and not episode:
        time.sleep(5)
        episode = next(iter(rest(device_2, "episodes", "select=episode_id,device_id,worked_on,people,thread_id")), None)
    latency = time.monotonic() - uploaded
    print(f"upload to understood episode: {latency:.0f} s; worked_on: {episode and episode['worked_on']}")
    assert episode, "no understood episode"
    assert latency <= UNDERSTOOD_WITHIN
    assert episode["device_id"] == "device-1" and episode["thread_id"]

    found = device_2.call("search", json.dumps({"query": "which company did we choose to back our hackathon team"}).encode())
    top = found["results"][0]
    print(f"search score {top['score']:.2f}: {top['worked_on']}")
    assert top["episode_id"] == episode["episode_id"] and top["device_id"] == "device-1"

    # The same memory through MCP, as an AI agent would see it.
    tools = mcp(device_2, "tools/list", {})["result"]["tools"]
    assert {t["name"] for t in tools} == {"search_memory", "get_timeline", "get_episode", "list_threads",
                                          "get_thread", "daily_digest"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    now = datetime.now().astimezone()
    timeline = tool(device_2, "get_timeline", {"since": (now - timedelta(hours=1)).isoformat(),
                                               "until": now.isoformat()})
    assert [e["episode_id"] for e in timeline["episodes"]] == [episode["episode_id"]]
    assert timeline["spans"][0]["title"] == "Sponsor sync"
    assert tool(device_2, "get_episode", {"episode_id": episode["episode_id"]})["spans"]
    assert tool(device_2, "get_thread", {"thread_id": episode["thread_id"]})["episodes"]


def test_another_user_sees_nothing(users):
    *_, other = users
    for table in ("episodes", "raw_episodes", "timeline_spans", "threads", "devices"):
        assert rest(other, table) == [], table
    assert other.call("search", json.dumps({"query": "hackathon sponsor"}).encode())["results"] == []
    assert tool(other, "search_memory", {"query": "hackathon sponsor"}) == []
    assert tool(other, "list_threads", {}) == []


def test_a_resent_batch_adds_nothing(users):
    device_1, *_ = users
    batch = {"device_id": "device-3", "episodes": [{
        "episode_id": secrets.token_hex(16), "started": "2026-09-27T09:00:00+01:00", "ended": "2026-09-27T09:05:00+01:00",
        "text": "## notepad.exe · todo.txt\nbook the venue", "spans": [
            {"app": "notepad.exe", "title": "todo.txt", "url": "", "started": "2026-09-27T09:00:00+01:00",
             "ended": "2026-09-27T09:05:00+01:00"}]}]}
    body = gzip.compress(json.dumps(batch).encode())
    for _ in range(2):
        assert device_1.call("ingest", body, {"x-recall-encoding": "gzip"}) == {"accepted": 1}
    assert len(rest(device_1, "raw_episodes", "select=episode_id&device_id=eq.device-3")) == 1
    assert len(rest(device_1, "timeline_spans", "select=title&device_id=eq.device-3")) == 1


def test_an_agent_connects_through_oauth_and_the_consent_page(users):
    """What Claude Code does: register itself, send you to consent, trade the code for a token, call MCP."""
    import base64
    import hashlib
    import re
    import urllib.error
    from urllib.parse import parse_qs, urlencode, urlparse

    from recall.sync.consent import ConsentServer

    device_1, *_ = users
    callback = "http://localhost:33418/callback"
    client = request("POST", f"{URL}/auth/v1/oauth/clients/register", {"Content-Type": "application/json"}, json.dumps({
        "client_name": "Recall test agent", "redirect_uris": [callback], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}).encode())
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    def location(req) -> str:
        try:
            urllib.request.build_opener(NoRedirect).open(req, timeout=30)
        except urllib.error.HTTPError as e:
            assert e.code in (302, 303), e.read()[:300]
            return e.headers["Location"]
        raise AssertionError("expected a redirect")

    authorize = f"{URL}/auth/v1/oauth/authorize?" + urlencode({
        "client_id": client["client_id"], "redirect_uri": callback, "response_type": "code", "state": "s1",
        "code_challenge": challenge, "code_challenge_method": "S256",
        "resource": f"{URL}/functions/v1/mcp"})
    consent_url = location(urllib.request.Request(authorize))
    assert consent_url.startswith("http://localhost:8766/oauth/consent?authorization_id=")

    server = ConsentServer(device_1, port=0)  # Recall's consent page, signed in as user A
    local = f"http://127.0.0.1:{server.httpd.server_address[1]}/oauth/consent"
    server.start()
    try:
        page = urllib.request.urlopen(local + "?" + urlparse(consent_url).query).read().decode()
        assert "Connect Recall test agent?" in page
        nonce = re.search(r"name=nonce value='([^']+)'", page)[1]
        authorization_id = parse_qs(urlparse(consent_url).query)["authorization_id"][0]
        back = location(urllib.request.Request(local, method="POST", data=urlencode(
            {"authorization_id": authorization_id, "action": "approve", "nonce": nonce}).encode()))
    finally:
        server.stop()
    query = parse_qs(urlparse(back).query)
    assert back.startswith(callback) and query["state"] == ["s1"]

    token = request("POST", f"{URL}/auth/v1/oauth/token", {"Content-Type": "application/x-www-form-urlencoded"},
                    urlencode({"grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": callback,
                               "client_id": client["client_id"], "code_verifier": verifier}).encode())
    agent = type("Agent", (), {"token": lambda self: token["access_token"]})()
    assert len(mcp(agent, "tools/list", {})["result"]["tools"]) == 6
    assert tool(agent, "list_threads", {})  # user A's memory, through the agent's own token


def test_yesterday_gets_a_digest(tmp_path):
    """A user whose day is over (3 am or later their time) gets one summary of yesterday."""
    key = secret_key()
    service = {"apikey": key, "Content-Type": "application/json"}
    user = admin("POST", "users", {"email": f"recall-test-d-{secrets.token_hex(4)}@example.com",
                                   "password": secrets.token_urlsafe(18), "email_confirm": True})["id"]
    try:
        now = datetime.utcnow()
        offset = ((12 - now.hour) % 24) * 60  # a timezone where it's midday now, so yesterday is over
        offset = offset - 1440 if offset > 720 else offset
        local_yesterday = (now + timedelta(minutes=offset)).date() - timedelta(days=1)
        start = datetime.combine(local_yesterday, datetime.min.time()) - timedelta(minutes=offset)
        request("POST", f"{URL}/rest/v1/devices", service, json.dumps(
            {"user_id": user, "device_id": "device-d", "utc_offset_minutes": offset}).encode())
        work = [(9, "Planned the hackathon demo script with Priya", ["Demo rehearsal at 4pm"]),
                (11, "Fixed the MCP consent page nonce check", []),
                (15, "Sponsor call: chose Contoso for mentoring", ["Tell Fabrikam by Thursday"])]
        request("POST", f"{URL}/rest/v1/episodes", service, json.dumps([
            {"user_id": user, "episode_id": f"d{hour}", "device_id": "device-d",
             "started": (start + timedelta(hours=hour)).isoformat() + "Z",
             "ended": (start + timedelta(hours=hour, minutes=40)).isoformat() + "Z",
             "worked_on": what, "important": important, "importance": 6} for hour, what, important in work]).encode())
        result = request("POST", f"{URL}/functions/v1/understand", service, b'{"task": "digests"}', timeout=90)
        assert {"day": local_yesterday.isoformat(), "episodes": 3} in result["digests"]
        [digest] = request("GET", f"{URL}/rest/v1/digests?user_id=eq.{user}&select=day,summary,highlights", service)
        print(ascii(f"digest: {digest['summary']} | {digest['highlights']}"))  # the console can't show every character
        assert "Contoso" in digest["summary"] + " ".join(digest["highlights"])
    finally:
        admin("DELETE", f"users/{user}")
