from recall import config


def test_first_load_creates_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    assert config.load_config() == {"tracked_apps": []}
    assert (tmp_path / "config.json").exists()


def test_saved_config_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    config.save_config({"tracked_apps": ["notepad.exe"]})
    assert config.load_config()["tracked_apps"] == ["notepad.exe"]
