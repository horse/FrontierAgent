from pathlib import Path

import pytest

from frontier_vsi.events import iter_events
from frontier_vsi.layout import initialize_project
from frontier_vsi.store import ProjectStore


def test_successful_commit_appends_one_audit_event(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")

    state = ProjectStore(root).commit(
        expected_revision=0,
        mutations={"a.txt": "one"},
        actor="editor",
        reason="seed evidence",
    )

    events = list(iter_events(root))
    assert len(events) == 1
    event = events[0]
    assert event.commit_id == state.last_commit_id
    assert event.old_revision == 0
    assert event.new_revision == 1
    assert event.actor == "editor"
    assert event.reason == "seed evidence"


def test_failed_pointer_commit_does_not_append_success_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    store = ProjectStore(root)

    def fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("pointer failed")

    monkeypatch.setattr(store, "_replace_project_state", fail_replace)
    with pytest.raises(OSError, match="pointer failed"):
        store.commit(
            expected_revision=0,
            mutations={"a.txt": "one"},
            actor="editor",
            reason="seed evidence",
        )

    assert list(iter_events(root)) == []


def test_project_reopens_with_gate_freshness_and_idempotency_intact(tmp_path: Path) -> None:
    from frontier_vsi.gates import dependency_fingerprint, evaluate_gate_freshness
    from frontier_vsi.models import GateRecord, GateStatus
    from frontier_vsi.requests import claim_request, complete_request

    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    first_store = ProjectStore(root)
    state1 = first_store.commit(
        expected_revision=0,
        mutations={"constitution/AUTHOR_CONSTITUTION.md": "version A\n"},
        actor="editor",
        reason="lock constitution",
    )
    dep_a = first_store.snapshot().artifacts["constitution/AUTHOR_CONSTITUTION.md"]
    gate = GateRecord(
        gate="CONTROL_CHAPTER_PASS",
        status=GateStatus.PASS,
        dependency_paths=[dep_a.path],
        input_fingerprint=dependency_fingerprint([dep_a]),
    )
    state2 = first_store.commit(
        expected_revision=state1.project_revision,
        mutations={"gates/CONTROL_CHAPTER_PASS.json": gate.model_dump_json()},
        actor="editor",
        reason="approve control chapter",
    )

    reopened = ProjectStore(root)
    snapshot2 = reopened.snapshot()
    restored_gate = GateRecord.model_validate_json(
        snapshot2.read_text("gates/CONTROL_CHAPTER_PASS.json")
    )
    current_dep = snapshot2.artifacts["constitution/AUTHOR_CONSTITUTION.md"]
    assert evaluate_gate_freshness(restored_gate, {current_dep.path: current_dep}) == GateStatus.PASS

    reopened.commit(
        expected_revision=state2.project_revision,
        mutations={"constitution/AUTHOR_CONSTITUTION.md": "version B\n"},
        actor="human",
        reason="material constitution change",
    )
    newest = ProjectStore(root).snapshot()
    changed_dep = newest.artifacts["constitution/AUTHOR_CONSTITUTION.md"]
    assert evaluate_gate_freshness(restored_gate, {changed_dep.path: changed_dep}) == GateStatus.STALE

    claimed = claim_request(root, "telegram-99", "c" * 64)
    assert claimed.status == "CLAIMED"
    complete_request(root, "telegram-99", "c" * 64, result={"revision": newest.state.project_revision})
    retry = claim_request(root, "telegram-99", "c" * 64)
    assert retry.status == "COMPLETED"
    assert retry.result == {"revision": newest.state.project_revision}


def test_restart_recovers_audit_event_after_pointer_committed_but_event_append_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import frontier_vsi.store as store_module

    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    original_append = store_module.append_event

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected event failure")

    monkeypatch.setattr(store_module, "append_event", fail_append)
    state = ProjectStore(root).commit(
        expected_revision=0,
        mutations={"a.txt": "one"},
        actor="editor",
        reason="commit survives audit failure",
    )

    assert state.project_revision == 1
    assert list(iter_events(root)) == []
    assert any((root / ".frontiervsi" / "transactions").iterdir())

    monkeypatch.setattr(store_module, "append_event", original_append)
    reopened = ProjectStore(root)
    assert reopened.snapshot().state.project_revision == 1
    events = list(iter_events(root))
    assert len(events) == 1
    assert events[0].commit_id == state.last_commit_id
    assert list((root / ".frontiervsi" / "transactions").iterdir()) == []
