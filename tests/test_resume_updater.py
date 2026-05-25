from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from resume_tracker.config import DEFAULT_CONFIG
from resume_tracker.models import MasterResume, SkillsSection
from resume_tracker.resume_updater import (
    _categorize_skill,
    export_markdown,
    sync_resume,
)
from resume_tracker.utils import utc_now, write_json


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_FILLED_REQ = """\
# Project: Test Project

## Description
A test project for unit testing.

## Goals & Milestones
- [x] Build the thing (target: 2026-05-01)
- [ ] Deploy it (target: 2026-06-01)

## Completion Criteria
Ship to production.

## Technologies & Skills
- Python
- FastAPI

## Resume Bullets
- Built a test pipeline (2026-05-20)
- Implemented CI/CD (2026-05-21)

## Status
- **Status:** active
- **Created:** 2026-05-01T00:00:00Z
- **Last Updated:** 2026-05-20T00:00:00Z
"""


def _cfg() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def _make_tracker_root(tmp_path: Path) -> Path:
    tracker_root = tmp_path / ".resume-tracker"
    tracker_root.mkdir()
    (tracker_root / "logs").mkdir()
    resume = MasterResume.empty(utc_now())
    write_json(tracker_root / "master_resume.json", resume.to_dict(), lock=False)
    return tracker_root


def _make_project(
    tmp_path: Path,
    name: str = "test-project",
    content: str = _FILLED_REQ,
    status: str = "active",
) -> dict:
    proj_path = tmp_path / name
    proj_path.mkdir(exist_ok=True)
    (proj_path / "requirements.md").write_text(content, encoding="utf-8")
    (proj_path / ".tracker").mkdir(exist_ok=True)
    return {
        "id": f"id-{name}",
        "name": name,
        "path": str(proj_path),
        "status": status,
        "created_at": "2026-05-01T00:00:00Z",
        "last_activity": "2026-05-20T00:00:00Z",
        "requirements_filled": True,
        "requirements_stale": False,
        "nag_count": 0,
        "last_nag": "",
        "milestones_total": 2,
        "milestones_completed": 1,
        "detected_skills": ["Python", "YAML Configuration"],
        "declared_skills": ["Python", "FastAPI"],
        "resume_synced": False,
    }


