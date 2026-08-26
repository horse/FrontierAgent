from pathlib import Path

import pytest

from frontier_vsi.errors import RevisionConflictError
from frontier_vsi.layout import initialize_project
from frontier_vsi.store import ProjectStore


def test_commit_advances_revision_once_and_persists_artifact(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    store = ProjectStore(root)

    state = store.commit(
        expected_revision=0,
        mutations={"constitution/BOOK_CHARTER.md": "hello\n"},
        actor="test",
        reason="first artifact",
    )

    assert state.project_revision == 1
    snapshot = ProjectStore(root).snapshot()
    assert snapshot.state.project_revision == 1
    assert snapshot.read_text("constitution/BOOK_CHARTER.md") == "hello\n"
    assert snapshot.artifacts["constitution/BOOK_CHARTER.md"].sha256


def test_commit_rejects_stale_expected_revision_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    store = ProjectStore(root)
    store.commit(
        expected_revision=0,
        mutations={"a.txt": "one"},
        actor="test",
        reason="first",
    )

    with pytest.raises(RevisionConflictError):
        store.commit(
            expected_revision=0,
            mutations={"b.txt": "two"},
            actor="test",
            reason="stale",
        )

    snapshot = ProjectStore(root).snapshot()
    assert snapshot.state.project_revision == 1
    assert snapshot.read_text("a.txt") == "one"
    assert "b.txt" not in snapshot.artifacts


def test_pointer_replace_failure_keeps_previous_revision_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    store = ProjectStore(root)

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected pointer failure")

    monkeypatch.setattr(store, "_replace_project_state", fail_replace)

    with pytest.raises(OSError, match="injected pointer failure"):
        store.commit(
            expected_revision=0,
            mutations={"new.txt": "not visible"},
            actor="test",
            reason="failure injection",
        )

    snapshot = ProjectStore(root).snapshot()
    assert snapshot.state.project_revision == 0
    assert "new.txt" not in snapshot.artifacts
