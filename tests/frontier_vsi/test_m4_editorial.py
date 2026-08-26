import json
from pathlib import Path

import pytest

from frontier_vsi.agent_runtime import AgentResponse
from frontier_vsi.architecture_stage import ArchitectureCoordinator
from frontier_vsi.control_chapter import ControlChapterCoordinator
from frontier_vsi.layout import initialize_project
from frontier_vsi.store import ProjectStore
from frontier_vsi.style_stage import StyleCalibrationCoordinator


class QueueRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        content = json.dumps(output) if isinstance(output, dict) else output
        return AgentResponse(role_id=request.role_id, final_content=content)


def seed(root: Path) -> ProjectStore:
    initialize_project(root, project_id="VSI-M4", title="Book")
    store = ProjectStore(root)
    store.commit(
        expected_revision=0,
        actor="test",
        reason="seed",
        mutations={
            "constitution/BOOK_CHARTER.md": "# Charter\nPromise\n",
            "constitution/AUTHORIAL_CONSTITUTION.md": "# Author\nPosition\n",
            "constitution/STYLE_PROFILE.yaml": "language: zh-CN\nregister: public\n",
            "constitution/VOICE_SPEC.md": "# Voice\nClear.\n",
            "constitution/VOICE_ANCHORS.md": "# Anchors\n",
            "research/RESEARCH_SYNTHESIS.md": "# Synthesis\nEvidence landscape.\n",
            "research/CONTRADICTIONS.md": "# Contradictions\n- tension\n",
            "research/RESEARCH_GAPS.md": "# Gaps\n",
            "research/CLAIM_LEDGER.jsonl": (
                '{"claim_id":"CLM-000001","text":"A",'
                '"evidence_ids":["EVD-000001"]}\n'
            ),
            "research/EVIDENCE_LEDGER.jsonl": (
                '{"evidence_id":"EVD-000001","source_id":"SRC-000001",'
                '"locator":"p1","summary":"S"}\n'
            ),
        },
    )
    return store


def architecture_payload() -> dict[str, object]:
    return {
        "master_argument": "One spine",
        "excluded_but_important": ["x"],
        "chapters": [
            {
                "chapter_id": "C01",
                "title": "Start",
                "reader_before": "naive",
                "chapter_question": "why",
                "cognitive_move": "reframe",
                "central_claim": "A",
                "chapter_function": "establish problem",
                "required_claim_ids": ["CLM-000001"],
                "anchor_cases": ["case"],
                "counterarguments": ["objection"],
                "reader_after": "oriented",
                "handoff_to_next": "mechanism",
                "word_budget": 5000,
            }
        ],
    }


@pytest.mark.asyncio
async def test_architecture_commits_unique_chapter_function_map(tmp_path):
    store = seed(tmp_path)
    runner = QueueRunner([architecture_payload(), {"pass_gate": True, "issues": []}])
    result = await ArchitectureCoordinator(runner).run(store)

    snapshot = store.snapshot()
    assert result.locked is True
    assert "architecture/MASTER_ARGUMENT.md" in snapshot.artifacts
    assert "architecture/CHAPTER_FUNCTION_MAP.yaml" in snapshot.artifacts
    assert "C01" in snapshot.read_text("architecture/CHAPTER_FUNCTION_MAP.yaml")


@pytest.mark.asyncio
async def test_architecture_rejects_duplicate_chapter_functions(tmp_path):
    store = seed(tmp_path)
    chapter = {
        "reader_before": "a",
        "chapter_question": "q",
        "cognitive_move": "m",
        "central_claim": "c",
        "chapter_function": "same",
        "required_claim_ids": [],
        "anchor_cases": [],
        "counterarguments": [],
        "reader_after": "b",
        "handoff_to_next": "n",
        "word_budget": 1000,
    }
    payload = {
        "master_argument": "x",
        "excluded_but_important": [],
        "chapters": [
            dict(chapter, chapter_id="C01", title="1"),
            dict(chapter, chapter_id="C02", title="2"),
        ],
    }
    with pytest.raises(ValueError, match="unique"):
        await ArchitectureCoordinator(QueueRunner([payload])).run(store)


