from __future__ import annotations

import json
from dataclasses import dataclass

import yaml

from .agent_runtime import AgentRunner
from .architecture_stage import ArchitectureCoordinator
from .chapter_pipeline import ChapterCoordinator
from .commission import CommissionCoordinator
from .control_chapter import ControlChapterCoordinator
from .delta_repair import EditorialDeltaRepairer
from .gates import dependency_fingerprint, evaluate_gate_freshness
from .models import GateRecord, GateStatus
from .publication import FullBookCoordinator, approve_author, finalize_publication
from .research import ResearchCoordinator
from .research_gap import FocusedResearchGapResolver
from .store import ProjectSnapshot, ProjectStore
from .style_stage import StyleCalibrationCoordinator


@dataclass(frozen=True)
class StageResult:
    stage: str
    status: str
    project_revision: int
    details: dict[str, object]


def gate_status(snapshot: ProjectSnapshot, gate: str) -> GateStatus:
    path = f"gates/{gate}.json"
    if path not in snapshot.artifacts:
        return GateStatus.PENDING
    record = GateRecord.model_validate_json(snapshot.read_text(path))
    return evaluate_gate_freshness(record, snapshot.artifacts)


def require_gate(store: ProjectStore, gate: str) -> None:
    status = gate_status(store.snapshot(), gate)
    if status != GateStatus.PASS:
        raise ValueError(f"required gate {gate} is {status.value}")


def _chapter_ids(store: ProjectStore) -> tuple[str, ...]:
    snapshot = store.snapshot()
    payload = yaml.safe_load(snapshot.read_text("architecture/CHAPTER_FUNCTION_MAP.yaml")) or {}
    chapters = tuple(str(item["chapter_id"]) for item in payload.get("chapters", []))
    if not chapters:
        raise ValueError("chapter function map contains no chapters")
    return chapters


def _commit_research_ready(store: ProjectStore) -> int:
    snapshot = store.snapshot()
    dependency_paths = [
        path
        for path in (
            "research/RESEARCH_PLAN.md",
            "research/SOURCE_REGISTRY.jsonl",
            "research/EVIDENCE_LEDGER.jsonl",
            "research/CLAIM_LEDGER.jsonl",
            "research/CONTRADICTIONS.md",
            "research/RESEARCH_GAPS.md",
            "research/RESEARCH_SYNTHESIS.md",
        )
        if path in snapshot.artifacts
    ]
    refs = [snapshot.artifacts[path] for path in dependency_paths]
    gate = GateRecord(
        gate="RESEARCH_READY",
        status=GateStatus.PASS,
        dependency_paths=dependency_paths,
        input_fingerprint=dependency_fingerprint(refs),
        approved_by="research_director",
    )
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={"gates/RESEARCH_READY.json": gate.model_dump_json(indent=2) + "\n"},
        actor="research_director",
        reason="hash-bind research-ready evidence base",
    )
    return state.project_revision


