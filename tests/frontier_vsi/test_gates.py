from frontier_vsi.gates import dependency_fingerprint, evaluate_gate_freshness
from frontier_vsi.models import ArtifactRef, GateRecord, GateStatus


def _ref(path: str, digest_char: str) -> ArtifactRef:
    return ArtifactRef(path=path, sha256=digest_char * 64, size_bytes=10)


def test_passed_gate_becomes_stale_when_dependency_hash_changes() -> None:
    old = _ref("constitution/AUTHOR_CONSTITUTION.md", "a")
    record = GateRecord(
        gate="CONTROL_CHAPTER_PASS",
        status=GateStatus.PASS,
        dependency_paths=[old.path],
        input_fingerprint=dependency_fingerprint([old]),
    )
    current = _ref(old.path, "b")

    assert evaluate_gate_freshness(record, {current.path: current}) == GateStatus.STALE


def test_dependency_fingerprint_is_order_independent() -> None:
    first = _ref("a.md", "a")
    second = _ref("b.md", "b")

    assert dependency_fingerprint([first, second]) == dependency_fingerprint([second, first])


def test_gate_remains_pass_when_dependencies_are_unchanged() -> None:
    dependency = _ref("a.md", "a")
    record = GateRecord(
        gate="RESEARCH_READY",
        status=GateStatus.PASS,
        dependency_paths=[dependency.path],
        input_fingerprint=dependency_fingerprint([dependency]),
    )

    assert evaluate_gate_freshness(record, {dependency.path: dependency}) == GateStatus.PASS
