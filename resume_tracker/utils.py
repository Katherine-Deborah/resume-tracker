from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from resume_tracker.models import MilestoneEntry

# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def utc_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_to_local_display(utc_str: str) -> str:
    """Convert a UTC ISO string to a local-time display string."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M %Z")
    except (ValueError, OSError):
        return utc_str


def days_since(utc_str: str) -> float:
    """Return fractional days elapsed since the given UTC ISO string."""
    if not utc_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 86400
    except ValueError:
        return 0.0


def month_year(utc_str: str) -> str:
    """Return 'Month YYYY' from a UTC ISO string."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        return dt.strftime("%B %Y")
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# File locking (cross-platform sentinel-file approach)
# ---------------------------------------------------------------------------

LOCK_TIMEOUT_SECONDS = 10
LOCK_STALE_SECONDS = 60  # consider a lock stale after 60s (handles crashes)


@contextmanager
def file_lock(path: Path) -> Generator[None, None, None]:
    """Acquire an exclusive lock on `path` via a sibling .lock file."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS

    while True:
        try:
            # Atomic exclusive create — fails if file already exists
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            # Check if the lock is stale
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > LOCK_STALE_SECONDS:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"Could not acquire lock on {path} within {LOCK_TIMEOUT_SECONDS}s")
            time.sleep(0.05)

    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# JSON registry helpers (with locking)
# ---------------------------------------------------------------------------

def read_json(path: Path) -> dict | list:
    with path.open("r", encoding="utf-8-sig") as f:  # utf-8-sig strips BOM if present
        return json.load(f)


def write_json(path: Path, data: dict | list, *, lock: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if lock:
        with file_lock(path):
            _atomic_write_json(path, data)
    else:
        _atomic_write_json(path, data)


def _atomic_write_json(path: Path, data: dict | list) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# requirements.md template
# ---------------------------------------------------------------------------

REQUIREMENTS_TEMPLATE = """\
# Project: [FILL IN PROJECT NAME]

## Description
[What is this project? 1-2 sentences.]

## Goals & Milestones
<!-- Add milestones with target dates. The tracker checks these. -->
<!-- Format: - [ ] Milestone description (target: YYYY-MM-DD) -->

- [ ] [Milestone 1] (target: YYYY-MM-DD)
- [ ] [Milestone 2] (target: YYYY-MM-DD)
- [ ] [Milestone 3] (target: YYYY-MM-DD)

## Completion Criteria
<!-- How does the tracker know this project is "done"? -->
[Define what "finished" means for this project.]

## Technologies & Skills
<!-- List the technologies/skills you expect to use or learn. -->
<!-- The tracker also auto-detects from file extensions, but this is your explicit list. -->

- [Skill 1]
- [Skill 2]

## Resume Bullets
<!-- When you hit a milestone, write 1-2 bullet points here. -->
<!-- These get pulled into your master resume automatically. -->
<!-- Format: - Bullet point describing accomplishment (YYYY-MM-DD) -->

## Status
<!-- Do not edit manually. The tracker updates this. -->
- **Status:** not_started
- **Created:** {created_at}
- **Last Updated:** {created_at}
"""

# Sentinel strings that indicate the template has NOT been filled
_UNFILLED_MARKERS = [
    "[FILL IN PROJECT NAME]",
    "[What is this project?",
    "[Milestone 1]",
    "[Define what",
    "[Skill 1]",
]


def render_requirements_template(created_at: str) -> str:
    return REQUIREMENTS_TEMPLATE.format(created_at=created_at)


def is_template_filled(content: str) -> bool:
    """Return True if the requirements.md content has been meaningfully edited."""
    for marker in _UNFILLED_MARKERS:
        if marker in content:
            return False
    return True


# ---------------------------------------------------------------------------
# requirements.md parsers
# ---------------------------------------------------------------------------

# Matches:  - [ ] Some text (target: 2026-06-01)
# or:       - [x] Some text (target: 2026-06-01)
# The target date portion is optional.
_MILESTONE_RE = re.compile(
    r"^- \[(?P<check>x| )\] (?P<text>.+?)(?:\s+\(target:\s*(?P<date>\d{4}-\d{2}-\d{2})\))?\s*$",
    re.IGNORECASE,
)

# Matches:  - Some bullet text (2026-05-20)
_BULLET_RE = re.compile(
    r"^- (?P<text>.+?)\s+\((?P<date>\d{4}-\d{2}-\d{2})\)\s*$"
)


def parse_milestones(content: str) -> list[MilestoneEntry]:
    """Extract milestone entries from requirements.md content."""
    milestones: list[MilestoneEntry] = []
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Goals") or stripped.startswith("## Milestones"):
            in_section = True
            continue
        if in_section and stripped.startswith("## ") and not stripped.startswith("## Goals"):
            in_section = False
            continue
        if not in_section:
            continue
        m = _MILESTONE_RE.match(stripped)
        if m:
            milestones.append(MilestoneEntry(
                text=m.group("text").strip(),
                target_date=m.group("date"),
                completed=m.group("check").lower() == "x",
            ))
    return milestones


def parse_resume_bullets(content: str) -> list[str]:
    """Extract resume bullet strings from the Resume Bullets section."""
    bullets: list[str] = []
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Resume Bullets"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            in_section = False
            continue
        if not in_section:
            continue
        m = _BULLET_RE.match(stripped)
        if m:
            bullets.append(m.group("text").strip())
    return bullets


def parse_project_title(content: str) -> str:
    """Extract the project title from the '# Project:' line."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# Project:"):
            title = stripped[len("# Project:"):].strip()
            if title and title != "[FILL IN PROJECT NAME]":
                return title
    return ""


def parse_description(content: str) -> str:
    """Extract the first non-empty, non-template line from ## Description."""
    in_section = False
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "## Description":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section:
            continue
        if stripped and not stripped.startswith("[") and not stripped.startswith("<!--"):
            lines.append(stripped)
    return " ".join(lines)


def parse_declared_skills(content: str) -> list[str]:
    """Extract skills listed under ## Technologies & Skills."""
    skills: list[str] = []
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Technologies") or stripped.startswith("## Skills"):
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section:
            continue
        if stripped.startswith("- ") and not stripped.startswith("- [Skill"):
            skill = stripped[2:].strip()
            if skill and not skill.startswith("<!--"):
                skills.append(skill)
    return skills


def parse_completion_criteria(content: str) -> str:
    """Return the completion criteria text (non-empty = criteria defined)."""
    in_section = False
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "## Completion Criteria":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section:
            continue
        if stripped and not stripped.startswith("<!--") and not stripped.startswith("[Define"):
            lines.append(stripped)
    return " ".join(lines)
