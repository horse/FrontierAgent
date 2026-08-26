from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from .canonical_json import canonical_json_bytes
from .layout import ProjectLayout
from .models import ProjectEvent


def _event_log_path(root: Path | str) -> Path:
    layout = ProjectLayout(root)
    layout.events_dir.mkdir(parents=True, exist_ok=True)
    return layout.events_dir / "project.jsonl"


def append_event(root: Path | str, event: ProjectEvent) -> None:
    path = _event_log_path(root)
    line = canonical_json_bytes(event.model_dump(mode="json")) + b"\n"
    with path.open("ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def iter_events(root: Path | str) -> Iterator[ProjectEvent]:
    path = _event_log_path(root)
    if not path.exists():
        return
    with path.open("rb") as handle:
        for raw_line in handle:
            if raw_line.strip():
                yield ProjectEvent.model_validate_json(raw_line)
