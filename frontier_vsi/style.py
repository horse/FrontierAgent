from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StyleProfile(BaseModel):
    """Book-specific prose choices; editorial process remains fixed."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    language: str = Field(min_length=1)
    prose_register: str = Field(min_length=1, alias="register")
    sentence_length: str = "mixed"
    paragraph_length: str = "mixed"
    narrative_distance: str = "controlled"
    terminology_policy: str = "explain-before-name"
    example_policy: str = "argument-carrying"
    quotation_policy: str = "selective"
    rhythm_notes: list[str] = Field(default_factory=list)
    prohibited_patterns: list[str] = Field(default_factory=list)
    sample_paths: list[str] = Field(default_factory=list)
    voice_anchor_paths: list[str] = Field(default_factory=list)
