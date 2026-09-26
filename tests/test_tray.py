from types import SimpleNamespace

from recall.config import load_config
from recall.main import TrayApp


def test_toggling_an_app_in_the_picker_saves_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    app = TrayApp(load_config())
    item = SimpleNamespace(text="notepad.exe")  # what pystray passes for a clicked menu item

    app.toggle_app(app.icon, item)
    assert load_config()["tracked_apps"] == ["notepad.exe"]
    assert app.audio.tracked == {"notepad.exe"}

    app.toggle_app(app.icon, item)
    assert load_config()["tracked_apps"] == []
    assert app.audio.tracked == set()


def test_pause_stops_tracking_without_forgetting_apps(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    app = TrayApp({"tracked_apps": ["notepad.exe"]})

    app.toggle_pause(app.icon, None)
    assert app.foreground.tracked == set()
    assert app.tracked == {"notepad.exe"}

    app.toggle_pause(app.icon, None)
    assert app.foreground.tracked == {"notepad.exe"}
