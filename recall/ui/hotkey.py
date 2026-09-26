"""A system-wide hotkey (RegisterHotKey) served by its own message-loop thread."""
import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
VK_SPACE = 0x20
WM_HOTKEY, WM_QUIT, WM_USER, PM_NOREMOVE = 0x0312, 0x0012, 0x0400, 0x0000
HOTKEY_ID = 1
# Tried in order; the first one no other app holds wins.
CANDIDATES = [
    (MOD_CONTROL | MOD_SHIFT, VK_SPACE, "Ctrl+Shift+Space"),
    (MOD_WIN | MOD_ALT, VK_SPACE, "Win+Alt+Space"),
    (MOD_ALT | MOD_SHIFT, VK_SPACE, "Alt+Shift+Space"),
    (MOD_CONTROL | MOD_SHIFT | MOD_ALT, VK_SPACE, "Ctrl+Shift+Alt+Space"),
]


class Hotkey:
    def __init__(self, on_press, candidates=CANDIDATES) -> None:
        self.on_press = on_press
        self.candidates = candidates
        self.label = ""  # the hotkey actually registered, e.g. "Ctrl+Alt+Space"
        self._ready = threading.Event()
        self._thread_id = 0
        self._thread = threading.Thread(target=self._run, name="hotkey", daemon=True)

    def start(self) -> str:
        """The registered hotkey's label, or "" if every candidate is taken."""
        self._thread.start()
        self._ready.wait(timeout=5)
        return self.label

    def stop(self) -> None:
        user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, WM_USER, WM_USER, PM_NOREMOVE)  # create the queue
        self._thread_id = kernel32.GetCurrentThreadId()
        for modifiers, key, label in self.candidates:
            if user32.RegisterHotKey(None, HOTKEY_ID, modifiers | MOD_NOREPEAT, key):
                self.label = label
                break
            log.info("%s is taken by another app", label)
        if not self.label:
            log.warning("no free search hotkey; use the tray's Search item")
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.on_press()
                except Exception:
                    log.exception("hotkey handler failed")
        if self.label:
            user32.UnregisterHotKey(None, HOTKEY_ID)
