from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from frontier_vsi.errors import ProjectLockedError
from frontier_vsi.layout import initialize_project
from frontier_vsi.locking import ProjectLock


def _hold_lock(root: str, ready: mp.Queue[bool], release: mp.Queue[bool]) -> None:
    with ProjectLock(Path(root), timeout_s=1.0):
        ready.put(True)
        release.get(timeout=5)


def test_project_lock_fails_closed_under_contention(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    context = mp.get_context("spawn")
    ready: mp.Queue[bool] = context.Queue()
    release: mp.Queue[bool] = context.Queue()
    process = context.Process(target=_hold_lock, args=(str(root), ready, release))
    process.start()
    assert ready.get(timeout=3) is True

    try:
        with pytest.raises(ProjectLockedError):
            with ProjectLock(root, timeout_s=0.1):
                raise AssertionError("contended lock must not be acquired")
    finally:
        release.put(True)
        process.join(timeout=3)
        if process.is_alive():
            process.terminate()

    assert process.exitcode == 0