def _read_resume(tracker_root: Path) -> dict:
    return json.loads((tracker_root / "master_resume.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Project entry creation
# ---------------------------------------------------------------------------

class TestCreateEntry:
    def test_creates_entry_when_filled(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        data = _read_resume(tracker_root)
        assert len(data["projects"]) == 1
        assert data["projects"][0]["title"] == "Test Project"
        assert data["projects"][0]["status"] == "active"
        assert data["projects"][0]["date_range"] == "May 2026 - Present"

    def test_skips_unfilled_requirements(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        proj["requirements_filled"] = False

        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        data = _read_resume(tracker_root)
        assert data["projects"] == []

    def test_sets_resume_synced_flag(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        registry = {"projects": [proj]}

        sync_resume(tracker_root, _cfg(), registry)

        assert registry["projects"][0]["resume_synced"] is True


# ---------------------------------------------------------------------------
# Bullet sync
# ---------------------------------------------------------------------------

class TestBulletSync:
    def test_bullets_stored_without_date_suffix(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        bullets = _read_resume(tracker_root)["projects"][0]["bullets"]
        assert "Built a test pipeline" in bullets
        assert "Implemented CI/CD" in bullets
        assert not any("2026" in b for b in bullets)

    def test_new_bullet_appears_on_resync(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        registry = {"projects": [proj]}
        sync_resume(tracker_root, _cfg(), registry)

        # Insert new bullet inside the Resume Bullets section (before ## Status)
        new_content = _FILLED_REQ.replace(
            "\n## Status",
            "\n- Deployed to production (2026-05-22)\n\n## Status",
        )
        (Path(proj["path"]) / "requirements.md").write_text(new_content, encoding="utf-8")
        sync_resume(tracker_root, _cfg(), registry)

        bullets = _read_resume(tracker_root)["projects"][0]["bullets"]
        assert "Deployed to production" in bullets
        assert len(bullets) == 3

    def test_removed_bullet_not_in_resume(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        registry = {"projects": [proj]}
        sync_resume(tracker_root, _cfg(), registry)

        shorter = _FILLED_REQ.replace("- Implemented CI/CD (2026-05-21)\n", "")
        (Path(proj["path"]) / "requirements.md").write_text(shorter, encoding="utf-8")
        sync_resume(tracker_root, _cfg(), registry)

        bullets = _read_resume(tracker_root)["projects"][0]["bullets"]
        assert "Implemented CI/CD" not in bullets
        assert "Built a test pipeline" in bullets


# ---------------------------------------------------------------------------
# Technologies
# ---------------------------------------------------------------------------

class TestTechnologies:
    def test_technologies_union_of_declared_and_detected(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        techs = _read_resume(tracker_root)["projects"][0]["technologies"]
        assert "Python" in techs
        assert "FastAPI" in techs
        assert "YAML Configuration" in techs


# ---------------------------------------------------------------------------
# Completed projects
# ---------------------------------------------------------------------------

class TestCompletedProject:
    def test_new_completed_project_closes_date_range(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path, status="completed")
        proj["last_activity"] = "2026-06-15T00:00:00Z"
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        date_range = _read_resume(tracker_root)["projects"][0]["date_range"]
        assert date_range == "May 2026 - June 2026"

    def test_existing_entry_closes_date_range_when_completed(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        registry = {"projects": [proj]}
        sync_resume(tracker_root, _cfg(), registry)

        proj["status"] = "completed"
        proj["last_activity"] = "2026-06-15T00:00:00Z"
        sync_resume(tracker_root, _cfg(), registry)

        date_range = _read_resume(tracker_root)["projects"][0]["date_range"]
        assert date_range == "May 2026 - June 2026"


# ---------------------------------------------------------------------------
# Abandoned projects
# ---------------------------------------------------------------------------

class TestAbandonedProject:
    def test_existing_entry_marked_abandoned(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        registry = {"projects": [proj]}
        sync_resume(tracker_root, _cfg(), registry)

        proj["status"] = "abandoned"
        sync_resume(tracker_root, _cfg(), registry)

        data = _read_resume(tracker_root)
        assert data["projects"][0]["status"] == "abandoned"

    def test_no_entry_created_for_new_abandoned_project(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path, status="abandoned")
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        data = _read_resume(tracker_root)
        assert data["projects"] == []


# ---------------------------------------------------------------------------
# Skills aggregation
# ---------------------------------------------------------------------------

class TestSkillsAggregation:
    def test_skills_categorized_into_correct_buckets(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        proj["detected_skills"] = ["Python", "Docker"]
        proj["declared_skills"] = ["FastAPI", "Python"]
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        skills = _read_resume(tracker_root)["skills"]
        assert "Python" in skills["languages"]
        assert "FastAPI" in skills["frameworks"]
        assert "Docker" in skills["tools"]

    def test_abandoned_project_skills_excluded(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj1 = _make_project(tmp_path, name="active-proj")
        proj1["detected_skills"] = ["Python"]
        proj1["declared_skills"] = []

        proj2_path = tmp_path / "abandoned-proj"
        proj2_path.mkdir()
        proj2 = {
            "id": "id-abandoned",
            "name": "abandoned-proj",
            "path": str(proj2_path),
            "status": "abandoned",
            "created_at": "2026-05-01T00:00:00Z",
            "last_activity": "",
            "requirements_filled": True,
            "detected_skills": ["Rust"],
            "declared_skills": [],
            "resume_synced": False,
        }
        sync_resume(tracker_root, _cfg(), {"projects": [proj1, proj2]})

        skills = _read_resume(tracker_root)["skills"]
        all_skills = skills["languages"] + skills["frameworks"] + skills["tools"] + skills["other"]
        assert "Rust" not in all_skills
        assert "Python" in all_skills

    def test_completed_project_skills_included(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path, status="completed")
        proj["detected_skills"] = ["Go"]
        proj["declared_skills"] = []
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        skills = _read_resume(tracker_root)["skills"]
        assert "Go" in skills["languages"]


# ---------------------------------------------------------------------------
# Metadata integrity
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_education_and_experience_preserved(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        resume = MasterResume.empty(utc_now())
        resume.education = [{"school": "USC", "degree": "MS"}]
        resume.experience = [{"company": "Keck", "role": "Analyst"}]
        write_json(tracker_root / "master_resume.json", resume.to_dict(), lock=False)

        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})

        data = _read_resume(tracker_root)
        assert data["education"] == [{"school": "USC", "degree": "MS"}]
        assert data["experience"] == [{"company": "Keck", "role": "Analyst"}]

    def test_version_increments_each_sync(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        registry = {"projects": [proj]}

        sync_resume(tracker_root, _cfg(), registry)
        v1 = _read_resume(tracker_root)["meta"]["version"]

        sync_resume(tracker_root, _cfg(), registry)
        v2 = _read_resume(tracker_root)["meta"]["version"]

        assert v2 == v1 + 1


# ---------------------------------------------------------------------------
# Skill categorization
# ---------------------------------------------------------------------------

class TestCategorizeSkill:
    def test_framework_skills(self):
        cfg = _cfg()
        assert _categorize_skill("FastAPI", cfg) == "frameworks"
        assert _categorize_skill("React", cfg) == "frameworks"
        assert _categorize_skill("Django", cfg) == "frameworks"

    def test_language_skills(self):
        cfg = _cfg()
        assert _categorize_skill("Python", cfg) == "languages"
        assert _categorize_skill("JavaScript", cfg) == "languages"
        assert _categorize_skill("Go", cfg) == "languages"

    def test_tool_skills(self):
        cfg = _cfg()
        assert _categorize_skill("Docker", cfg) == "tools"
        assert _categorize_skill("pip (Python)", cfg) == "tools"
        assert _categorize_skill("Make", cfg) == "tools"

    def test_other_skills(self):
        cfg = _cfg()
        assert _categorize_skill("Kubernetes", cfg) == "other"
        assert _categorize_skill("Agile", cfg) == "other"


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------

class TestExportMarkdown:
    def test_creates_markdown_file(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})
        export_markdown(tracker_root)

        md_path = tracker_root / "master_resume.md"
        assert md_path.exists()

    def test_markdown_contains_project_and_skills(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})
        export_markdown(tracker_root)

        content = (tracker_root / "master_resume.md").read_text(encoding="utf-8")
        assert "# Master Resume" in content
        assert "Test Project" in content
        assert "## Skills" in content
        assert "## Projects" in content

    def test_markdown_contains_bullets(self, tmp_path):
        tracker_root = _make_tracker_root(tmp_path)
        proj = _make_project(tmp_path)
        sync_resume(tracker_root, _cfg(), {"projects": [proj]})
        export_markdown(tracker_root)

        content = (tracker_root / "master_resume.md").read_text(encoding="utf-8")
        assert "Built a test pipeline" in content
        assert "Implemented CI/CD" in content
