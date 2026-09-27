"""Reads a tracked window's text through UI Automation (UIA), only after its content changes.

Order of preference per read: the window's first Document text (one fast call, used by
browsers, Electron apps and editors), else a capped walk of the UIA control tree.
VS Code is special: only its chat panels (webviews) are read here, since the rest of its
UIA text is menus and file trees; editor code comes from recall.capture.vscode.
Only lines not seen before in that window are emitted.
"""
import logging
import re
import threading
import time
from collections import OrderedDict

import comtypes
import comtypes.client

from recall.watcher import event

log = logging.getLogger(__name__)

DEBOUNCE_S = 1.5  # read once the content has been quiet this long...
MAX_WAIT_S = 4.0  # ...or at least this often while it keeps changing (a streaming answer, a busy chat): under 5 s
MAX_NODES = 2000
MAX_DOC_CHARS = 200_000
MAX_WINDOWS = 50  # windows whose seen-lines we remember
MAX_SEEN_LINES = 20_000
BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe", "vivaldi.exe"}
PRIVATE_MARKERS = ("InPrivate", "Incognito")
# Chats keep their history on screen; the first read of one sends only its latest lines, the rest counts as seen.
CHAT_APPS = {"code.exe", "ms-teams.exe", "slack.exe", "discord.exe", "whatsapp.exe", "telegram.exe", "signal.exe"}
FIRST_READ_LINES = 80
ADDRESS_BAR_NAME = "Address and search bar"  # Chrome and Edge, English UI
HAS_WORDS = re.compile(r"\w\w")
WEBVIEW_ID = re.compile(r"^vscode-webview://.*?[?&]id=([\w-]+)")
# Chat panel controls that read as text but aren't conversation.
CHAT_NOISE = re.compile(r"^(Thought for \d+s|Copy response to clipboard|Show more|Show less|New session|"
                        r"Queue another message…|Message input)$")


def clean_lines(text: str) -> list[str]:
    """Unique, whitespace-normalised lines that contain at least two word characters, in order."""
    lines = (" ".join(line.replace("￼", " ").split()) for line in text.splitlines())
    return list(dict.fromkeys(line for line in lines if HAS_WORDS.search(line)))


def _uia_module():
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient

    return UIAutomationClient


class WindowReader:
    """UIA reads for one thread. Create it on the thread that uses it (MTA)."""

    def __init__(self) -> None:
        self.U = U = _uia_module()
        self.uia = comtypes.client.CreateObject(U.CUIAutomation8, interface=U.IUIAutomation2)
        self.uia.ConnectionTimeout = 1000  # never hang on an unresponsive app
        self.uia.TransactionTimeout = 3000
        self.document = self.uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_DocumentControlTypeId)
        self.address_bar = self.uia.CreateAndCondition(
            self.uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_EditControlTypeId),
            self.uia.CreatePropertyCondition(U.UIA_NamePropertyId, ADDRESS_BAR_NAME),
        )
        self.value_cache = self.uia.CreateCacheRequest()
        self.value_cache.AddProperty(U.UIA_ValueValuePropertyId)
        self.tree_cache = self.uia.CreateCacheRequest()
        for prop in (U.UIA_ControlTypePropertyId, U.UIA_NamePropertyId, U.UIA_ValueValuePropertyId,
                     U.UIA_IsPasswordPropertyId):
            self.tree_cache.AddProperty(prop)
        self.tree_cache.TreeScope = U.TreeScope_Subtree
        self.tree_cache.TreeFilter = self.uia.ControlViewCondition
        self.leaf_text_types = {
            U.UIA_TextControlTypeId, U.UIA_ListItemControlTypeId, U.UIA_DataItemControlTypeId,
            U.UIA_TreeItemControlTypeId, U.UIA_HyperlinkControlTypeId, U.UIA_HeaderItemControlTypeId,
        }

    def read(self, hwnd: int, app: str) -> tuple[str, str]:
        """(text, url) of a top-level window."""
        root = self.uia.ElementFromHandle(hwnd)
        if app == "code.exe":
            return self._webview_text(root), ""
        text = self._document_text(root) or self._tree_text(hwnd)
        url = self._url(root) if app in BROWSERS else ""
        return text, url

    def _webview_text(self, root) -> str:
        """Text of VS Code's webview panels (Claude Code, Gemini, Codex chats), each read once."""
        U = self.U
        docs = root.FindAllBuildCache(U.TreeScope_Descendants, self.document, self.value_cache)
        seen_ids, parts = set(), []
        for i in range(docs.Length if docs else 0):
            doc = docs.GetElement(i)
            value = doc.GetCachedPropertyValue(U.UIA_ValueValuePropertyId)
            match = WEBVIEW_ID.match(value) if isinstance(value, str) else None
            if not match or match.group(1) in seen_ids:  # a webview nests its page; the outer one holds it all
                continue
            seen_ids.add(match.group(1))
            pattern = doc.GetCurrentPattern(U.UIA_TextPatternId)
            if pattern:
                text = pattern.QueryInterface(U.IUIAutomationTextPattern).DocumentRange.GetText(MAX_DOC_CHARS)
                parts.extend(line for line in text.splitlines() if not CHAT_NOISE.match(line.strip()))
        return "\n".join(parts)

    def _document_text(self, root) -> str:
        doc = root.FindFirst(self.U.TreeScope_Descendants, self.document)
        if not doc:
            return ""
        pattern = doc.GetCurrentPattern(self.U.UIA_TextPatternId)
        if not pattern:
            return ""
        text_pattern = pattern.QueryInterface(self.U.IUIAutomationTextPattern)
        return text_pattern.DocumentRange.GetText(MAX_DOC_CHARS)

    def _tree_text(self, hwnd: int) -> str:
        U = self.U
        root = self.uia.ElementFromHandleBuildCache(hwnd, self.tree_cache)  # one cross-process call
        out, stack, visited = [], [root], 0
        while stack and visited < MAX_NODES:
            el = stack.pop()
            visited += 1
            control_type = el.CachedControlType
            if control_type == U.UIA_EditControlTypeId:
                if not el.GetCachedPropertyValue(U.UIA_IsPasswordPropertyId):
                    value = el.GetCachedPropertyValue(U.UIA_ValueValuePropertyId)
                    if isinstance(value, str):
                        out.append(value)
                continue
            children = el.GetCachedChildren()
            if children and children.Length:
                stack.extend(children.GetElement(i) for i in reversed(range(children.Length)))
            elif control_type in self.leaf_text_types:
                out.append(el.CachedName or "")
        return "\n".join(out)

    def _url(self, root) -> str:
        bar = root.FindFirst(self.U.TreeScope_Descendants, self.address_bar)
        value = bar.GetCurrentPropertyValue(self.U.UIA_ValueValuePropertyId) if bar else ""
        return value if isinstance(value, str) else ""


