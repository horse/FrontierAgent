from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import yaml

from .store import ProjectStore


@dataclass(frozen=True)
class ChapterMaterialsResult:
    chapter_id: str
    project_revision: int
    claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]


def _jsonl(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def build_chapter_materials(store: ProjectStore, *, chapter_id: str) -> ChapterMaterialsResult:
    snapshot = store.snapshot()
    required_paths = (
        "architecture/CHAPTER_FUNCTION_MAP.yaml",
        "research/CLAIM_LEDGER.jsonl",
        "research/EVIDENCE_LEDGER.jsonl",
    )
    missing = [path for path in required_paths if path not in snapshot.artifacts]
    if missing:
        raise ValueError(f"chapter materials missing prerequisites: {missing}")

    function_map = yaml.safe_load(
        snapshot.read_text("architecture/CHAPTER_FUNCTION_MAP.yaml")
    ) or {}
    chapters = function_map.get("chapters", [])
    chapter = next(
        (item for item in chapters if item.get("chapter_id") == chapter_id),
        None,
    )
    if chapter is None:
        raise ValueError(f"chapter not found in function map: {chapter_id}")

    claims = {
        str(row.get("claim_id")): row
        for row in _jsonl(snapshot.read_text("research/CLAIM_LEDGER.jsonl"))
    }
    evidence = {
        str(row.get("evidence_id")): row
        for row in _jsonl(snapshot.read_text("research/EVIDENCE_LEDGER.jsonl"))
    }
    required_claim_ids = [str(value) for value in chapter.get("required_claim_ids", [])]

    selected_claims: list[dict[str, Any]] = []
    selected_evidence: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    for claim_id in required_claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            raise ValueError(f"missing claim required by chapter {chapter_id}: {claim_id}")
        selected_claims.append(claim)
        for evidence_id in claim.get("evidence_ids", []):
            evidence_id = str(evidence_id)
            item = evidence.get(evidence_id)
            if item is None:
                raise ValueError(f"missing evidence required by claim {claim_id}: {evidence_id}")
            if evidence_id not in seen_evidence:
                selected_evidence.append(item)
                seen_evidence.add(evidence_id)

    brief_lines = [
        f"# Chapter Brief — {chapter_id}: {chapter.get('title', '')}",
        "",
        f"- Reader before: {chapter.get('reader_before', '')}",
        f"- Chapter question: {chapter.get('chapter_question', '')}",
        f"- Cognitive move: {chapter.get('cognitive_move', '')}",
        f"- Central claim: {chapter.get('central_claim', '')}",
        f"- Chapter function: {chapter.get('chapter_function', '')}",
        f"- Reader after: {chapter.get('reader_after', '')}",
        f"- Handoff to next: {chapter.get('handoff_to_next', '')}",
        f"- Word budget: {chapter.get('word_budget', '')}",
        "",
        "## Anchor cases",
        *[f"- {item}" for item in chapter.get("anchor_cases", [])],
        "",
        "## Counterarguments / complications",
        *[f"- {item}" for item in chapter.get("counterarguments", [])],
        "",
    ]

    packet_lines = [f"# Evidence Packet — {chapter_id}", "", "## Required claims"]
    for claim in selected_claims:
        packet_lines += [
            f"### {claim.get('claim_id')}",
            str(claim.get("text", "")),
            f"Strength: {claim.get('strength', '')}",
            "Evidence IDs: "
            + ", ".join(str(value) for value in claim.get("evidence_ids", [])),
            "",
        ]
    packet_lines += ["## Evidence"]
    for item in selected_evidence:
        packet_lines += [
            f"### {item.get('evidence_id')}",
            f"Source: {item.get('source_id', '')}",
            f"Locator: {item.get('locator', '')}",
            f"Strength: {item.get('strength', '')}",
            str(item.get("summary", "")),
            "",
        ]

    chapter_root = f"chapters/{chapter_id}"
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={
            f"{chapter_root}/BRIEF.md": "\n".join(brief_lines).rstrip() + "\n",
            f"{chapter_root}/EVIDENCE_PACKET.md": (
                "\n".join(packet_lines).rstrip() + "\n"
            ),
        },
        actor="editor",
        reason=f"build deterministic chapter materials for {chapter_id}",
    )
    return ChapterMaterialsResult(
        chapter_id=chapter_id,
        project_revision=state.project_revision,
        claim_ids=tuple(required_claim_ids),
        evidence_ids=tuple(str(item["evidence_id"]) for item in selected_evidence),
    )
