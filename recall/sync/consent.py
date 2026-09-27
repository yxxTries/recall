"""The OAuth consent screen for AI agents, served by Recall itself on http://localhost:8766.

When an agent (Claude Code, Cursor...) connects to Recall's MCP server, Supabase Auth sends your
browser here. Recall is already signed in, so you only choose Allow or Deny; the agent then gets
read-only access to your memory. A per-run nonce stops other web pages from approving for you.
"""
import hmac
import html
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from recall.sync.cloud import CloudError, CloudSession, request

log = logging.getLogger(__name__)
PORT = 8766
ORIGIN = f"http://localhost:{PORT}"
PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Recall · Connect an agent</title>
<style>body{{font:15px system-ui;max-width:28rem;margin:4rem auto;padding:0 1rem;color:#222}}
button{{font:inherit;padding:.5rem 1.2rem;margin-right:.5rem;border-radius:6px;border:1px solid #888;cursor:pointer}}
.allow{{background:#222;color:#fff}}code{{background:#f2f2f2;padding:0 .2rem}}</style>
<h1>{heading}</h1>{body}"""


class ConsentServer:
    def __init__(self, cloud: CloudSession, port: int = PORT) -> None:
        self.cloud = cloud
        self.nonce = secrets.token_urlsafe(16)
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                if url.path != "/oauth/consent":  # e.g. the sign-up confirmation email lands on the Site URL
                    return self.page(200, "Recall", "<p>If you just confirmed your email, you're done: close this tab "
                                     "and run <code>python -m recall.sync.cloud login</code>.</p>")
                server.show(self, parse_qs(url.query).get("authorization_id", [""])[0])

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                form = parse_qs(self.rfile.read(min(length, 4096)).decode())
                field = lambda k: form.get(k, [""])[0]
                if (urlparse(self.path).path != "/oauth/consent" or self.headers.get("Origin") not in (ORIGIN, None)
                        or not hmac.compare_digest(field("nonce"), server.nonce)):
                    return self.page(403, "Refused", "<p>This request didn't come from Recall's consent page.</p>")
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

    def show(self, handler, authorization_id: str) -> None:
        if not self.cloud.signed_in:
            return handler.page(401, "an agent", "<p>Recall isn't signed in to the cloud on this device. Run "
                                "<code>python -m recall.sync.cloud login</code>, then connect again.</p>")
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
