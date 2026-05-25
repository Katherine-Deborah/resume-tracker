# Project Tracker + Master Resume Automation System

A local, zero-cost Python CLI tool that watches `~/Documents` for new project folders, enforces a structured requirements file per project, tracks progress against milestones, and syncs skills and resume bullets into a central `master_resume.json`. No APIs, no databases, no LLM calls — runs entirely on your machine.

The master resume feeds a separate downstream JD-tailoring agent. This tool keeps the source of truth current.

---

## Installation

```bash
git clone https://github.com/Katherine-Deborah/resume-tracker.git
cd automated-resume-tracker
pip install -e .
```

Requires Python 3.10+. Dependencies (`watchdog`, `schedule`, `pyyaml`, `colorama`) are installed automatically.

---

## Quick Start

```bash
# 1. Initialize the tracker (creates ~/Documents/.resume-tracker/)
tracker init

# 2. Start the folder watcher (foreground — Ctrl+C to stop)
tracker watch

# 3. Create a new folder inside ~/Documents/
#    The watcher detects it, creates .tracker/ and requirements.md automatically.

# 4. Fill out requirements.md in the new folder.

# 5. Run a scan to update status and sync your resume
tracker scan

# 6. View project status
tracker status

# 7. View your master resume
tracker resume
```

---

## Commands

| Command | Description |
|---------|-------------|
| `tracker init` | Initialize `.resume-tracker/` in `~/Documents` |
| `tracker watch` | Start the folder watcher in the foreground |
| `tracker watch --daemon` | Start the watcher as a background process |
| `tracker watch --stop` | Stop the background watcher |
| `tracker scan` | Run a progress scan immediately |
| `tracker status` | Show a summary table of all projects |
| `tracker status <name>` | Show detailed status for one project |
| `tracker complete <name>` | Manually mark a project as completed |
| `tracker abandon <name>` | Manually mark a project as abandoned |
| `tracker reactivate <name>` | Reactivate a completed or abandoned project |
| `tracker resume` | Print the current `master_resume.json` |
| `tracker resume export` | Export to human-readable `master_resume.md` |
| `tracker config` | Show current configuration |
| `tracker config set <key> <value>` | Update a config value |
| `tracker ignore <folder>` | Add a folder to the ignore list |
| `tracker unignore <folder>` | Remove a folder from the ignore list |

Project names support partial, case-insensitive matching (e.g., `tracker status ml` matches `my-ml-project`).

---

## How It Works

### Folder Watcher (`tracker watch`)
Monitors `~/Documents` for new top-level folders. When a folder is created:
1. Creates `.tracker/status.json` and `.tracker/history.json` inside it.
2. Creates `requirements.md` from a template.
3. Registers the project in `project_registry.json`.
4. Dispatches a notification to `tracker.log`.

The watcher also runs a periodic scan on `scan_interval_minutes` and a daily summary at `daily_summary_hour`.

### Requirements File (`requirements.md`)
The template is pre-filled with placeholders. Edit it to:
- Name the project and write a description.
- Define milestones with target dates: `- [ ] Milestone text (target: YYYY-MM-DD)`
- List expected technologies and skills.
- Write resume bullets as you hit milestones: `- Bullet text (YYYY-MM-DD)`

### Progress Scanner (`tracker scan`)
On each scan, for every active project:
- Counts files by extension and finds the latest modified timestamp.
- Detects skills from file extensions and dependency files.
- Parses milestones: counts total, completed, and overdue.
- Determines status (priority order: `completed` → `overdue` → `stale` → `not_started` → `active`).
- Writes `status.json` and appends to `history.json` (capped at 365 entries).
- Syncs bullets and skills into `master_resume.json`.

### Status Colors (`tracker status`)
| Color | Status |
|-------|--------|
| Green | active |
| Yellow | stale |
| Red | overdue |
| Dim | completed / abandoned |

### Unfilled Requirements Reminder
If `requirements.md` is not filled within 24 hours, a reminder is logged. Reminders repeat every 24 hours up to `max_nag_count` (default: 5), then stop and flag the project as `requirements_stale`.

---

## Config Reference

Located at `~/Documents/.resume-tracker/config.yaml`.

| Key | Default | Description |
|-----|---------|-------------|
| `watch_directory` | `~/Documents` | Directory to monitor |
| `scan_interval_minutes` | `60` | How often the scanner runs (when watcher is active) |
| `stale_threshold_days` | `3` | Days of inactivity before `stale` status |
| `max_nag_count` | `5` | Max unfilled-requirements reminders before stopping |
| `daily_summary_hour` | `9` | Hour (24h) for the daily summary notification |
| `ignored_folders` | `[.resume-tracker, node_modules, ...]` | Folders never tracked |
| `ignored_patterns` | `[".*"]` | Glob patterns to ignore (e.g., hidden folders) |
| `file_extension_to_skill_map` | `.py → Python`, `.ts → TypeScript`, ... | Extension → skill name |
| `dependency_file_to_skill_map` | `Dockerfile → Docker`, ... | Config file → skill name |

Edit with `tracker config set <key> <value>` or directly in `config.yaml`.

---

## Data Files

| File | Description |
|------|-------------|
| `~/.resume-tracker/master_resume.json` | Master resume (auto-managed `projects` and `skills` sections; `education` and `experience` are user-managed) |
| `~/.resume-tracker/project_registry.json` | Index of all tracked projects |
| `~/.resume-tracker/config.yaml` | User settings |
| `~/.resume-tracker/logs/tracker.log` | Rotating notification log (1 MB max, 3 backups) |
| `<project>/.tracker/status.json` | Latest scan snapshot for the project |
| `<project>/.tracker/history.json` | Historical scan entries (365 max) |

---

## Notes

- All timestamps are stored in UTC; displayed in local time.
- JSON files are written atomically (`.tmp` rename) with file-lock protection.
- The watcher uses Windows-safe cross-platform file locking (no `fcntl`).
- Never write tracker JSON files from PowerShell `Set-Content` — it adds a UTF-8 BOM. Use Python's `write_json()` or the CLI.
