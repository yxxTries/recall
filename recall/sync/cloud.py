"""Recall's cloud account on this device: sign in once, keep the session, call the Edge Functions.

The device holds only the project URL, the publishable key and the user's own session; row-level
security in the cloud does the rest. The secret key never reaches the device's code paths.
Sign in from a terminal: python -m recall.sync.cloud login
"""
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from recall.config import data_dir

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
REFRESH_MARGIN = 60  # seconds before expiry


def settings() -> tuple[str, str]:
    """(project URL, publishable key) from the environment or the repo's .env file."""
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.startswith("#"):
                env.setdefault(key.strip(), value.strip())
    return env.get("SUPABASE_URL", "").rstrip("/"), env.get("SUPABASE_ANON_KEY", "")


class CloudError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status


def request(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = res.read()
    except urllib.error.HTTPError as e:
        raise CloudError(e.code, e.read().decode(errors="replace")[:300]) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise CloudError(0, str(e)) from None  # offline
    return json.loads(data) if data else {}


class CloudSession:
    def __init__(self, path: Path | None = None, url: str = "", key: str = "") -> None:
        default_url, default_key = settings()
        self.url, self.key = url or default_url, key or default_key
        self.path = path or data_dir() / "cloud.json"
        self.session = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None

    @property
    def signed_in(self) -> bool:
        return bool(self.url and self.key and self.session)

    @property
    def user_id(self) -> str:
        return self.session["user"]["id"] if self.session else ""

    def _auth(self, grant: str, payload: dict) -> None:
        self.session = request("POST", f"{self.url}/auth/v1/token?grant_type={grant}",
                               {"apikey": self.key, "Content-Type": "application/json"}, json.dumps(payload).encode())
        self.path.write_text(json.dumps(self.session), encoding="utf-8")

    def sign_in(self, email: str, password: str) -> None:
        self._auth("password", {"email": email, "password": password})

    def sign_up(self, email: str, password: str) -> dict:
        return request("POST", f"{self.url}/auth/v1/signup", {"apikey": self.key, "Content-Type": "application/json"},
                       json.dumps({"email": email, "password": password}).encode())

    def sign_out(self) -> None:
        self.session = None
        self.path.unlink(missing_ok=True)

    def token(self) -> str:
        """A valid access token, refreshed when it's about to expire."""
        if not self.session:
            raise CloudError(401, "not signed in")
        if self.session.get("expires_at", 0) - REFRESH_MARGIN < time.time():
            self._auth("refresh_token", {"refresh_token": self.session["refresh_token"]})
        return self.session["access_token"]

    def call(self, function: str, body: bytes, headers: dict | None = None, timeout: float = 60) -> dict:
        """POST to an Edge Function as the signed-in user."""
        base = {"Authorization": f"Bearer {self.token()}", "apikey": self.key, "Content-Type": "application/json"}
        return request("POST", f"{self.url}/functions/v1/{function}", base | (headers or {}), body, timeout)


def main(args: list[str]) -> None:
    session = CloudSession()
    command = args[0] if args else "status"
    if command in ("login", "signup"):
        email = input("Email: ").strip()
        password = getpass.getpass("Password: ")
        if command == "signup":
            session.sign_up(email, password)
            print("Account created. If the project asks for email confirmation, confirm it, then run login.")
            return
        session.sign_in(email, password)
    elif command == "logout":
        session.sign_out()
    print(f"Signed in as {session.session['user']['email']}" if session.signed_in else "Not signed in")


if __name__ == "__main__":
    main(sys.argv[1:])
