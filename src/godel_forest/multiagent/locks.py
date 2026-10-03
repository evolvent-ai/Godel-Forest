"""Small file-lock helpers used around budget-state mutations."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def file_lock(path: Path) -> Iterator[TextIO]:
    """Take an exclusive advisory lock on ``path`` for the duration of the block."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def budget_lock_path(work_dir: Path) -> Path:
    """Return the RSIBench-Data-owned lock beside ``budget_state.json``."""
    return Path(work_dir).parent / "runner_state" / "budget_state.lock"