class EditorialService:
    """Fixed FrontierVSI editorial operating system above low-level coordinators."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    async def commission(self, store: ProjectStore, *, brief: str) -> StageResult:
        result = await CommissionCoordinator(self.runner).run(store, brief=brief)
        return StageResult(
            stage="commission",
            status="FRAMING_READY" if result.passed else "FRAMING_FAILED",
            project_revision=result.project_revision,
            details={"passed": result.passed},
        )

    async def research(
        self,
        store: ProjectStore,
        *,
        focus: str,
        max_parallel: int = 4,
    ) -> StageResult:
        require_gate(store, "FRAMING_READY")
        result = await ResearchCoordinator(
            self.runner,
            max_parallel=max_parallel,
            web_policy="search_only",
        ).run(store, focus=focus)
        if result.curated.research_gaps:
            return StageResult(
                stage="research",
                status="RESEARCH_GAP",
                project_revision=result.project_revision,
                details={"research_gaps": result.curated.research_gaps},
            )
        revision = _commit_research_ready(store)
        return StageResult(
            stage="research",
            status="RESEARCH_READY",
            project_revision=revision,
            details={
                "tasks": len(result.plan.tasks),
                "reports": len(result.reports),
            },
        )

    async def architecture(self, store: ProjectStore) -> StageResult:
        require_gate(store, "RESEARCH_READY")
        result = await ArchitectureCoordinator(self.runner).run(store)
        return StageResult(
            stage="architecture",
            status="ARCHITECTURE_LOCKED" if result.locked else "ARCHITECTURE_FAILED",
            project_revision=result.project_revision,
            details={"chapters": len(result.architecture.chapters)},
        )

    async def style(self, store: ProjectStore) -> StageResult:
        require_gate(store, "ARCHITECTURE_LOCKED")
        result = await StyleCalibrationCoordinator(self.runner).run(store)
        return StageResult(
            stage="style",
            status="STYLE_LOCKED" if result.locked else "STYLE_FAILED",
            project_revision=result.project_revision,
            details={"samples": list(result.sample_paths)},
        )

    async def control_chapter(
        self,
        store: ProjectStore,
        *,
        chapter_id: str | None = None,
    ) -> StageResult:
        require_gate(store, "STYLE_LOCKED")
        control_id = chapter_id or _chapter_ids(store)[0]
        result = await ControlChapterCoordinator(self.runner).run(
            store, chapter_id=control_id
        )
        return StageResult(
            stage="control-chapter",
            status="CONTROL_CHAPTER_PASS" if result.passed else "CONTROL_CHAPTER_FAILED",
            project_revision=result.project_revision,
            details={"chapter_id": control_id},
        )

    async def chapter(self, store: ProjectStore, *, chapter_id: str) -> StageResult:
        require_gate(store, "CONTROL_CHAPTER_PASS")
        resolver = FocusedResearchGapResolver(self.runner)
        result = await ChapterCoordinator(
            self.runner,
            research_gap_resolver=resolver,
        ).run(store, chapter_id=chapter_id)
        return StageResult(
            stage="chapter",
            status=result.status,
            project_revision=result.project_revision,
            details={
                "chapter_id": chapter_id,
                "cycles": result.cycles,
                "issue_ids": list(result.issue_ids),
            },
        )

    async def chapters(self, store: ProjectStore) -> StageResult:
        require_gate(store, "CONTROL_CHAPTER_PASS")
        chapter_ids = _chapter_ids(store)
        completed: list[str] = []
        for chapter_id in chapter_ids[1:]:
            result = await self.chapter(store, chapter_id=chapter_id)
            if result.status != "APPROVED":
                return StageResult(
                    stage="chapters",
                    status=result.status,
                    project_revision=result.project_revision,
                    details={"completed": completed, "blocked_chapter": chapter_id},
                )
            completed.append(chapter_id)
        return StageResult(
            stage="chapters",
            status="FULL_DRAFT",
            project_revision=store.snapshot().state.project_revision,
            details={"completed": completed},
        )

    async def full_audit(self, store: ProjectStore) -> StageResult:
        require_gate(store, "CONTROL_CHAPTER_PASS")
        result = await FullBookCoordinator(
            self.runner,
            delta_repairer=EditorialDeltaRepairer(self.runner),
        ).run(store)
        return StageResult(
            stage="full-audit",
            status=result.status,
            project_revision=result.project_revision,
            details={"issue_ids": list(result.issue_ids)},
        )

    def approve_author(
        self,
        store: ProjectStore,
        *,
        approved_by: str,
        note: str = "",
    ) -> StageResult:
        revision = approve_author(store, approved_by=approved_by, note=note)
        return StageResult(
            stage="approve-author",
            status="AUTHOR_APPROVED",
            project_revision=revision,
            details={"approved_by": approved_by},
        )

    def export(self, store: ProjectStore) -> StageResult:
        result = finalize_publication(store)
        return StageResult(
            stage="export",
            status=result.status,
            project_revision=result.project_revision,
            details={
                "manuscript": "publication/MANUSCRIPT.md",
                "manifest": "publication/MANIFEST.json",
            },
        )

    def status(self, store: ProjectStore) -> dict[str, object]:
        snapshot = store.snapshot()
        gates = {
            gate: gate_status(snapshot, gate).value
            for gate in (
                "FRAMING_READY",
                "RESEARCH_READY",
                "ARCHITECTURE_LOCKED",
                "STYLE_LOCKED",
                "CONTROL_CHAPTER_PASS",
                "AUTHOR_APPROVED",
                "PUBLICATION_READY",
            )
        }
        chapter_states: dict[str, str] = {}
        if "architecture/CHAPTER_FUNCTION_MAP.yaml" in snapshot.artifacts:
            for chapter_id in _chapter_ids(store):
                state_path = f"chapters/{chapter_id}/STATE.json"
                if state_path in snapshot.artifacts:
                    payload = json.loads(snapshot.read_text(state_path))
                    chapter_states[chapter_id] = str(payload.get("status", "UNKNOWN"))
        return {
            "project_id": snapshot.state.project_id,
            "title": snapshot.state.title,
            "revision": snapshot.state.project_revision,
            "gates": gates,
            "chapters": chapter_states,
        }
