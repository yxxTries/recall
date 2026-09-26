"""Real windows and input for integration tests (they briefly take the foreground)."""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
WM_CHAR = 0x0102

# Tracked vs untracked test windows differ only by interpreter: pythonw.exe vs python.exe.
PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
PYTHON = sys.executable
TK_WINDOW = "import sys, tkinter; r = tkinter.Tk(); r.title(sys.argv[1]); r.geometry('320x120'); r.mainloop()"

# A native window with a multi-line EDIT and a password EDIT: both fully visible to UI Automation.
EDIT_WINDOW = r"""
import ctypes, sys
from ctypes import wintypes as w
u, k = ctypes.windll.user32, ctypes.windll.kernel32
u.CreateWindowExW.restype = w.HWND
u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", w.UINT), ("lpfnWndProc", ctypes.c_void_p), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]
wc = WNDCLASS(lpfnWndProc=ctypes.cast(u.DefWindowProcW, ctypes.c_void_p).value, hInstance=k.GetModuleHandleW(None),
              hbrBackground=6, lpszClassName="RecallTestWindow")
u.RegisterClassW(ctypes.byref(wc))
top = u.CreateWindowExW(0, "RecallTestWindow", sys.argv[1], 0x10CF0000, 100, 100, 520, 320, None, None, None, None)
u.CreateWindowExW(0, "EDIT", "", 0x50A00004, 0, 0, 500, 220, top, 1, None, None)
u.CreateWindowExW(0, "EDIT", "", 0x50800020, 0, 230, 500, 24, top, 2, None, None)
m = w.MSG()
while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(m)); u.DispatchMessageW(ctypes.byref(m))
"""


def open_window(exe: str, title: str, script: str = TK_WINDOW):
    proc = subprocess.Popen([exe, "-c", script, title])
    for _ in range(100):
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return proc, hwnd
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"window {title!r} never appeared")


def edit_controls(hwnd) -> tuple[int, int]:
    """(text box, password box) of an EDIT_WINDOW."""
    return user32.GetDlgItem(hwnd, 1), user32.GetDlgItem(hwnd, 2)


def post_text(edit_hwnd, text: str) -> None:
    # WM_CHAR straight to the control: types without touching the real keyboard focus.
    for ch in text:
        user32.PostMessageW(edit_hwnd, WM_CHAR, ord(ch), 0)
        time.sleep(0.005)


def focus(hwnd) -> None:
    # Only the foreground thread may move the foreground; borrow its input state for the switch.
    # (No synthetic keys: an Alt tap would leave the target in menu mode, eating keystrokes.)
    foreground_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, foreground_thread, True)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.AttachThreadInput(me, foreground_thread, False)


def wait_for(predicate, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False
