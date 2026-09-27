"""Phase 7: the uploader against a fake cloud: redaction, network cuts, no gaps and no duplicates."""
import gzip
import json
import threading
from datetime import datetime, timedelta

from recall.sync.cloud import CloudError
from recall.sync.uploader import MAX_BATCHES, Outbox, SyncWorker, aware

T0 = datetime(2026, 9, 26, 14, 0)


class FakeCloud:
    """Stores episodes by id like the ingest function; `down` makes calls fail as if offline."""

    signed_in = True

    def __init__(self) -> None:
        self.episodes: dict[str, dict] = {}
        self.calls = 0
        self.down = False
        self.fail_with: int | None = None

    def call(self, function, body, headers=None, timeout=60):
        self.calls += 1
        if self.down:
            raise CloudError(0, "offline")
        if self.fail_with:
            status, self.fail_with = self.fail_with, None
            raise CloudError(status, "error")
        batch = json.loads(gzip.decompress(body))
        for e in batch["episodes"]:
            self.episodes.setdefault(e["episode_id"], e)
        return {"accepted": len(batch["episodes"])}


def task(start: float, words: str, n: int = 12) -> list[dict]:
    """n text events 20 s apart in one VS Code workspace."""
    return [{"type": "text", "time": (T0 + timedelta(seconds=start + 20 * i)).isoformat(timespec="seconds"),
             "app": "code.exe", "title": f"{words.split()[0]}.py - {words.split()[0]} - Visual Studio Code",
             "url": f"vscode://file/C:/dev/{words.split()[0]}.py:1",
             "text": f"{words} step {i}" + (" key sk-proj-4fQ9zX2LmN7pR1tV8wY3bC6dE0gH5jK mail dev@contoso.com" if i == 0 else "")}
            for i in range(n)]


def worker(tmp_path, cloud) -> SyncWorker:
    return SyncWorker(tmp_path, cloud, device_id="dev-a", device_name="laptop")


def test_episodes_upload_redacted_and_timezone_aware(tmp_path):
    cloud = FakeCloud()
    sync = worker(tmp_path, cloud)
    sync.add(task(0, "ledger invoice expense payee receipt balance"))
    sync.add(task(400, "cusum drift window episode boundary slack"))  # a new task: the first episode closes
    assert sync.tick()  # the test's times are long past, so the second episode closes as idle
    episode = min(cloud.episodes.values(), key=lambda e: e["started"])
    assert "sk-proj" not in episode["text"] and "contoso" not in episode["text"]
    assert "⟨SECRET:1⟩" in episode["text"] and "⟨EMAIL:2⟩" in episode["text"]
    assert episode["started"] == aware("2026-09-26T14:00:00") and episode["started"][-6] in "+-"
    assert episode["spans"][0]["title"] == "ledger.py - ledger"
    assert sync.vault.restore(episode["text"]).count("dev@contoso.com") == episode["text"].count("⟨EMAIL:2⟩")


def test_a_network_cut_loses_nothing_and_duplicates_nothing(tmp_path):
    cloud = FakeCloud()
    sync = worker(tmp_path, cloud)
    topics = ["ledger invoice expense payee", "cusum drift window boundary", "vendor shortlist acme globex",
              "groq rate limits tokens", "judging demo pitch sponsors", "vector database hnsw pgvector"]
    for i, words in enumerate(topics[:3]):  # tasks 10 min apart: each episode closes as idle
        sync.add(task(600 * i, words))
    assert sync.tick() and len(cloud.episodes) == 3
    cloud.down = True  # network cut mid-sync
    for i, words in enumerate(topics[3:], start=3):
        sync.add(task(600 * i, words))
    assert not sync.tick() and sync.outbox.count() == 3
    cloud.down = False
    cloud.fail_with = 401  # the session expired meanwhile: kept for the next try
    assert not sync.tick() and sync.outbox.count() == 3
    assert sync.tick() and sync.outbox.count() == 0
    starts = sorted(e["started"] for e in cloud.episodes.values())
    assert starts == [aware((T0 + timedelta(seconds=600 * i)).isoformat()) for i in range(6)]  # no gaps


