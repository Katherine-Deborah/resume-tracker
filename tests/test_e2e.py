"""End-to-end integration tests: scanner + resume pipeline (no live watcher).

Each test builds a real tracker environment in a tmp directory, calls run_scan()
or related public functions directly, and asserts the full pipeline output.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

from resume_tracker.models import MasterResume
from resume_tracker.scanner import dispatch_daily_summary, run_scan
from resume_tracker.utils import read_json, render_requirements_template, utc_now, write_json


# ---------------------------------------------------------------------------
# Shared test content
# ---------------------------------------------------------------------------

def _filled_req(title: str = "Test Project", skills: str = "- Python\n- Docker", bullets: str = "") -> str:
    created = "2026-01-01T00:00:00Z"
    bullet_section = bullets or "- Built the test suite (2026-05-24)\n- Added integration tests (2026-05-24)"
    return f"""\
# Project: {title}

## Description
A project for integration testing.

## Goals & Milestones
- [x] Complete milestone 1 (target: 2026-01-01)
- [ ] Complete milestone 2 (target: 2099-12-31)

## Completion Criteria
Ship the tests.

## Technologies & Skills
{skills}

## Resume Bullets
{bullet_section}

## Status
- **Status:** active
- **Created:** {created}
- **Last Updated:** {created}
"""


def _all_done_req() -> str:
    created = "2026-01-01T00:00:00Z"
    return f"""\
# Project: Done Project

## Description
A finished project.

## Goals & Milestones
- [x] Milestone 1 done (target: 2026-01-01)
- [x] Milestone 2 done (target: 2026-02-01)

## Completion Criteria
Everything is shipped.

## Technologies & Skills
- Python

## Resume Bullets
- Shipped everything (2026-05-24)

## Status
- **Status:** completed
- **Created:** {created}
- **Last Updated:** {created}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_env(tmp_path: Path) -> tuple[Path, Path, dict]:
    """Create a minimal tracker environment. Returns (watch_dir, tracker_root, cfg)."""
    watch_dir = tmp_path / "Documents"
    watch_dir.mkdir()
    tracker_root = watch_dir / ".resume-tracker"
    tracker_root.mkdir()
    (tracker_root / "logs").mkdir()

    cfg = {
        "watch_directory": str(watch_dir),
        "scan_interval_minutes": 60,
        "stale_threshold_days": 3,
        "max_nag_count": 5,
        "daily_summary_hour": 9,
        "ignored_folders": [".resume-tracker"],
        "ignored_patterns": [".*"],
        "file_extension_to_skill_map": {
            ".py": "Python",
            ".ts": "TypeScript",
            ".js": "JavaScript",
        },
        "dependency_file_to_skill_map": {
            "Dockerfile": "Docker",
        },
    }

    resume = MasterResume.empty(utc_now())
    write_json(tracker_root / "master_resume.json", resume.to_dict(), lock=False)
    write_json(tracker_root / "project_registry.json", {"projects": []}, lock=False)

    return watch_dir, tracker_root, cfg


