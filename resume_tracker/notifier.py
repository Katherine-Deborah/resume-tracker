from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


class Notifier:
    def send(self, title: str, body: str, priority: str = "normal") -> None:
        raise NotImplementedError


class LogNotifier(Notifier):
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("resume_tracker")
        if not self._logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                log_dir / "tracker.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            self._logger.addHandler(handler)
            self._logger.setLevel(logging.DEBUG)
            self._logger.propagate = False

    def send(self, title: str, body: str, priority: str = "normal") -> None:
        level = logging.WARNING if priority == "high" else logging.INFO
        self._logger.log(level, "%s — %s", title, body)