def test_send_now_uploads_the_open_episode_at_once(tmp_path):
    cloud = FakeCloud()
    sync = worker(tmp_path, cloud)
    now = datetime.now()
    sync.add([{"type": "text", "time": (now - timedelta(seconds=40 - 10 * i)).isoformat(timespec="seconds"),
               "app": "code.exe", "title": "demo.py - recall - Visual Studio Code", "url": "",
               "text": f"judges watch the demo step {i}"} for i in range(4)])
    sync.start()
    assert sync.tick() and not cloud.episodes  # still open: the timer alone would wait 5 idle minutes
    sent, done = [], threading.Event()
    sync.send_now(lambda reached, episodes: (sent.append((reached, episodes)), done.set()))
    assert done.wait(5)
    sync.stop()
    assert sent == [(True, 1)] and len(cloud.episodes) == 1


def test_resent_batches_are_harmless_and_bad_ones_are_dropped(tmp_path):
    cloud = FakeCloud()
    sync = worker(tmp_path, cloud)
    sync.add(task(0, "ledger invoice expense payee"))
    sync.stop()
    body = sync.outbox.oldest()[1]
    sync.outbox.put(body)  # the same batch twice, as after a crash between upload and delete
    assert sync.tick() and len(cloud.episodes) == 1 and cloud.calls == 2
    sync.outbox.put(body)
    cloud.fail_with = 400
    assert sync.tick() and sync.outbox.count() == 0


def test_the_outbox_is_bounded(tmp_path):
    outbox = Outbox(tmp_path / "outbox.db")
    for i in range(MAX_BATCHES + 10):
        outbox.put(str(i).encode())
    assert outbox.count() == MAX_BATCHES and outbox.oldest()[1] == b"10"


def test_nothing_is_sent_while_signed_out(tmp_path):
    cloud = FakeCloud()
    cloud.signed_in = False
    sync = worker(tmp_path, cloud)
    sync.add(task(0, "ledger invoice expense payee"))
    sync.stop()
    assert sync.tick() and cloud.calls == 0 and sync.outbox.count() == 1


def test_a_revoked_session_signs_out_and_loses_nothing(tmp_path, monkeypatch):
    from recall.sync import cloud

    def refused(method, url, headers, body=None, timeout=30):  # what Supabase says to a revoked refresh token
        raise CloudError(400, '{"error_code":"validation_failed","msg":"Refresh token is not valid"}')

    monkeypatch.setattr(cloud, "request", refused)
    path = tmp_path / "cloud.json"
    stored = {"access_token": "a", "refresh_token": "r", "expires_at": 0, "user": {"id": "u1", "email": "me@example.com"}}
    path.write_text(json.dumps(stored))
    session = cloud.CloudSession(path, url="https://p.supabase.co", key="k")
    sync = worker(tmp_path, session)
    signed_out = []
    sync.on_signed_out = lambda: signed_out.append(True)
    sync.add(task(0, "ledger invoice expense payee"))
    sync.stop()
    assert not sync.tick() and signed_out == [True]
    assert not session.signed_in and not path.exists() and sync.outbox.count() == 1  # kept, not dropped as a bad batch
    assert sync.tick() and sync.outbox.count() == 1 and signed_out == [True]
    path.write_text(json.dumps(stored))  # signed in again from a terminal: no restart needed
    assert session.signed_in


