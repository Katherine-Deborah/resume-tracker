import json
import time
from pathlib import Path

import pytest

from resume_tracker.utils import (
    days_since,
    file_lock,
    is_template_filled,
    month_year,
    parse_declared_skills,
    parse_description,
    parse_milestones,
    parse_project_title,
    parse_resume_bullets,
    render_requirements_template,
    utc_now,
    write_json,
    read_json,
)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def test_utc_now_format():
    ts = utc_now()
    assert ts.endswith("Z")
    assert len(ts) == 20  # 2026-05-24T10:00:00Z


def test_days_since_recent():
    ts = utc_now()
    elapsed = days_since(ts)
    assert 0 <= elapsed < 0.01


def test_days_since_empty_string():
    assert days_since("") == 0.0


def test_month_year():
    assert month_year("2026-05-24T10:00:00Z") == "May 2026"


def test_month_year_invalid():
    assert month_year("not-a-date") == ""


# ---------------------------------------------------------------------------
# File locking
# ---------------------------------------------------------------------------

def test_file_lock_creates_and_removes_lock_file(tmp_path):
    target = tmp_path / "data.json"
    target.write_text("{}")
    lock_file = target.with_suffix(".json.lock")

    with file_lock(target):
        assert lock_file.exists()
    assert not lock_file.exists()


def test_write_json_roundtrip(tmp_path):
    path = tmp_path / "test.json"
    data = {"key": "value", "num": 42}
    write_json(path, data)
    loaded = read_json(path)
    assert loaded == data


def test_write_json_creates_parent_dirs(tmp_path):
    path = tmp_path / "deep" / "nested" / "file.json"
    write_json(path, {"x": 1})
    assert path.exists()


# ---------------------------------------------------------------------------
# requirements.md template
# ---------------------------------------------------------------------------

def test_render_template_contains_placeholder(tmp_path):
    ts = utc_now()
    content = render_requirements_template(ts)
    assert "[FILL IN PROJECT NAME]" in content
    assert ts in content


def test_is_template_filled_false_for_raw_template():
    ts = utc_now()
    content = render_requirements_template(ts)
    assert is_template_filled(content) is False


def test_is_template_filled_true_for_edited():
    content = """\
# Project: My Cool Project

## Description
A real description.

## Goals & Milestones
- [ ] Ship v1.0 (target: 2026-07-01)

## Completion Criteria
v1.0 deployed to production.

## Technologies & Skills
- Python
- FastAPI

## Resume Bullets

## Status
- **Status:** not_started
- **Created:** 2026-05-24T10:00:00Z
- **Last Updated:** 2026-05-24T10:00:00Z
"""
    assert is_template_filled(content) is True


# ---------------------------------------------------------------------------
# Milestone parser
# ---------------------------------------------------------------------------

def test_parse_milestones_incomplete():
    content = """\
## Goals & Milestones
- [ ] Build the API (target: 2026-06-01)
- [ ] Write tests (target: 2026-06-15)
"""
    milestones = parse_milestones(content)
    assert len(milestones) == 2
    assert milestones[0].text == "Build the API"
    assert milestones[0].target_date == "2026-06-01"
    assert milestones[0].completed is False


def test_parse_milestones_complete():
    content = """\
## Goals & Milestones
- [x] Build the API (target: 2026-06-01)
"""
    milestones = parse_milestones(content)
    assert milestones[0].completed is True


def test_parse_milestones_no_date():
    content = """\
## Goals & Milestones
- [ ] Milestone without a date
"""
    milestones = parse_milestones(content)
    assert len(milestones) == 1
    assert milestones[0].target_date is None


def test_parse_milestones_malformed_skipped():
    content = """\
## Goals & Milestones
- not a real milestone line
- [ ] Valid one (target: 2026-07-01)
"""
    milestones = parse_milestones(content)
    assert len(milestones) == 1
    assert milestones[0].text == "Valid one"


def test_parse_milestones_stops_at_next_section():
    content = """\
## Goals & Milestones
- [ ] First (target: 2026-06-01)

## Completion Criteria
- [ ] This should NOT be parsed as a milestone
"""
    milestones = parse_milestones(content)
    assert len(milestones) == 1


