"""Data folder and the tracked-app config (config.json)."""
import json
import os
from pathlib import Path


def data_dir() -> Path:
    # RECALL_HOME lets tests (and a second "device" on one machine) use their own folder.
    root = os.environ.get("RECALL_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Recall"
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> dict:
    config = {"tracked_apps": []}
    path = config_path()
    if path.exists():
        config.update(json.loads(path.read_text(encoding="utf-8")))
    else:
        save_config(config)
    return config


def save_config(config: dict) -> None:
    config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")
