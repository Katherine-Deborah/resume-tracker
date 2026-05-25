"""Integration tests for watcher.py.

Uses watchdog's PollingObserver (reliable on Windows) instead of the native
ReadDirectoryChanges observer. Each test gets its own temp directory so there
is no cross-test state.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from watchdog.observers.polling import PollingObserver

from resume_tracker.notifier import LogNotifier
from resume_tracker.utils import read_json, write_json, utc_now, is_template_filled
from resume_tracker.watcher import (
    _TrackerEventHandler,
    _reconcile,
    _initial_status,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_cfg(watch_dir: Path) -> dict:
    return {
        "watch_directory": str(watch_dir),
        "scan_interval_minutes": 60,
        "stale_threshold_days": 3,
        "ignored_folders": [".resume-tracker", "node_modules", ".git", "__pycache__", ".venv"],
        "ignored_patterns": [".*"],
    }


def _init_tracker(watch_dir: Path) -> tuple[Path, dict]:
    """Create a minimal tracker root and return (tracker_root, cfg)."""
    tracker_root = watch_dir / ".resume-tracker"
    tracker_root.mkdir(parents=True, exist_ok=True)
    (tracker_root / "logs").mkdir(exist_ok=True)
    write_json(tracker_root / "project_registry.json", {"projects": []}, lock=False)
    cfg = _make_cfg(watch_dir)
    return tracker_root, cfg


@pytest.fixture
def env(tmp_path):
    """Yields (watch_dir, tracker_root, cfg)."""
    watch_dir = tmp_path / "Documents"
    watch_dir.mkdir()
    tracker_root, cfg = _init_tracker(watch_dir)
    yield watch_dir, tracker_root, cfg


@pytest.fixture
def live(env):
    """Yields (observer, handler, watch_dir, tracker_root, cfg) with observer running."""
    watch_dir, tracker_root, cfg = env
    notifier = LogNotifier(tracker_root / "logs")
    handler = _TrackerEventHandler(watch_dir, tracker_root, cfg, notifier)
    observer = PollingObserver(timeout=0.1)
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()
    yield observer, handler, watch_dir, tracker_root, cfg
    observer.stop()
    observer.join()


def _registry(tracker_root: Path) -> dict:
    return read_json(tracker_root / "project_registry.json")


def _wait(condition, timeout=3.0, interval=0.1):
    """Poll condition() until True or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# Tests: startup reconciliation (no observer needed)
# ---------------------------------------------------------------------------

def test_reconcile_bootstraps_preexisting_folders(env):
    watch_dir, tracker_root, cfg = env
    (watch_dir / "project-alpha").mkdir()
    (watch_dir / "project-beta").mkdir()

    notifier = LogNotifier(tracker_root / "logs")
    _reconcile(watch_dir, tracker_root, cfg, notifier)

    registry = _registry(tracker_root)
    names = {p["name"] for p in registry["projects"]}
    assert "project-alpha" in names
    assert "project-beta" in names
    assert (watch_dir / "project-alpha" / ".tracker").is_dir()
    assert (watch_dir / "project-beta" / "requirements.md").is_file()


def test_reconcile_skips_ignored_folders(env):
    watch_dir, tracker_root, cfg = env
    (watch_dir / "node_modules").mkdir()
    (watch_dir / ".git").mkdir()

    notifier = LogNotifier(tracker_root / "logs")
    _reconcile(watch_dir, tracker_root, cfg, notifier)

    registry = _registry(tracker_root)
    assert registry["projects"] == []


def test_reconcile_skips_resume_tracker_itself(env):
    watch_dir, tracker_root, cfg = env
    # .resume-tracker already exists as the tracker root
    notifier = LogNotifier(tracker_root / "logs")
    _reconcile(watch_dir, tracker_root, cfg, notifier)

    registry = _registry(tracker_root)
    assert all(p["name"] != ".resume-tracker" for p in registry["projects"])


def test_reconcile_marks_missing_project_abandoned(env):
    watch_dir, tracker_root, cfg = env
    # Pre-register a project whose folder no longer exists
    now = utc_now()
    fake_entry = {
        "id": "test-uuid",
        "name": "vanished-project",
        "path": str(watch_dir / "vanished-project"),
        "status": "active",
        "created_at": now,
        "last_activity": now,
        "requirements_filled": False,
        "requirements_stale": False,
        "nag_count": 0,
        "last_nag": "",
        "milestones_total": 0,
        "milestones_completed": 0,
        "detected_skills": [],
        "declared_skills": [],
        "resume_synced": False,
    }
    write_json(tracker_root / "project_registry.json", {"projects": [fake_entry]}, lock=False)

    notifier = LogNotifier(tracker_root / "logs")
    _reconcile(watch_dir, tracker_root, cfg, notifier)

    registry = _registry(tracker_root)
    assert registry["projects"][0]["status"] == "abandoned"


def test_reconcile_does_not_overwrite_existing_tracker_dir(env):
    watch_dir, tracker_root, cfg = env
    proj_dir = watch_dir / "existing-project"
    proj_dir.mkdir()
    tracker_dir = proj_dir / ".tracker"
    tracker_dir.mkdir()
    sentinel = tracker_dir / "custom_file.txt"
    sentinel.write_text("do not delete me", encoding="utf-8")

    notifier = LogNotifier(tracker_root / "logs")
    _reconcile(watch_dir, tracker_root, cfg, notifier)

    # Custom file should survive
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8") == "do not delete me"


def test_reconcile_does_not_overwrite_existing_requirements_md(env):
    watch_dir, tracker_root, cfg = env
    proj_dir = watch_dir / "existing-project"
    proj_dir.mkdir()
    req_path = proj_dir / "requirements.md"
    req_path.write_text("# My custom content", encoding="utf-8")

    notifier = LogNotifier(tracker_root / "logs")
    _reconcile(watch_dir, tracker_root, cfg, notifier)

    assert req_path.read_text(encoding="utf-8") == "# My custom content"


