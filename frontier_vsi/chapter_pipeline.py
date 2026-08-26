from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .agent_runtime import AgentRequest, AgentRunner
from .chapter_materials import build_chapter_materials
from .context import build_context_pack
from .editorial_models import (
    AuthorDraft,
    AuthorialReview,
    ClaimExtraction,
    ReviewIssue,
    ReviewPacket,
)
from .gates import dependency_fingerprint, evaluate_gate_freshness
from .issues import IssueRecord, commit_issues, load_issue
from .models import GateRecord, GateStatus
from .store import ProjectSnapshot, ProjectStore
from .structured import parse_structured_output

_AUTHOR_CONTRACT = (
    "Return exactly one JSON object: prose, provenance[], new_claim_candidates[]."
)
_CLAIM_CONTRACT = (
    "Return exactly one JSON object: claims[{text,claim_type,risk}], orphan_claims[]."
)
_REVIEW_CONTRACT = (
    "Return exactly one JSON object: pass_gate, score, "
    "issues[{severity,code,message,location,repair_route}]."
)
_AUTHORIAL_CONTRACT = (
    "Return exactly one JSON object: pass_gate, "
    "dimensions{Position,Interpretation,Architecture,Judgement,Voice}, issues[]."
)


class ResearchGapResolver(Protocol):
    async def resolve(
        self,
        store: ProjectStore,
        *,
        chapter_id: str,
        issues: tuple[IssueRecord, ...],
    ) -> int: ...


@dataclass(frozen=True)
class ChapterRunResult:
    chapter_id: str
    status: str
    cycles: int
    project_revision: int
    issue_ids: tuple[str, ...] = ()


def _gate_status(snapshot: ProjectSnapshot, path: str) -> GateStatus:
    if path not in snapshot.artifacts:
        return GateStatus.PENDING
    record = GateRecord.model_validate_json(snapshot.read_text(path))
    return evaluate_gate_freshness(record, snapshot.artifacts)


def chapter_approval_status(snapshot: ProjectSnapshot, chapter_id: str) -> str:
    return _gate_status(snapshot, f"chapters/{chapter_id}/APPROVAL.json").value


def _review_issues(
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
                    code="GATE_FAIL",
                    message=f"{role_id} failed the chapter gate without a structured issue",
                    repair_route=(
                        "RESEARCH_GAP" if role_id == "fact_reviewer" else "REPAIR"
                    ),
                ),
            )
        )
    return issues


def _claims_jsonl(claims: ClaimExtraction) -> str:
    rows = [item.model_dump(mode="json") for item in claims.claims]
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )


