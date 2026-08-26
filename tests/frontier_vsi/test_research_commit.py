import json
from pathlib import Path

import pytest

from frontier_vsi.layout import initialize_project
from frontier_vsi.research_commit import commit_curated_research
from frontier_vsi.research_models import CuratedResearch
from frontier_vsi.store import ProjectStore


def _curated() -> CuratedResearch:
    return CuratedResearch.model_validate(
        {
            "sources": [{"key":"s1","title":"Primary","url":"https://example.test/p","source_type":"primary","read":True}],
            "evidence": [{"key":"e1","source_key":"s1","locator":"p. 4","summary":"Observed fact","stance":"supports","strength":"high"}],
            "claims": [{"text":"A bounded fact","claim_type":"factual","strength":"medium","evidence_keys":["e1"],"confidence":"high"}],
            "contradictions": ["A real disagreement"],
            "research_gaps": ["Need a second independent source"],
            "synthesis": "Current evidence supports a bounded formulation.",
        }
    )


def test_host_assigns_ids_and_commits_ledgers_atomically(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-R", title="Research")
    store = ProjectStore(root)
    state = commit_curated_research(store, _curated(), expected_revision=0, actor="curator")
    snap = store.snapshot()
    assert state.project_revision == 1
    source = json.loads(snap.read_text("research/SOURCE_REGISTRY.jsonl").splitlines()[0])
    evidence = json.loads(snap.read_text("research/EVIDENCE_LEDGER.jsonl").splitlines()[0])
    claim = json.loads(snap.read_text("research/CLAIM_LEDGER.jsonl").splitlines()[0])
    assert source["source_id"] == "SRC-000001"
    assert evidence["evidence_id"] == "EVD-000001" and evidence["source_id"] == source["source_id"]
    assert claim["claim_id"] == "CLM-000001" and claim["evidence_ids"] == [evidence["evidence_id"]]
    assert "Need a second" in snap.read_text("research/RESEARCH_GAPS.md")


def test_unread_source_cannot_become_evidence(tmp_path: Path) -> None:
    data = _curated().model_dump()
    data["sources"][0]["read"] = False
    curated = CuratedResearch.model_validate(data)
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-R", title="Research")
    with pytest.raises(ValueError, match="read source"):
        commit_curated_research(ProjectStore(root), curated, expected_revision=0, actor="curator")