def test_parse_milestones_empty():
    assert parse_milestones("No milestones here.") == []


# ---------------------------------------------------------------------------
# Resume bullet parser
# ---------------------------------------------------------------------------

def test_parse_resume_bullets_basic():
    content = """\
## Resume Bullets
- Built a pipeline processing 10K events/sec (2026-05-20)
- Reduced deploy time by 40% via CI/CD (2026-05-21)

## Status
"""
    bullets = parse_resume_bullets(content)
    assert bullets == [
        "Built a pipeline processing 10K events/sec",
        "Reduced deploy time by 40% via CI/CD",
    ]


def test_parse_resume_bullets_empty_section():
    content = "## Resume Bullets\n\n## Status\n"
    assert parse_resume_bullets(content) == []


def test_parse_resume_bullets_no_section():
    assert parse_resume_bullets("No bullets here.") == []


# ---------------------------------------------------------------------------
# Title / description / skills parsers
# ---------------------------------------------------------------------------

def test_parse_project_title():
    content = "# Project: My Awesome Tool\n\n## Description\n"
    assert parse_project_title(content) == "My Awesome Tool"


def test_parse_project_title_unfilled():
    ts = utc_now()
    content = render_requirements_template(ts)
    assert parse_project_title(content) == ""


def test_parse_description():
    content = """\
## Description
A useful tool for tracking things.

## Goals
"""
    assert parse_description(content) == "A useful tool for tracking things."


def test_parse_declared_skills():
    content = """\
## Technologies & Skills
- Python
- FastAPI
- Docker

## Resume Bullets
"""
    skills = parse_declared_skills(content)
    assert "Python" in skills
    assert "FastAPI" in skills
    assert "Docker" in skills
    assert len(skills) == 3


def test_parse_declared_skills_excludes_placeholders():
    content = """\
## Technologies & Skills
- [Skill 1]
- [Skill 2]
"""
    assert parse_declared_skills(content) == []


# ---------------------------------------------------------------------------
# Init command (integration)
# ---------------------------------------------------------------------------

def _patch_home(tmp_path, monkeypatch):
    """Redirect ~ expansion to tmp_path so tracker_root resolves inside the temp dir."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_tracker_init_creates_structure(tmp_path, monkeypatch):
    """tracker init should create the .resume-tracker directory with all expected files."""
    docs_dir = tmp_path / "Documents"
    docs_dir.mkdir()
    _patch_home(tmp_path, monkeypatch)

    from resume_tracker.cli import cmd_init
    import argparse
    cmd_init(argparse.Namespace())

    tracker_root = docs_dir / ".resume-tracker"
    assert tracker_root.exists()
    assert (tracker_root / "config.yaml").exists()
    assert (tracker_root / "project_registry.json").exists()
    assert (tracker_root / "master_resume.json").exists()
    assert (tracker_root / "logs").exists()


def test_tracker_init_idempotent(tmp_path, monkeypatch, capsys):
    """Running tracker init twice should not overwrite or crash."""
    docs_dir = tmp_path / "Documents"
    docs_dir.mkdir()
    _patch_home(tmp_path, monkeypatch)

    from resume_tracker.cli import cmd_init
    import argparse
    args = argparse.Namespace()
    cmd_init(args)
    cmd_init(args)  # second call

    captured = capsys.readouterr()
    assert "already initialized" in captured.out


def test_tracker_init_master_resume_structure(tmp_path, monkeypatch):
    docs_dir = tmp_path / "Documents"
    docs_dir.mkdir()
    _patch_home(tmp_path, monkeypatch)

    from resume_tracker.cli import cmd_init
    import argparse
    cmd_init(argparse.Namespace())

    resume_path = docs_dir / ".resume-tracker" / "master_resume.json"
    data = json.loads(resume_path.read_text())
    assert "meta" in data
    assert "education" in data
    assert "experience" in data
    assert "projects" in data
    assert "skills" in data
    assert data["projects"] == []
    assert data["education"] == []
