from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from resume_tracker.models import MilestoneEntry, StatusSnapshot
from resume_tracker.scanner import (
    _detect_skills,
    _determine_status,
    _file_activity,
    _milestone_summary,
    _update_history,
    display_status,
    run_scan,
)
from resume_tracker.utils import render_requirements_template, utc_now


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg():
    return {
        "stale_threshold_days": 3,
        "file_extension_to_skill_map": {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
        },
        "dependency_file_to_skill_map": {
            "Dockerfile": "Docker",
            "requirements.txt": "pip (Python)",
        },
    }


def _make_project(tmp_path: Path, name: str = "my-project", status: str = "active") -> tuple[Path, dict]:
    proj_path = tmp_path / name
    proj_path.mkdir()
    (proj_path / ".tracker").mkdir()
    (proj_path / ".tracker" / "history.json").write_text("[]", encoding="utf-8")
    req_content = render_requirements_template(utc_now())
    (proj_path / "requirements.md").write_text(req_content, encoding="utf-8")
    entry = {
        "id": "test-id-1",
        "name": name,
        "path": str(proj_path),
        "status": status,
        "created_at": utc_now(),
        "last_activity": "",
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
    return proj_path, entry


def _make_tracker_root(tmp_path: Path, projects: list[dict]) -> Path:
    tracker_root = tmp_path / ".resume-tracker"
    tracker_root.mkdir()
    (tracker_root / "logs").mkdir()
    registry = {"projects": projects}
    (tracker_root / "project_registry.json").write_text(
        json.dumps(registry, indent=2), encoding="utf-8"
    )
    return tracker_root


# ---------------------------------------------------------------------------
# _determine_status — 5 cases
# ---------------------------------------------------------------------------

def test_determine_status_completed():
    milestones = [
        MilestoneEntry("M1", "2026-01-01", completed=True),
        MilestoneEntry("M2", "2026-02-01", completed=True),
    ]
    assert _determine_status(True, milestones, 0.0, 3, "Done when deployed") == "completed"


def test_determine_status_completed_requires_criteria():
    milestones = [MilestoneEntry("M1", "2026-01-01", completed=True)]
    # no completion criteria → should NOT be "completed"
    result = _determine_status(True, milestones, 0.0, 3, "")
    assert result != "completed"


def test_determine_status_completed_requires_nonempty_milestones():
    # empty milestone list → not completed
    result = _determine_status(True, [], 0.0, 3, "Some criteria")
    assert result != "completed"


def test_determine_status_overdue():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    milestones = [MilestoneEntry("M1", yesterday, completed=False)]
    assert _determine_status(True, milestones, 0.0, 3, "") == "overdue"


def test_determine_status_stale():
    assert _determine_status(True, [], 10.0, 3, "") == "stale"


def test_determine_status_not_started():
    assert _determine_status(False, [], 0.0, 3, "") == "not_started"


def test_determine_status_active():
    assert _determine_status(True, [], 1.0, 3, "") == "active"


# ---------------------------------------------------------------------------
# _milestone_summary
# ---------------------------------------------------------------------------

def test_milestone_summary_empty():
    result = _milestone_summary([])
    assert result == {"total": 0, "completed": 0, "overdue": 0, "next_due": None}


def test_milestone_summary_counts():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    milestones = [
        MilestoneEntry("A", yesterday, completed=False),   # overdue
        MilestoneEntry("B", tomorrow, completed=False),    # upcoming
        MilestoneEntry("C", yesterday, completed=True),    # done (not overdue)
    ]
    result = _milestone_summary(milestones)
    assert result["total"] == 3
    assert result["completed"] == 1
    assert result["overdue"] == 1
    assert result["next_due"] == tomorrow


def test_milestone_summary_next_due_is_soonest():
    d1 = (date.today() + timedelta(days=5)).isoformat()
    d2 = (date.today() + timedelta(days=2)).isoformat()
    milestones = [
        MilestoneEntry("A", d1, completed=False),
        MilestoneEntry("B", d2, completed=False),
    ]
    assert _milestone_summary(milestones)["next_due"] == d2


# ---------------------------------------------------------------------------
# _detect_skills
# ---------------------------------------------------------------------------

def test_detect_skills_extensions(tmp_path, cfg):
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "app.js").write_text("", encoding="utf-8")
    skills = _detect_skills(tmp_path, cfg)
    assert "Python" in skills
    assert "JavaScript" in skills


