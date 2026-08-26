from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from frontier_vsi.layout import initialize_project
from frontier_vsi.store import ProjectStore


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "frontier_vsi.cli", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_init_and_status_emit_single_json_object(tmp_path: Path) -> None:
    root = tmp_path / "book"
    init = _run(
        "init",
        "--book",
        str(root),
        "--title",
        "Test Book",
        "--project-id",
        "VSI-001",
        "--json",
    )
    assert init.returncode == 0
    init_payload = json.loads(init.stdout)
    assert init_payload["ok"] is True
    assert init_payload["project_id"] == "VSI-001"
    assert init_payload["revision"] == 0
    assert init.stderr == ""
    assert init.stdout.count("\n") == 1

    status = _run("status", "--book", str(root), "--json")
    assert status.returncode == 0
    status_payload = json.loads(status.stdout)
    assert status_payload["ok"] is True
    assert status_payload["revision"] == 0
    assert status_payload["artifact_count"] == 0


def test_init_existing_project_returns_configuration_error_code_3(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")

    result = _run(
        "init",
        "--book",
        str(root),
        "--title",
        "Other",
        "--project-id",
        "VSI-002",
        "--json",
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error_class"] == "FileExistsError"


def test_doctor_detects_tampered_canonical_artifact_and_returns_6(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    store = ProjectStore(root)
    store.commit(
        expected_revision=0,
        mutations={"a.txt": "original"},
        actor="test",
        reason="seed",
    )
    snapshot = store.snapshot()
    (snapshot.root / "a.txt").write_text("tampered", encoding="utf-8")

    result = _run("doctor", "--book", str(root), "--json")

    assert result.returncode == 6
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("hash mismatch" in message for message in payload["errors"])
