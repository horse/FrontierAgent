from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from .canonical_json import canonical_json_bytes, sha256_bytes
from .errors import InvalidArtifactPathError, RevisionConflictError
from .events import append_event, iter_events
from .layout import ProjectLayout
from .locking import ProjectLock
from .models import ArtifactRef, ProjectEvent, ProjectState, TransactionJournal

type MutationValue = str | bytes | None


class ProjectSnapshot:
    def __init__(self, layout: ProjectLayout, state: ProjectState) -> None:
        self.layout = layout
        self.state = state
        self.artifacts = state.artifacts
        self.root = layout.snapshot_dir(state.current_snapshot)

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        return (self.root / _validated_relative_path(path)).read_text(encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        return (self.root / _validated_relative_path(path)).read_bytes()


class ProjectStore:
    def __init__(self, root: Path | str) -> None:
        self.layout = ProjectLayout(root)
        if self.layout.project_file.exists():
            self._recover_pending_transactions()

    def snapshot(self) -> ProjectSnapshot:
        state = self.layout.read_state()
        return ProjectSnapshot(self.layout, state)

    def commit(
        self,
        *,
        expected_revision: int,
        mutations: Mapping[str, MutationValue],
        actor: str,
        reason: str,
    ) -> ProjectState:
        with ProjectLock(self.layout.root):
            current = self.layout.read_state()
            if current.project_revision != expected_revision:
                raise RevisionConflictError(
                    f"expected revision {expected_revision}, current is {current.project_revision}"
                )

            commit_id = uuid.uuid4().hex
            new_revision = current.project_revision + 1
            snapshot_id = f"r{new_revision:08d}-{commit_id[:12]}"
            txn_dir = self.layout.transactions_dir / commit_id
            staged_snapshot = txn_dir / "revision"
            txn_dir.mkdir(parents=True, exist_ok=False)
            final_snapshot = self.layout.snapshot_dir(snapshot_id)
            event = ProjectEvent(
                event_id=f"EVT-{commit_id}",
                commit_id=commit_id,
                project_id=current.project_id,
                old_revision=current.project_revision,
                new_revision=new_revision,
                actor=actor,
                reason=reason,
            )
            journal = TransactionJournal(
                commit_id=commit_id,
                project_id=current.project_id,
                old_revision=current.project_revision,
                new_revision=new_revision,
                snapshot_id=snapshot_id,
                event=event,
            )
            _write_journal(txn_dir / "journal.json", journal)

            pointer_replaced = False
            try:
                shutil.copytree(
                    self.layout.snapshot_dir(current.current_snapshot),
                    staged_snapshot,
                    dirs_exist_ok=False,
                )
                for relative, value in mutations.items():
                    rel_path = _validated_relative_path(relative)
                    target = staged_snapshot / rel_path
                    if value is None:
                        if target.exists():
                            target.unlink()
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = value.encode("utf-8") if isinstance(value, str) else value
                    target.write_bytes(data)

                artifact_index = _index_artifacts(staged_snapshot)
                os.replace(staged_snapshot, final_snapshot)

                next_state = current.model_copy(
                    update={
                        "project_revision": new_revision,
                        "current_snapshot": snapshot_id,
                        "artifacts": artifact_index,
                        "last_commit_id": commit_id,
                    }
                )
                self._replace_project_state(next_state, commit_id=commit_id)
                pointer_replaced = True
                try:
                    append_event(self.layout.root, event)
                except Exception:
                    # The canonical pointer is already committed. Keep the durable
                    # journal so the next ProjectStore can reconstruct the audit event.
                    return next_state
                shutil.rmtree(txn_dir, ignore_errors=True)
                return next_state
            except Exception:
                if not pointer_replaced:
                    if final_snapshot.exists():
                        shutil.rmtree(final_snapshot, ignore_errors=True)
                    shutil.rmtree(txn_dir, ignore_errors=True)
                raise

    def _replace_project_state(self, state: ProjectState, *, commit_id: str) -> None:
        temp_path = self.layout.meta_dir / f"project.json.{commit_id}.tmp"
        payload = canonical_json_bytes(state.model_dump(mode="json")) + b"\n"
        with temp_path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.layout.project_file)

    def _recover_pending_transactions(self) -> None:
        self.layout.transactions_dir.mkdir(parents=True, exist_ok=True)
        pending = [path for path in self.layout.transactions_dir.iterdir() if path.is_dir()]
        if not pending:
            return
        with ProjectLock(self.layout.root):
            state = self.layout.read_state()
            event_commit_ids = {event.commit_id for event in iter_events(self.layout.root)}
            for txn_dir in sorted(pending):
                journal_path = txn_dir / "journal.json"
                if not journal_path.is_file():
                    continue
                journal = TransactionJournal.model_validate_json(journal_path.read_bytes())
                final_snapshot = self.layout.snapshot_dir(journal.snapshot_id)
                committed = (
                    state.last_commit_id == journal.commit_id
                    and state.project_revision == journal.new_revision
                    and state.current_snapshot == journal.snapshot_id
                )
                not_committed = state.project_revision == journal.old_revision
                if committed:
                    if journal.commit_id not in event_commit_ids:
                        append_event(self.layout.root, journal.event)
                        event_commit_ids.add(journal.commit_id)
                    shutil.rmtree(txn_dir, ignore_errors=True)
                elif not_committed:
                    if final_snapshot.exists():
                        shutil.rmtree(final_snapshot, ignore_errors=True)
                    shutil.rmtree(txn_dir, ignore_errors=True)


def _write_journal(path: Path, journal: TransactionJournal) -> None:
    payload = canonical_json_bytes(journal.model_dump(mode="json")) + b"\n"
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _validated_relative_path(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise InvalidArtifactPathError(f"invalid canonical artifact path: {value!r}")
    return Path(*pure.parts)


def _index_artifacts(root: Path) -> dict[str, ArtifactRef]:
    result: dict[str, ArtifactRef] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        result[relative] = ArtifactRef(
            path=relative,
            sha256=sha256_bytes(data),
            size_bytes=len(data),
        )
    return result