def _add_project(
    watch_dir: Path,
    tracker_root: Path,
    name: str,
    req_content: str,
    extra_files: dict[str, str] | None = None,
    requirements_filled: bool = True,
    nag_count: int = 0,
    last_nag: str = "",
) -> Path:
    """Create project folder + tracking infrastructure and register it."""
    proj = watch_dir / name
    proj.mkdir(exist_ok=True)
    (proj / ".tracker").mkdir(exist_ok=True)
    (proj / ".tracker" / "history.json").write_text("[]", encoding="utf-8")
    (proj / "requirements.md").write_text(req_content, encoding="utf-8")

    if extra_files:
        for fname, content in extra_files.items():
            fpath = proj / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")

    entry = {
        "id": str(uuid.uuid4()),
        "name": name,
        "path": str(proj),
        "status": "not_started",
        "created_at": utc_now(),
        "last_activity": "",
        "requirements_filled": requirements_filled,
        "requirements_stale": False,
        "nag_count": nag_count,
        "last_nag": last_nag,
        "milestones_total": 0,
        "milestones_completed": 0,
        "detected_skills": [],
        "declared_skills": [],
        "resume_synced": False,
    }

    registry_path = tracker_root / "project_registry.json"
    registry = read_json(registry_path) if registry_path.exists() else {"projects": []}
    registry["projects"].append(entry)
    write_json(registry_path, registry, lock=False)

    return proj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_e2e_scan_creates_status_json(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    proj = _add_project(
        watch_dir, tracker_root, "my-project",
        _filled_req(),
        extra_files={"main.py": "print('hello')"},
    )

    run_scan(tracker_root, cfg)

    status_path = proj / ".tracker" / "status.json"
    assert status_path.exists(), "run_scan() must write .tracker/status.json"
    snap = read_json(status_path)
    assert snap["status"] in ("active", "stale", "overdue", "completed", "not_started")
    assert snap["file_count"] >= 1
    assert "milestones" in snap
    assert "detected_skills" in snap


def test_e2e_registry_updated_after_scan(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    _add_project(
        watch_dir, tracker_root, "skill-project",
        _filled_req(skills="- Python\n- Docker"),
        extra_files={"main.py": "x = 1", "Dockerfile": "FROM python:3.11"},
    )

    run_scan(tracker_root, cfg)

    registry = read_json(tracker_root / "project_registry.json")
    entry = registry["projects"][0]
    assert entry["requirements_filled"] is True
    assert "Python" in entry["detected_skills"]
    assert "Docker" in entry["detected_skills"]
    assert entry["milestones_total"] == 2


def test_e2e_resume_bullets_synced(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    _add_project(
        watch_dir, tracker_root, "resume-project",
        _filled_req(bullets="- Built the test suite (2026-05-24)\n- Added CI/CD (2026-05-24)"),
    )

    run_scan(tracker_root, cfg)

    resume = read_json(tracker_root / "master_resume.json")
    assert len(resume["projects"]) == 1
    bullets = resume["projects"][0]["bullets"]
    assert any("Built the test suite" in b for b in bullets)
    assert any("Added CI/CD" in b for b in bullets)


def test_e2e_milestone_completion_marks_completed(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    _add_project(
        watch_dir, tracker_root, "done-project",
        _all_done_req(),
    )

    run_scan(tracker_root, cfg)

    registry = read_json(tracker_root / "project_registry.json")
    assert registry["projects"][0]["status"] == "completed"


def test_e2e_skills_union_across_projects(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    _add_project(
        watch_dir, tracker_root, "proj-a",
        _filled_req(title="Project A", skills="- Python"),
        extra_files={"main.py": ""},
    )
    _add_project(
        watch_dir, tracker_root, "proj-b",
        _filled_req(title="Project B", skills="- TypeScript"),
        extra_files={"app.ts": ""},
    )

    run_scan(tracker_root, cfg)

    resume = read_json(tracker_root / "master_resume.json")
    all_skills = (
        resume["skills"]["languages"]
        + resume["skills"]["frameworks"]
        + resume["skills"]["tools"]
        + resume["skills"]["other"]
    )
    assert "Python" in all_skills
    assert "TypeScript" in all_skills


def test_e2e_nag_counter_increments(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    unfilled = render_requirements_template(utc_now())
    _add_project(
        watch_dir, tracker_root, "lazy-project",
        unfilled,
        requirements_filled=False,
        nag_count=0,
        last_nag="",
    )

    run_scan(tracker_root, cfg)

    registry = read_json(tracker_root / "project_registry.json")
    entry = registry["projects"][0]
    assert entry["nag_count"] == 1, "First scan should increment nag_count to 1"
    assert entry["last_nag"] != "", "last_nag should be set after first nag"


def test_e2e_nag_stops_at_max(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    unfilled = render_requirements_template(utc_now())
    # Set nag_count at max and last_nag far in the past
    _add_project(
        watch_dir, tracker_root, "stale-project",
        unfilled,
        requirements_filled=False,
        nag_count=5,
        last_nag="2020-01-01T00:00:00Z",
    )

    run_scan(tracker_root, cfg)

    registry = read_json(tracker_root / "project_registry.json")
    entry = registry["projects"][0]
    assert entry["nag_count"] == 5, "nag_count must not exceed max_nag_count"
    assert entry["requirements_stale"] is True


def test_e2e_dispatch_daily_summary_logs(tmp_path):
    watch_dir, tracker_root, cfg = _make_env(tmp_path)
    proj = _add_project(
        watch_dir, tracker_root, "summary-project",
        _filled_req(),
        extra_files={"main.py": ""},
    )
    # Write a status.json so summary can read next_due
    status = {
        "last_scan": utc_now(),
        "file_count": 1,
        "file_types": {".py": 1},
        "last_file_modified": utc_now(),
        "days_since_activity": 0.0,
        "status": "active",
        "milestones": {"total": 1, "completed": 0, "overdue": 0, "next_due": "2099-12-31"},
        "detected_skills": ["Python"],
        "requirements_complete": True,
    }
    write_json(proj / ".tracker" / "status.json", status, lock=False)

    # Update registry with a known status so counters work
    registry = read_json(tracker_root / "project_registry.json")
    registry["projects"][0]["status"] = "active"
    write_json(tracker_root / "project_registry.json", registry, lock=False)

    # Clear the module-level logger so LogNotifier attaches a fresh handler to our tmp log_dir.
    logging.getLogger("resume_tracker").handlers.clear()

    dispatch_daily_summary(tracker_root, cfg)

    log_file = tracker_root / "logs" / "tracker.log"
    assert log_file.exists(), "dispatch_daily_summary must write to tracker.log"
    content = log_file.read_text(encoding="utf-8")
    assert "Daily Summary" in content
    assert "Active: 1" in content
