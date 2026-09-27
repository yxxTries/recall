"""The search window: a hidden pywebview page with a small Python API behind it."""
import ctypes
import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from recall.sync.cloud import DASHBOARD, CloudError

log = logging.getLogger(__name__)
PAGE = Path(__file__).with_name("search.html")


def since_for(time_range: str, now: datetime | None = None) -> str | None:
    now = now or datetime.now()
    if time_range == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    if time_range == "week":
        return (now - timedelta(days=7)).isoformat(timespec="seconds")
    return None


def openable(url: str) -> str:
    """Browsers show "example.com/page"; local pages show "C:/dir/page.html"."""
    if re.match(r"^[a-zA-Z]:[\\/]", url):
        return Path(url).as_uri()
    return url if "://" in url else "https://" + url


def copy_to_clipboard(text: str) -> bool:
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    data = text.encode("utf-16-le") + b"\0\0"
    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        return bool(user32.SetClipboardData(13, handle))  # CF_UNICODETEXT; the clipboard now owns it
    finally:
        user32.CloseClipboard()


def cloud_results(rows: list[dict], app: str = "") -> list[dict]:
    """Understood episodes from any device, shaped like local results; opening one shows it on the dashboard."""
    return [{"title": r["worked_on"], "text": "\n".join(r.get("important") or r.get("evidence") or []),
             "app": (r.get("apps") or [""])[0], "url": "", "source": "cloud", "start": r["started"], "time": r["ended"],
             "link": f"{DASHBOARD}/#{r['episode_id']}" if r.get("episode_id") else ""}
            for r in rows if not app or app in (r.get("apps") or [])]


class SearchApi:
    """Called from the page as pywebview.api.<method>. Only public methods are exposed."""

    def __init__(self, store, on_hide=None, cloud=None) -> None:
        self._store = store
        self._on_hide = on_hide
        self._cloud = cloud

    def search(self, query: str, app: str = "", time_range: str = "any", where: str = "device") -> list[dict]:
        query = query.strip()
        filters = {"app": app or None, "since": since_for(time_range)}
        if where == "all" and self.cloud():
            try:
                return self._search_cloud(query, app, filters["since"])
            except CloudError as e:
                log.warning("cloud search failed, showing this device: %s", e)  # offline: local search still works
        rows = self._store.search(query, k=20, **filters) if query else self._store.recent(k=20, **filters)
        return [{"title": row["summary"], "text": row["key_lines"], "app": row["app"], "url": row["url"],
                 "source": row["source"], "start": row["started"], "time": row["ended"]} for row in rows]

    def _search_cloud(self, query: str, app: str, since: str | None) -> list[dict]:
        since = datetime.fromisoformat(since).astimezone().isoformat() if since else None
        if query:
            rows = self._cloud.call("search", json.dumps({"query": query, "since": since, "k": 20}).encode())["results"]
        else:
            rows = self._cloud.select("episodes", "select=episode_id,started,ended,apps,worked_on,important,evidence"
                                      "&order=ended.desc&limit=20" + (f"&ended=gte.{quote(since)}" if since else ""))
        return cloud_results(rows, app)

    def cloud(self) -> bool:
        return bool(self._cloud and self._cloud.signed_in)

    def apps(self) -> list[str]:
        return self._store.apps()

    def open(self, url: str) -> None:
        os.startfile(openable(url))
        self.hide()

    def sign_in(self) -> None:
        from recall.sync.consent import ORIGIN
        self.open(ORIGIN + "/")

    def copy(self, text: str) -> bool:
        return copy_to_clipboard(text)

    def hide(self) -> None:
        if self._on_hide:
            self._on_hide()


class SearchWindow:
    def __init__(self, store, cloud=None) -> None:
        import webview  # loads .NET/WebView2; keep it out of import time for tests

        self.api = SearchApi(store, on_hide=self.hide, cloud=cloud)
        self.visible = False
        self._closing_for_real = False
        self.window = webview.create_window(
            "Recall search", html=PAGE.read_text(encoding="utf-8"), js_api=self.api,
            hidden=True, frameless=True, on_top=True, width=760, height=540, background_color="#16161a",
        )
        self.window.events.closing += self._on_closing

    def show(self) -> None:
        self.visible = True
        self.window.show()
        self.window.evaluate_js("window.onShown && window.onShown()")

    def hide(self) -> None:
        self.visible = False
        self.window.hide()

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    def destroy(self) -> None:
        self._closing_for_real = True
        self.window.destroy()

    def _on_closing(self):
        # Alt+F4 hides the window; only quitting Recall really closes it (and ends the UI loop).
        if self._closing_for_real:
            return True
        self.hide()
        return False
