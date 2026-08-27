from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Protocol

import yaml

from .agent_runtime import AgentRequest, AgentRunner
from .chapter_pipeline import chapter_approval_status
from .context import build_context_pack
from .editorial_models import AuthorialReview, ReviewIssue, ReviewPacket
from .gates import dependency_fingerprint, evaluate_gate_freshness
from .issues import IssueRecord, commit_issues, iter_issues, load_issue, resolve_open_issues
from .models import GateRecord, GateStatus
from .store import ProjectSnapshot, ProjectStore
from .structured import parse_structured_output

_REVIEW_CONTRACT = (
    "Return exactly one JSON object: pass_gate, score 0-100, "
    "issues[{severity,code,message,location,repair_route}]."
)
_AUTHORIAL_CONTRACT = (
    "Return exactly one JSON object: pass_gate, "
    "dimensions{Position,Interpretation,Architecture,Judgement,Voice}, issues[]."
)


class DeltaRepairer(Protocol):
    async def repair(
        self,
        store: ProjectStore,
        *,
        issues: tuple[IssueRecord, ...],
    ) -> int: ...


@dataclass(frozen=True)
class AssemblyResult:
    chapter_ids: tuple[str, ...]
    project_revision: int
    manuscript_path: str = "manuscript/MANUSCRIPT.md"


@dataclass(frozen=True)
class FullBookRunResult:
    status: str
    project_revision: int
    issue_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class FinalizeResult:
    status: str
    project_revision: int


def _gate_status(snapshot: ProjectSnapshot, path: str) -> GateStatus:
    if path not in snapshot.artifacts:
        return GateStatus.PENDING
    record = GateRecord.model_validate_json(snapshot.read_text(path))
    return evaluate_gate_freshness(record, snapshot.artifacts)


def _architecture_chapters(snapshot: ProjectSnapshot) -> list[dict[str, object]]:
    path = "architecture/CHAPTER_FUNCTION_MAP.yaml"
    if path not in snapshot.artifacts:
        raise ValueError("chapter function map required before manuscript assembly")
    payload = yaml.safe_load(snapshot.read_text(path)) or {}
    chapters = payload.get("chapters") or []
    if not chapters:
        raise ValueError("chapter function map contains no chapters")
    return list(chapters)


def assemble_manuscript(store: ProjectStore) -> AssemblyResult:
    snapshot = store.snapshot()
    chapters = _architecture_chapters(snapshot)
    chapter_ids = tuple(str(chapter["chapter_id"]) for chapter in chapters)

    if _gate_status(snapshot, "gates/CONTROL_CHAPTER_PASS.json") != GateStatus.PASS:
        raise ValueError("control chapter is not currently approved")

    sections: list[str] = []
    manifest_chapters: list[dict[str, str]] = []
    for index, chapter in enumerate(chapters):
        chapter_id = str(chapter["chapter_id"])
        title = str(chapter.get("title") or chapter_id)
        draft_path = f"chapters/{chapter_id}/DRAFT.md"
        if draft_path not in snapshot.artifacts:
            raise ValueError(f"chapter draft missing: {chapter_id}")
        if index > 0 and chapter_approval_status(snapshot, chapter_id) != "PASS":
            raise ValueError(f"chapter {chapter_id} is not currently approved")
        draft = snapshot.read_text(draft_path).strip()
        sections.append(f"# {title}\n\n{draft}\n")
        manifest_chapters.append(
            {
                "chapter_id": chapter_id,
                "title": title,
                "draft_path": draft_path,
                "draft_sha256": snapshot.artifacts[draft_path].sha256,
            }
        )

    manuscript = "\n".join(sections).rstrip() + "\n"
    manifest = {
        "project_id": snapshot.state.project_id,
        "source_revision": snapshot.state.project_revision,
        "chapter_order": list(chapter_ids),
        "chapters": manifest_chapters,
    }
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={
            "manuscript/MANUSCRIPT.md": manuscript,
            "manuscript/MANIFEST.json": json.dumps(
                manifest, ensure_ascii=False, indent=2
            )
            + "\n",
        },
        actor="publisher",
        reason="deterministically assemble approved chapter drafts",
    )
    return AssemblyResult(chapter_ids=chapter_ids, project_revision=state.project_revision)


