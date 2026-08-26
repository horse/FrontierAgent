import json
from pathlib import Path

import pytest

from frontier_vsi.chapter_materials import build_chapter_materials
from frontier_vsi.layout import initialize_project
from frontier_vsi.store import ProjectStore


def seed(root: Path) -> ProjectStore:
    initialize_project(root, project_id="VSI-MAT", title="Book")
    store = ProjectStore(root)
    function_map = """chapters:
- chapter_id: C01
  title: Start
  reader_before: naive
  chapter_question: why
  cognitive_move: reframe
  central_claim: A
  chapter_function: establish problem
  required_claim_ids:
  - CLM-000001
  anchor_cases:
  - Case
  counterarguments:
  - Objection
  reader_after: oriented
  handoff_to_next: mechanism
  word_budget: 5000
"""
    store.commit(
        expected_revision=0,
        actor="test",
        reason="seed",
        mutations={
            "architecture/CHAPTER_FUNCTION_MAP.yaml": function_map,
            "research/CLAIM_LEDGER.jsonl": json.dumps(
                {
                    "claim_id": "CLM-000001",
                    "text": "Claim A",
                    "evidence_ids": ["EVD-000001"],
                    "strength": "high",
                }
            )
            + "\n",
            "research/EVIDENCE_LEDGER.jsonl": json.dumps(
                {
                    "evidence_id": "EVD-000001",
                    "source_id": "SRC-000001",
                    "locator": "p.1",
                    "summary": "Evidence A",
                    "strength": "high",
                }
            )
            + "\n",
        },
    )
    return store


def test_build_chapter_materials_selects_required_claims_and_evidence(tmp_path):
    store = seed(tmp_path)
    result = build_chapter_materials(store, chapter_id="C01")
    snapshot = store.snapshot()
    brief = snapshot.read_text("chapters/C01/BRIEF.md")
    packet = snapshot.read_text("chapters/C01/EVIDENCE_PACKET.md")

    assert result.chapter_id == "C01"
    assert "establish problem" in brief
    assert "CLM-000001" in packet
    assert "EVD-000001" in packet
    assert "p.1" in packet


def test_build_chapter_materials_fails_closed_on_missing_evidence(tmp_path):
    store = seed(tmp_path)
    snapshot = store.snapshot()
    store.commit(
        expected_revision=snapshot.state.project_revision,
        actor="test",
        reason="break evidence",
        mutations={"research/EVIDENCE_LEDGER.jsonl": ""},
    )
    with pytest.raises(ValueError, match="missing evidence"):
        build_chapter_materials(store, chapter_id="C01")
