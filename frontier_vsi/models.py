from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class ProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    project_revision: int = Field(default=0, ge=0)
    current_snapshot: str = "r00000000-initial"
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    last_commit_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    commit_id: str = Field(min_length=1)
    event_type: str = "canonical_commit"
    project_id: str = Field(min_length=1)
    old_revision: int = Field(ge=0)
    new_revision: int = Field(ge=1)
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GateStatus(StrEnum):
    PENDING = "PENDING"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    STALE = "STALE"


class GateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    gate: str = Field(min_length=1)
    status: GateStatus
    dependency_paths: list[str] = Field(default_factory=list)
    input_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str | None = None
    approved_at: datetime | None = None


class RequestStatus(StrEnum):
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RequestRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    command_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: RequestStatus = RequestStatus.CLAIMED
    result: dict[str, object] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    request_id: str | None = None
    project_id: str = Field(min_length=1)
    project_revision_at_start: int = Field(ge=0)
    stage: str = Field(min_length=1)
    command: str = Field(min_length=1)
    upstream_frontieragent_sha: str | None = None
    frontiervsi_version: str = "0.1.0"
    models_by_role: dict[str, str] = Field(default_factory=dict)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    method_resource_hashes: dict[str, str] = Field(default_factory=dict)
    context_pack_hashes: dict[str, str] = Field(default_factory=dict)
    input_artifact_refs: list[str] = Field(default_factory=list)
    status: str = "RUNNING"
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    candidate_output_refs: list[str] = Field(default_factory=list)
    committed_artifact_refs: list[str] = Field(default_factory=list)
    project_revision_after_commit: int | None = None
    error_class: str | None = None
    error_message: str | None = None


class TransactionJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    commit_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    old_revision: int = Field(ge=0)
    new_revision: int = Field(ge=1)
    snapshot_id: str = Field(min_length=1)
    event: ProjectEvent