def _packet_issues(
    role_id: str,
    packet: ReviewPacket | AuthorialReview,
) -> list[tuple[str, ReviewIssue | str]]:
    issues = [(role_id, issue) for issue in packet.issues]
    if not packet.pass_gate and not issues:
        issues.append(
            (
                role_id,
                ReviewIssue(
                    severity="MAJOR",
                    code="FULL_BOOK_GATE_FAIL",
                    message=f"{role_id} failed full-book review without a structured issue",
                    repair_route=(
                        "RESEARCH_GAP" if role_id == "fact_reviewer" else "REPAIR"
                    ),
                ),
            )
        )
    return issues


def _registry_rows(snapshot: ProjectSnapshot, path: str) -> list[dict[str, object]]:
    if path not in snapshot.artifacts:
        return []
    rows: list[dict[str, object]] = []
    for line in snapshot.read_text(path).splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def run_publication_preflight(store: ProjectStore) -> dict[str, object]:
    snapshot = store.snapshot()
    failures: list[str] = []

    for path in sorted(snapshot.artifacts):
        if path.endswith("/ORPHAN_CLAIMS.json"):
            values = json.loads(snapshot.read_text(path))
            if values:
                failures.append(f"unresolved orphan claims: {path}")

    unresolved = [
        issue.issue_id
        for issue in iter_issues(store)
        if issue.status not in {"RESOLVED", "VERIFIED", "REJECTED"}
    ]
    if unresolved:
        failures.append("unresolved issues: " + ", ".join(unresolved))

    gaps_path = "research/RESEARCH_GAPS.md"
    if gaps_path in snapshot.artifacts:
        meaningful = [
            line.strip()
            for line in snapshot.read_text(gaps_path).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if meaningful:
            failures.append("research gaps remain open")

    registry_checks = {
        "research/QUOTE_REGISTRY.jsonl": {"VERIFIED", "APPROVED", "NOT_REQUIRED"},
        "research/FRESHNESS_REQUIREMENTS.jsonl": {"VERIFIED", "CURRENT", "NOT_REQUIRED"},
        "research/TERMINOLOGY.jsonl": {"VERIFIED", "APPROVED", "NOT_REQUIRED"},
    }
    for path, accepted in registry_checks.items():
        for row in _registry_rows(snapshot, path):
            status = str(row.get("status", "")).upper()
            if status not in accepted:
                failures.append(f"publication registry row not verified: {path}")
                break

    result: dict[str, object] = {
        "passed": not failures,
        "failures": failures,
        "checks": {
            "orphan_claims": not any("orphan claims" in item for item in failures),
            "issues": not any("unresolved issues" in item for item in failures),
            "research_gaps": not any("research gaps" in item for item in failures),
            "quote_provenance": not any("QUOTE_REGISTRY" in item for item in failures),
            "freshness": not any("FRESHNESS_REQUIREMENTS" in item for item in failures),
            "terminology": not any("TERMINOLOGY" in item for item in failures),
        },
    }
    snapshot = store.snapshot()
    store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={
            "publication/PREFLIGHT.json": json.dumps(
                result, ensure_ascii=False, indent=2
            )
            + "\n"
        },
        actor="publisher",
        reason="run deterministic publication preflight",
    )
    return result


