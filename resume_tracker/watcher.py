from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Type

import schedule

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileModifiedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from resume_tracker.models import ProjectEntry
from resume_tracker.notifier import LogNotifier, Notifier
from resume_tracker.utils import (
    is_template_filled,
    parse_declared_skills,
    parse_milestones,
    read_json,
    render_requirements_template,
    utc_now,
    write_json,
)


def _initial_status(now: str) -> dict:
    return {
        "last_scan": now,
        "file_count": 0,
        "file_types": {},
        "last_file_modified": "",
        "days_since_activity": 0,
        "status": "not_started",
        "milestones": {"total": 0, "completed": 0, "overdue": 0, "next_due": None},
        "detected_skills": [],
        "requirements_complete": False,
    }


class _TrackerEventHandler(FileSystemEventHandler):
    def __init__(
        self,
        watch_dir: Path,
        tracker_root: Path,
        cfg: dict,
        notifier: Notifier,
    ) -> None:
        super().__init__()
        self._watch_dir = watch_dir
        self._tracker_root = tracker_root
        self._cfg = cfg
        self._notifier = notifier
        self._debounce: dict[str, float] = {}
        self._debounce_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _registry_path(self) -> Path:
        return self._tracker_root / "project_registry.json"

    def _is_top_level(self, path: str) -> bool:
        return Path(path).parent == self._watch_dir

    def _should_ignore(self, name: str, path: Path) -> bool:
        if path.is_symlink():
            self._notifier.send(
                "Symlink skipped",
                f"Not following symlink at {path}",
                priority="normal",
            )
            return True
        if name == ".resume-tracker":
            return True
        if name in self._cfg.get("ignored_folders", []):
            return True
        for pattern in self._cfg.get("ignored_patterns", []):
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    # ------------------------------------------------------------------
    # watchdog event hooks
    # ------------------------------------------------------------------

    def on_created(self, event: DirCreatedEvent) -> None:
        if not event.is_directory:
            return
        src = event.src_path
        if not self._is_top_level(src):
            return
        proj_path = Path(src)
        if self._should_ignore(proj_path.name, proj_path):
            return

        with self._debounce_lock:
            now = time.monotonic()
            if now - self._debounce.get(src, 0.0) < 2.0:
                return
            self._debounce[src] = now

        try:
            self._bootstrap_project(proj_path)
        except Exception as exc:
            self._notifier.send("Bootstrap error", str(exc), priority="high")

    def on_deleted(self, event: DirDeletedEvent) -> None:
        if not event.is_directory:
            return
        if not self._is_top_level(event.src_path):
            return
        try:
            self._mark_abandoned(str(Path(event.src_path)))
        except Exception as exc:
            self._notifier.send("Registry update error", str(exc), priority="high")

    def on_moved(self, event: DirMovedEvent) -> None:
        if not event.is_directory:
            return
        if not self._is_top_level(event.src_path):
            return
        try:
            self._update_path(str(Path(event.src_path)), str(Path(event.dest_path)))
        except Exception as exc:
            self._notifier.send("Registry update error", str(exc), priority="high")

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        p = Path(event.src_path)
        if p.name != "requirements.md":
            return
        # Must be <watch_dir>/<project>/requirements.md
        if p.parent.parent != self._watch_dir:
            return
        try:
            self._handle_requirements_modified(p)
        except Exception as exc:
            self._notifier.send("Requirements parse error", str(exc), priority="high")

    # ------------------------------------------------------------------
    # Registry operations
    # ------------------------------------------------------------------

    def _bootstrap_project(self, proj_path: Path) -> None:
        tracker_dir = proj_path / ".tracker"
        req_path = proj_path / "requirements.md"
        status_path = tracker_dir / "status.json"
        history_path = tracker_dir / "history.json"

        try:
            tracker_dir.mkdir(exist_ok=True)
        except OSError as exc:
            self._notifier.send("Permission error", f"Cannot create {tracker_dir}: {exc}", priority="high")
            return

        now = utc_now()

        if not status_path.exists():
            write_json(status_path, _initial_status(now), lock=False)
        if not history_path.exists():
            write_json(history_path, [], lock=False)
        if not req_path.exists():
            req_path.write_text(render_requirements_template(now), encoding="utf-8")

        reg_path = self._registry_path()
        registry = read_json(reg_path) if reg_path.exists() else {"projects": []}
        proj_str = str(proj_path)
        if not any(p["path"] == proj_str for p in registry["projects"]):
            entry = ProjectEntry(
                id=str(uuid.uuid4()),
                name=proj_path.name,
                path=proj_str,
                status="not_started",
                created_at=now,
                last_activity=now,
            )
            registry["projects"].append(entry.to_dict())
            write_json(reg_path, registry)

        self._notifier.send(
            "New project detected",
            f"{proj_path.name}. Fill out requirements.md to start tracking.",
        )

    def _mark_abandoned(self, proj_path_str: str) -> None:
        reg_path = self._registry_path()
        if not reg_path.exists():
            return
        registry = read_json(reg_path)
        changed = False
        for p in registry["projects"]:
            if p["path"] == proj_path_str:
                p["status"] = "abandoned"
                changed = True
        if changed:
            write_json(reg_path, registry)

    def _update_path(self, old_path_str: str, new_path_str: str) -> None:
        reg_path = self._registry_path()
        if not reg_path.exists():
            return
        registry = read_json(reg_path)
        changed = False
        for p in registry["projects"]:
            if p["path"] == old_path_str:
                p["path"] = new_path_str
                p["name"] = Path(new_path_str).name
                changed = True
        if changed:
            write_json(reg_path, registry)

    def _handle_requirements_modified(self, req_path: Path) -> None:
        proj_path_str = str(req_path.parent)
        reg_path = self._registry_path()
        if not reg_path.exists():
            return
        try:
            content = req_path.read_text(encoding="utf-8")
        except OSError:
            return
        if not is_template_filled(content):
            return
        registry = read_json(reg_path)
        changed = False
        for p in registry["projects"]:
            if p["path"] == proj_path_str:
                p["requirements_filled"] = True
                p["declared_skills"] = parse_declared_skills(content)
                p["milestones_total"] = len(parse_milestones(content))
                changed = True
        if changed:
            write_json(reg_path, registry)


