import json
from pathlib import Path

import pytest

from frontier_vsi.agent_runtime import AgentRequest, AgentResponse
from frontier_vsi.cli import EXIT_CONFLICT, EXIT_OK, run as cli_run
from frontier_vsi.commission import CommissionCoordinator
from frontier_vsi.decisions import decide_issue
from frontier_vsi.issues import IssueRecord
from frontier_vsi.layout import initialize_project
from frontier_vsi.requests import begin_request, lookup_request
from frontier_vsi.runlog import RecordingRunner, RunTracker
from frontier_vsi.service import EditorialService, gate_status
from frontier_vsi.store import ProjectStore


class QueueRunner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, dict):
            output = json.dumps(output)
        return AgentResponse(role_id=request.role_id, final_content=output)


def test_begin_request_grants_exactly_one_execution_owner(tmp_path):
    initialize_project(tmp_path, project_id="VSI-IDEMP", title="Book")
    first, first_owner = begin_request(tmp_path, "req-1", "a" * 64)
    second, second_owner = begin_request(tmp_path, "req-1", "a" * 64)
    assert first_owner is True
    assert second_owner is False
    assert first.request_id == second.request_id == "req-1"


@pytest.mark.asyncio
async def test_run_tracker_records_prompt_context_and_candidate(tmp_path):
    initialize_project(tmp_path, project_id="VSI-RUN", title="Book")
    store = ProjectStore(tmp_path)
    tracker = RunTracker.start(
        store,
        stage="test",
        command="run test",
        request_id="req-run",
    )
    runner = RecordingRunner(QueueRunner(["candidate prose"]), tracker)
    response = await runner.run(
        AgentRequest(
            role_id="author",
            instruction="Write.",
            context_markdown="context_hash: " + "b" * 64 + "\n",
            task_id="task-1",
        )
    )
    assert response.final_content == "candidate prose"
    tracker.complete(store, {"ok": True, "status": "DONE"})
    manifest = json.loads((tracker.run_dir / "manifest.json").read_text())
    assert manifest["status"] == "SUCCEEDED"
    assert manifest["prompt_hashes"]
    assert manifest["context_pack_hashes"]
    assert list((tracker.run_dir / "candidates").glob("*.txt"))


@pytest.mark.asyncio
async def test_commission_creates_hash_bound_framing_ready(tmp_path):
    initialize_project(tmp_path, project_id="VSI-COM", title="Book")
    package = {
        "book_charter_md": "# Charter\n\nA clear public promise for an intelligent non-specialist reader.",
        "authorial_constitution_md": "# Authorial Constitution\n\nInterpret, select, judge, and state uncertainty explicitly.",
        "provisional_outline_md": "# Provisional Outline\n\n1. Entry\n2. Mechanism\n3. Complication\n",
        "source_policy_md": "# Source Policy\n\nRead sources before evidence claims and record precise locators.",
        "style_profile": {"language": "zh-CN", "register": "public-intellectual"},
        "voice_spec_md": "# Voice\n\nLong coherent paragraphs, explicit explanation, controlled judgement.",
    }
    review = {"pass_gate": True, "score": 94, "issues": []}
    runner = QueueRunner([package, review])
    result = await CommissionCoordinator(runner).run(
        ProjectStore(tmp_path),
        brief="Explain the subject through one central argument for general readers.",
    )
    assert result.passed is True
    snapshot = ProjectStore(tmp_path).snapshot()
    assert gate_status(snapshot, "FRAMING_READY").value == "PASS"
    assert "architecture/PROVISIONAL_OUTLINE.md" in snapshot.artifacts
    assert "constitution/STYLE_PROFILE.yaml" in snapshot.artifacts


