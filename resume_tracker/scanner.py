from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from colorama import Fore, Style

from resume_tracker.models import MilestoneEntry, StatusSnapshot
from resume_tracker.notifier import LogNotifier
from resume_tracker.resume_updater import sync_resume
from resume_tracker.utils import (
    days_since,
    is_template_filled,
    parse_completion_criteria,
    parse_declared_skills,
    parse_milestones,
    parse_resume_bullets,
    read_json,
    utc_now,
    utc_to_local_display,
    write_json,
)

_log = logging.getLogger("resume_tracker.scanner")

_EXCLUDED_DIRS = {".tracker", ".git", "node_modules", "__pycache__", ".venv"}
_FILE_CAP = 10_000


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _file_activity(proj_path: Path) -> tuple[int, dict[str, int], str, float]:
    """Walk project dir, return (file_count, file_types, last_modified_utc, days_since)."""
    file_count = 0
    file_types: dict[str, int] = {}
    latest_mtime: float = 0.0
    capped = False

    for root, dirs, files in os.walk(proj_path):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for fname in files:
            if file_count >= _FILE_CAP:
                capped = True
                break
            fpath = Path(root) / fname
            try:
                mtime = fpath.stat().st_mtime
            except OSError:
                continue
            file_count += 1
            ext = fpath.suffix.lower() or "(no ext)"
            file_types[ext] = file_types.get(ext, 0) + 1
            if mtime > latest_mtime:
                latest_mtime = mtime
        if capped:
            _log.warning("File cap (%d) reached for %s — using partial results", _FILE_CAP, proj_path)
            break

    if latest_mtime == 0.0:
        return file_count, file_types, "", 0.0

    last_modified_utc = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    elapsed = days_since(last_modified_utc)
    return file_count, file_types, last_modified_utc, elapsed


def _detect_skills(proj_path: Path, cfg: dict) -> list[str]:
    """Detect skills from file extensions and presence of dependency/config files."""
    ext_map: dict[str, str] = cfg.get("file_extension_to_skill_map", {})
    dep_map: dict[str, str] = cfg.get("dependency_file_to_skill_map", {})
    skills: set[str] = set()

    for root, dirs, files in os.walk(proj_path):
        dirs[:] = [d for d in dirs if d not in _EXCLUDED_DIRS]
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext in ext_map:
                skills.add(ext_map[ext])
            if fname in dep_map:
                skills.add(dep_map[fname])

    # dependency_file_to_skill_map entries that are directory paths (e.g. ".github/workflows")
    for dep_path, skill in dep_map.items():
        if (proj_path / dep_path).exists():
            skills.add(skill)

    return sorted(skills)


def _milestone_summary(milestones: list[MilestoneEntry]) -> dict:
    """Return milestone counts and next due date."""
    today = date.today().isoformat()
    total = len(milestones)
    completed = sum(1 for m in milestones if m.completed)
    overdue = sum(
        1 for m in milestones
        if not m.completed and m.target_date and m.target_date < today
    )
    upcoming = [
        m.target_date for m in milestones
        if not m.completed and m.target_date and m.target_date >= today
    ]
    next_due = min(upcoming) if upcoming else None
    return {"total": total, "completed": completed, "overdue": overdue, "next_due": next_due}


def _determine_status(
    requirements_filled: bool,
    milestones: list[MilestoneEntry],
    days_since_activity: float,
    stale_threshold: int,
    completion_criteria: str,
) -> str:
    today = date.today().isoformat()

    # 1. All milestones checked AND completion criteria non-empty → completed
    if (
        milestones
        and all(m.completed for m in milestones)
        and completion_criteria.strip()
    ):
        return "completed"

    # 2. Any milestone overdue → overdue
    if any(
        not m.completed and m.target_date and m.target_date < today
        for m in milestones
    ):
        return "overdue"

    # 3. Stale
    if days_since_activity > stale_threshold:
        return "stale"

    # 4. Requirements not filled
    if not requirements_filled:
        return "not_started"

    return "active"


def _update_history(history_path: Path, snapshot: StatusSnapshot) -> None:
    """Append snapshot entry to history.json, capped at 365 entries."""
    existing: list = []
    if history_path.exists():
        try:
            existing = read_json(history_path)  # type: ignore[assignment]
        except Exception:
            existing = []

    entry = {
        "timestamp": snapshot.last_scan,
        "status": snapshot.status,
        "file_count": snapshot.file_count,
        "days_since_activity": snapshot.days_since_activity,
        "milestones_completed": snapshot.milestones.get("completed", 0),
        "milestones_total": snapshot.milestones.get("total", 0),
    }
    existing.append(entry)
    if len(existing) > 365:
        existing = existing[-365:]
    write_json(history_path, existing)


