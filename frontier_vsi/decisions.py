from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

from .issues import IssueRecord, load_issue
from .store import ProjectStore

_ALLOWED = {
    "REPAIR": "TRIAGED",
    "RESEARCH_GAP": "TRIAGED",
    "RESOLVED": "RESOLVED",
    "REJECTED": "REJECTED",
}


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(pattern=r"^DEC-\d{6}$")
    issue_id: str = Field(pattern=r"^ISS-\d{6}$")
    disposition: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def _next_decision_number(store: ProjectStore) -> int:
    values: list[int] = []
    for path in store.snapshot().artifacts:
        pure = PurePosixPath(path)
        if pure.parent.as_posix() != "decisions" or not pure.stem.startswith("DEC-"):
            continue
        try:
            values.append(int(pure.stem.split("-")[-1]))
        except ValueError:
            continue
    return max(values, default=0) + 1


def decide_issue(
    store: ProjectStore,
    *,
    issue_id: str,
    disposition: str,
    rationale: str,
    decided_by: str,
) -> DecisionRecord:
    if not decided_by.startswith("human:"):
        raise ValueError("editorial decisions must be attributed to an explicit human identity")
    normalized = disposition.strip().upper()
    if normalized not in _ALLOWED:
        raise ValueError(f"unsupported issue disposition: {disposition}")
    if not rationale.strip():
        raise ValueError("decision rationale must not be empty")

    issue = load_issue(store, issue_id)
    decision_id = f"DEC-{_next_decision_number(store):06d}"
    record = DecisionRecord(
        decision_id=decision_id,
        issue_id=issue_id,
        disposition=normalized,
        rationale=rationale.strip(),
        decided_by=decided_by,
    )
    updated_issue = IssueRecord(
        **issue.model_dump(exclude={"status", "repair_route"}),
        status=_ALLOWED[normalized],
        repair_route=(
            normalized
            if normalized in {"REPAIR", "RESEARCH_GAP"}
            else issue.repair_route
        ),
    )
    snapshot = store.snapshot()
    store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={
            f"decisions/{decision_id}.json": record.model_dump_json(indent=2) + "\n",
            f"issues/{issue_id}.json": updated_issue.model_dump_json(indent=2) + "\n",
        },
        actor=decided_by,
        reason=f"record editorial decision {decision_id} for {issue_id}",
    )
    return record


def list_decisions(store: ProjectStore) -> tuple[DecisionRecord, ...]:
    snapshot = store.snapshot()
    records: list[DecisionRecord] = []
    for path in sorted(snapshot.artifacts):
        if path.startswith("decisions/DEC-") and path.endswith(".json"):
            records.append(DecisionRecord.model_validate_json(snapshot.read_text(path)))
    return tuple(records)
