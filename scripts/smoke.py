"""Smoke test: launch Recall in a throwaway data folder, check it came up, then stop it."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    home = Path(tempfile.mkdtemp(prefix="recall-smoke-"))
    env = {**os.environ, "RECALL_HOME": str(home)}
    proc = subprocess.Popen([sys.executable, "-m", "recall"], cwd=ROOT, env=env)
    try:
        time.sleep(5)
        log = home / "logs" / "recall.log"
        text = log.read_text(encoding="utf-8") if log.exists() else ""
        checks = {
            "process still running": proc.poll() is None,
            "PID file written": (home / "recall.pid").exists(),
            "config created": (home / "config.json").exists(),
            "tray icon shown": "tray icon visible" in text,
            "watchers started": "watchers started" in text,
            "no errors logged": "ERROR" not in text,
        }
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
