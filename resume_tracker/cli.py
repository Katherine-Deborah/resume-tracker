from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from resume_tracker.config import (
    DEFAULT_CONFIG,
    get_tracker_root,
    load_config,
    save_config,
    set_config_value,
    validate_config,
)
from resume_tracker.models import MasterResume
from resume_tracker.utils import utc_now, write_json


def _require_init(tracker_root: Path) -> None:
    if not tracker_root.exists():
        print("Error: tracker not initialized. Run `tracker init` first.")
        sys.exit(1)


def _load_registry(tracker_root: Path) -> dict:
    reg_path = tracker_root / "project_registry.json"
    if not reg_path.exists():
        return {"projects": []}
    with reg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_registry(tracker_root: Path, registry: dict) -> None:
    write_json(tracker_root / "project_registry.json", registry)


def _find_project(registry: dict, name: str) -> list[dict]:
    """Return all projects whose name contains `name` (case-insensitive)."""
    needle = name.lower()
    return [p for p in registry["projects"] if needle in p["name"].lower()]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    cfg = DEFAULT_CONFIG.copy()
    tracker_root = get_tracker_root(cfg)

    if tracker_root.exists():
        print(f"Tracker already initialized at {tracker_root}")
        print("Use `tracker config` to view or modify settings.")
        return

    tracker_root.mkdir(parents=True, exist_ok=True)
    (tracker_root / "logs").mkdir(exist_ok=True)

    # Write default config
    save_config(cfg, tracker_root)

    # Empty project registry
    write_json(tracker_root / "project_registry.json", {"projects": []}, lock=False)

    # Empty master resume scaffold
    resume = MasterResume.empty(utc_now())
    write_json(tracker_root / "master_resume.json", resume.to_dict(), lock=False)

    print(f"Tracker initialized at {tracker_root}")
    print("  config.yaml          — edit to customize settings")
    print("  project_registry.json — auto-managed project index")
    print("  master_resume.json   — your master resume (add education/experience manually)")
    print()
    print("Next: run `tracker watch` to start monitoring ~/Documents for new project folders.")


