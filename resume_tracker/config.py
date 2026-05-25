from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "watch_directory": "~/Documents",
    "scan_interval_minutes": 60,
    "stale_threshold_days": 3,
    "ignored_folders": [
        ".resume-tracker",
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
    ],
    "ignored_patterns": [".*"],
    "file_extension_to_skill_map": {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".jsx": "React",
        ".tsx": "React + TypeScript",
        ".java": "Java",
        ".cpp": "C++",
        ".c": "C",
        ".rs": "Rust",
        ".go": "Go",
        ".sql": "SQL",
        ".html": "HTML",
        ".css": "CSS",
        ".scss": "SASS/SCSS",
        ".ipynb": "Jupyter Notebooks",
        ".r": "R",
        ".swift": "Swift",
        ".kt": "Kotlin",
        ".dart": "Dart",
        ".sh": "Bash/Shell Scripting",
        ".tf": "Terraform",
        ".yml": "YAML Configuration",
        ".yaml": "YAML Configuration",
        ".docker": "Docker",
        ".proto": "Protocol Buffers",
    },
    "dependency_file_to_skill_map": {
        "Dockerfile": "Docker",
        "docker-compose.yml": "Docker Compose",
        "requirements.txt": "pip (Python)",
        "setup.py": "Python Packaging",
        "pyproject.toml": "Python Packaging",
        "package.json": "Node.js/npm",
        "Cargo.toml": "Rust/Cargo",
        "go.mod": "Go Modules",
        "Makefile": "Make",
        "CMakeLists.txt": "CMake",
        ".github/workflows": "GitHub Actions",
        "Jenkinsfile": "Jenkins CI",
        ".gitlab-ci.yml": "GitLab CI",
        "terraform.tfvars": "Terraform",
        "mlflow": "MLflow",
        "dvc.yaml": "DVC (Data Version Control)",
    },
    "notification_method": "log",
    "max_nag_count": 5,
    "daily_summary_hour": 9,
}

# Skills that should be categorized as "frameworks" rather than "tools"
FRAMEWORK_SKILLS = {
    "React",
    "React + TypeScript",
    "FastAPI",
    "Django",
    "Flask",
    "Spring",
    "Express",
    "Vue",
    "Angular",
    "MLflow",
}


def _tracker_root() -> Path:
    """Return the .resume-tracker directory path (not guaranteed to exist)."""
    config_path = Path.home() / "Documents" / ".resume-tracker"
    return config_path


def get_tracker_root(config: dict | None = None) -> Path:
    if config and "watch_directory" in config:
        watch = Path(config["watch_directory"]).expanduser()
    else:
        watch = Path.home() / "Documents"
    return watch / ".resume-tracker"


def config_path(tracker_root: Path) -> Path:
    return tracker_root / "config.yaml"


def load_config(tracker_root: Path | None = None) -> dict:
    root = tracker_root or _tracker_root()
    path = config_path(root)
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    merged = copy.deepcopy(DEFAULT_CONFIG)
    _deep_update(merged, loaded)
    return merged


def save_config(cfg: dict, tracker_root: Path | None = None) -> None:
    root = tracker_root or _tracker_root()
    root.mkdir(parents=True, exist_ok=True)
    path = config_path(root)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


def validate_config(cfg: dict) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []
    if not isinstance(cfg.get("scan_interval_minutes"), int) or cfg["scan_interval_minutes"] < 1:
        errors.append("scan_interval_minutes must be a positive integer")
    if not isinstance(cfg.get("stale_threshold_days"), (int, float)) or cfg["stale_threshold_days"] < 0:
        errors.append("stale_threshold_days must be a non-negative number")
    if not isinstance(cfg.get("ignored_folders"), list):
        errors.append("ignored_folders must be a list")
    return errors


def set_config_value(cfg: dict, key: str, value: str) -> dict:
    """Set a top-level config key, coercing value to the correct type."""
    int_keys = {"scan_interval_minutes", "max_nag_count", "daily_summary_hour"}
    float_keys = {"stale_threshold_days"}
    if key in int_keys:
        cfg[key] = int(value)
    elif key in float_keys:
        cfg[key] = float(value)
    else:
        cfg[key] = value
    return cfg


def _deep_update(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
