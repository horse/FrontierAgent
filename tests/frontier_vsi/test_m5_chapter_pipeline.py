import json
from pathlib import Path

import pytest

from frontier_vsi.agent_runtime import AgentResponse
from frontier_vsi.chapter_pipeline import ChapterCoordinator, chapter_approval_status
from frontier_vsi.gates import dependency_fingerprint
from frontier_vsi.layout import initialize_project
from frontier_vsi.models import GateRecord, GateStatus
from frontier_vsi.store import ProjectStore


class QueueRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        return AgentResponse(
            role_id=request.role_id,
            final_content=(json.dumps(output) if isinstance(output, dict) else output),
        )


class FakeGapResolver:
    async def resolve(self, store, *, chapter_id, issues):
        snapshot = store.snapshot()
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            actor="research_director",
            reason="resolve gap",
            mutations={
                f"chapters/{chapter_id}/RESEARCH_GAP_RESOLUTION.md": (
                    "# Research Gap Resolution\nNew evidence.\n"
                )
            },
        )
        return state.project_revision


def _gate(store, *, gate, dependency_paths):
    snapshot = store.snapshot()
    refs = [snapshot.artifacts[path] for path in dependency_paths]
    record = GateRecord(
        gate=gate,
        status=GateStatus.PASS,
        dependency_paths=dependency_paths,
        input_fingerprint=dependency_fingerprint(refs),
        approved_by="editor",
    )
    return record.model_dump_json(indent=2) + "\n"


def seed(root: Path) -> ProjectStore:
    initialize_project(root, project_id="VSI-M5", title="Book")
    store = ProjectStore(root)
    function_map = """chapters:
- chapter_id: C01
  title: Control
  reader_before: a
  chapter_question: q0
  cognitive_move: m0
  central_claim: A
  chapter_function: control job
  required_claim_ids: [CLM-000001]
  anchor_cases: [case]
  counterarguments: [obj]
  reader_after: b
  handoff_to_next: C02
  word_budget: 1000
- chapter_id: C02
  title: Two
  reader_before: b
  chapter_question: q
  cognitive_move: m
  central_claim: A
  chapter_function: explain mechanism
  required_claim_ids: [CLM-000001]
  anchor_cases: [case]
  counterarguments: [obj]
  reader_after: c
  handoff_to_next: C03
  word_budget: 1000
"""
    store.commit(
        expected_revision=0,
        actor="test",
        reason="seed",
        mutations={
            "constitution/BOOK_CHARTER.md": "# Charter\n",
            "constitution/AUTHORIAL_CONSTITUTION.md": "# Author\n",
            "constitution/STYLE_PROFILE.yaml": "language: zh-CN\nregister: public\n",
            "constitution/VOICE_SPEC.md": "# Voice\n",
            "constitution/VOICE_ANCHORS.md": "# Anchors\n",
            "constitution/STYLE_LOCK.md": "# Style Lock\n",
            "architecture/MASTER_ARGUMENT.md": "# A\n",
            "architecture/OUTLINE.md": "# O\n",
            "architecture/CHAPTER_FUNCTION_MAP.yaml": function_map,
            "research/RESEARCH_SYNTHESIS.md": "# S\n",
            "research/CLAIM_LEDGER.jsonl": (
                '{"claim_id":"CLM-000001","text":"A",'
                '"evidence_ids":["EVD-000001"],"strength":"high"}\n'
            ),
            "research/EVIDENCE_LEDGER.jsonl": (
                '{"evidence_id":"EVD-000001","source_id":"SRC-1",'
                '"locator":"p1","summary":"E","strength":"high"}\n'
            ),
        },
    )
    style_gate = _gate(
        store,
        gate="STYLE_LOCKED",
        dependency_paths=[
            "constitution/STYLE_PROFILE.yaml",
            "constitution/VOICE_SPEC.md",
        ],
    )
    snapshot = store.snapshot()
    store.commit(
        expected_revision=snapshot.state.project_revision,
        actor="editor",
        reason="style pass",
        mutations={"gates/STYLE_LOCKED.json": style_gate},
    )
    control_gate = _gate(
        store,
        gate="CONTROL_CHAPTER_PASS",
        dependency_paths=[
            "architecture/CHAPTER_FUNCTION_MAP.yaml",
            "gates/STYLE_LOCKED.json",
        ],
    )
    snapshot = store.snapshot()
    store.commit(
        expected_revision=snapshot.state.project_revision,
        actor="editor",
        reason="control pass",
        mutations={"gates/CONTROL_CHAPTER_PASS.json": control_gate},
    )
    return store