def test_detect_skills_dependency_file(tmp_path, cfg):
    (tmp_path / "Dockerfile").write_text("FROM python:3.11", encoding="utf-8")
    skills = _detect_skills(tmp_path, cfg)
    assert "Docker" in skills


def test_detect_skills_excludes_hidden_dirs(tmp_path, cfg):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config.py").write_text("", encoding="utf-8")
    # .git is excluded — Python should not be detected from it
    skills = _detect_skills(tmp_path, cfg)
    # Only way Python appears is from .git/config.py — should be absent
    assert "Python" not in skills


# ---------------------------------------------------------------------------
# _file_activity
# ---------------------------------------------------------------------------

def test_file_activity_basic(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.py").write_text("y", encoding="utf-8")
    count, types, last_mod, elapsed = _file_activity(tmp_path)
    assert count == 2
    assert types.get(".py") == 2
    assert last_mod != ""
    assert elapsed >= 0.0


def test_file_activity_excludes_tracker_and_git(tmp_path):
    (tmp_path / "real.py").write_text("x", encoding="utf-8")
    tracker = tmp_path / ".tracker"
    tracker.mkdir()
    (tracker / "status.json").write_text("{}", encoding="utf-8")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    count, types, _, _ = _file_activity(tmp_path)
    assert count == 1
    assert ".py" in types


def test_file_activity_empty_dir(tmp_path):
    count, types, last_mod, elapsed = _file_activity(tmp_path)
    assert count == 0
    assert types == {}
    assert last_mod == ""
    assert elapsed == 0.0


# ---------------------------------------------------------------------------
# _update_history
# ---------------------------------------------------------------------------

def test_update_history_appends(tmp_path):
    history_path = tmp_path / "history.json"
    history_path.write_text("[]", encoding="utf-8")
    snap = StatusSnapshot(
        last_scan=utc_now(), file_count=5, file_types={}, last_file_modified="",
        days_since_activity=0.0, status="active",
        milestones={"total": 2, "completed": 1, "overdue": 0, "next_due": None},
        detected_skills=[], requirements_complete=True,
    )
    _update_history(history_path, snap)
    data = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["status"] == "active"
    assert data[0]["file_count"] == 5


def test_update_history_caps_at_365(tmp_path):
    history_path = tmp_path / "history.json"
    existing = [{"timestamp": utc_now(), "status": "active", "file_count": i,
                 "days_since_activity": 0, "milestones_completed": 0, "milestones_total": 0}
                for i in range(365)]
    history_path.write_text(json.dumps(existing), encoding="utf-8")
    snap = StatusSnapshot(
        last_scan=utc_now(), file_count=999, file_types={}, last_file_modified="",
        days_since_activity=0.0, status="stale",
        milestones={"total": 0, "completed": 0, "overdue": 0, "next_due": None},
        detected_skills=[], requirements_complete=False,
    )
    _update_history(history_path, snap)
    data = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(data) == 365
    assert data[-1]["file_count"] == 999


# ---------------------------------------------------------------------------
# run_scan integration
# ---------------------------------------------------------------------------

def _filled_requirements(created_at: str) -> str:
    return f"""# Project: Test Project

## Description
A real project description.

## Goals & Milestones

- [ ] First milestone (target: 2099-12-31)

## Completion Criteria
Ship to production.

## Technologies & Skills

- Python
- Docker

## Resume Bullets

## Status
- **Status:** active
- **Created:** {created_at}
- **Last Updated:** {created_at}
"""


def test_run_scan_writes_status_json(tmp_path, cfg):
    proj_path, entry = _make_project(tmp_path, "alpha")
    now = utc_now()
    (proj_path / "requirements.md").write_text(_filled_requirements(now), encoding="utf-8")
    (proj_path / "main.py").write_text("print('hello')", encoding="utf-8")
    tracker_root = _make_tracker_root(tmp_path, [entry])

    run_scan(tracker_root, cfg)

    status_path = proj_path / ".tracker" / "status.json"
    assert status_path.exists()
    snap = json.loads(status_path.read_text(encoding="utf-8"))
    assert snap["file_count"] >= 1
    assert snap["status"] in ("active", "not_started", "stale", "overdue", "completed")
    assert "milestones" in snap
    assert "detected_skills" in snap


def test_run_scan_updates_registry(tmp_path, cfg):
    proj_path, entry = _make_project(tmp_path, "beta")
    now = utc_now()
    (proj_path / "requirements.md").write_text(_filled_requirements(now), encoding="utf-8")
    (proj_path / "main.py").write_text("x = 1", encoding="utf-8")
    tracker_root = _make_tracker_root(tmp_path, [entry])

    run_scan(tracker_root, cfg)

    registry = json.loads((tracker_root / "project_registry.json").read_text(encoding="utf-8"))
    proj = registry["projects"][0]
    assert proj["requirements_filled"] is True
    assert "Python" in proj["detected_skills"]
    assert proj["status"] in ("active", "stale", "overdue", "completed", "not_started")


def test_run_scan_appends_history(tmp_path, cfg):
    proj_path, entry = _make_project(tmp_path, "gamma")
    tracker_root = _make_tracker_root(tmp_path, [entry])

    run_scan(tracker_root, cfg)
    run_scan(tracker_root, cfg)

    history_path = proj_path / ".tracker" / "history.json"
    data = json.loads(history_path.read_text(encoding="utf-8"))
    assert len(data) == 2


def test_run_scan_skips_completed_projects(tmp_path, cfg):
    proj_path, entry = _make_project(tmp_path, "done-proj", status="completed")
    tracker_root = _make_tracker_root(tmp_path, [entry])

    run_scan(tracker_root, cfg)

    status_path = proj_path / ".tracker" / "status.json"
    assert not status_path.exists()


def test_run_scan_no_registry(tmp_path, cfg):
    tracker_root = tmp_path / ".resume-tracker"
    tracker_root.mkdir()
    (tracker_root / "logs").mkdir()
    # No project_registry.json — should not crash
    run_scan(tracker_root, cfg)


# ---------------------------------------------------------------------------
# display_status
# ---------------------------------------------------------------------------

def test_display_status_summary_no_projects(tmp_path, cfg, capsys):
    tracker_root = _make_tracker_root(tmp_path, [])
    display_status(tracker_root, cfg)
    out = capsys.readouterr().out
    assert "No projects" in out


def test_display_status_summary_table(tmp_path, cfg, capsys):
    proj_path, entry = _make_project(tmp_path, "delta")
    tracker_root = _make_tracker_root(tmp_path, [entry])
    run_scan(tracker_root, cfg)

    display_status(tracker_root, cfg)
    out = capsys.readouterr().out
    assert "delta" in out
    assert "Status" in out


def test_display_status_detail(tmp_path, cfg, capsys):
    proj_path, entry = _make_project(tmp_path, "epsilon")
    now = utc_now()
    (proj_path / "requirements.md").write_text(_filled_requirements(now), encoding="utf-8")
    tracker_root = _make_tracker_root(tmp_path, [entry])
    run_scan(tracker_root, cfg)

    display_status(tracker_root, cfg, project_name="epsilon")
    out = capsys.readouterr().out
    assert "epsilon" in out
    assert "Status" in out
    assert "Files" in out


def test_display_status_detail_not_found(tmp_path, cfg, capsys):
    proj_path, entry = _make_project(tmp_path, "zeta")
    tracker_root = _make_tracker_root(tmp_path, [entry])

    display_status(tracker_root, cfg, project_name="nonexistent")
    out = capsys.readouterr().out
    assert "No project matching" in out