def cmd_status(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    try:
        from resume_tracker.scanner import display_status  # noqa: PLC0415
        display_status(tracker_root, cfg, project_name=getattr(args, "project", None))
    except ImportError:
        # Scanner not yet implemented (Sessions 1–2)
        registry = _load_registry(tracker_root)
        projects = registry.get("projects", [])
        if not projects:
            print("No projects tracked yet. Start `tracker watch` to begin monitoring.")
            return
        name_filter = getattr(args, "project", None)
        if name_filter:
            projects = _find_project(registry, name_filter)
            if not projects:
                print(f"No project matching '{name_filter}' found.")
                return
        for p in projects:
            print(f"  [{p['status']:12s}]  {p['name']}  ({p['path']})")


def cmd_scan(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    try:
        from resume_tracker.scanner import run_scan  # noqa: PLC0415
        run_scan(tracker_root, cfg)
    except ImportError:
        print("Scanner not yet available (Session 3).")


def cmd_complete(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    registry = _load_registry(tracker_root)
    matches = _find_project(registry, args.project)
    if not matches:
        print(f"No project matching '{args.project}' found.")
        return
    if len(matches) > 1:
        print(f"Multiple projects match '{args.project}':")
        for p in matches:
            print(f"  {p['name']}")
        print("Please be more specific.")
        return

    project = matches[0]
    if not project.get("resume_synced") or not project.get("milestones_completed"):
        print(f"Warning: project '{project['name']}' has no resume bullets. "
              "Add some to requirements.md before or after marking complete.")

    project["status"] = "completed"
    _save_registry(tracker_root, registry)
    print(f"Project '{project['name']}' marked as completed.")


def cmd_abandon(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    registry = _load_registry(tracker_root)
    matches = _find_project(registry, args.project)
    if not matches:
        print(f"No project matching '{args.project}' found.")
        return
    if len(matches) > 1:
        print(f"Multiple projects match '{args.project}':")
        for p in matches:
            print(f"  {p['name']}")
        print("Please be more specific.")
        return

    matches[0]["status"] = "abandoned"
    _save_registry(tracker_root, registry)
    print(f"Project '{matches[0]['name']}' marked as abandoned.")


def cmd_reactivate(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    registry = _load_registry(tracker_root)
    matches = _find_project(registry, args.project)
    if not matches:
        print(f"No project matching '{args.project}' found.")
        return
    if len(matches) > 1:
        print(f"Multiple projects match '{args.project}':")
        for p in matches:
            print(f"  {p['name']}")
        print("Please be more specific.")
        return

    matches[0]["status"] = "active"
    _save_registry(tracker_root, registry)
    print(f"Project '{matches[0]['name']}' reactivated.")


def cmd_resume(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    resume_path = tracker_root / "master_resume.json"
    if not resume_path.exists():
        print("master_resume.json not found. Run `tracker init` first.")
        return

    subcommand = getattr(args, "resume_subcommand", None)
    if subcommand == "export":
        try:
            from resume_tracker.resume_updater import export_markdown  # noqa: PLC0415
            export_markdown(tracker_root)
        except ImportError:
            print("Resume updater not yet available (Session 4).")
        return

    with resume_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    print(json.dumps(data, indent=2, ensure_ascii=False))


def cmd_config(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)

    subcommand = getattr(args, "config_subcommand", None)
    if subcommand == "set":
        if not tracker_root.exists():
            print("Error: tracker not initialized. Run `tracker init` first.")
            sys.exit(1)
        try:
            cfg = set_config_value(cfg, args.key, args.value)
        except (ValueError, KeyError) as e:
            print(f"Error setting config: {e}")
            sys.exit(1)
        errors = validate_config(cfg)
        if errors:
            for e in errors:
                print(f"Validation error: {e}")
            sys.exit(1)
        save_config(cfg, tracker_root)
        print(f"Set {args.key} = {args.value}")
        return

    # Show current config
    import yaml  # noqa: PLC0415
    print(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))


def cmd_watch(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    try:
        from resume_tracker.watcher import start_watcher, stop_watcher  # noqa: PLC0415
    except ImportError:
        print("Watcher not yet available (Session 2).")
        return

    if getattr(args, "stop", False):
        stop_watcher(tracker_root)
        return

    daemon = getattr(args, "daemon", False)
    start_watcher(tracker_root, cfg, daemon=daemon)


def cmd_ignore(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    if args.folder not in cfg["ignored_folders"]:
        cfg["ignored_folders"].append(args.folder)
        save_config(cfg, tracker_root)
        print(f"Added '{args.folder}' to ignore list.")
    else:
        print(f"'{args.folder}' is already in the ignore list.")


def cmd_unignore(args: argparse.Namespace) -> None:
    cfg = load_config()
    tracker_root = get_tracker_root(cfg)
    _require_init(tracker_root)

    if args.folder in cfg["ignored_folders"]:
        cfg["ignored_folders"].remove(args.folder)
        save_config(cfg, tracker_root)
        print(f"Removed '{args.folder}' from ignore list.")
    else:
        print(f"'{args.folder}' was not in the ignore list.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracker",
        description="Project Tracker + Master Resume Automation System",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # init
    sub.add_parser("init", help="Initialize tracker in ~/Documents")

    # status
    status_p = sub.add_parser("status", help="Show project status summary")
    status_p.add_argument("project", nargs="?", help="Project name (partial match)")

    # scan
    sub.add_parser("scan", help="Run a progress scan immediately")

    # complete
    complete_p = sub.add_parser("complete", help="Mark a project as completed")
    complete_p.add_argument("project", help="Project name")

    # abandon
    abandon_p = sub.add_parser("abandon", help="Mark a project as abandoned")
    abandon_p.add_argument("project", help="Project name")

    # reactivate
    reactivate_p = sub.add_parser("reactivate", help="Reactivate a completed or abandoned project")
    reactivate_p.add_argument("project", help="Project name")

    # resume
    resume_p = sub.add_parser("resume", help="Show or export the master resume")
    resume_sub = resume_p.add_subparsers(dest="resume_subcommand")
    resume_sub.add_parser("export", help="Export master_resume.json to markdown")

    # config
    config_p = sub.add_parser("config", help="View or update configuration")
    config_sub = config_p.add_subparsers(dest="config_subcommand")
    config_set_p = config_sub.add_parser("set", help="Set a config value")
    config_set_p.add_argument("key", help="Config key")
    config_set_p.add_argument("value", help="New value")

    # watch
    watch_p = sub.add_parser("watch", help="Start the folder watcher daemon")
    watch_p.add_argument("--daemon", action="store_true", help="Run as background process")
    watch_p.add_argument("--stop", action="store_true", help="Stop the background watcher")

    # ignore / unignore
    ignore_p = sub.add_parser("ignore", help="Add a folder to the ignore list")
    ignore_p.add_argument("folder", help="Folder name to ignore")
    unignore_p = sub.add_parser("unignore", help="Remove a folder from the ignore list")
    unignore_p.add_argument("folder", help="Folder name to unignore")

    return parser


def main() -> None:
    import colorama  # noqa: PLC0415
    colorama.init(autoreset=True)
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "init": cmd_init,
        "status": cmd_status,
        "scan": cmd_scan,
        "complete": cmd_complete,
        "abandon": cmd_abandon,
        "reactivate": cmd_reactivate,
        "resume": cmd_resume,
        "config": cmd_config,
        "watch": cmd_watch,
        "ignore": cmd_ignore,
        "unignore": cmd_unignore,
    }

    handler = commands.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
