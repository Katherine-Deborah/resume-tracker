from __future__ import annotations

import logging
from pathlib import Path

from resume_tracker.config import FRAMEWORK_SKILLS
from resume_tracker.models import MasterResume, ResumeProject, SkillsSection
from resume_tracker.utils import (
    month_year,
    parse_declared_skills,
    parse_description,
    parse_project_title,
    parse_resume_bullets,
    read_json,
    utc_now,
    write_json,
)

_log = logging.getLogger("resume_tracker.resume_updater")


def sync_resume(tracker_root: Path, cfg: dict, registry: dict) -> None:
    """Update master_resume.json from all projects. Sets resume_synced on project dicts in place."""
    resume_path = tracker_root / "master_resume.json"
    if not resume_path.exists():
        _log.warning("master_resume.json not found — skipping resume sync.")
        return

    try:
        data = read_json(resume_path)
    except Exception as exc:
        _log.error("Failed to read master_resume.json: %s", exc)
        return

    resume = MasterResume.from_dict(data)
    id_to_idx: dict[str, int] = {
        p.source_project_id: i for i, p in enumerate(resume.projects)
    }

    for proj in registry.get("projects", []):
        proj_id = proj["id"]
        status = proj.get("status", "not_started")

        if status == "abandoned":
            if proj_id in id_to_idx:
                resume.projects[id_to_idx[proj_id]].status = "abandoned"
                proj["resume_synced"] = True
            continue

        if not proj.get("requirements_filled", False):
            continue

        proj_path = Path(proj["path"])
        req_path = proj_path / "requirements.md"
        if not proj_path.exists() or not req_path.exists():
            continue

        try:
            content = req_path.read_text(encoding="utf-8-sig")  # strips UTF-8 BOM if present
        except OSError as exc:
            _log.warning("Could not read requirements.md for %s: %s", proj["name"], exc)
            continue

        title = parse_project_title(content) or proj["name"]
        description = parse_description(content)
        bullets = parse_resume_bullets(content)
        declared = parse_declared_skills(content)
        detected = proj.get("detected_skills", [])
        technologies = sorted(set(declared) | set(detected))

        if proj_id in id_to_idx:
            entry = resume.projects[id_to_idx[proj_id]]
            entry.title = title
            entry.description = description
            entry.technologies = technologies
            entry.bullets = bullets
            entry.status = status
            if status == "completed" and entry.date_range.endswith("Present"):
                last_activity = proj.get("last_activity", "")
                end = month_year(last_activity) if last_activity else month_year(utc_now())
                start = entry.date_range.split(" - ")[0]
                entry.date_range = f"{start} - {end}"
        else:
            created_at = proj.get("created_at", "")
            start_date = month_year(created_at) if created_at else "Unknown"
            if status == "completed":
                last_activity = proj.get("last_activity", "")
                end = month_year(last_activity) if last_activity else start_date
                date_range = f"{start_date} - {end}"
            else:
                date_range = f"{start_date} - Present"

            entry = ResumeProject(
                source_project_id=proj_id,
                title=title,
                date_range=date_range,
                description=description,
                technologies=technologies,
                bullets=bullets,
                status=status,
            )
            resume.projects.append(entry)
            id_to_idx[proj_id] = len(resume.projects) - 1

        proj["resume_synced"] = True

    _rebuild_skills(resume, registry.get("projects", []), cfg)
    resume.version += 1
    resume.last_updated = utc_now()

    try:
        write_json(resume_path, resume.to_dict())
    except Exception as exc:
        _log.error("Failed to write master_resume.json: %s", exc)


def _rebuild_skills(resume: MasterResume, projects: list[dict], cfg: dict) -> None:
    """Rebuild the skills section from all non-abandoned projects."""
    all_skills: set[str] = set()
    for proj in projects:
        if proj.get("status") == "abandoned":
            continue
        all_skills.update(proj.get("declared_skills", []))
        all_skills.update(proj.get("detected_skills", []))

    languages: list[str] = []
    frameworks: list[str] = []
    tools: list[str] = []
    other: list[str] = []

    for skill in sorted(all_skills):
        category = _categorize_skill(skill, cfg)
        if category == "languages":
            languages.append(skill)
        elif category == "frameworks":
            frameworks.append(skill)
        elif category == "tools":
            tools.append(skill)
        else:
            other.append(skill)

    resume.skills = SkillsSection(
        languages=languages,
        frameworks=frameworks,
        tools=tools,
        other=other,
    )


def _categorize_skill(skill: str, cfg: dict) -> str:
    """Return 'frameworks', 'languages', 'tools', or 'other' for a skill."""
    if skill in FRAMEWORK_SKILLS:
        return "frameworks"
    # dep_map (build tools, package managers) → tools, checked before ext_map
    # because some skills (e.g. "Docker") appear in both maps
    if skill in set(cfg.get("dependency_file_to_skill_map", {}).values()):
        return "tools"
    if skill in set(cfg.get("file_extension_to_skill_map", {}).values()):
        return "languages"
    return "other"


def export_markdown(tracker_root: Path) -> None:
    """Write a human-readable markdown file of master_resume.json."""
    resume_path = tracker_root / "master_resume.json"
    if not resume_path.exists():
        print("master_resume.json not found. Run `tracker init` first.")
        return

    try:
        data = read_json(resume_path)
    except Exception as exc:
        print(f"Error reading master_resume.json: {exc}")
        return

    resume = MasterResume.from_dict(data)
    lines: list[str] = []

    last_updated = month_year(resume.last_updated) if resume.last_updated else "Unknown"
    lines.append("# Master Resume")
    lines.append(f"_Last updated: {last_updated} — Version {resume.version}_")
    lines.append("")

    lines.append("## Skills")
    s = resume.skills
    lines.append(f"**Languages:** {', '.join(s.languages) if s.languages else '(none)'}")
    lines.append(f"**Frameworks:** {', '.join(s.frameworks) if s.frameworks else '(none)'}")
    lines.append(f"**Tools:** {', '.join(s.tools) if s.tools else '(none)'}")
    lines.append(f"**Other:** {', '.join(s.other) if s.other else '(none)'}")
    lines.append("")

    if resume.projects:
        lines.append("## Projects")
        lines.append("")
        for proj in resume.projects:
            lines.append(f"### {proj.title}")
            lines.append(f"**Status:** {proj.status} | **Date:** {proj.date_range}")
            if proj.technologies:
                lines.append(f"**Technologies:** {', '.join(proj.technologies)}")
            if proj.description:
                lines.append("")
                lines.append(f"> {proj.description}")
            if proj.bullets:
                lines.append("")
                lines.append("**Resume Bullets:**")
                for bullet in proj.bullets:
                    lines.append(f"- {bullet}")
            lines.append("")

    output_path = tracker_root / "master_resume.md"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Resume exported to {output_path}")
