from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from .agent_runtime import AgentRequest, AgentRunner
from .chapter_materials import build_chapter_materials
from .context import build_context_pack
from .editorial_models import AuthorDraft, AuthorialReview, ClaimExtraction, ReviewPacket
from .gates import dependency_fingerprint
from .structured import parse_structured_output
from .store import ProjectStore

_AUTHOR_CONTRACT = (
    "Return one JSON object with prose string, "
    "provenance[{span,claim_ids,evidence_ids}], new_claim_candidates[]."
)
_CLAIM_CONTRACT = (
    "Return one JSON object with claims[{text,claim_type,risk}], orphan_claims[]."
)
_REVIEW_CONTRACT = (
    "Return one JSON object: pass_gate boolean, optional score 0-100, issues[]."
)
_AUTHORIAL_CONTRACT = (
    "Return one JSON object: pass_gate, dimensions with exactly "
    "Position,Interpretation,Architecture,Judgement,Voice booleans, issues[]."
)


@dataclass(frozen=True)
class ControlChapterResult:
    chapter_id: str
    passed: bool
    project_revision: int


class ControlChapterCoordinator:
    def __init__(self, runner: AgentRunner, *, include_public_reader: bool = True) -> None:
        self.runner = runner
        self.include_public_reader = include_public_reader

    async def run(self, store: ProjectStore, *, chapter_id: str) -> ControlChapterResult:
        snapshot = store.snapshot()
        root = f"chapters/{chapter_id}"
        if "constitution/STYLE_LOCK.md" not in snapshot.artifacts:
            raise ValueError("control chapter prerequisite missing: constitution/STYLE_LOCK.md")

        brief_path = f"{root}/BRIEF.md"
        evidence_path = f"{root}/EVIDENCE_PACKET.md"
        if brief_path not in snapshot.artifacts or evidence_path not in snapshot.artifacts:
            build_chapter_materials(store, chapter_id=chapter_id)
            snapshot = store.snapshot()

        author_response = await self.runner.run(
            AgentRequest(
                role_id="author",
                instruction=(
                    "Draft the control chapter. Return prose plus provenance sidecar. "
                    "Do not invent unsupported research conclusions."
                ),
                context_markdown=build_context_pack(
                    snapshot, role_id="author", chapter_id=chapter_id
                ).render_markdown(),
                task_id=f"{snapshot.state.project_id}:control:{chapter_id}:draft",
                output_contract=_AUTHOR_CONTRACT,
            )
        )
        draft = parse_structured_output(author_response.final_content, AuthorDraft)
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={
                f"{root}/DRAFT.md": draft.prose.rstrip() + "\n",
                f"{root}/PROVENANCE.json": json.dumps(
                    {
                        "chapter": chapter_id,
                        "mappings": draft.provenance,
                        "new_claim_candidates": draft.new_claim_candidates,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            },
            actor="author",
            reason=f"draft control chapter {chapter_id}",
        )

        claim_response = await self.runner.run(
            AgentRequest(
                role_id="claim_extractor",
                instruction=(
                    "Extract every checkable/high-risk claim and flag orphan factual claims not "
                    "covered by the Author provenance sidecar."
                ),
                context_markdown=build_context_pack(
                    store.snapshot(), role_id="claim_extractor", chapter_id=chapter_id
                ).render_markdown(),
                task_id=f"{state.project_id}:control:{chapter_id}:claims",
                output_contract=_CLAIM_CONTRACT,
            )
        )
        claims = parse_structured_output(claim_response.final_content, ClaimExtraction)
        claims_jsonl = "\n".join(
            json.dumps(claim.model_dump(), ensure_ascii=False, sort_keys=True)
            for claim in claims.claims
        )
        if claims_jsonl:
            claims_jsonl += "\n"
        state = store.commit(
            expected_revision=state.project_revision,
            mutations={
                f"{root}/CLAIMS.jsonl": claims_jsonl,
                f"{root}/ORPHAN_CLAIMS.json": json.dumps(
                    claims.orphan_claims, ensure_ascii=False, indent=2
                )
                + "\n",
            },
            actor="claim_extractor",
            reason=f"extract claims for control chapter {chapter_id}",
        )
        review_snapshot = store.snapshot()

        async def ordinary_review(role_id: str) -> tuple[str, ReviewPacket]:
            response = await self.runner.run(
                AgentRequest(
                    role_id=role_id,
                    instruction=(
                        "Independently review the control chapter. Do not inspect other reviewers. "
                        "Report concrete issues and hard failures."
                    ),
                    context_markdown=build_context_pack(
                        review_snapshot, role_id=role_id, chapter_id=chapter_id
                    ).render_markdown(),
                    task_id=f"{state.project_id}:control:{chapter_id}:{role_id}",
                    output_contract=_REVIEW_CONTRACT,
                )
            )
            return role_id, parse_structured_output(response.final_content, ReviewPacket)

        async def authorial_review() -> tuple[str, AuthorialReview]:
            response = await self.runner.run(
                AgentRequest(
                    role_id="authorial_reviewer",
                    instruction=(
                        "Independently apply the five-dimensional Authorial Presence hard gate."
                    ),
                    context_markdown=build_context_pack(
                        review_snapshot,
                        role_id="authorial_reviewer",
                        chapter_id=chapter_id,
                    ).render_markdown(),
                    task_id=f"{state.project_id}:control:{chapter_id}:authorial",
                    output_contract=_AUTHORIAL_CONTRACT,
                )
            )
            return (
                "authorial_reviewer",
                parse_structured_output(response.final_content, AuthorialReview),
            )

        review_coroutines = [
            ordinary_review("fact_reviewer"),
            ordinary_review("structural_reviewer"),
            authorial_review(),
        ]
        if self.include_public_reader:
            review_coroutines.append(ordinary_review("public_reader_reviewer"))
        reviews = dict(await asyncio.gather(*review_coroutines))

        passed = not claims.orphan_claims and all(
            review.pass_gate for review in reviews.values()
        )
        summary = {
            "chapter_id": chapter_id,
            "passed": passed,
            "orphan_claims": claims.orphan_claims,
            "reviews": {
                role_id: review.model_dump(mode="json")
                for role_id, review in reviews.items()
            },
        }
        mutations: dict[str, str] = {
            f"{root}/REVIEWS/summary.json": json.dumps(
                summary, ensure_ascii=False, indent=2
            )
            + "\n"
        }
        if passed:
            dependency_paths = (
                f"{root}/DRAFT.md",
                f"{root}/CLAIMS.jsonl",
                "architecture/CHAPTER_FUNCTION_MAP.yaml",
                "constitution/STYLE_PROFILE.yaml",
                "constitution/VOICE_SPEC.md",
                "gates/STYLE_LOCKED.json",
            )
            refs = [
                review_snapshot.artifacts[path]
                for path in dependency_paths
                if path in review_snapshot.artifacts
            ]
            mutations["gates/CONTROL_CHAPTER_PASS.json"] = json.dumps(
                {
                    "gate": "CONTROL_CHAPTER_PASS",
                    "status": "PASS",
                    "dependency_paths": [ref.path for ref in refs],
                    "input_fingerprint": dependency_fingerprint(refs),
                    "approved_by": "editor",
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"

        final_state = store.commit(
            expected_revision=state.project_revision,
            mutations=mutations,
            actor="editor",
            reason=f"normalize control chapter review {chapter_id}",
        )
        return ControlChapterResult(
            chapter_id=chapter_id,
            passed=passed,
            project_revision=final_state.project_revision,
        )
