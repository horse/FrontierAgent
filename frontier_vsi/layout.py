from __future__ import annotations

import json
from pathlib import Path

from .models import ProjectState


class ProjectLayout:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.meta_dir = self.root / ".frontiervsi"
        self.project_file = self.meta_dir / "project.json"
        self.canonical_dir = self.root / "canonical"
        self.revisions_dir = self.canonical_dir / "revisions"
        self.runs_dir = self.root / "runs"
        self.events_dir = self.meta_dir / "events"
        self.requests_dir = self.meta_dir / "requests"
        self.locks_dir = self.meta_dir / "locks"
        self.transactions_dir = self.meta_dir / "transactions"

    def snapshot_dir(self, snapshot_id: str) -> Path:
        return self.revisions_dir / snapshot_id

    def read_state(self) -> ProjectState:
        data = json.loads(self.project_file.read_text(encoding="utf-8"))
        return ProjectState.model_validate(data)


def initialize_project(root: Path | str, *, project_id: str, title: str) -> ProjectState:
    layout = ProjectLayout(root)
    layout.root.mkdir(parents=True, exist_ok=True)
    if layout.project_file.exists():
        raise FileExistsError(f"FrontierVSI project already exists: {layout.root}")

    layout.meta_dir.mkdir(parents=True, exist_ok=True)
    for directory in (
        layout.canonical_dir,
        layout.revisions_dir,
        layout.runs_dir,
        layout.events_dir,
        layout.requests_dir,
        layout.locks_dir,
        layout.transactions_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    state = ProjectState(project_id=project_id, title=title)
    layout.snapshot_dir(state.current_snapshot).mkdir(parents=True, exist_ok=False)
    payload = state.model_dump(mode="json")
    with layout.project_file.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return state