@pytest.mark.asyncio
async def test_style_calibration_requires_three_fixed_sample_types(tmp_path):
    store = seed(tmp_path)
    store.commit(
        expected_revision=1,
        actor="test",
        reason="arch",
        mutations={
            "architecture/MASTER_ARGUMENT.md": "# A\n",
            "architecture/OUTLINE.md": "# O\n",
            "architecture/CHAPTER_FUNCTION_MAP.yaml": "chapters: []\n",
        },
    )
    outputs = []
    for kind in ("opening", "core_explanation", "high_risk"):
        outputs += [f"{kind} sample", {"pass_gate": True, "score": 92, "issues": []}]

    result = await StyleCalibrationCoordinator(QueueRunner(outputs)).run(store)
    assert set(result.sample_paths) == {
        "style/calibration/opening.md",
        "style/calibration/core_explanation.md",
        "style/calibration/high_risk.md",
    }
    assert "constitution/STYLE_LOCK.md" in store.snapshot().artifacts


@pytest.mark.asyncio
async def test_control_chapter_runs_claim_extraction_and_independent_reviews(tmp_path):
    store = seed(tmp_path)
    store.commit(
        expected_revision=1,
        actor="test",
        reason="arch",
        mutations={
            "architecture/MASTER_ARGUMENT.md": "# A\n",
            "architecture/OUTLINE.md": "# O\n",
            "architecture/CHAPTER_FUNCTION_MAP.yaml": (
                "chapters:\n"
                "- chapter_id: C01\n"
                "  title: Start\n"
                "  reader_before: naive\n"
                "  chapter_question: why\n"
                "  cognitive_move: reframe\n"
                "  central_claim: A\n"
                "  chapter_function: establish problem\n"
                "  required_claim_ids: [CLM-000001]\n"
                "  anchor_cases: [case]\n"
                "  counterarguments: [objection]\n"
                "  reader_after: oriented\n"
                "  handoff_to_next: mechanism\n"
                "  word_budget: 5000\n"
            ),
            "constitution/STYLE_LOCK.md": "# Style Lock\nStatus: LOCKED\n",
        },
    )
    claim = {
        "claims": [{"text": "A", "claim_type": "factual", "risk": "high"}],
        "orphan_claims": [],
    }
    review = {"pass_gate": True, "score": 92, "issues": []}
    author = {
        "prose": "Draft prose.",
        "provenance": [
            {"span": "P1", "claim_ids": ["CLM-000001"], "evidence_ids": ["EVD-000001"]}
        ],
        "new_claim_candidates": [],
    }
    runner = QueueRunner(
        [
            author,
            claim,
            review,
            review,
            {
                "pass_gate": True,
                "dimensions": {
                    "Position": True,
                    "Interpretation": True,
                    "Architecture": True,
                    "Judgement": True,
                    "Voice": True,
                },
                "issues": [],
            },
            review,
        ]
    )
    result = await ControlChapterCoordinator(runner).run(store, chapter_id="C01")

    snapshot = store.snapshot()
    assert result.passed is True
    assert "chapters/C01/DRAFT.md" in snapshot.artifacts
    assert "chapters/C01/REVIEWS/summary.json" in snapshot.artifacts
    roles = [request.role_id for request in runner.requests]
    assert roles[0] == "author"
    assert roles[1] == "claim_extractor"
    assert set(roles[2:]) == {
        "fact_reviewer",
        "structural_reviewer",
        "authorial_reviewer",
        "public_reader_reviewer",
    }


@pytest.mark.asyncio
async def test_architecture_and_style_locks_are_hash_bound(tmp_path):
    store = seed(tmp_path)
    await ArchitectureCoordinator(
        QueueRunner([architecture_payload(), {"pass_gate": True, "issues": []}])
    ).run(store)
    architecture_lock = json.loads(
        store.snapshot().read_text("gates/ARCHITECTURE_LOCKED.json")
    )
    assert architecture_lock["status"] == "PASS"
    assert len(architecture_lock["input_fingerprint"]) == 64

    outputs = []
    for kind in ("opening", "core_explanation", "high_risk"):
        outputs += [f"{kind} sample", {"pass_gate": True, "score": 92, "issues": []}]
    await StyleCalibrationCoordinator(QueueRunner(outputs)).run(store)
    style_lock = json.loads(store.snapshot().read_text("gates/STYLE_LOCKED.json"))
    assert style_lock["status"] == "PASS"
    assert len(style_lock["input_fingerprint"]) == 64
