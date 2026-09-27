"""Recall's own pages, served by Recall itself on http://localhost:8766.

/ is your cloud account: sign in, create an account or sign out, without a terminal. The tray's
cloud item opens it, and the sign-up confirmation email lands on it.
/oauth/consent is the consent screen for AI agents. When an agent (Claude Code, Cursor...) connects
to Recall's MCP server, Supabase Auth sends your browser here. Recall is already signed in, so you
only choose Allow or Deny; the agent then gets read-only access to your memory.
A per-run nonce stops other web pages from approving for you, or signing Recall in to their account.
"""
import hmac
import html
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from recall.sync.cloud import DASHBOARD, CloudError, CloudSession, request

log = logging.getLogger(__name__)
PORT = 8766
ORIGIN = f"http://localhost:{PORT}"
PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Recall</title>
<style>:root{{--bg:#f4f6f7;--card:#fff;--ink:#152026;--muted:#5a6972;--line:#d9e0e4;--accent:#0b6e78;--on:#fff;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1417;--card:#151d21;--ink:#e3eaed;--muted:#93a3ab;--line:#27343a;
--accent:#56c2cb;--on:#062a2e;--bad:#ff8a80}}}}
body{{font:15px/1.5 "Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--ink);max-width:30rem;margin:4rem auto;padding:0 1rem}}
h1{{font-size:1.5rem;margin:0 0 1rem}}a{{color:var(--accent)}}.muted{{color:var(--muted);font-size:.9rem}}.error{{color:var(--bad)}}
label{{display:grid;gap:.3rem;margin-bottom:.8rem;font-size:.85rem;color:var(--muted)}}
input{{font:inherit;color:var(--ink);background:var(--card);padding:.55rem .7rem;border:1px solid var(--line);border-radius:8px}}
button{{font:inherit;padding:.5rem 1.2rem;margin:.3rem .5rem 0 0;border-radius:8px;border:1px solid var(--line);
background:var(--card);color:var(--ink);cursor:pointer}}
.allow{{background:var(--accent);border-color:var(--accent);color:var(--on);font-weight:600}}
code{{display:block;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:.4rem .6rem;
margin-top:.3rem;font-size:.85rem;word-break:break-all}}</style>
<h1>{heading}</h1>{body}"""


def friendly(e: CloudError) -> str:
    if e.status == 0:
        return "Couldn't reach the cloud. Check your connection and try again."
    if "Invalid login credentials" in e.reason:
        return "That email and password don't match a Recall account."
    if "Email not confirmed" in e.reason:
        return "Confirm your email first: open the link we sent you, then sign in here."
    return e.reason


class ConsentServer:
    def __init__(self, cloud: CloudSession, port: int = PORT, on_account=None) -> None:
        self.cloud = cloud
        self.on_account = on_account  # called after a sign-in or sign-out here
        self.nonce = secrets.token_urlsafe(16)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                if url.path != "/oauth/consent":  # the account page; the sign-up confirmation email lands here too
                    return server.account(self)
                server.show(self, parse_qs(url.query).get("authorization_id", [""])[0])

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(min(length, 4096)).decode())
                field = lambda k: form.get(k, [""])[0]
                path = urlparse(self.path).path
                if (path not in ("/oauth/consent", "/account") or self.headers.get("Origin") not in (ORIGIN, None)
                        or not hmac.compare_digest(field("nonce"), server.nonce)):
                    return self.page(403, "Recall", "<p>This request didn't come from Recall's own page.</p>")
                if path == "/account":
                    return server.change_account(self, field("action"), field("email").strip(), field("password"))
                server.decide(self, field("authorization_id"), field("action"))

            def page(self, code: int, client: str, body: str) -> None:
                heading = "Recall" if client == "Recall" else f"Connect {client}?"
                data = PAGE.format(heading=html.escape(heading), body=body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Frame-Options", "DENY")  # no clickjacking the Allow button
                self.end_headers()
                self.wfile.write(data)

            def redirect(self, location: str) -> None:
                self.send_response(303)
                self.send_header("Location", location)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="oauth-consent", daemon=True)

    def _auth(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {"apikey": self.cloud.key, "Authorization": f"Bearer {self.cloud.token()}",
                   "Content-Type": "application/json"}
        return request(method, f"{self.cloud.url}/auth/v1/oauth/authorizations/{quote(path)}", headers,
                       json.dumps(body).encode() if body else None)

    def account(self, handler, message: str = "", status: int = 200, email: str = "") -> None:
        e = html.escape
        form = lambda fields: f"<form method=post action=/account><input type=hidden name=nonce value='{self.nonce}'>{fields}</form>"
        mcp = f"{self.cloud.url}/functions/v1/mcp"
        if self.cloud.signed_in:
            body = (f"<p>Signed in as <b>{e(self.cloud.email)}</b>. What you do in tracked apps goes to your private "
                    "cloud memory, so you can search it from any device.</p>"
                    f"<p><a href='{DASHBOARD}'>Open the dashboard</a> to ask your memory questions.</p>"
                    "<p>Connect any AI agent that speaks MCP: add this server URL, then choose Allow when this page asks."
                    f"<code>{e(mcp)}</code></p><p class=muted>Claude Code:"
                    f"<code>claude mcp add --transport http recall {e(mcp)}</code>"
                    f"A client that only runs local commands:<code>npx -y mcp-remote {e(mcp)}</code></p>"
                    + form("<button name=action value=signout>Sign out</button>"))
        else:
            body = ("<p>Sign in to keep your memory in the cloud: search it from any device, ask it questions on the "
                    "dashboard and let your AI agents read it. Only you can see it.</p>"
                    + form(f"<label>Email<input name=email type=email autocomplete=username required value='{e(email)}'"
                           f"{'' if email else ' autofocus'}></label><label>Password<input name=password type=password "
                           f"autocomplete=current-password required minlength=6{' autofocus' if email else ''}></label>"
                           "<button class=allow name=action value=signin>Sign in</button>"
                           "<button name=action value=signup>Create account</button>")
                    + "<p class=muted>Without an account, Recall still remembers and searches on this device.</p>")
        note = f"<p class={'error' if status >= 400 else 'muted'}>{e(message)}</p>" if message else ""
        handler.page(status, "Recall", note + body)

    def change_account(self, handler, action: str, email: str, password: str) -> None:
        try:
            if action == "signout":
                self.cloud.sign_out()
            elif action == "signin":
                self.cloud.sign_in(email, password)
            elif action == "signup":
                if "access_token" not in self.cloud.sign_up(email, password):  # the project confirms emails first
                    return self.account(handler, "Account created. Open the link we emailed you, then sign in here.",
                                        email=email)
                self.cloud.sign_in(email, password)
            else:
                return self.account(handler, "Choose Sign in or Create account.", 400, email)
        except CloudError as e:
            return self.account(handler, friendly(e), 400, email)
        log.info("cloud account: %s", action)
        if self.on_account:
            self.on_account()
        handler.redirect("/")

    def show(self, handler, authorization_id: str) -> None:
        if not self.cloud.signed_in:
            return handler.page(401, "an agent", "<p>Recall isn't signed in to the cloud on this device. "
                                "<a href='/'>Sign in</a>, then connect the agent again.</p>")
        try:
            details = self._auth("GET", authorization_id)
        except CloudError as e:
            return handler.page(400, "an agent", f"<p>Couldn't load this request ({html.escape(str(e))}).</p>")
        if "authorization_id" not in details:  # already approved earlier
            return handler.redirect(details["redirect_url"])
        client = details["client"].get("name") or details["client"].get("id", "an agent")
        e = html.escape
        body = (f"<p><b>{e(client)}</b> wants read-only access to your Recall memory as "
                f"<b>{e(details['user']['email'])}</b>: episodes, timeline, threads and digests from all your devices.</p>"
                f"<p>It will return to <code>{e(details['redirect_uri'])}</code>.</p>"
                f"<form method=post><input type=hidden name=authorization_id value='{e(authorization_id)}'>"
                f"<input type=hidden name=nonce value='{self.nonce}'>"
                "<button class=allow name=action value=approve>Allow</button>"
                "<button name=action value=deny>Deny</button></form>")
        handler.page(200, client, body)

    def decide(self, handler, authorization_id: str, action: str) -> None:
        if action not in ("approve", "deny"):
            return handler.page(400, "an agent", "<p>Choose Allow or Deny.</p>")
        try:
            result = self._auth("POST", f"{authorization_id}/consent", {"action": action})
        except CloudError as e:
            return handler.page(400, "an agent", f"<p>That didn't work ({html.escape(str(e))}).</p>")
        log.info("agent access %s", "approved" if action == "approve" else "denied")
        handler.redirect(result["redirect_url"])

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread.is_alive():
            self.httpd.shutdown()
        self.httpd.server_close()
