from pathlib import Path

import pytest

from frontier_vsi.layout import ProjectLayout, initialize_project


def test_initialize_project_creates_durable_layout(tmp_path: Path) -> None:
    root = tmp_path / "book"

    state = initialize_project(root, project_id="VSI-001", title="Test Book")
    layout = ProjectLayout(root)

    assert state.schema_version == 1
    assert state.project_id == "VSI-001"
    assert state.title == "Test Book"
    assert state.project_revision == 0
    assert layout.project_file.is_file()
    assert layout.canonical_dir.is_dir()
    assert layout.runs_dir.is_dir()
    assert layout.events_dir.is_dir()
    assert layout.requests_dir.is_dir()
    assert layout.locks_dir.is_dir()
    assert layout.transactions_dir.is_dir()


def test_initialize_project_refuses_to_overwrite_existing_project(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Test Book")

    with pytest.raises(FileExistsError):
        initialize_project(root, project_id="VSI-002", title="Replacement")

    state = ProjectLayout(root).read_state()
    assert state.project_id == "VSI-001"
    assert state.project_revision == 0
