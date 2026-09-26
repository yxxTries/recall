from recall import config


def test_first_load_creates_default_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    first = config.load_config()
    assert first["tracked_apps"] == []
    assert len(first["device_id"]) == 32
    assert (tmp_path / "config.json").exists()
    assert config.load_config()["device_id"] == first["device_id"]  # stable across runs


def test_saved_config_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("RECALL_HOME", str(tmp_path))
    config.save_config({"tracked_apps": ["notepad.exe"], "device_id": "d1"})
    assert config.load_config() == {"tracked_apps": ["notepad.exe"], "device_id": "d1"}
