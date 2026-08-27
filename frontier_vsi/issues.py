from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .canonical_json import canonical_json_bytes, sha256_bytes
from .editorial_models import ReviewIssue
from .store import ProjectStore


class IssueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue_id: str = Field(pattern=r"^ISS-\d{6}$")
    scope: str = Field(min_length=1)
    source_role: str = Field(min_length=1)
    cycle: int = Field(ge=1)
    severity: str = Field(min_length=1)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    repair_route: str = Field(min_length=1)
    status: str = "OPEN"
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IssueCommitResult:
    issue_ids: tuple[str, ...]
    project_revision: int


def issue_fingerprint(*, scope: str, source_role: str, code: str, message: str) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "scope": scope,
                "source_role": source_role,
                "code": code,
                "message": " ".join(message.split()),
            }
        )
    )


def _next_issue_number(store: ProjectStore) -> int:
    values: list[int] = []
    for path in store.snapshot().artifacts:
        pure = PurePosixPath(path)
        if pure.parent.as_posix() != "issues" or not pure.stem.startswith("ISS-"):
            continue
        try:
            values.append(int(pure.stem.split("-")[-1]))
        except ValueError:
            continue
    return max(values, default=0) + 1


def normalize_review_issue(issue: ReviewIssue | str, *, source_role: str) -> ReviewIssue:
    if isinstance(issue, ReviewIssue):
        route = issue.repair_route
        if route:
            return issue
        route = "RESEARCH_GAP" if source_role == "fact_reviewer" else "REPAIR"
        return issue.model_copy(update={"repair_route": route})
    return ReviewIssue(
        severity="MAJOR",
        code="REVIEW_FAILURE",
        message=issue,
        repair_route="RESEARCH_GAP" if source_role == "fact_reviewer" else "REPAIR",
    )


def commit_issues(
    store: ProjectStore,
    *,
    scope: str,
    cycle: int,
    issues: Iterable[tuple[str, ReviewIssue | str]],
) -> IssueCommitResult:
    normalized = [
        (role, normalize_review_issue(issue, source_role=role))
        for role, issue in issues
    ]
    if not normalized:
        return IssueCommitResult(
            issue_ids=(), project_revision=store.snapshot().state.project_revision
        )
    start = _next_issue_number(store)
    mutations: dict[str, str] = {}
    ids: list[str] = []
    for offset, (role, issue) in enumerate(normalized):
        issue_id = f"ISS-{start + offset:06d}"
        ids.append(issue_id)
        record = IssueRecord(
            issue_id=issue_id,
            scope=scope,
            source_role=role,
            cycle=cycle,
            severity=issue.severity.upper(),
            code=issue.code,
            message=issue.message,
            repair_route=(issue.repair_route or "REPAIR").upper(),
            fingerprint=issue_fingerprint(
                scope=scope,
                source_role=role,
                code=issue.code,
                message=issue.message,
            ),
        )
        mutations[f"issues/{issue_id}.json"] = record.model_dump_json(indent=2) + "\n"
    snapshot = store.snapshot()
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations=mutations,
        actor="editor",
        reason=f"record normalized review issues for {scope} cycle {cycle}",
    )
    return IssueCommitResult(issue_ids=tuple(ids), project_revision=state.project_revision)


def load_issue(store: ProjectStore, issue_id: str) -> IssueRecord:
    return IssueRecord.model_validate_json(
        store.snapshot().read_text(f"issues/{issue_id}.json")
    )


def iter_issues(store: ProjectStore, *, scope: str | None = None) -> tuple[IssueRecord, ...]:
    records: list[IssueRecord] = []
    snapshot = store.snapshot()
    for path in sorted(snapshot.artifacts):
        if not path.startswith("issues/ISS-") or not path.endswith(".json"):
            continue
        record = IssueRecord.model_validate_json(snapshot.read_text(path))
        if scope is None or record.scope == scope:
            records.append(record)
    return tuple(records)


def resolve_open_issues(store: ProjectStore, *, scope: str) -> int:
    snapshot = store.snapshot()
    mutations: dict[str, str] = {}
    for record in iter_issues(store, scope=scope):
        if record.status not in {"RESOLVED", "VERIFIED", "REJECTED"}:
            updated = record.model_copy(update={"status": "RESOLVED"})
            mutations[f"issues/{record.issue_id}.json"] = updated.model_dump_json(indent=2) + "\n"
    if not mutations:
        return snapshot.state.project_revision
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations=mutations,
        actor="editor",
        reason=f"resolve superseded editorial issues for {scope}",
    )
    return state.project_revision
