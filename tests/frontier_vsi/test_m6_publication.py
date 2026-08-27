import json
from pathlib import Path

import pytest

from frontier_vsi.agent_runtime import AgentResponse
from frontier_vsi.gates import dependency_fingerprint
from frontier_vsi.issues import IssueRecord, commit_issues, resolve_open_issues
from frontier_vsi.layout import initialize_project
from frontier_vsi.models import GateRecord, GateStatus
from frontier_vsi.publication import (
    FullBookCoordinator,
    approve_author,
    assemble_manuscript,
    finalize_publication,
    publication_ready_status,
)
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
            final_content=json.dumps(output) if isinstance(output, dict) else output,
        )


def _gate(store, gate, paths, approved_by="editor"):
    snapshot = store.snapshot()
    refs = [snapshot.artifacts[path] for path in paths]
    record = GateRecord(
        gate=gate,
        status=GateStatus.PASS,
        dependency_paths=paths,
        input_fingerprint=dependency_fingerprint(refs),
        approved_by=approved_by,
    )
    return record.model_dump_json(indent=2) + "\n"


def seed_book(root: Path) -> ProjectStore:
    initialize_project(root, project_id="VSI-M6", title="Book")
    store = ProjectStore(root)
    fmap = """chapters:
- chapter_id: C01
  title: Opening
  reader_before: a
  chapter_question: q1
  cognitive_move: m1
  central_claim: A
  chapter_function: open
  required_claim_ids: []
  anchor_cases: []
  counterarguments: []
  reader_after: b
  handoff_to_next: C02
  word_budget: 1000
- chapter_id: C02
  title: Mechanism
  reader_before: b
  chapter_question: q2
  cognitive_move: m2
  central_claim: B
  chapter_function: explain
  required_claim_ids: []
  anchor_cases: []
  counterarguments: []
  reader_after: c
  handoff_to_next: END
  word_budget: 1000
"""
    store.commit(
        expected_revision=0,
        actor="test",
        reason="seed",
        mutations={
            "constitution/BOOK_CHARTER.md": "# Charter\nPromise.\n",
            "constitution/AUTHORIAL_CONSTITUTION.md": "# Author\nPosition.\n",
            "constitution/STYLE_PROFILE.yaml": "language: zh-CN\nregister: public\n",
            "constitution/VOICE_SPEC.md": "# Voice\n",
            "constitution/VOICE_ANCHORS.md": "# Anchors\n",
            "architecture/MASTER_ARGUMENT.md": "# Argument\n",
            "architecture/OUTLINE.md": "# Outline\n",
            "architecture/CHAPTER_FUNCTION_MAP.yaml": fmap,
            "research/RESEARCH_SYNTHESIS.md": "# Synthesis\n",
            "research/RESEARCH_GAPS.md": "# Research Gaps\n",
            "chapters/C01/DRAFT.md": "Opening text.\n",
            "chapters/C01/CLAIMS.jsonl": "",
            "chapters/C02/DRAFT.md": "Mechanism text.\n",
            "chapters/C02/BRIEF.md": "# Brief\n",
            "chapters/C02/EVIDENCE_PACKET.md": "# Evidence\n",
            "chapters/C02/PROVENANCE.json": "{}\n",
            "chapters/C02/CLAIMS.jsonl": "",
            "chapters/C02/REVIEWS/cycle-01.json": "{}\n",
            "publication/PUBLICATION_RUBRIC.md": "# Rubric\nThreshold 90.\n",
        },
    )
    style = _gate(
        store,
        "STYLE_LOCKED",
        ["constitution/STYLE_PROFILE.yaml", "constitution/VOICE_SPEC.md"],
    )
    snap = store.snapshot()
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="editor",
        reason="style",
        mutations={"gates/STYLE_LOCKED.json": style},
    )
    control = _gate(
        store,
        "CONTROL_CHAPTER_PASS",
        [
            "chapters/C01/DRAFT.md",
            "architecture/CHAPTER_FUNCTION_MAP.yaml",
            "gates/STYLE_LOCKED.json",
        ],
    )
    snap = store.snapshot()
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="editor",
        reason="control",
        mutations={"gates/CONTROL_CHAPTER_PASS.json": control},
    )
    approval = _gate(
        store,
        "CHAPTER_APPROVED:C02",
        [
            "chapters/C02/BRIEF.md",
            "chapters/C02/EVIDENCE_PACKET.md",
            "chapters/C02/DRAFT.md",
            "chapters/C02/PROVENANCE.json",
            "chapters/C02/CLAIMS.jsonl",
            "chapters/C02/REVIEWS/cycle-01.json",
            "architecture/CHAPTER_FUNCTION_MAP.yaml",
            "constitution/STYLE_PROFILE.yaml",
            "constitution/VOICE_SPEC.md",
            "gates/STYLE_LOCKED.json",
        ],
    )
    snap = store.snapshot()
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="editor",
        reason="chapter",
        mutations={"chapters/C02/APPROVAL.json": approval},
    )
    return store


def passing_reviews(score=94):
    review = {"pass_gate": True, "score": score, "issues": []}
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
    return [
        review,
        review,
        review,
        authorial,
        review,
        review,
        review,
        authorial,
        review,
        authorial,
    ]