def test_the_account_page_signs_in_and_out_only_from_itself(tmp_path, monkeypatch):
    import re
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    from recall.sync import cloud, consent

    def fake_auth(method, url, headers, body=None, timeout=30):
        creds = json.loads(body)
        if url.endswith("/signup"):  # this project confirms new accounts by email: no session yet
            return {"id": "u2", "email": creds["email"], "confirmation_sent_at": "2026-09-27T09:00:00Z"}
        if creds["password"] != "right-password":
            raise CloudError(400, '{"code":400,"error_code":"invalid_credentials","msg":"Invalid login credentials"}')
        return {"access_token": "a", "refresh_token": "r", "expires_at": 9e9, "user": {"id": "u1", "email": creds["email"]}}

    monkeypatch.setattr(cloud, "request", fake_auth)
    session = cloud.CloudSession(tmp_path / "cloud.json", url="https://p.supabase.co", key="k")
    changed = []
    server = consent.ConsentServer(session, port=0, on_account=lambda: changed.append(session.signed_in))
    base = f"http://127.0.0.1:{server.httpd.server_address[1]}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None

    def post(form: dict, origin: str | None = None) -> tuple[int, str]:
        req = urllib.request.Request(base + "/account", data=urlencode(form).encode(), headers={"Origin": origin} if origin else {})
        try:
            with urllib.request.build_opener(NoRedirect).open(req) as res:
                return res.status, res.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    server.start()
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Sign in" in page and "Create account" in page
        nonce = re.search(r"name=nonce value='([^']+)'", page)[1]
        me = {"email": "me@example.com", "password": "right-password"}
        assert post({**me, "action": "signin", "nonce": "forged"})[0] == 403
        assert post({**me, "action": "signin", "nonce": nonce}, origin="https://evil.example")[0] == 403  # login CSRF
        assert not session.signed_in and changed == []
        status, page = post({**me, "password": "wrong", "action": "signin", "nonce": nonce})
        assert status == 400 and "match a Recall account" in page and not session.signed_in
        assert "value='me@example.com'" in page  # no retyping the email
        status, page = post({"email": "new@example.com", "password": "pw123456", "action": "signup", "nonce": nonce})
        assert status == 200 and "Open the link we emailed you" in page and not session.signed_in
        assert post({**me, "action": "signin", "nonce": nonce}, origin=consent.ORIGIN)[0] == 303 and changed == [True]
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Signed in as <b>me@example.com</b>" in page and "https://p.supabase.co/functions/v1/mcp" in page
        assert post({"action": "signout", "nonce": nonce})[0] == 303 and changed == [True, False]
        assert not session.signed_in and not (tmp_path / "cloud.json").exists()
    finally:
        server.stop()


def test_consent_page_approves_only_with_its_nonce(tmp_path, monkeypatch):
    import re
    import urllib.error
    import urllib.request

    from recall.sync import consent

    calls = []

    def fake_request(method, url, headers, body=None, timeout=30):
        calls.append((method, url.rsplit("/v1/", 1)[1], body))
        if method == "GET":
            return {"authorization_id": "abc", "redirect_uri": "http://localhost:33418/callback",
                    "client": {"id": "c1", "name": "Claude Code"}, "user": {"id": "u1", "email": "me@example.com"},
                    "scope": "openid"}
        return {"redirect_url": "http://localhost:33418/callback?code=xyz"}

    monkeypatch.setattr(consent, "request", fake_request)
    session = type("S", (), {"signed_in": True, "url": "https://p.supabase.co", "key": "k", "token": lambda self: "t"})()
    server = consent.ConsentServer(session, port=0)
    base = f"http://127.0.0.1:{server.httpd.server_address[1]}/oauth/consent"
    server.start()
    try:
        page = urllib.request.urlopen(f"{base}?authorization_id=abc").read().decode()
        assert "Connect Claude Code?" in page and "me@example.com" in page
        nonce = re.search(r"name=nonce value='([^']+)'", page)[1]

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        post = lambda form: opener.open(urllib.request.Request(base, data=form.encode(), method="POST"))
        try:
            post("authorization_id=abc&action=approve&nonce=wrong")
            raise AssertionError("approved without the nonce")
        except urllib.error.HTTPError as e:
            assert e.code == 403
        try:
            post(f"authorization_id=abc&action=approve&nonce={nonce}")
        except urllib.error.HTTPError as e:
            assert e.code == 303 and e.headers["Location"] == "http://localhost:33418/callback?code=xyz"
        assert calls[-1] == ("POST", "oauth/authorizations/abc/consent", b'{"action": "approve"}')
    finally:
        server.stop()
