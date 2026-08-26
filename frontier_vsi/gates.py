from __future__ import annotations

from collections.abc import Iterable, Mapping

from .canonical_json import canonical_json_bytes, sha256_bytes
from .models import ArtifactRef, GateRecord, GateStatus


def dependency_fingerprint(refs: Iterable[ArtifactRef]) -> str:
    normalized = [
        {"path": ref.path, "sha256": ref.sha256, "size_bytes": ref.size_bytes}
        for ref in sorted(refs, key=lambda item: item.path)
    ]
    return sha256_bytes(canonical_json_bytes(normalized))


def evaluate_gate_freshness(
    record: GateRecord, current_refs: Mapping[str, ArtifactRef]
) -> GateStatus:
    if record.status != GateStatus.PASS:
        return record.status
    try:
        refs = [current_refs[path] for path in record.dependency_paths]
    except KeyError:
        return GateStatus.STALE
    return (
        GateStatus.PASS
        if dependency_fingerprint(refs) == record.input_fingerprint
        else GateStatus.STALE
    )
