"""Tells the rest of Recall when a tracked app is in use, from Windows events only (no polling).

ForegroundWatcher: SetWinEventHook(EVENT_SYSTEM_FOREGROUND) -> session_start / session_end.
AudioWatcher: audio session notifications (pycaw) -> audio_start / audio_stop.
Pausing is just set_tracked(set()).
"""
import ctypes
import logging
import threading
from ctypes import wintypes
from datetime import datetime

import comtypes
import psutil
from pycaw.callbacks import AudioSessionEvents, AudioSessionNotification
from pycaw.utils import AudioUtilities

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
WM_QUIT = 0x0012

WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, WinEventProc,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
]
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


def event(kind: str, **fields) -> dict:
    return {"type": kind, "time": datetime.now().isoformat(timespec="seconds"), **fields}


def window_app(hwnd) -> str | None:
    """Lower-case exe name of the process that owns hwnd."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return None


def window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def windowed_apps() -> set[str]:
    """Exe names of processes that own a visible, titled top-level window."""
    apps = set()

    @WNDENUMPROC
    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            app = window_app(hwnd)
            if app:
                apps.add(app)
        return True

    user32.EnumWindows(collect, 0)
    return apps


class ForegroundWatcher:
    """Emits session_start / session_end as tracked windows gain and lose the foreground."""

    def __init__(self, tracked, on_event, on_new_app=None) -> None:
        self.tracked = {a.lower() for a in tracked}
        self.on_event = on_event
        self.on_new_app = on_new_app  # called once per exe name first seen in the foreground
        self.seen_apps: set[str] = set()
        self.current = None  # {"app", "hwnd", "title"} of the live session
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._thread_id = 0
        self._thread = threading.Thread(target=self._run, name="foreground-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(timeout=5)
        self.set_tracked(set())  # ends any live session

    def set_tracked(self, apps) -> None:
        self.tracked = {a.lower() for a in apps}
        self._switch_to(user32.GetForegroundWindow())

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        proc = WinEventProc(self._on_foreground)  # must outlive the hook
        hook = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND, None, proc, 0, 0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
        )
        self._switch_to(user32.GetForegroundWindow())
        self._ready.set()
        # Out-of-context hooks are delivered through this thread's message loop.
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWinEvent(hook)

    def _on_foreground(self, hook, event_id, hwnd, id_object, id_child, thread, time_ms) -> None:
        try:
            self._switch_to(hwnd)
        except Exception:
            log.exception("foreground event failed")

    def _switch_to(self, hwnd) -> None:
        app = window_app(hwnd) if hwnd else None
        out, new_app = [], None
        with self._lock:
            if app and app not in self.seen_apps:
                self.seen_apps.add(app)
                new_app = app
            live = app in self.tracked
            if self.current and live and self.current["hwnd"] == hwnd:
                return
            if self.current:
                out.append(event("session_end", **self.current))
                self.current = None
            if live:
                self.current = {"app": app, "hwnd": hwnd, "title": window_title(hwnd)}
                out.append(event("session_start", **self.current))
        for e in out:
            self.on_event(e)
        if new_app and self.on_new_app:
            self.on_new_app(new_app)


class _SessionCreated(AudioSessionNotification):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def on_session_created(self, new_session) -> None:
        self.callback(new_session)


class _SessionState(AudioSessionEvents):
    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback

    def on_state_changed(self, new_state, new_state_id) -> None:
        self.callback(new_state)


class AudioWatcher:
    """Emits audio_start / audio_stop when a tracked app starts or stops playing sound."""

    def __init__(self, tracked, on_event) -> None:
        self.tracked = {a.lower() for a in tracked}
        self.on_event = on_event
        self.active: dict[str, set[str]] = {}  # app -> ids of its sessions that are playing
        self._sessions = []  # keep COM objects alive while registered
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._run, name="audio-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        self.set_tracked(set())  # emits audio_stop for anything still playing
        self._stopping.set()
        self._thread.join(timeout=5)

    def set_tracked(self, apps) -> None:
        with self._lock:
            before = self._playing_tracked()
            self.tracked = {a.lower() for a in apps}
            after = self._playing_tracked()
        self._emit_changes(before, after)

    def _run(self) -> None:
        # Audio session callbacks need a multithreaded COM apartment on this thread.
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        try:
            mgr = AudioUtilities.GetAudioSessionManager()
            notifier = _SessionCreated(self._watch)
            mgr.RegisterSessionNotification(notifier)
            mgr.GetSessionEnumerator()  # notifications only start after one enumeration
            for session in AudioUtilities.GetAllSessions():
                self._watch(session)
            self._ready.set()
            self._stopping.wait()
            mgr.UnregisterSessionNotification(notifier)
            for session in self._sessions:
                session.unregister_notification()
        except Exception:
            log.exception("audio watcher failed")
            self._ready.set()
        finally:
            comtypes.CoUninitialize()

    def _watch(self, session) -> None:
        try:
            if session.Process is None:  # the system-sounds session has no process
                return
            try:
                app = session.Process.name().lower()
            except psutil.Error:  # the session outlived its process
                return
            sid = session.InstanceIdentifier
            session.register_notification(_SessionState(lambda state: self._on_state(app, sid, state)))
            self._sessions.append(session)
            if session.State == 1:  # AudioSessionStateActive
                self._on_state(app, sid, "Active")
        except Exception:
            log.exception("could not watch audio session")

    def _on_state(self, app: str, sid: str, state: str) -> None:
        with self._lock:
            before = self._playing_tracked()
            ids = self.active.setdefault(app, set())
            if state == "Active":
                ids.add(sid)
            else:
                ids.discard(sid)
            after = self._playing_tracked()
        self._emit_changes(before, after)

    def _playing_tracked(self) -> set[str]:
        return {app for app, ids in self.active.items() if ids and app in self.tracked}

    def _emit_changes(self, before: set[str], after: set[str]) -> None:
        for app in sorted(before - after):
            self.on_event(event("audio_stop", app=app))
        for app in sorted(after - before):
            self.on_event(event("audio_start", app=app))