class ChapterCoordinator:
    def __init__(
        self,
        runner: AgentRunner,
        *,
        research_gap_resolver: ResearchGapResolver | None = None,
        max_cycles: int = 3,
        include_public_reader: bool = True,
    ) -> None:
        self.runner = runner
        self.research_gap_resolver = research_gap_resolver
        self.max_cycles = max(1, max_cycles)
        self.include_public_reader = include_public_reader

    async def run(self, store: ProjectStore, *, chapter_id: str) -> ChapterRunResult:
        prerequisite = _gate_status(store.snapshot(), "gates/CONTROL_CHAPTER_PASS.json")
        if prerequisite != GateStatus.PASS:
            return ChapterRunResult(
                chapter_id=chapter_id,
                status="BLOCKED",
                cycles=0,
                project_revision=store.snapshot().state.project_revision,
            )

        snapshot = store.snapshot()
        brief_path = f"chapters/{chapter_id}/BRIEF.md"
        evidence_path = f"chapters/{chapter_id}/EVIDENCE_PACKET.md"
        if brief_path not in snapshot.artifacts or evidence_path not in snapshot.artifacts:
            build_chapter_materials(store, chapter_id=chapter_id)

        seen_major: Counter[str] = Counter()
        all_issue_ids: list[str] = []
        previous_review_summary: str | None = None
        research_resolution: str | None = None

        for cycle in range(1, self.max_cycles + 1):
            extra_paths: list[str] = []
            draft_path = f"chapters/{chapter_id}/DRAFT.md"
            if draft_path in store.snapshot().artifacts:
                extra_paths.append(draft_path)
            if previous_review_summary:
                extra_paths.append(previous_review_summary)
            if research_resolution:
                extra_paths.append(research_resolution)

            author_pack = build_context_pack(
                store.snapshot(),
                role_id="author",
                chapter_id=chapter_id,
                extra_paths=tuple(extra_paths),
            )
            instruction = (
                "Write the chapter from its brief and evidence packet. This is a revision cycle; "
                "repair only documented issues and preserve supported claims."
                if cycle > 1
                else "Write the chapter from its brief and evidence packet. Use only supplied evidence."
            )
            author_response = await self.runner.run(
                AgentRequest(
                    role_id="author",
                    instruction=instruction,
                    context_markdown=author_pack.render_markdown(),
                    task_id=(
                        f"{store.snapshot().state.project_id}:chapter:{chapter_id}:author:{cycle}"
                    ),
                    output_contract=_AUTHOR_CONTRACT,
                )
            )
            draft = parse_structured_output(author_response.final_content, AuthorDraft)
            chapter_root = f"chapters/{chapter_id}"
            state = store.commit(
                expected_revision=store.snapshot().state.project_revision,
                mutations={
                    f"{chapter_root}/DRAFT.md": draft.prose.rstrip() + "\n",
                    f"{chapter_root}/PROVENANCE.json": json.dumps(
                        {
                            "mappings": draft.provenance,
                            "new_claim_candidates": draft.new_claim_candidates,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                },
                actor="author",
                reason=f"write {chapter_id} cycle {cycle}",
            )

            claim_pack = build_context_pack(
                store.snapshot(), role_id="claim_extractor", chapter_id=chapter_id
            )
            claim_response = await self.runner.run(
                AgentRequest(
                    role_id="claim_extractor",
                    instruction=(
                        "Extract every checkable claim. Mark unsupported or unlinked statements "
                        "as orphan_claims."
                    ),
                    context_markdown=claim_pack.render_markdown(),
                    task_id=f"{state.project_id}:chapter:{chapter_id}:claims:{cycle}",
                    output_contract=_CLAIM_CONTRACT,
                )
            )
            claims = parse_structured_output(claim_response.final_content, ClaimExtraction)
            store.commit(
                expected_revision=store.snapshot().state.project_revision,
                mutations={
                    f"{chapter_root}/CLAIMS.jsonl": _claims_jsonl(claims),
                    f"{chapter_root}/ORPHAN_CLAIMS.json": json.dumps(
                        claims.orphan_claims, ensure_ascii=False, indent=2
                    )
                    + "\n",
                },
                actor="claim_extractor",
                reason=f"extract claims from {chapter_id} cycle {cycle}",
            )

            review_snapshot = store.snapshot()

            async def ordinary_review(role_id: str) -> tuple[str, ReviewPacket]:
                pack = build_context_pack(
                    review_snapshot, role_id=role_id, chapter_id=chapter_id
                )
                response = await self.runner.run(
                    AgentRequest(
                        role_id=role_id,
                        instruction=(
                            "Independently review the current chapter. Do not rely on other "
                            "reviewer verdicts."
                        ),
                        context_markdown=pack.render_markdown(),
                        task_id=(
                            f"{state.project_id}:chapter:{chapter_id}:{role_id}:{cycle}"
                        ),
                        output_contract=_REVIEW_CONTRACT,
                    )
                )
                return role_id, parse_structured_output(
                    response.final_content, ReviewPacket
                )

            async def authorial_review() -> tuple[str, AuthorialReview]:
                pack = build_context_pack(
                    review_snapshot,
                    role_id="authorial_reviewer",
                    chapter_id=chapter_id,
                )
                response = await self.runner.run(
                    AgentRequest(
                        role_id="authorial_reviewer",
                        instruction=(
                            "Independently apply the five-dimensional Authorial Presence hard gate."
                        ),
                        context_markdown=pack.render_markdown(),
                        task_id=(
                            f"{state.project_id}:chapter:{chapter_id}:authorial_reviewer:{cycle}"
                        ),
                        output_contract=_AUTHORIAL_CONTRACT,
                    )
                )
                return "authorial_reviewer", parse_structured_output(
                    response.final_content, AuthorialReview
                )

            review_coroutines = [
                ordinary_review("fact_reviewer"),
                ordinary_review("structural_reviewer"),
                authorial_review(),
            ]
            if self.include_public_reader:
                review_coroutines.append(ordinary_review("public_reader_reviewer"))
            packets = dict(await asyncio.gather(*review_coroutines))

            normalized: list[tuple[str, ReviewIssue | str]] = []
            for orphan in claims.orphan_claims:
                normalized.append(
                    (
                        "claim_extractor",
                        ReviewIssue(
                            severity="CRITICAL",
                            code="ORPHAN_CLAIM",
                            message=orphan,
                            repair_route="RESEARCH_GAP",
                        ),
                    )
                )
            for role_id, packet in packets.items():
                normalized.extend(_review_issues(role_id, packet))

            review_summary_path = f"{chapter_root}/REVIEWS/cycle-{cycle:02d}.json"
            review_summary = {
                "cycle": cycle,
                "orphan_claims": claims.orphan_claims,
                "reviews": {
                    role_id: packet.model_dump(mode="json")
                    for role_id, packet in packets.items()
                },
            }
            store.commit(
                expected_revision=store.snapshot().state.project_revision,
                mutations={
                    review_summary_path: json.dumps(
                        review_summary, ensure_ascii=False, indent=2
                    )
                    + "\n"
                },
                actor="editor",
                reason=(
                    f"collect independent chapter reviews for {chapter_id} cycle {cycle}"
                ),
            )
            previous_review_summary = review_summary_path

            issue_commit = commit_issues(
                store,
                scope=chapter_id,
                cycle=cycle,
                issues=normalized,
            )
            cycle_issue_ids = list(issue_commit.issue_ids)
            all_issue_ids.extend(cycle_issue_ids)
            issue_records = tuple(
                load_issue(store, issue_id) for issue_id in cycle_issue_ids
            )

            for issue in issue_records:
                if issue.severity in {"MAJOR", "CRITICAL"}:
                    seen_major[issue.fingerprint] += 1
                    if seen_major[issue.fingerprint] >= 2:
                        self._write_state(
                            store,
                            chapter_id,
                            status="REBUILD_REQUIRED",
                            cycle=cycle,
                            issue_ids=all_issue_ids,
                        )
                        return ChapterRunResult(
                            chapter_id,
                            "REBUILD_REQUIRED",
                            cycle,
                            store.snapshot().state.project_revision,
                            tuple(all_issue_ids),
                        )

            gap_issues = tuple(
                issue for issue in issue_records if issue.repair_route == "RESEARCH_GAP"
            )
            if gap_issues:
                if self.research_gap_resolver is None:
                    self._write_state(
                        store,
                        chapter_id,
                        status="RESEARCH_GAP",
                        cycle=cycle,
                        issue_ids=all_issue_ids,
                    )
                    return ChapterRunResult(
                        chapter_id,
                        "RESEARCH_GAP",
                        cycle,
                        store.snapshot().state.project_revision,
                        tuple(all_issue_ids),
                    )
                await self.research_gap_resolver.resolve(
                    store, chapter_id=chapter_id, issues=gap_issues
                )
                research_resolution = (
                    f"chapters/{chapter_id}/RESEARCH_GAP_RESOLUTION.md"
                )
                if research_resolution not in store.snapshot().artifacts:
                    raise ValueError(
                        "research gap resolver must commit RESEARCH_GAP_RESOLUTION.md"
                    )
                continue

            if not issue_records and all(packet.pass_gate for packet in packets.values()):
                self._approve(
                    store,
                    chapter_id=chapter_id,
                    cycle=cycle,
                    review_summary_path=review_summary_path,
                )
                return ChapterRunResult(
                    chapter_id,
                    "APPROVED",
                    cycle,
                    store.snapshot().state.project_revision,
                    tuple(all_issue_ids),
                )

            if cycle == self.max_cycles:
                self._write_state(
                    store,
                    chapter_id,
                    status="REBUILD_REQUIRED",
                    cycle=cycle,
                    issue_ids=all_issue_ids,
                )
                return ChapterRunResult(
                    chapter_id,
                    "REBUILD_REQUIRED",
                    cycle,
                    store.snapshot().state.project_revision,
                    tuple(all_issue_ids),
                )

        raise AssertionError("unreachable")

    def _write_state(
        self,
        store: ProjectStore,
        chapter_id: str,
        *,
        status: str,
        cycle: int,
        issue_ids: list[str],
    ) -> None:
        snapshot = store.snapshot()
        store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={
                f"chapters/{chapter_id}/STATE.json": json.dumps(
                    {
                        "chapter_id": chapter_id,
                        "status": status,
                        "cycle": cycle,
                        "issue_ids": issue_ids,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            },
            actor="editor",
            reason=f"set {chapter_id} state to {status}",
        )

    def _approve(
        self,
        store: ProjectStore,
        *,
        chapter_id: str,
        cycle: int,
        review_summary_path: str,
    ) -> None:
        chapter_root = f"chapters/{chapter_id}"
        dependency_paths = [
            f"{chapter_root}/BRIEF.md",
            f"{chapter_root}/EVIDENCE_PACKET.md",
            f"{chapter_root}/DRAFT.md",
            f"{chapter_root}/PROVENANCE.json",
            f"{chapter_root}/CLAIMS.jsonl",
            review_summary_path,
            "architecture/CHAPTER_FUNCTION_MAP.yaml",
            "constitution/STYLE_PROFILE.yaml",
            "constitution/VOICE_SPEC.md",
            "gates/STYLE_LOCKED.json",
        ]
        snapshot = store.snapshot()
        refs = [snapshot.artifacts[path] for path in dependency_paths]
        record = GateRecord(
            gate=f"CHAPTER_APPROVED:{chapter_id}",
            status=GateStatus.PASS,
            dependency_paths=dependency_paths,
            input_fingerprint=dependency_fingerprint(refs),
            approved_by="editor",
        )
        store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={
                f"{chapter_root}/APPROVAL.json": record.model_dump_json(indent=2) + "\n",
                f"{chapter_root}/STATE.json": json.dumps(
                    {"chapter_id": chapter_id, "status": "APPROVED", "cycle": cycle},
                    indent=2,
                )
                + "\n",
            },
            actor="editor",
            reason=f"approve chapter {chapter_id}",
        )
