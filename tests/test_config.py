import pytest
from pathlib import Path

from resume_tracker.config import (
    DEFAULT_CONFIG,
    load_config,
    save_config,
    set_config_value,
    validate_config,
)


def test_load_config_returns_defaults_when_no_file(tmp_path):
    cfg = load_config(tracker_root=tmp_path / "nonexistent")
    assert cfg["scan_interval_minutes"] == DEFAULT_CONFIG["scan_interval_minutes"]
    assert cfg["stale_threshold_days"] == DEFAULT_CONFIG["stale_threshold_days"]
    assert ".resume-tracker" in cfg["ignored_folders"]


def test_save_and_reload_roundtrip(tmp_path):
    tracker_root = tmp_path / ".resume-tracker"
    cfg = load_config(tracker_root=tracker_root)
    cfg["scan_interval_minutes"] = 30
    save_config(cfg, tracker_root=tracker_root)

    reloaded = load_config(tracker_root=tracker_root)
    assert reloaded["scan_interval_minutes"] == 30
    # Defaults still present for untouched keys
    assert reloaded["stale_threshold_days"] == DEFAULT_CONFIG["stale_threshold_days"]


def test_save_merges_nested_maps(tmp_path):
    tracker_root = tmp_path / ".resume-tracker"
    cfg = load_config(tracker_root=tracker_root)
    cfg["file_extension_to_skill_map"][".xyz"] = "XYZLang"
    save_config(cfg, tracker_root=tracker_root)

    reloaded = load_config(tracker_root=tracker_root)
    assert reloaded["file_extension_to_skill_map"][".xyz"] == "XYZLang"
    assert reloaded["file_extension_to_skill_map"][".py"] == "Python"


def test_validate_config_passes_defaults():
    cfg = load_config(tracker_root=None)
    errors = validate_config(cfg)
    assert errors == []


def test_validate_config_catches_bad_interval():
    cfg = load_config(tracker_root=None)
    cfg["scan_interval_minutes"] = 0
    errors = validate_config(cfg)
    assert any("scan_interval_minutes" in e for e in errors)


def test_set_config_value_int(tmp_path):
    cfg = load_config(tracker_root=tmp_path)
    cfg = set_config_value(cfg, "scan_interval_minutes", "45")
    assert cfg["scan_interval_minutes"] == 45
    assert isinstance(cfg["scan_interval_minutes"], int)


def test_set_config_value_float(tmp_path):
    cfg = load_config(tracker_root=tmp_path)
    cfg = set_config_value(cfg, "stale_threshold_days", "5.5")
    assert cfg["stale_threshold_days"] == 5.5


def test_set_config_value_string(tmp_path):
    cfg = load_config(tracker_root=tmp_path)
    cfg = set_config_value(cfg, "notification_method", "log")
    assert cfg["notification_method"] == "log"
