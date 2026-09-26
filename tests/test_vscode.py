"""VS Code capture: the extension's views become memories; secrets and repeats are skipped."""
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from recall.capture.vscode import EditorCapture, IngestServer, new_blocks

EXTENSION = Path(__file__).resolve().parent.parent / "vscode-extension"


def view(lines, first_line=1, path=r"C:\demo\src\app.py"):
    return {"path": path, "relative": "src/app.py", "workspace": "demo", "language": "python",
            "first_line": first_line, "lines": lines}


def test_new_blocks_widens_new_lines_with_context():
    lines = [f"l{i}" for i in range(10)]
    seen = set(lines) - {"l4", "l5"}
    assert new_blocks(lines, 100, seen, context=1) == [(103, ["l3", "l4", "l5", "l6"])]
    assert new_blocks(lines, 1, set(lines)) == []


def test_first_view_is_stored_with_a_link_back_to_the_line():
    out = []
    EditorCapture(out.append).ingest(view(["def fuse(a, b):", "    return a + b"], first_line=40))
    [e] = out
    assert e["app"] == "code.exe" and e["title"] == "src/app.py - demo"
    assert e["url"] == "vscode://file/C:/demo/src/app.py:40"
    assert e["text"] == "def fuse(a, b):\n    return a + b"


def test_scrolling_stores_only_the_new_part():
    out = []
    capture = EditorCapture(out.append)
    capture.ingest(view([f"line {i}" for i in range(1, 51)], first_line=1))
    capture.ingest(view([f"line {i}" for i in range(21, 71)], first_line=21))  # scrolled 20 lines
    assert len(out) == 2
    assert out[1]["text"].splitlines()[0] == "line 49"  # 2 lines of context before line 51
    assert out[1]["url"].endswith(":49")


def test_secret_files_are_never_stored():
    out = []
    capture = EditorCapture(out.append)
    for name in (".env", "server.pem", "id_rsa", "aws_credentials.json", "secrets.yaml"):
        capture.ingest(view(["API_KEY=abc123"], path=rf"C:\demo\{name}"))
    assert out == []


def post(port, token, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/vscode", data=json.dumps(body).encode(),
                                 headers={"X-Recall-Token": token, "Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def test_ingest_server_needs_the_token(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    views = []
    server = IngestServer(views.append)
    server.start()
    try:
        info = json.loads((tmp_path / "ingest.json").read_text())
        assert post(info["port"], "wrong", view(["x = 1"])) == 403
        assert post(info["port"], info["token"], view(["x = 1"])) == 204
    finally:
        server.stop()
    assert views == [view(["x = 1"])]
    assert not (tmp_path / "ingest.json").exists()


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_extension_js():
    result = subprocess.run(["node", "--test", "test/extension.test.js"], cwd=EXTENSION,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
