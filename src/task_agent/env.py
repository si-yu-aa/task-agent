"""Minimal .env loading for task-agent."""

from __future__ import annotations

from pathlib import Path
import os


def load_project_env(start_dir: str | Path | None = None, filename: str = ".env") -> Path | None:
    base = Path(start_dir or Path.cwd()).resolve()
    for candidate_dir in [base, *base.parents]:
        env_path = candidate_dir / filename
        if env_path.exists() and env_path.is_file():
            _apply_dotenv(env_path)
            return env_path
    return None


def _apply_dotenv(path: Path) -> None:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