def _dispatch_notifications(notifier, results: list[tuple[dict, StatusSnapshot, str]]) -> None:
    """Fire stale/overdue/completed notifications for each project result."""
    for proj, snapshot, _ in results:
        name = proj["name"]
        status = snapshot.status
        ms = snapshot.milestones

        if status == "stale":
            next_info = ""
            if ms.get("next_due"):
                next_info = f" Next milestone due {ms['next_due']}."
            notifier.send(
                f"Stale project: {name}",
                f"Project {name} has had no activity for "
                f"{snapshot.days_since_activity:.0f} days.{next_info}",
                priority="normal",
            )
        elif status == "overdue":
            n_overdue = ms.get("overdue", 0)
            notifier.send(
                f"Overdue milestone: {name}",
                f"Project {name} has {n_overdue} overdue milestone(s).",
                priority="high",
            )
        elif status == "completed":
            bullets = parse_resume_bullets(_read_requirements(Path(proj["path"])))
            notifier.send(
                f"Project complete: {name}",
                f"Project {name} marked complete! {len(bullets)} resume bullet(s) synced.",
                priority="normal",
            )


def _read_requirements(proj_path: Path) -> str:
    req = proj_path / "requirements.md"
    if not req.exists():
        return ""
    try:
        return req.read_text(encoding="utf-8-sig")  # strips UTF-8 BOM if present
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_scan(tracker_root: Path, cfg: dict) -> None:
    """Scan all active projects, update status.json / history.json / registry."""
    registry_path = tracker_root / "project_registry.json"
    if not registry_path.exists():
        _log.warning("No project registry found — nothing to scan.")
        return

    registry = read_json(registry_path)
    projects: list[dict] = registry.get("projects", [])
    stale_threshold: int = cfg.get("stale_threshold_days", 3)

    notifier = LogNotifier(tracker_root / "logs")
    results: list[tuple[dict, StatusSnapshot, str]] = []

    for proj in projects:
        if proj.get("status") in ("completed", "abandoned"):
            continue

        proj_path = Path(proj["path"])
        if not proj_path.exists():
            _log.warning("Project path missing, skipping: %s", proj_path)
            continue

        # Re-parse requirements.md each scan (catch-up if watcher was off)
        req_content = _read_requirements(proj_path)
        requirements_filled = is_template_filled(req_content) if req_content else False
        declared_skills = parse_declared_skills(req_content) if req_content else []
        milestones = parse_milestones(req_content) if req_content else []
        completion_criteria = parse_completion_criteria(req_content) if req_content else ""

        # Nag reminder for unfilled requirements
        if not requirements_filled:
            nag_count = proj.get("nag_count", 0)
            last_nag = proj.get("last_nag", "")
            max_nag = cfg.get("max_nag_count", 5)
            if nag_count < max_nag and (not last_nag or days_since(last_nag) >= 1.0):
                notifier.send(
                    f"Requirements unfilled: {proj['name']}",
                    (
                        f"Project '{proj['name']}' has an unfilled requirements.md. "
                        f"Fill it out to start tracking. "
                        f"(Reminder {nag_count + 1}/{max_nag})"
                    ),
                    priority="normal",
                )
                proj["nag_count"] = nag_count + 1
                proj["last_nag"] = utc_now()
            elif nag_count >= max_nag:
                proj["requirements_stale"] = True

        file_count, file_types, last_modified, elapsed = _file_activity(proj_path)
        detected_skills = _detect_skills(proj_path, cfg)

        all_skills = sorted(set(declared_skills) | set(detected_skills))
        ms_summary = _milestone_summary(milestones)
        status = _determine_status(requirements_filled, milestones, elapsed, stale_threshold, completion_criteria)

        now = utc_now()
        snapshot = StatusSnapshot(
            last_scan=now,
            file_count=file_count,
            file_types=file_types,
            last_file_modified=last_modified,
            days_since_activity=round(elapsed, 2),
            status=status,
            milestones=ms_summary,
            detected_skills=detected_skills,
            requirements_complete=requirements_filled,
        )

        tracker_dir = proj_path / ".tracker"
        tracker_dir.mkdir(exist_ok=True)
        write_json(tracker_dir / "status.json", snapshot.to_dict())
        _update_history(tracker_dir / "history.json", snapshot)

        # Update registry entry
        proj["status"] = status
        proj["last_activity"] = last_modified or proj.get("last_activity", "")
        proj["milestones_total"] = ms_summary["total"]
        proj["milestones_completed"] = ms_summary["completed"]
        proj["detected_skills"] = detected_skills
        proj["declared_skills"] = declared_skills
        proj["requirements_filled"] = requirements_filled

        results.append((proj, snapshot, "\n".join(all_skills)))

    sync_resume(tracker_root, cfg, registry)
    write_json(registry_path, registry)
    _dispatch_notifications(notifier, results)
    _log.info("Scan complete. %d project(s) scanned.", len(results))


def display_status(tracker_root: Path, cfg: dict, project_name: str | None = None) -> None:
    """Print project status to stdout."""
    registry_path = tracker_root / "project_registry.json"
    if not registry_path.exists():
        print("No projects tracked yet. Start `tracker watch` to begin monitoring.")
        return

    registry = read_json(registry_path)
    projects: list[dict] = registry.get("projects", [])

    if not projects:
        print("No projects tracked yet. Start `tracker watch` to begin monitoring.")
        return

    if project_name:
        needle = project_name.lower()
        matches = [p for p in projects if needle in p["name"].lower()]
        if not matches:
            print(f"No project matching '{project_name}' found.")
            return
        if len(matches) > 1:
            print(f"Multiple projects match '{project_name}':")
            for p in matches:
                print(f"  {p['name']}")
            print("Please be more specific.")
            return
        _display_detail(matches[0])
    else:
        _display_summary(projects)