class TextCapture:
    """Debounced text snapshots of the live tracked window, on its own thread."""

    def __init__(self, on_text) -> None:
        self.on_text = on_text
        self.session = None  # the live session from ForegroundWatcher, or None
        self._seen: OrderedDict[int, set[str]] = OrderedDict()  # hwnd -> lines already emitted
        self._last_change = 0.0
        self._last_read = (0, 0.0)  # (hwnd, when) of the latest read
        self._leaving = None  # a window you switched away from with changes not yet read
        self._wake = threading.Event()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="text-capture", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_session(self, session) -> None:
        old, self.session = self.session, dict(session) if session else None
        hwnd, read_at = self._last_read
        # A window that changed after its last read (a message sent, a line typed) and that you left before the debounce
        # ran out is read once more now, not on your next visit. Windows only passed through were never read: no cost.
        # (>= because a change in the same clock tick as the read's start may not be in it.)
        if old and old["hwnd"] == hwnd and self._last_change >= read_at:
            self._leaving = old
            self._wake.set()
        if session:
            self.content_changed(session)

    def content_changed(self, session) -> None:
        self.session = dict(session)  # carries the latest window title
        self._last_change = time.monotonic()
        self._wake.set()

    def _run(self) -> None:
        # UIA recommends an MTA thread that owns no UI.
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            reader = WindowReader()
            while not self._stopping:
                self._wake.wait()  # no timers while nothing changes
                first = time.monotonic()
                while not self._stopping:
                    self._wake.clear()
                    leaving, self._leaving = self._leaving, None
                    if leaving:
                        self._snapshot(reader, leaving)
                    now = time.monotonic()
                    quiet_left = self._last_change + DEBOUNCE_S - now
                    budget_left = first + MAX_WAIT_S - now
                    if quiet_left <= 0 or budget_left <= 0:
                        break
                    self._wake.wait(min(quiet_left, budget_left))
                session = self.session
                if session and not self._stopping:
                    self._snapshot(reader, session)
        except Exception:
            log.exception("text capture failed")
        finally:
            comtypes.CoUninitialize()

    def _snapshot(self, reader: WindowReader, session: dict) -> None:
        if any(marker in session["title"] for marker in PRIVATE_MARKERS):
            return
        hwnd = session["hwnd"]
        self._last_read = (hwnd, time.monotonic())
        started = time.perf_counter()
        try:
            text, url = reader.read(hwnd, session["app"])
        except comtypes.COMError as e:  # e.g. the window closed mid-read
            log.debug("read failed for %s: %s", session["app"], e)
            return
        read_ms = (time.perf_counter() - started) * 1000

        first = hwnd not in self._seen
        seen = self._seen.pop(hwnd, set())
        self._seen[hwnd] = seen  # most recently used last
        while len(self._seen) > MAX_WINDOWS:
            self._seen.popitem(last=False)
        lines = clean_lines(text)
        new = [line for line in lines if line not in seen]
        if first and session["app"] in CHAT_APPS:
            new = new[-FIRST_READ_LINES:]  # a chat's history is earlier work: only its latest messages are now
        if len(seen) + len(new) > MAX_SEEN_LINES:
            seen.clear()
        seen.update(lines)
        log.info("read %s: %d lines, %d new, %.0f ms", session["app"], len(lines), len(new), read_ms)
        if new:
            self.on_text(event(
                "text", app=session["app"], title=session["title"], url=url, hwnd=hwnd,
                text="\n".join(new), read_ms=round(read_ms),
            ))