# ---------------------------------------------------------------------------
# Startup reconciliation
# ---------------------------------------------------------------------------

def _reconcile(
    watch_dir: Path,
    tracker_root: Path,
    cfg: dict,
    notifier: Notifier,
) -> None:
    """Bootstrap untracked folders; mark registry entries whose folders are gone."""
    reg_path = tracker_root / "project_registry.json"
    registry = read_json(reg_path) if reg_path.exists() else {"projects": []}
    registered_paths = {p["path"] for p in registry["projects"]}

    handler = _TrackerEventHandler(watch_dir, tracker_root, cfg, notifier)
    existing_paths: set[str] = set()
    dirs_to_bootstrap: list[Path] = []

    try:
        for entry in watch_dir.iterdir():
            if not entry.is_dir():
                continue
            if handler._should_ignore(entry.name, entry):
                continue
            existing_paths.add(str(entry))
            if str(entry) not in registered_paths:
                dirs_to_bootstrap.append(entry)
    except PermissionError:
        pass

    for d in dirs_to_bootstrap:
        handler._bootstrap_project(d)

    # Reload in case bootstrap wrote new entries
    if dirs_to_bootstrap and reg_path.exists():
        registry = read_json(reg_path)

    changed = False
    for p in registry["projects"]:
        if p["path"] not in existing_paths and p.get("status") != "abandoned":
            p["status"] = "abandoned"
            changed = True
    if changed:
        write_json(reg_path, registry)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_watcher(
    tracker_root: Path,
    cfg: dict,
    daemon: bool = False,
    observer_class: type | None = None,
) -> None:
    if daemon:
        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            [sys.executable, "-m", "resume_tracker.cli", "watch"],
            **kwargs,
        )
        pid_path = tracker_root / "watcher.pid"
        pid_path.write_text(str(proc.pid), encoding="utf-8")
        print(f"Watcher started as background process (PID {proc.pid}).")
        print(f"Stop with: tracker watch --stop")
        return

    # Foreground mode
    if observer_class is None:
        observer_class = Observer

    watch_dir = Path(cfg["watch_directory"]).expanduser()
    notifier = LogNotifier(tracker_root / "logs")

    _reconcile(watch_dir, tracker_root, cfg, notifier)

    handler = _TrackerEventHandler(watch_dir, tracker_root, cfg, notifier)
    observer = observer_class()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()

    from resume_tracker.scanner import dispatch_daily_summary, run_scan  # noqa: PLC0415

    schedule.clear()
    schedule.every(cfg.get("scan_interval_minutes", 60)).minutes.do(run_scan, tracker_root, cfg)
    hour = cfg.get("daily_summary_hour", 9)
    schedule.every().day.at(f"{hour:02d}:00").do(dispatch_daily_summary, tracker_root, cfg)

    print(f"Watching {watch_dir} — press Ctrl+C to stop.")
    try:
        while observer.is_alive():
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        schedule.clear()
        print("Watcher stopped.")


def stop_watcher(tracker_root: Path) -> None:
    pid_path = tracker_root / "watcher.pid"
    if not pid_path.exists():
        print("No watcher PID file found. Is the watcher running as a daemon?")
        return

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError) as exc:
        print(f"Error reading PID file: {exc}")
        return

    if sys.platform == "win32":
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
        )
        if result.returncode == 0:
            print(f"Watcher (PID {pid}) stopped.")
        else:
            print(f"Failed to stop watcher (PID {pid}): {result.stderr.decode().strip()}")
    else:
        import signal as _signal
        try:
            os.kill(pid, _signal.SIGTERM)
            print(f"Watcher (PID {pid}) stopped.")
        except ProcessLookupError:
            print(f"Process {pid} not found — it may have already stopped.")
        except PermissionError:
            print(f"Permission denied to stop process {pid}.")

    pid_path.unlink(missing_ok=True)