@pytest.mark.asyncio
async def test_service_research_creates_research_ready_only_when_gaps_are_empty(tmp_path):
    initialize_project(tmp_path, project_id="VSI-RES", title="Book")
    store = ProjectStore(tmp_path)
    package = {
        "book_charter_md": "# Charter\n\nA clear public promise for an intelligent non-specialist reader.",
        "authorial_constitution_md": "# Authorial Constitution\n\nInterpret, select, judge, and state uncertainty explicitly.",
        "provisional_outline_md": "# Provisional Outline\n\n1. Entry\n2. Mechanism\n3. Complication\n",
        "source_policy_md": "# Source Policy\n\nRead sources before evidence claims and record precise locators.",
        "style_profile": {"language": "zh-CN", "register": "public-intellectual"},
        "voice_spec_md": "# Voice\n\nLong coherent paragraphs, explicit explanation, controlled judgement.",
    }
    commission_runner = QueueRunner(
        [package, {"pass_gate": True, "score": 94, "issues": []}]
    )
    await EditorialService(commission_runner).commission(store, brief="Commission this book clearly.")

    plan = {
        "focus": "mechanism",
        "tasks": [
            {
                "task_id": "R1",
                "question": "What is the mechanism?",
                "objective": "Establish the core explanation",
                "search_terms": ["mechanism"],
                "required_source_types": ["primary"],
                "counterexample_target": "Find a case that should break it",
            }
        ],
        "stop_conditions": ["one strong source plus an active counterexample test"],
    }
    report = {
        "task_id": "R1",
        "question": "What is the mechanism?",
        "findings": [
            {
                "claim": "The mechanism works this way.",
                "confidence": "high",
                "source_keys": ["S1"],
                "notes": "read directly",
            }
        ],
        "counterevidence": [],
        "disagreements": [],
        "uncertainties": [],
        "recommended_claims": ["The mechanism works this way."],
        "do_not_claim": [],
        "followup_questions": [],
        "source_candidates": [
            {
                "key": "S1",
                "title": "Primary source",
                "url": "https://example.com/source",
                "source_type": "primary",
                "read": True,
            }
        ],
    }
    curated = {
        "sources": [
            {
                "key": "S1",
                "title": "Primary source",
                "url": "https://example.com/source",
                "source_type": "primary",
                "read": True,
            }
        ],
        "evidence": [
            {
                "key": "E1",
                "source_key": "S1",
                "locator": "section 2",
                "summary": "Direct evidence for the mechanism.",
                "stance": "supports",
                "strength": "high",
            }
        ],
        "claims": [
            {
                "text": "The mechanism works this way.",
                "claim_type": "factual",
                "strength": "high",
                "evidence_keys": ["E1"],
                "confidence": "high",
                "disputed": False,
            }
        ],
        "contradictions": [],
        "research_gaps": [],
        "synthesis": "The evidence supports a bounded mechanism explanation.",
    }
    research_runner = QueueRunner([plan, report, curated])
    result = await EditorialService(research_runner).research(
        store,
        focus="mechanism",
        max_parallel=1,
    )
    assert result.status == "RESEARCH_READY"
    assert gate_status(store.snapshot(), "RESEARCH_READY").value == "PASS"


def test_cli_ingest_is_idempotent_and_replays_completed_result(tmp_path, capsys):
    book = tmp_path / "book"
    source = tmp_path / "sample.md"
    source.write_text("sample prose", encoding="utf-8")
    assert cli_run(
        [
            "init",
            "--book",
            str(book),
            "--title",
            "Book",
            "--project-id",
            "VSI-CLI",
            "--json",
        ]
    ) == EXIT_OK
    capsys.readouterr()
    argv = [
        "ingest",
        "--book",
        str(book),
        "--path",
        "samples/user_sample_01.md",
        "--file",
        str(source),
        "--request-id",
        "ingest-1",
        "--if-revision",
        "0",
        "--json",
    ]
    assert cli_run(argv) == EXIT_OK
    first = json.loads(capsys.readouterr().out)
    assert first["replayed"] is False
    assert cli_run(argv) == EXIT_OK
    second = json.loads(capsys.readouterr().out)
    assert second["replayed"] is True
    assert second["sha256"] == first["sha256"]
    record = lookup_request(book, "ingest-1")
    assert record is not None and record.status.value == "COMPLETED"


def test_cli_same_request_id_different_command_fails_closed(tmp_path, capsys):
    book = tmp_path / "book"
    first_source = tmp_path / "a.md"
    second_source = tmp_path / "b.md"
    first_source.write_text("a", encoding="utf-8")
    second_source.write_text("b", encoding="utf-8")
    cli_run(
        [
            "init",
            "--book",
            str(book),
            "--title",
            "Book",
            "--project-id",
            "VSI-CONFLICT",
            "--json",
        ]
    )
    capsys.readouterr()
    common = ["ingest", "--book", str(book), "--request-id", "same", "--json"]
    assert cli_run(common + ["--path", "samples/a.md", "--file", str(first_source)]) == EXIT_OK
    capsys.readouterr()
    assert (
        cli_run(common + ["--path", "samples/b.md", "--file", str(second_source)])
        == EXIT_CONFLICT
    )


def test_human_decision_record_updates_issue_and_rejects_agent_identity(tmp_path):
    initialize_project(tmp_path, project_id="VSI-DEC", title="Book")
    store = ProjectStore(tmp_path)
    issue = IssueRecord(
        issue_id="ISS-000001",
        scope="C02",
        source_role="structural_reviewer",
        cycle=1,
        severity="MAJOR",
        code="STRUCTURE",
        message="Move this argument upstream.",
        repair_route="REPAIR",
        fingerprint="0" * 64,
    )
    store.commit(
        expected_revision=0,
        mutations={"issues/ISS-000001.json": issue.model_dump_json(indent=2) + "\n"},
        actor="editor",
        reason="seed issue",
    )
    with pytest.raises(ValueError, match="human identity"):
        decide_issue(
            store,
            issue_id="ISS-000001",
            disposition="RESOLVED",
            rationale="done",
            decided_by="editor-agent",
        )
    decision = decide_issue(
        store,
        issue_id="ISS-000001",
        disposition="RESOLVED",
        rationale="Reviewed the repaired chapter.",
        decided_by="human:owner",
    )
    assert decision.decision_id == "DEC-000001"
    updated = IssueRecord.model_validate_json(
        store.snapshot().read_text("issues/ISS-000001.json")
    )
    assert updated.status == "RESOLVED"
