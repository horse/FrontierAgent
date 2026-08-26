from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChapterFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(pattern=r"^C\d{2,3}$")
    title: str = Field(min_length=1)
    reader_before: str = Field(min_length=1)
    chapter_question: str = Field(min_length=1)
    cognitive_move: str = Field(min_length=1)
    central_claim: str = Field(min_length=1)
    chapter_function: str = Field(min_length=1)
    required_claim_ids: list[str] = Field(default_factory=list)
    anchor_cases: list[str] = Field(default_factory=list)
    counterarguments: list[str] = Field(default_factory=list)
    reader_after: str = Field(min_length=1)
    handoff_to_next: str = Field(min_length=1)
    word_budget: int = Field(gt=0)


class BookArchitecture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    master_argument: str = Field(min_length=1)
    excluded_but_important: list[str] = Field(default_factory=list)
    chapters: list[ChapterFunction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_chapter_jobs(self) -> BookArchitecture:
        chapter_ids = [chapter.chapter_id for chapter in self.chapters]
        if len(set(chapter_ids)) != len(chapter_ids):
            raise ValueError("chapter ids must be unique")

        jobs = [" ".join(chapter.chapter_function.lower().split()) for chapter in self.chapters]
        if len(set(jobs)) != len(jobs):
            raise ValueError("chapter functions must be unique")
        return self


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: str = "MAJOR"
    code: str = "EDITORIAL"
    message: str = Field(min_length=1)
    location: str | None = None
    repair_route: str | None = None


class ReviewPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass_gate: bool
    score: float | None = Field(default=None, ge=0, le=100)
    issues: list[ReviewIssue | str] = Field(default_factory=list)


class AuthorialReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass_gate: bool
    dimensions: dict[str, bool]
    issues: list[ReviewIssue | str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_five_dimensions(self) -> AuthorialReview:
        required = {"Position", "Interpretation", "Architecture", "Judgement", "Voice"}
        if set(self.dimensions) != required:
            raise ValueError("authorial review requires five dimensions")
        if self.pass_gate and not all(self.dimensions.values()):
            raise ValueError("authorial pass requires all dimensions")
        return self


class AuthorDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prose: str = Field(min_length=1)
    provenance: list[dict[str, object]] = Field(default_factory=list)
    new_claim_candidates: list[dict[str, object] | str] = Field(default_factory=list)


class ClaimItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    claim_type: str = "factual"
    risk: str = "medium"


class ClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimItem] = Field(default_factory=list)
    orphan_claims: list[str] = Field(default_factory=list)
