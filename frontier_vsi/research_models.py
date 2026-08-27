from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict, Field

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class ResearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    search_terms: list[str] = Field(default_factory=list)
    required_source_types: list[str] = Field(default_factory=list)
    counterexample_target: str | None = None


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: str = Field(min_length=1)
    tasks: list[ResearchTask] = Field(min_length=1)
    stop_conditions: list[str] = Field(min_length=1)


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str = Field(min_length=1)
    confidence: str = "medium"
    source_keys: list[str] = Field(default_factory=list)
    notes: str = ""


class SourceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str | None = None
    local_path: str | None = None
    author: str | None = None
    year: int | None = None
    source_type: str = "secondary"
    read: bool = False


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    findings: list[ResearchFinding] = Field(default_factory=list)
    counterevidence: list[str] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    recommended_claims: list[str] = Field(default_factory=list)
    do_not_claim: list[str] = Field(default_factory=list)
    followup_questions: list[str] = Field(default_factory=list)
    source_candidates: list[SourceCandidate] = Field(default_factory=list)


class CuratedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str | None = None
    local_path: str | None = None
    author: str | None = None
    year: int | None = None
    source_type: str = "secondary"
    read: bool = False


class CuratedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    stance: str = "supports"
    strength: str = "medium"


class CuratedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    claim_type: str = "factual"
    strength: str = "medium"
    evidence_keys: list[str] = Field(min_length=1)
    confidence: str = "medium"
    disputed: bool = False


class CuratedResearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[CuratedSource] = Field(default_factory=list)
    evidence: list[CuratedEvidence] = Field(default_factory=list)
    claims: list[CuratedClaim] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    research_gaps: list[str] = Field(default_factory=list)
    synthesis: str = ""


def parse_structured_output[T: BaseModel](text: str, model_type: type[T]) -> T:
    stripped = text.strip()
    match = _FENCE.fullmatch(stripped)
    if match:
        stripped = match.group(1).strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        raise ValueError("structured agent output must be one JSON object")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON structured output: {exc}") from exc
    return model_type.model_validate(payload)
