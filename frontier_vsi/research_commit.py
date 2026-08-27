from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from .models import ProjectState
from .research_models import CuratedResearch
from .store import ProjectStore

_STRENGTH = {"low": 1, "medium": 2, "high": 3}


def _existing_lines(store: ProjectStore, path: str) -> list[dict[str, Any]]:
    snap = store.snapshot()
    if path not in snap.artifacts:
        return []
    return [json.loads(line) for line in snap.read_text(path).splitlines() if line.strip()]


def _next(prefix: str, existing: list[dict[str, Any]], key: str) -> int:
    values: list[int] = []
    for row in existing:
        value = str(row.get(key, ""))
        if value.startswith(prefix + "-"):
            with suppress(ValueError):
                values.append(int(value.split("-")[-1]))
    return max(values, default=0) + 1


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def commit_curated_research(
    store: ProjectStore,
    curated: CuratedResearch,
    *,
    expected_revision: int,
    actor: str,
) -> ProjectState:
    existing_sources = _existing_lines(store, "research/SOURCE_REGISTRY.jsonl")
    existing_evidence = _existing_lines(store, "research/EVIDENCE_LEDGER.jsonl")
    existing_claims = _existing_lines(store, "research/CLAIM_LEDGER.jsonl")
    source_n = _next("SRC", existing_sources, "source_id")
    evidence_n = _next("EVD", existing_evidence, "evidence_id")
    claim_n = _next("CLM", existing_claims, "claim_id")

    source_key_to_id: dict[str, str] = {}
    source_read: dict[str, bool] = {}
    new_sources: list[dict[str, Any]] = []
    for index, source in enumerate(curated.sources):
        if source.key in source_key_to_id:
            raise ValueError(f"duplicate source key: {source.key}")
        source_id = f"SRC-{source_n + index:06d}"
        source_key_to_id[source.key] = source_id
        source_read[source.key] = source.read
        new_sources.append(
            {
                "source_id": source_id,
                "title": source.title,
                "url": source.url,
                "local_path": source.local_path,
                "author": source.author,
                "year": source.year,
                "source_type": source.source_type,
                "status": "read" if source.read else "discovered",
            }
        )

    evidence_key_to_id: dict[str, str] = {}
    evidence_strength: dict[str, int] = {}
    new_evidence: list[dict[str, Any]] = []
    for index, evidence in enumerate(curated.evidence):
        if evidence.key in evidence_key_to_id:
            raise ValueError(f"duplicate evidence key: {evidence.key}")
        if evidence.source_key not in source_key_to_id:
            raise ValueError(f"unknown source key for evidence: {evidence.source_key}")
        if not source_read[evidence.source_key]:
            raise ValueError(f"evidence requires a read source: {evidence.source_key}")
        if evidence.strength not in _STRENGTH:
            raise ValueError(f"unknown evidence strength: {evidence.strength}")
        evidence_id = f"EVD-{evidence_n + index:06d}"
        evidence_key_to_id[evidence.key] = evidence_id
        evidence_strength[evidence.key] = _STRENGTH[evidence.strength]
        new_evidence.append(
            {
                "evidence_id": evidence_id,
                "source_id": source_key_to_id[evidence.source_key],
                "locator": evidence.locator,
                "summary": evidence.summary,
                "stance": evidence.stance,
                "strength": evidence.strength,
            }
        )

    new_claims: list[dict[str, Any]] = []
    for index, claim in enumerate(curated.claims):
        if claim.strength not in _STRENGTH:
            raise ValueError(f"unknown claim strength: {claim.strength}")
        missing = [key for key in claim.evidence_keys if key not in evidence_key_to_id]
        if missing:
            raise ValueError(f"claim references unknown evidence: {missing}")
        strongest = max((evidence_strength[key] for key in claim.evidence_keys), default=0)
        if _STRENGTH[claim.strength] > strongest:
            raise ValueError("claim strength exceeds supporting evidence strength")
        new_claims.append(
            {
                "claim_id": f"CLM-{claim_n + index:06d}",
                "text": claim.text,
                "type": claim.claim_type,
                "strength": claim.strength,
                "evidence_ids": [evidence_key_to_id[key] for key in claim.evidence_keys],
                "confidence": claim.confidence,
                "disputed": claim.disputed,
                "status": "curated",
            }
        )

    contradictions = "# Contradictions\n\n" + "\n".join(f"- {item}" for item in curated.contradictions) + "\n"
    gaps = "# Research Gaps\n\n" + "\n".join(f"- {item}" for item in curated.research_gaps) + "\n"
    synthesis = "# Research Synthesis\n\n" + curated.synthesis.strip() + "\n"
    mutations = {
        "research/SOURCE_REGISTRY.jsonl": _jsonl([*existing_sources, *new_sources]),
        "research/EVIDENCE_LEDGER.jsonl": _jsonl([*existing_evidence, *new_evidence]),
        "research/CLAIM_LEDGER.jsonl": _jsonl([*existing_claims, *new_claims]),
        "research/CONTRADICTIONS.md": contradictions,
        "research/RESEARCH_GAPS.md": gaps,
        "research/RESEARCH_SYNTHESIS.md": synthesis,
    }
    return store.commit(
        expected_revision=expected_revision,
        mutations=mutations,
        actor=actor,
        reason="curate research sprint",
    )
