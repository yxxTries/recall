"""Recall entry point: logging, PID file and the tray icon."""
import logging
import os
from logging.handlers import RotatingFileHandler

import pystray
from PIL import Image, ImageDraw

from recall.config import data_dir, load_config, save_config
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
        self.foreground = ForegroundWatcher(self.tracked, log_event, on_new_app=self.on_new_app)
        self.audio = AudioWatcher(self.tracked, log_event)
        self.icon = pystray.Icon(
            "recall",
            make_icon(live=True),
            "Recall",
            menu=pystray.Menu(
                pystray.MenuItem("Tracked apps", pystray.Menu(self.app_items)),
                pystray.MenuItem("Pause", self.toggle_pause, checked=lambda item: self.paused),
                pystray.MenuItem("Quit", self.quit),
            ),
        )

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
        icon.stop()

    def on_ready(self, icon) -> None:
        icon.visible = True
        log.info("tray icon visible")
        self.foreground.start()
        self.audio.start()
        log.info("watchers started")

    def run(self) -> None:
        # Blocks in the Win32 message loop, so an idle Recall uses no CPU.
        self.icon.run(setup=self.on_ready)


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
