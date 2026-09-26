"""VS Code editors: the lines you view arrive from the Recall Companion extension over localhost.

VS Code hides editor text from UI Automation, so the extension (vscode-extension/) posts the
active editor's visible lines here. Only lines not seen before in that file are stored, each
run with a little surrounding code for context.
"""
import hmac
import json
import logging
import re
import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from recall.config import data_dir
from recall.watcher import event

log = logging.getLogger(__name__)

CONTEXT_LINES = 2
MAX_BODY = 1_000_000
MAX_FILES = 200  # files whose seen-lines we remember
SENSITIVE = re.compile(r"^\.env|\.(pem|key|pfx|p12)$|^id_(rsa|ed25519|ecdsa)|secret|credential|password", re.I)


def new_blocks(lines: list[str], first_line: int, seen: set[str], context: int = CONTEXT_LINES):
    """Runs of lines not in `seen`, widened by `context` lines: [(first line number, lines)]."""
    fresh = [i for i, line in enumerate(lines) if line.strip() and line not in seen]
    spans = []
    for i in fresh:
        lo, hi = max(0, i - context), min(len(lines) - 1, i + context)
        if spans and lo <= spans[-1][1] + 1:
            spans[-1][1] = hi
        else:
            spans.append([lo, hi])
    return [(first_line + lo, lines[lo:hi + 1]) for lo, hi in spans]


class EditorCapture:
    def __init__(self, on_text) -> None:
        self.on_text = on_text
        self._seen: OrderedDict[str, set[str]] = OrderedDict()  # file path -> lines already stored
        self._lock = threading.Lock()

    def ingest(self, view: dict) -> int:
        """Store what's new in one editor view; returns the number of blocks emitted."""
        path = view["path"]
        if SENSITIVE.search(Path(path).name):
            return 0
        with self._lock:
            seen = self._seen.pop(path, set())
            self._seen[path] = seen  # most recently used last
            while len(self._seen) > MAX_FILES:
                self._seen.popitem(last=False)
            blocks = new_blocks(view["lines"], view["first_line"], seen)
            seen.update(line for line in view["lines"] if line.strip())
        title = f"{view['relative']} - {view['workspace']}" if view.get("workspace") else view["relative"]
        for line_no, block in blocks:
            self.on_text(event(
                "text", app="code.exe", title=title, text="\n".join(block),
                url=f"vscode://file/{path.replace(chr(92), '/')}:{line_no}",  # opens VS Code at that line
            ))
        return len(blocks)


class IngestServer:
    """POST /vscode on 127.0.0.1, guarded by a per-run token that only this user can read."""

    def __init__(self, on_view) -> None:
        self.token = secrets.token_hex(16)
        self.on_view = on_view
        self.file = data_dir() / "ingest.json"
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if self.path != "/vscode" or not 0 < length <= MAX_BODY:
                    return self.reply(404)
                if not hmac.compare_digest(self.headers.get("X-Recall-Token", ""), server.token):
                    return self.reply(403)
                try:
                    server.on_view(json.loads(self.rfile.read(length)))
                except Exception:
                    log.exception("bad VS Code view")
                    return self.reply(400)
                self.reply(204)

            def reply(self, code: int) -> None:
                self.send_response_only(code)
                self.end_headers()

            def log_message(self, *args):  # keep the default per-request stderr lines out of the console
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="vscode-ingest", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.file.write_text(json.dumps({"port": self.port, "token": self.token}), encoding="utf-8")
        log.info("VS Code ingest listening on 127.0.0.1:%d", self.port)

    def stop(self) -> None:
        self.file.unlink(missing_ok=True)
        if self._thread.is_alive():  # shutdown() waits for serve_forever, so only call it if that runs
            self.httpd.shutdown()
        self.httpd.server_close()
