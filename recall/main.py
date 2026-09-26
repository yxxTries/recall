"""Recall entry point: logging, PID file and the tray icon."""
import logging
import os
from logging.handlers import RotatingFileHandler

import pystray
from PIL import Image, ImageDraw

from recall.config import data_dir, load_config

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


class TrayApp:
    def __init__(self) -> None:
        self.paused = False
        self.icon = pystray.Icon(
            "recall",
            make_icon(live=True),
            "Recall",
            menu=pystray.Menu(
                pystray.MenuItem("Pause", self.toggle_pause, checked=lambda item: self.paused),
                pystray.MenuItem("Quit", self.quit),
            ),
        )

    def toggle_pause(self, icon, item) -> None:
        self.paused = not self.paused
        icon.icon = make_icon(live=not self.paused)
        icon.title = "Recall (paused)" if self.paused else "Recall"
        log.info("capture %s", "paused" if self.paused else "resumed")

    def quit(self, icon, item) -> None:
        log.info("quit from tray")
        icon.stop()

    def on_ready(self, icon) -> None:
        icon.visible = True
        log.info("tray icon visible")

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
        TrayApp().run()
    finally:
        pid_file.unlink(missing_ok=True)
        log.info("Recall stopped")