def test_assemble_manuscript_requires_current_chapter_approvals(tmp_path):
    store = seed_book(tmp_path)
    result = assemble_manuscript(store)
    assert result.chapter_ids == ("C01", "C02")
    assert "Opening text." in store.snapshot().read_text("manuscript/MANUSCRIPT.md")

    snap = store.snapshot()
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="test",
        reason="make stale",
        mutations={"chapters/C02/DRAFT.md": "Changed.\n"},
    )
    with pytest.raises(ValueError, match="not currently approved"):
        assemble_manuscript(store)


@pytest.mark.asyncio
async def test_full_book_passes_to_human_author_approval_with_fresh_blind_review(tmp_path):
    store = seed_book(tmp_path)
    runner = QueueRunner(passing_reviews())
    result = await FullBookCoordinator(runner).run(store)

    assert result.status == "AWAITING_AUTHOR_APPROVAL"
    snapshot = store.snapshot()
    assert "publication/FULL_AUDIT_01.json" in snapshot.artifacts
    assert "publication/FINAL_FULL_AUDIT.json" in snapshot.artifacts
    assert "publication/BLIND_REVIEW.json" in snapshot.artifacts
    assert "publication/FINAL_AUTHORIAL_REVIEW.json" in snapshot.artifacts
    assert "publication/PREFLIGHT.json" in snapshot.artifacts
    blind_request = [
        request for request in runner.requests if request.role_id == "blind_reviewer"
    ][-1]
    assert "MANUSCRIPT.md" in blind_request.context_markdown
    assert "FULL_AUDIT" not in blind_request.context_markdown
    assert "issues/" not in blind_request.context_markdown


def test_human_approval_and_publication_ready_are_hash_bound(tmp_path):
    store = seed_book(tmp_path)
    assemble_manuscript(store)
    snap = store.snapshot()
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="test",
        reason="final reviews",
        mutations={
            "publication/FINAL_FULL_AUDIT.json": '{"passed":true}\n',
            "publication/BLIND_REVIEW.json": '{"pass_gate":true,"score":95,"issues":[]}\n',
            "publication/FINAL_AUTHORIAL_REVIEW.json": '{"pass_gate":true}\n',
            "publication/PREFLIGHT.json": '{"passed":true}\n',
        },
    )
    approve_author(store, approved_by="human:owner", note="approved")
    final = finalize_publication(store)
    assert final.status == "PUBLICATION_READY"
    assert publication_ready_status(store.snapshot()) == "PASS"
    assert "publication/MANUSCRIPT.md" in store.snapshot().artifacts

    snap = store.snapshot()
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="test",
        reason="change manuscript",
        mutations={"manuscript/MANUSCRIPT.md": "changed\n"},
    )
    assert publication_ready_status(store.snapshot()) == "STALE"


@pytest.mark.asyncio
async def test_failed_first_full_audit_stops_without_delta_repairer(tmp_path):
    store = seed_book(tmp_path)
    bad = {
        "pass_gate": False,
        "score": 80,
        "issues": [
            {
                "severity": "MAJOR",
                "code": "STRUCTURE",
                "message": "Book repeats itself",
                "repair_route": "REPAIR",
            }
        ],
    }
    good = {"pass_gate": True, "score": 92, "issues": []}
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
    result = await FullBookCoordinator(
        QueueRunner([good, bad, good, authorial])
    ).run(store)
    assert result.status == "REPAIR_REQUIRED"


def test_resolving_scope_issues_removes_publication_blocker(tmp_path):
    store = seed_book(tmp_path)
    snap = store.snapshot()
    issue = IssueRecord(
        issue_id="ISS-000001",
        scope="C02",
        source_role="fact_reviewer",
        cycle=1,
        severity="MAJOR",
        code="FACT",
        message="Repair me",
        repair_route="REPAIR",
        fingerprint="0" * 64,
    )
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="editor",
        reason="seed issue",
        mutations={"issues/ISS-000001.json": issue.model_dump_json(indent=2) + "\n"},
    )
    resolve_open_issues(store, scope="C02")
    stored = IssueRecord.model_validate_json(
        store.snapshot().read_text("issues/ISS-000001.json")
    )
    assert stored.status == "RESOLVED"


def test_clean_issue_cycle_resolves_older_scope_issues(tmp_path):
    store = seed_book(tmp_path)
    snap = store.snapshot()
    issue = IssueRecord(
        issue_id="ISS-000001",
        scope="C02",
        source_role="fact_reviewer",
        cycle=1,
        severity="MAJOR",
        code="FACT",
        message="Repair me",
        repair_route="REPAIR",
        fingerprint="0" * 64,
    )
    store.commit(
        expected_revision=snap.state.project_revision,
        actor="editor",
        reason="seed issue",
        mutations={"issues/ISS-000001.json": issue.model_dump_json(indent=2) + "\n"},
    )
    commit_issues(store, scope="C02", cycle=2, issues=[])
    stored = IssueRecord.model_validate_json(
        store.snapshot().read_text("issues/ISS-000001.json")
    )
    assert stored.status == "RESOLVED"
