from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .canonical_json import sha256_bytes
from .events import iter_events
from .layout import ProjectLayout
from .models import RequestRecord

_SNAPSHOT_REVISION = re.compile(r"^r(?P<revision>\d{8})-")


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def run_doctor(root: Path | str) -> DoctorReport:
    layout = ProjectLayout(root)
    checks: dict[str, bool] = {}
    errors: list[str] = []
    warnings: list[str] = []

    try:
        state = layout.read_state()
        checks["project_state"] = True
    except Exception as exc:
        checks["project_state"] = False
        errors.append(f"project state unreadable: {exc}")
        return DoctorReport(ok=False, checks=checks, errors=errors, warnings=warnings)

    snapshot_root = layout.snapshot_dir(state.current_snapshot)
    checks["current_snapshot_exists"] = snapshot_root.is_dir()
    if not snapshot_root.is_dir():
        errors.append(f"current snapshot missing: {state.current_snapshot}")

    match = _SNAPSHOT_REVISION.match(state.current_snapshot)
    current_snapshot_revision = int(match.group("revision")) if match else None
    checks["snapshot_revision_matches"] = current_snapshot_revision == state.project_revision
    if current_snapshot_revision != state.project_revision:
        errors.append("current snapshot revision does not match project revision")

    if snapshot_root.is_dir():
        indexed = set(state.artifacts)
        actual = {path.relative_to(snapshot_root).as_posix() for path in snapshot_root.rglob("*") if path.is_file()}
        if actual != indexed:
            checks["artifact_index_complete"] = False
            missing = sorted(indexed - actual)
            unindexed = sorted(actual - indexed)
            if missing:
                errors.append(f"indexed artifacts missing from snapshot: {missing}")
            if unindexed:
                errors.append(f"unindexed artifacts in snapshot: {unindexed}")
        else:
            checks["artifact_index_complete"] = True

        hashes_ok = True
        for relative, ref in state.artifacts.items():
            path = snapshot_root / relative
            if not path.is_file():
                hashes_ok = False
                continue
            data = path.read_bytes()
            digest = sha256_bytes(data)
            if digest != ref.sha256:
                hashes_ok = False
                errors.append(f"artifact hash mismatch: {relative}")
            if len(data) != ref.size_bytes:
                hashes_ok = False
                errors.append(f"artifact size mismatch: {relative}")
        checks["artifact_hashes"] = hashes_ok

    unfinished = [path.name for path in layout.transactions_dir.iterdir()] if layout.transactions_dir.exists() else []
    checks["transactions_clean"] = not unfinished
    if unfinished:
        errors.append(f"unfinished transaction entries: {sorted(unfinished)}")

    future_snapshots: list[str] = []
    if layout.revisions_dir.exists():
        for path in layout.revisions_dir.iterdir():
            if not path.is_dir():
                continue
            candidate = _SNAPSHOT_REVISION.match(path.name)
            if candidate and int(candidate.group("revision")) > state.project_revision:
                future_snapshots.append(path.name)
    checks["no_future_orphan_snapshots"] = not future_snapshots
    if future_snapshots:
        errors.append(f"future orphan snapshots: {sorted(future_snapshots)}")

    try:
        list(iter_events(root))
        checks["event_log_parseable"] = True
    except Exception as exc:
        checks["event_log_parseable"] = False
        errors.append(f"event log unreadable: {exc}")

    requests_ok = True
    if layout.requests_dir.exists():
        for path in layout.requests_dir.glob("*.json"):
            try:
                RequestRecord.model_validate_json(path.read_bytes())
            except Exception as exc:
                requests_ok = False
                errors.append(f"request record unreadable {path.name}: {exc}")
    checks["request_records_parseable"] = requests_ok

    return DoctorReport(ok=not errors, checks=checks, errors=errors, warnings=warnings)