class FullBookCoordinator:
    def __init__(
        self,
        runner: AgentRunner,
        *,
        delta_repairer: DeltaRepairer | None = None,
        blind_threshold: float = 90.0,
    ) -> None:
        self.runner = runner
        self.delta_repairer = delta_repairer
        self.blind_threshold = blind_threshold

    async def _full_audit(
        self,
        store: ProjectStore,
        *,
        label: str,
    ) -> tuple[dict[str, ReviewPacket | AuthorialReview], tuple[str, ...]]:
        snapshot = store.snapshot()

        async def ordinary(role_id: str) -> tuple[str, ReviewPacket]:
            response = await self.runner.run(
                AgentRequest(
                    role_id=role_id,
                    instruction=(
                        "Perform an independent full-book audit of the assembled manuscript. "
                        "Do not inspect other reviewer verdicts."
                    ),
                    context_markdown=build_context_pack(
                        snapshot,
                        role_id=role_id,
                        book_level=True,
                        extra_paths=("manuscript/MANUSCRIPT.md",),
                    ).render_markdown(),
                    task_id=f"{snapshot.state.project_id}:full-book:{label}:{role_id}",
                    output_contract=_REVIEW_CONTRACT,
                )
            )
            return role_id, parse_structured_output(response.final_content, ReviewPacket)

        async def authorial() -> tuple[str, AuthorialReview]:
            response = await self.runner.run(
                AgentRequest(
                    role_id="authorial_reviewer",
                    instruction="Apply the five-dimensional Authorial Presence hard gate to the full book.",
                    context_markdown=build_context_pack(
                        snapshot,
                        role_id="authorial_reviewer",
                        book_level=True,
                        extra_paths=("manuscript/MANUSCRIPT.md",),
                    ).render_markdown(),
                    task_id=f"{snapshot.state.project_id}:full-book:{label}:authorial",
                    output_contract=_AUTHORIAL_CONTRACT,
                )
            )
            return "authorial_reviewer", parse_structured_output(
                response.final_content, AuthorialReview
            )

        packets = dict(
            await asyncio.gather(
                ordinary("fact_reviewer"),
                ordinary("structural_reviewer"),
                ordinary("public_reader_reviewer"),
                authorial(),
            )
        )
        normalized: list[tuple[str, ReviewIssue | str]] = []
        for role_id, packet in packets.items():
            normalized.extend(_packet_issues(role_id, packet))
        issue_commit = commit_issues(
            store,
            scope="FULL_BOOK",
            cycle=1 if label == "FULL_AUDIT_01" else 2,
            issues=normalized,
        )
        snapshot = store.snapshot()
        store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={
                f"publication/{label}.json": json.dumps(
                    {
                        "passed": not normalized
                        and all(packet.pass_gate for packet in packets.values()),
                        "reviews": {
                            role_id: packet.model_dump(mode="json")
                            for role_id, packet in packets.items()
                        },
                        "issue_ids": list(issue_commit.issue_ids),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            },
            actor="editor",
            reason=f"commit {label} full-book audit",
        )
        return packets, issue_commit.issue_ids

    async def run(self, store: ProjectStore) -> FullBookRunResult:
        assemble_manuscript(store)
        first, first_issue_ids = await self._full_audit(store, label="FULL_AUDIT_01")
        first_passed = not first_issue_ids and all(
            packet.pass_gate for packet in first.values()
        )
        all_issue_ids = list(first_issue_ids)

        if not first_passed:
            if self.delta_repairer is None:
                return FullBookRunResult(
                    status="REPAIR_REQUIRED",
                    project_revision=store.snapshot().state.project_revision,
                    issue_ids=tuple(all_issue_ids),
                )
            issues = tuple(load_issue(store, issue_id) for issue_id in first_issue_ids)
            await self.delta_repairer.repair(store, issues=issues)
            assemble_manuscript(store)

        final, final_issue_ids = await self._full_audit(
            store, label="FINAL_FULL_AUDIT"
        )
        all_issue_ids.extend(final_issue_ids)
        final_passed = not final_issue_ids and all(
            packet.pass_gate for packet in final.values()
        )
        if not final_passed:
            return FullBookRunResult(
                status="REBUILD_REQUIRED",
                project_revision=store.snapshot().state.project_revision,
                issue_ids=tuple(all_issue_ids),
            )

        resolve_open_issues(store, scope="FULL_BOOK")
        preflight = run_publication_preflight(store)
        if not bool(preflight["passed"]):
            return FullBookRunResult(
                status="PREFLIGHT_BLOCKED",
                project_revision=store.snapshot().state.project_revision,
                issue_ids=tuple(all_issue_ids),
            )

        blind_snapshot = store.snapshot()
        blind_response = await self.runner.run(
            AgentRequest(
                role_id="blind_reviewer",
                instruction=(
                    "Fresh blind publication review. Judge only the candidate manuscript, Book "
                    "Charter and publication rubric; do not infer or request prior review history."
                ),
                context_markdown=build_context_pack(
                    blind_snapshot,
                    role_id="blind_reviewer",
                    book_level=True,
                    extra_paths=("manuscript/MANUSCRIPT.md",),
                ).render_markdown(),
                task_id=f"{blind_snapshot.state.project_id}:publication:blind",
                output_contract=_REVIEW_CONTRACT,
            )
        )
        blind = parse_structured_output(blind_response.final_content, ReviewPacket)

        authorial_snapshot = store.snapshot()
        authorial_response = await self.runner.run(
            AgentRequest(
                role_id="authorial_reviewer",
                instruction=(
                    "Final independent Authorial Presence publication gate. Do not use aggregate "
                    "quality score to compensate any failed dimension."
                ),
                context_markdown=build_context_pack(
                    authorial_snapshot,
                    role_id="authorial_reviewer",
                    book_level=True,
                    extra_paths=("manuscript/MANUSCRIPT.md",),
                ).render_markdown(),
                task_id=f"{authorial_snapshot.state.project_id}:publication:authorial",
                output_contract=_AUTHORIAL_CONTRACT,
            )
        )
        authorial = parse_structured_output(
            authorial_response.final_content, AuthorialReview
        )
        blind_passed = (
            blind.pass_gate
            and blind.score is not None
            and blind.score >= self.blind_threshold
        )
        snapshot = store.snapshot()
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={
                "publication/BLIND_REVIEW.json": json.dumps(
                    {
                        **blind.model_dump(mode="json"),
                        "threshold": self.blind_threshold,
                        "threshold_passed": blind_passed,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                "publication/FINAL_AUTHORIAL_REVIEW.json": (
                    json.dumps(
                        authorial.model_dump(mode="json"),
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                ),
            },
            actor="editor",
            reason="commit fresh blind and final authorial publication reviews",
        )
        if not blind_passed or not authorial.pass_gate:
            return FullBookRunResult(
                status="PUBLICATION_REVIEW_FAILED",
                project_revision=state.project_revision,
                issue_ids=tuple(all_issue_ids),
            )

        candidate_snapshot = store.snapshot()
        dependency_paths = [
            "manuscript/MANUSCRIPT.md",
            "manuscript/MANIFEST.json",
            "publication/FINAL_FULL_AUDIT.json",
            "publication/BLIND_REVIEW.json",
            "publication/FINAL_AUTHORIAL_REVIEW.json",
            "publication/PREFLIGHT.json",
        ]
        refs = [candidate_snapshot.artifacts[path] for path in dependency_paths]
        candidate = {
            "status": "AWAITING_AUTHOR_APPROVAL",
            "dependency_paths": dependency_paths,
            "input_fingerprint": dependency_fingerprint(refs),
        }
        state = store.commit(
            expected_revision=candidate_snapshot.state.project_revision,
            mutations={
                "publication/CANDIDATE.json": json.dumps(
                    candidate, ensure_ascii=False, indent=2
                )
                + "\n"
            },
            actor="editor",
            reason="freeze publication candidate awaiting human author approval",
        )
        return FullBookRunResult(
            status="AWAITING_AUTHOR_APPROVAL",
            project_revision=state.project_revision,
            issue_ids=tuple(all_issue_ids),
        )


def approve_author(
    store: ProjectStore,
    *,
    approved_by: str,
    note: str = "",
) -> int:
    if not approved_by.startswith("human:"):
        raise ValueError("AUTHOR_APPROVED must be attributed to an explicit human identity")
    snapshot = store.snapshot()
    required = [
        "manuscript/MANUSCRIPT.md",
        "publication/FINAL_FULL_AUDIT.json",
        "publication/BLIND_REVIEW.json",
        "publication/FINAL_AUTHORIAL_REVIEW.json",
        "publication/PREFLIGHT.json",
    ]
    missing = [path for path in required if path not in snapshot.artifacts]
    if missing:
        raise ValueError(f"publication candidate incomplete: {missing}")
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={
            "publication/AUTHOR_APPROVAL.md": (
                "# Author Approval\n\n"
                f"Approved by: {approved_by}\n\n"
                f"{note.strip()}\n"
            )
        },
        actor=approved_by,
        reason="record explicit human author approval note",
    )
    snapshot = store.snapshot()
    dependency_paths = [*required, "publication/AUTHOR_APPROVAL.md"]
    refs = [snapshot.artifacts[path] for path in dependency_paths]
    gate = GateRecord(
        gate="AUTHOR_APPROVED",
        status=GateStatus.PASS,
        dependency_paths=dependency_paths,
        input_fingerprint=dependency_fingerprint(refs),
        approved_by=approved_by,
    )
    state = store.commit(
        expected_revision=state.project_revision,
        mutations={
            "gates/AUTHOR_APPROVED.json": gate.model_dump_json(indent=2) + "\n"
        },
        actor=approved_by,
        reason="hash-bind explicit human author approval",
    )
    return state.project_revision


def finalize_publication(store: ProjectStore) -> FinalizeResult:
    snapshot = store.snapshot()
    if _gate_status(snapshot, "gates/AUTHOR_APPROVED.json") != GateStatus.PASS:
        raise ValueError("fresh human AUTHOR_APPROVED gate required")
    manuscript = snapshot.read_text("manuscript/MANUSCRIPT.md")
    source_sha = snapshot.artifacts["manuscript/MANUSCRIPT.md"].sha256
    state = store.commit(
        expected_revision=snapshot.state.project_revision,
        mutations={
            "publication/MANUSCRIPT.md": manuscript,
            "publication/MANIFEST.json": json.dumps(
                {
                    "project_id": snapshot.state.project_id,
                    "source_manuscript_sha256": source_sha,
                    "source_revision": snapshot.state.project_revision,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        },
        actor="publisher",
        reason="materialize deterministic publication package",
    )
    snapshot = store.snapshot()
    dependency_paths = [
        "manuscript/MANUSCRIPT.md",
        "publication/MANUSCRIPT.md",
        "publication/MANIFEST.json",
        "publication/FINAL_FULL_AUDIT.json",
        "publication/BLIND_REVIEW.json",
        "publication/FINAL_AUTHORIAL_REVIEW.json",
        "publication/PREFLIGHT.json",
        "gates/AUTHOR_APPROVED.json",
    ]
    refs = [snapshot.artifacts[path] for path in dependency_paths]
    gate = GateRecord(
        gate="PUBLICATION_READY",
        status=GateStatus.PASS,
        dependency_paths=dependency_paths,
        input_fingerprint=dependency_fingerprint(refs),
        approved_by="publisher",
    )
    state = store.commit(
        expected_revision=state.project_revision,
        mutations={
            "gates/PUBLICATION_READY.json": gate.model_dump_json(indent=2) + "\n"
        },
        actor="publisher",
        reason="hash-bind deterministic publication package",
    )
    return FinalizeResult(status="PUBLICATION_READY", project_revision=state.project_revision)


def publication_ready_status(snapshot: ProjectSnapshot) -> str:
    return _gate_status(snapshot, "gates/PUBLICATION_READY.json").value
