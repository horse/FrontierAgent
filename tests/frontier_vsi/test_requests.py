from pathlib import Path

import pytest

from frontier_vsi.errors import IdempotencyConflictError
from frontier_vsi.layout import initialize_project
from frontier_vsi.requests import claim_request, complete_request, lookup_request


def test_same_request_id_and_fingerprint_returns_prior_completed_result(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")

    first = claim_request(root, "telegram-42", "a" * 64)
    assert first.status == "CLAIMED"
    complete_request(root, "telegram-42", "a" * 64, result={"revision": 1})

    retry = claim_request(root, "telegram-42", "a" * 64)
    assert retry.status == "COMPLETED"
    assert retry.result == {"revision": 1}
    assert lookup_request(root, "telegram-42") == retry


def test_same_request_id_with_different_fingerprint_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-001", title="Book")
    claim_request(root, "telegram-42", "a" * 64)

    with pytest.raises(IdempotencyConflictError):
        claim_request(root, "telegram-42", "b" * 64)