def pass_outputs(prose="Draft"):
    author = {
        "prose": prose,
        "provenance": [
            {
                "span": "P1",
                "claim_ids": ["CLM-000001"],
                "evidence_ids": ["EVD-000001"],
            }
        ],
        "new_claim_candidates": [],
    }
    claims = {
        "claims": [{"text": "A", "claim_type": "factual", "risk": "high"}],
        "orphan_claims": [],
    }
    review = {"pass_gate": True, "score": 92, "issues": []}
    authorial = {
        "pass_gate": True,
        "dimensions": {
            "Position": True,
            "Interpretation": True,
            "Architecture": True,
            "Judgement": True,
            "Voice": True,
        },
        "issues": [],
    }
    return [author, claims, review, review, authorial, review]


@pytest.mark.asyncio
async def test_approval_is_hash_bound_and_style_sensitive(tmp_path):
    store = seed(tmp_path)
    result = await ChapterCoordinator(QueueRunner(pass_outputs())).run(
        store, chapter_id="C02"
    )
    assert result.status == "APPROVED"
    assert chapter_approval_status(store.snapshot(), "C02") == "PASS"

    snapshot = store.snapshot()
    store.commit(
        expected_revision=snapshot.state.project_revision,
        actor="editor",
        reason="change style",
        mutations={
            "constitution/STYLE_PROFILE.yaml": "language: zh-CN\nregister: academic\n"
        },
    )
    assert chapter_approval_status(store.snapshot(), "C02") == "STALE"


@pytest.mark.asyncio
async def test_orphan_claim_stops_at_research_gap(tmp_path):
    store = seed(tmp_path)
    author = {"prose": "Draft", "provenance": [], "new_claim_candidates": []}
    claims = {"claims": [], "orphan_claims": ["Unsupported date claim"]}
    review = {"pass_gate": True, "score": 90, "issues": []}
    authorial = {
        "pass_gate": True,
        "dimensions": {
            "Position": True,
            "Interpretation": True,
            "Architecture": True,
            "Judgement": True,
            "Voice": True,
        },
        "issues": [],
    }
    result = await ChapterCoordinator(
        QueueRunner([author, claims, review, review, authorial, review])
    ).run(store, chapter_id="C02")
    assert result.status == "RESEARCH_GAP"
    issue_paths = [
        path for path in store.snapshot().artifacts if path.startswith("issues/ISS-")
    ]
    assert issue_paths
    assert "ORPHAN_CLAIM" in store.snapshot().read_text(issue_paths[0])


@pytest.mark.asyncio
async def test_gap_resolution_returns_to_single_author_revision(tmp_path):
    store = seed(tmp_path)
    author = {"prose": "Draft1", "provenance": [], "new_claim_candidates": []}
    claims = {"claims": [], "orphan_claims": ["Need source"]}
    review = {"pass_gate": True, "score": 88, "issues": []}
    authorial = {
        "pass_gate": True,
        "dimensions": {
            "Position": True,
            "Interpretation": True,
            "Architecture": True,
            "Judgement": True,
            "Voice": True,
        },
        "issues": [],
    }
    runner = QueueRunner(
        [author, claims, review, review, authorial, review, *pass_outputs("Draft2")]
    )
    result = await ChapterCoordinator(
        runner, research_gap_resolver=FakeGapResolver()
    ).run(store, chapter_id="C02")
    assert result.status == "APPROVED"
    assert result.cycles == 2
    author_requests = [request for request in runner.requests if request.role_id == "author"]
    assert len(author_requests) == 2
    assert "RESEARCH_GAP_RESOLUTION" in author_requests[-1].context_markdown


@pytest.mark.asyncio
async def test_repeated_major_issue_routes_to_rebuild(tmp_path):
    store = seed(tmp_path)
    issue = {
        "severity": "MAJOR",
        "code": "STRUCT_DUP",
        "message": "Same structural problem",
        "repair_route": "REPAIR",
    }
    bad = {"pass_gate": False, "score": 80, "issues": [issue]}
    good = {"pass_gate": True, "score": 85, "issues": []}
    authorial = {
        "pass_gate": True,
        "dimensions": {
            "Position": True,
            "Interpretation": True,
            "Architecture": True,
            "Judgement": True,
            "Voice": True,
        },
        "issues": [],
    }

    def cycle(prose):
        return [
            {
                "prose": prose,
                "provenance": [
                    {
                        "span": "P",
                        "claim_ids": ["CLM-000001"],
                        "evidence_ids": ["EVD-000001"],
                    }
                ],
                "new_claim_candidates": [],
            },
            {
                "claims": [{"text": "A", "claim_type": "factual", "risk": "medium"}],
                "orphan_claims": [],
            },
            good,
            bad,
            authorial,
            good,
        ]

    result = await ChapterCoordinator(QueueRunner([*cycle("D1"), *cycle("D2")])).run(
        store, chapter_id="C02"
    )
    assert result.status == "REBUILD_REQUIRED"
    assert result.cycles == 2
