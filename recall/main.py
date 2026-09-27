"""Recall entry point: logging, PID file and the tray icon."""
import logging
import os
import socket
from logging.handlers import RotatingFileHandler

import pystray
from PIL import Image, ImageDraw

from recall.capture.text_uia import TextCapture
from recall.capture.vscode import EditorCapture, IngestServer
from recall.config import data_dir, load_config, save_config
from recall.memory.store import MemoryStore
from recall.memory.worker import MemoryWorker
from recall.sync.cloud import CloudSession
from recall.sync.consent import ConsentServer
from recall.sync.uploader import SyncWorker
from recall.ui.hotkey import SEND_CANDIDATES, Hotkey
from recall.watcher import AudioWatcher, ForegroundWatcher, windowed_apps

log = logging.getLogger("recall")


def setup_logging() -> None:
    log_dir = data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "recall.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def make_icon(live: bool) -> Image.Image:
    # Solid red dot while capture is live, hollow grey ring while paused.
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if live:
        draw.ellipse((8, 8, 56, 56), fill=(220, 50, 50, 255))
    else:
        draw.ellipse((8, 8, 56, 56), outline=(150, 150, 150, 255), width=6)
    return img


def log_event(e: dict) -> None:
    log.info("event %s", e)


class TrayApp:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.tracked = {a.lower() for a in config["tracked_apps"]}
        self.known_apps = windowed_apps() | self.tracked  # choices in the "Tracked apps" menu
        self.paused = False
        self.store = MemoryStore(data_dir() / "memory.db", config["device_id"])
        self.sync = SyncWorker(data_dir(), CloudSession(), config["device_id"], socket.gethostname())
        self.memory = MemoryWorker(self.store, self.sync)
        self.consent = None  # started in on_ready: the OAuth consent page for AI agents
        self.text = TextCapture(self.memory.submit)
        self.editor = EditorCapture(self.memory.submit)
        self.ingest = IngestServer(self.on_vscode_view)
        self.foreground = ForegroundWatcher(
            self.tracked, self.on_event, on_new_app=self.on_new_app, on_content=self.text.content_changed
        )
        self.audio = AudioWatcher(self.tracked, log_event)
        self.hotkey = Hotkey(self.toggle_search)
        self.send_hotkey = Hotkey(self.send_now, SEND_CANDIDATES, name="send")
        self.search = None  # created in run(): it needs the UI loop
        self.icon = pystray.Icon(
            "recall",
            make_icon(live=True),
            "Recall",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: f"Search   {self.hotkey.label}".strip(),
                                 lambda icon, item: self.toggle_search(), default=True),
                pystray.MenuItem(lambda item: f"Send to cloud now   {self.send_hotkey.label}".strip(),
                                 lambda icon, item: self.send_now()),
                pystray.MenuItem("Tracked apps", pystray.Menu(self.app_items)),
                pystray.MenuItem("Pause", self.toggle_pause, checked=lambda item: self.paused),
                pystray.MenuItem("Quit", self.quit),
            ),
        )

    def toggle_search(self) -> None:
        if self.search:
            self.search.toggle()

    def send_now(self) -> None:
        # For demos: what you just did reaches the cloud now, not after 5 idle minutes.
        if not self.sync.cloud.signed_in:
            self.icon.notify("Not signed in to the cloud", "Recall")
            return
        log.info("send to cloud now")
        self.sync.send_now(self.on_sent)

    def on_sent(self, reached: bool, episodes: int) -> None:
        if not reached:
            message = "Couldn't reach the cloud; Recall will keep trying"
        elif episodes:
            message = f"Sent {episodes} episode{'s' if episodes > 1 else ''} to the cloud; understood in about 30 s"
        else:
            message = "Nothing new to send"
        log.info(message)
        self.icon.notify(message, "Recall")

    def on_vscode_view(self, view: dict) -> None:
        # The extension posts whenever VS Code has focus; Recall decides whether it's tracked.
        if not self.paused and "code.exe" in self.tracked:
            self.editor.ingest(view)

    def on_event(self, e: dict) -> None:
        log_event(e)
        if e["type"] == "session_start":
            self.text.set_session(e)
        elif e["type"] == "session_end":
            self.text.set_session(None)
        self.memory.submit(e)  # time in a tracked app is context even when it shows no readable text

    def app_items(self):
        for app in sorted(self.known_apps):
            yield pystray.MenuItem(app, self.toggle_app, checked=lambda item: item.text in self.tracked)

    def on_new_app(self, app: str) -> None:
        # A new exe reached the foreground: offer it in the picker.
        self.known_apps.add(app)
        self.icon.update_menu()

    def toggle_app(self, icon, item) -> None:
        self.tracked ^= {item.text}
        self.config["tracked_apps"] = sorted(self.tracked)
        save_config(self.config)
        log.info("tracked apps: %s", self.config["tracked_apps"])
        self.apply_tracking()

    def toggle_pause(self, icon, item) -> None:
        self.paused = not self.paused
        icon.icon = make_icon(live=not self.paused)
        icon.title = "Recall (paused)" if self.paused else "Recall"
        log.info("capture %s", "paused" if self.paused else "resumed")
        self.apply_tracking()

    def apply_tracking(self) -> None:
        live = set() if self.paused else self.tracked
        self.foreground.set_tracked(live)
        self.audio.set_tracked(live)

    def quit(self, icon, item) -> None:
        log.info("quit from tray")
        self.foreground.stop()
        self.audio.stop()
        self.text.stop()
        self.ingest.stop()
        self.memory.stop()
        self.sync.stop()  # after memory: closes the open episode and keeps it in the outbox
        if self.consent:
            self.consent.stop()
        self.hotkey.stop()
        self.send_hotkey.stop()
        icon.stop()
        if self.search:
            self.search.destroy()  # ends the UI loop in run()

    def on_ready(self, icon) -> None:
        icon.visible = True
        log.info("tray icon visible")
        self.memory.start()
        self.sync.start()
        log.info("cloud sync %s", "on" if self.sync.cloud.signed_in else "off (not signed in)")
        try:
            self.consent = ConsentServer(self.sync.cloud)
            self.consent.start()
        except OSError:
            log.warning("agent consent page unavailable: port in use")
        self.ingest.start()
        self.text.start()
        self.foreground.start()
        self.audio.start()
        log.info("watchers started")
        if self.hotkey.start():
            log.info("search hotkey ready: %s", self.hotkey.label)
        if self.send_hotkey.start():
            log.info("send hotkey ready: %s", self.send_hotkey.label)
        self.icon.update_menu()

    def run(self) -> None:
        import webview

        from recall.ui.search import SearchWindow

        self.search = SearchWindow(self.store, self.sync.cloud)
        self.icon.run_detached(setup=self.on_ready)
        # The search window's UI loop owns the main thread; like the tray's, it idles in GetMessage.
        webview.start()


def main() -> None:
    setup_logging()
    config = load_config()
    pid_file = data_dir() / "recall.pid"
    pid_file.write_text(str(os.getpid()))
    log.info("Recall started (pid %d), tracking %d apps", os.getpid(), len(config["tracked_apps"]))
    try:
        TrayApp(config).run()
    finally:
        pid_file.unlink(missing_ok=True)
        log.info("Recall stopped")
