from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .canonical_json import canonical_json_bytes, sha256_bytes
from .errors import IdempotencyConflictError, RequestNotFoundError
from .layout import ProjectLayout
from .locking import ProjectLock
from .models import RequestRecord, RequestStatus


def _request_path(root: Path | str, request_id: str) -> Path:
    layout = ProjectLayout(root)
    layout.requests_dir.mkdir(parents=True, exist_ok=True)
    name = sha256_bytes(request_id.encode("utf-8"))
    return layout.requests_dir / f"{name}.json"


def _read(path: Path) -> RequestRecord:
    return RequestRecord.model_validate_json(path.read_bytes())


def _write_atomic(path: Path, record: RequestRecord) -> None:
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    payload = canonical_json_bytes(record.model_dump(mode="json")) + b"\n"
    with temp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def claim_request(root: Path | str, request_id: str, command_fingerprint: str) -> RequestRecord:
    path = _request_path(root, request_id)
    with ProjectLock(root):
        if path.exists():
            record = _read(path)
            if record.request_id != request_id or record.command_fingerprint != command_fingerprint:
                raise IdempotencyConflictError(
                    f"request id {request_id!r} already belongs to a different command"
                )
            return record
        record = RequestRecord(
            request_id=request_id,
            command_fingerprint=command_fingerprint,
            status=RequestStatus.CLAIMED,
        )
        _write_atomic(path, record)
        return record


def complete_request(
    root: Path | str,
    request_id: str,
    command_fingerprint: str,
    *,
    result: dict[str, object],
) -> RequestRecord:
    path = _request_path(root, request_id)
    with ProjectLock(root):
        if not path.exists():
            raise RequestNotFoundError(request_id)
        record = _read(path)
        if record.command_fingerprint != command_fingerprint:
            raise IdempotencyConflictError(
                f"request id {request_id!r} already belongs to a different command"
            )
        completed = record.model_copy(
            update={
                "status": RequestStatus.COMPLETED,
                "result": result,
                "error": None,
                "completed_at": datetime.now(timezone.utc),
            }
        )
        _write_atomic(path, completed)
        return completed


def lookup_request(root: Path | str, request_id: str) -> RequestRecord | None:
    path = _request_path(root, request_id)
    return _read(path) if path.exists() else None
