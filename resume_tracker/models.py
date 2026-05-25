from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MilestoneEntry:
    text: str
    target_date: Optional[str]  # ISO date string YYYY-MM-DD or None
    completed: bool = False


@dataclass
class ProjectEntry:
    id: str
    name: str
    path: str
    status: str = "not_started"
    created_at: str = ""
    last_activity: str = ""
    requirements_filled: bool = False
    requirements_stale: bool = False
    nag_count: int = 0
    last_nag: str = ""
    milestones_total: int = 0
    milestones_completed: int = 0
    detected_skills: list[str] = field(default_factory=list)
    declared_skills: list[str] = field(default_factory=list)
    resume_synced: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "status": self.status,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "requirements_filled": self.requirements_filled,
            "requirements_stale": self.requirements_stale,
            "nag_count": self.nag_count,
            "last_nag": self.last_nag,
            "milestones_total": self.milestones_total,
            "milestones_completed": self.milestones_completed,
            "detected_skills": self.detected_skills,
            "declared_skills": self.declared_skills,
            "resume_synced": self.resume_synced,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectEntry":
        return cls(
            id=d["id"],
            name=d["name"],
            path=d["path"],
            status=d.get("status", "not_started"),
            created_at=d.get("created_at", ""),
            last_activity=d.get("last_activity", ""),
            requirements_filled=d.get("requirements_filled", False),
            requirements_stale=d.get("requirements_stale", False),
            nag_count=d.get("nag_count", 0),
            last_nag=d.get("last_nag", ""),
            milestones_total=d.get("milestones_total", 0),
            milestones_completed=d.get("milestones_completed", 0),
            detected_skills=d.get("detected_skills", []),
            declared_skills=d.get("declared_skills", []),
            resume_synced=d.get("resume_synced", False),
        )


@dataclass
class StatusSnapshot:
    last_scan: str
    file_count: int
    file_types: dict[str, int]
    last_file_modified: str
    days_since_activity: float
    status: str
    milestones: dict
    detected_skills: list[str]
    requirements_complete: bool

    def to_dict(self) -> dict:
        return {
            "last_scan": self.last_scan,
            "file_count": self.file_count,
            "file_types": self.file_types,
            "last_file_modified": self.last_file_modified,
            "days_since_activity": self.days_since_activity,
            "status": self.status,
            "milestones": self.milestones,
            "detected_skills": self.detected_skills,
            "requirements_complete": self.requirements_complete,
        }


@dataclass
class SkillsSection:
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    other: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "languages": self.languages,
            "frameworks": self.frameworks,
            "tools": self.tools,
            "other": self.other,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillsSection":
        return cls(
            languages=d.get("languages", []),
            frameworks=d.get("frameworks", []),
            tools=d.get("tools", []),
            other=d.get("other", []),
        )


@dataclass
class ResumeProject:
    source_project_id: str
    title: str
    date_range: str
    description: str
    technologies: list[str]
    bullets: list[str]
    status: str

    def to_dict(self) -> dict:
        return {
            "source_project_id": self.source_project_id,
            "title": self.title,
            "date_range": self.date_range,
            "description": self.description,
            "technologies": self.technologies,
            "bullets": self.bullets,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeProject":
        return cls(
            source_project_id=d["source_project_id"],
            title=d["title"],
            date_range=d.get("date_range", ""),
            description=d.get("description", ""),
            technologies=d.get("technologies", []),
            bullets=d.get("bullets", []),
            status=d.get("status", "in_progress"),
        )


@dataclass
class MasterResume:
    version: int
    last_updated: str
    education: list
    experience: list
    projects: list[ResumeProject]
    skills: SkillsSection

    def to_dict(self) -> dict:
        return {
            "meta": {
                "version": self.version,
                "last_updated": self.last_updated,
            },
            "education": self.education,
            "experience": self.experience,
            "projects": [p.to_dict() for p in self.projects],
            "skills": self.skills.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MasterResume":
        meta = d.get("meta", {})
        return cls(
            version=meta.get("version", 1),
            last_updated=meta.get("last_updated", ""),
            education=d.get("education", []),
            experience=d.get("experience", []),
            projects=[ResumeProject.from_dict(p) for p in d.get("projects", [])],
            skills=SkillsSection.from_dict(d.get("skills", {})),
        )

    @classmethod
    def empty(cls, timestamp: str) -> "MasterResume":
        return cls(
            version=1,
            last_updated=timestamp,
            education=[],
            experience=[],
            projects=[],
            skills=SkillsSection(),
        )