_STATUS_COLORS = {
    "active": Fore.GREEN,
    "stale": Fore.YELLOW,
    "overdue": Fore.RED,
    "completed": Style.DIM,
    "abandoned": Style.DIM,
}


def _color_status(status: str) -> str:
    color = _STATUS_COLORS.get(status, "")
    if color:
        return f"{color}{status}{Style.RESET_ALL}"
    return status


def _display_summary(projects: list[dict]) -> None:
    col_name = max((len(p["name"]) for p in projects), default=10)
    col_name = max(col_name, 20)
    header = f"{'Project':<{col_name}}  {'Status':<12}  {'Milestones':<12}  Last Activity"
    print(header)
    print("-" * (len(header) + 4))
    for p in projects:
        ms_total = p.get("milestones_total", 0)
        ms_done = p.get("milestones_completed", 0)
        ms_str = f"{ms_done}/{ms_total}"
        last = p.get("last_activity", "")
        if last:
            d = days_since(last)
            if d < 1:
                last_str = "today"
            elif d < 2:
                last_str = "1 day ago"
            else:
                last_str = f"{int(d)} days ago"
        else:
            last_str = "—"
        status_raw = p.get('status', '?')
        colored = _color_status(status_raw) + " " * max(0, 12 - len(status_raw))
        print(f"{p['name']:<{col_name}}  {colored}  {ms_str:<12}  {last_str}")


def _display_detail(proj: dict) -> None:
    print(f"Project:  {proj['name']}")
    print(f"Path:     {proj['path']}")
    print(f"Status:   {_color_status(proj.get('status', '?'))}")

    status_path = Path(proj["path"]) / ".tracker" / "status.json"
    if status_path.exists():
        try:
            snap = read_json(status_path)
        except Exception:
            snap = {}
    else:
        snap = {}

    if snap:
        ft = snap.get("file_types", {})
        ft_str = ", ".join(f"{ext} ({n})" for ext, n in sorted(ft.items(), key=lambda x: -x[1])[:5])
        print(f"Files:    {snap.get('file_count', 0)}  |  Types: {ft_str or '—'}")

        last = snap.get("last_file_modified", "")
        if last:
            d = days_since(last)
            if d < 1:
                last_str = f"today ({utc_to_local_display(last)})"
            else:
                last_str = f"{int(d)} days ago ({utc_to_local_display(last)})"
        else:
            last_str = "—"
        print(f"Activity: {last_str}")

        ms = snap.get("milestones", {})
        ms_str = (
            f"{ms.get('completed', 0)}/{ms.get('total', 0)} complete"
            f" | {ms.get('overdue', 0)} overdue"
        )
        if ms.get("next_due"):
            ms_str += f" | Next due: {ms['next_due']}"
        print(f"Milest.:  {ms_str}")

        skills = snap.get("detected_skills", [])
        print(f"Skills:   {', '.join(skills) if skills else '—'}")
        print(f"Req. filled: {'yes' if snap.get('requirements_complete') else 'no'}")
        print(f"Last scan:   {utc_to_local_display(snap.get('last_scan', ''))}")
    else:
        # Fall back to registry data if status.json not yet created
        print("(Run `tracker scan` to populate detailed status.)")
        if proj.get("detected_skills"):
            print(f"Skills:   {', '.join(proj['detected_skills'])}")


def dispatch_daily_summary(tracker_root: Path, cfg: dict) -> None:
    """Send a daily summary notification across all active projects."""
    registry_path = tracker_root / "project_registry.json"
    if not registry_path.exists():
        return

    registry = read_json(registry_path)
    projects: list[dict] = registry.get("projects", [])

    active = stale = overdue = 0
    upcoming: list[str] = []
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=7)).isoformat()

    for p in projects:
        status = p.get("status", "")
        if status == "active":
            active += 1
        elif status == "stale":
            stale += 1
        elif status == "overdue":
            overdue += 1

        if status in ("completed", "abandoned"):
            continue

        status_path = Path(p["path"]) / ".tracker" / "status.json"
        if not status_path.exists():
            continue
        try:
            snap = read_json(status_path)
        except Exception:
            continue
        next_due = snap.get("milestones", {}).get("next_due")
        if next_due and today <= next_due <= cutoff:
            upcoming.append(f"  {p['name']}: {next_due}")

    lines = [f"Active: {active}  Stale: {stale}  Overdue: {overdue}"]
    if upcoming:
        lines.append("Milestones due in next 7 days:")
        lines.extend(upcoming)

    notifier = LogNotifier(tracker_root / "logs")
    notifier.send("Daily Summary", "\n".join(lines), priority="normal")