# ---------------------------------------------------------------------------
# Tests: bootstrap helper (unit-level, via handler)
# ---------------------------------------------------------------------------

def test_bootstrap_creates_all_files(env):
    watch_dir, tracker_root, cfg = env
    notifier = LogNotifier(tracker_root / "logs")
    handler = _TrackerEventHandler(watch_dir, tracker_root, cfg, notifier)

    proj_dir = watch_dir / "new-project"
    proj_dir.mkdir()
    handler._bootstrap_project(proj_dir)

    assert (proj_dir / ".tracker").is_dir()
    assert (proj_dir / ".tracker" / "status.json").is_file()
    assert (proj_dir / ".tracker" / "history.json").is_file()
    assert (proj_dir / "requirements.md").is_file()

    registry = _registry(tracker_root)
    assert len(registry["projects"]) == 1
    assert registry["projects"][0]["name"] == "new-project"
    assert registry["projects"][0]["status"] == "not_started"


def test_bootstrap_idempotent(env):
    watch_dir, tracker_root, cfg = env
    notifier = LogNotifier(tracker_root / "logs")
    handler = _TrackerEventHandler(watch_dir, tracker_root, cfg, notifier)

    proj_dir = watch_dir / "idempotent-project"
    proj_dir.mkdir()
    handler._bootstrap_project(proj_dir)
    handler._bootstrap_project(proj_dir)  # second call should be a no-op

    registry = _registry(tracker_root)
    assert len(registry["projects"]) == 1  # not duplicated


def test_initial_status_shape():
    now = utc_now()
    s = _initial_status(now)
    assert s["status"] == "not_started"
    assert s["file_count"] == 0
    assert s["milestones"]["total"] == 0
    assert s["requirements_complete"] is False


# ---------------------------------------------------------------------------
# Tests: live observer events
# ---------------------------------------------------------------------------

def test_new_folder_triggers_bootstrap(live):
    observer, handler, watch_dir, tracker_root, cfg = live

    proj_dir = watch_dir / "live-project"
    proj_dir.mkdir()

    ok = _wait(lambda: (tracker_root / "project_registry.json").exists() and
                        len(_registry(tracker_root)["projects"]) == 1)
    assert ok, "Registry was not updated after creating a new folder"
    assert (proj_dir / ".tracker").is_dir()
    assert (proj_dir / "requirements.md").is_file()


def test_ignored_folder_not_bootstrapped(live):
    observer, handler, watch_dir, tracker_root, cfg = live

    (watch_dir / "node_modules").mkdir()
    time.sleep(1.0)

    registry = _registry(tracker_root)
    assert registry["projects"] == []


def test_deleted_folder_marks_abandoned(live):
    observer, handler, watch_dir, tracker_root, cfg = live

    proj_dir = watch_dir / "doomed-project"
    proj_dir.mkdir()
    _wait(lambda: len(_registry(tracker_root)["projects"]) == 1)

    shutil.rmtree(proj_dir)
    ok = _wait(lambda: _registry(tracker_root)["projects"][0]["status"] == "abandoned")
    assert ok, "Project was not marked abandoned after folder deletion"


def test_renamed_folder_updates_path(live):
    observer, handler, watch_dir, tracker_root, cfg = live

    old_dir = watch_dir / "old-name"
    new_dir = watch_dir / "new-name"
    old_dir.mkdir()
    _wait(lambda: len(_registry(tracker_root)["projects"]) == 1)

    old_dir.rename(new_dir)
    ok = _wait(lambda: any(p["name"] == "new-name" for p in _registry(tracker_root)["projects"]))
    assert ok, "Registry path was not updated after rename"

    registry = _registry(tracker_root)
    project = registry["projects"][0]
    assert project["name"] == "new-name"
    assert project["path"] == str(new_dir)


def test_requirements_md_fill_detected(live):
    observer, handler, watch_dir, tracker_root, cfg = live

    proj_dir = watch_dir / "fill-me"
    proj_dir.mkdir()
    _wait(lambda: len(_registry(tracker_root)["projects"]) == 1)
    # Let the observer complete its current poll cycle so it has a stable
    # snapshot of requirements.md before we overwrite it with filled content.
    time.sleep(0.3)

    # Write filled requirements.md
    filled_content = """\
# Project: My Awesome Project

## Description
A project that does something great.

## Goals & Milestones
- [x] Build the thing (target: 2026-06-01)

## Completion Criteria
Ship it.

## Technologies & Skills
- Python
- Docker

## Resume Bullets
- Built a thing (2026-05-24)

## Status
- **Status:** active
- **Created:** 2026-05-24T00:00:00Z
- **Last Updated:** 2026-05-24T00:00:00Z
"""
    assert is_template_filled(filled_content), "Test content should be considered filled"
    (proj_dir / "requirements.md").write_text(filled_content, encoding="utf-8")

    ok = _wait(lambda: _registry(tracker_root)["projects"][0].get("requirements_filled") is True)
    assert ok, "requirements_filled was not set after writing a filled requirements.md"

    registry = _registry(tracker_root)
    project = registry["projects"][0]
    assert "Python" in project["declared_skills"]
    assert "Docker" in project["declared_skills"]
    assert project["milestones_total"] == 1


def test_hidden_folder_not_bootstrapped(live):
    observer, handler, watch_dir, tracker_root, cfg = live

    (watch_dir / ".hidden-project").mkdir()
    time.sleep(1.0)

    registry = _registry(tracker_root)
    assert registry["projects"] == []
