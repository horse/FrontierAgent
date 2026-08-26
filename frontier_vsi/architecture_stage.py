from __future__ import annotations

import json
from dataclasses import dataclass

import yaml

from .agent_runtime import AgentRequest, AgentRunner
from .context import build_context_pack
from .editorial_models import BookArchitecture, ReviewPacket
from .gates import dependency_fingerprint
from .research_models import parse_structured_output
from .store import ProjectStore

_ARCH_CONTRACT = (
    "Return exactly one JSON object: master_argument, excluded_but_important[], "
    "chapters[] with chapter_id,title,reader_before,chapter_question,cognitive_move,"
    "central_claim,chapter_function,required_claim_ids,anchor_cases,counterarguments,"
    "reader_after,handoff_to_next,word_budget."
)
_REVIEW_CONTRACT = (
    "Return exactly one JSON object: pass_gate boolean, optional score 0-100, issues[]."
)


@dataclass(frozen=True)
class ArchitectureRunResult:
    architecture: BookArchitecture
    locked: bool
    project_revision: int


class ArchitectureCoordinator:
    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    async def run(self, store: ProjectStore) -> ArchitectureRunResult:
        snapshot = store.snapshot()
        if "research/RESEARCH_SYNTHESIS.md" not in snapshot.artifacts:
            raise ValueError("research synthesis required before final architecture")

        pack = build_context_pack(snapshot, role_id="architect")
        response = await self.runner.run(
            AgentRequest(
                role_id="architect",
                instruction=(
                    "Re-outline after research. Design the shortest non-distorting cognitive route; "
                    "every chapter must have one unique job. Preserve contradictions and "
                    "excluded-but-important material."
                ),
                context_markdown=pack.render_markdown(),
                task_id=f"{snapshot.state.project_id}:architecture",
                output_contract=_ARCH_CONTRACT,
            )
        )
        architecture = parse_structured_output(response.final_content, BookArchitecture)

        challenge = await self.runner.run(
            AgentRequest(
                role_id="structural_reviewer",
                instruction=(
                    "Challenge this proposed architecture for duplicate chapter jobs, topic-container "
                    "chapters, hidden contradictions, weak evidence load, accidental chronology, "
                    "unnecessary transitions, and impossible word budgets.\n"
                    + architecture.model_dump_json(indent=2)
                ),
                context_markdown=build_context_pack(
                    snapshot,
                    role_id="structural_reviewer",
                    chapter_id="C00",
                    book_level=True,
                ).render_markdown(),
                task_id=f"{snapshot.state.project_id}:architecture-challenge",
                output_contract=_REVIEW_CONTRACT,
            )
        )
        verdict = parse_structured_output(challenge.final_content, ReviewPacket)
        if not verdict.pass_gate:
            raise ValueError("architecture structural challenge failed")

        outline = "\n".join(
            f"{index + 1}. {chapter.chapter_id} — {chapter.title}: {chapter.chapter_function}"
            for index, chapter in enumerate(architecture.chapters)
        ) + "\n"
        function_map = {"chapters": [chapter.model_dump() for chapter in architecture.chapters]}
        budgets = {
            "total": sum(chapter.word_budget for chapter in architecture.chapters),
            "chapters": {
                chapter.chapter_id: chapter.word_budget for chapter in architecture.chapters
            },
        }
        mutations = {
            "architecture/MASTER_ARGUMENT.md": (
                "# Master Argument\n\n"
                + architecture.master_argument.strip()
                + "\n\n## Excluded but important\n"
                + "\n".join(f"- {item}" for item in architecture.excluded_but_important)
                + "\n"
            ),
            "architecture/OUTLINE.md": "# Outline\n\n" + outline,
            "architecture/CHAPTER_FUNCTION_MAP.yaml": yaml.safe_dump(
                function_map, allow_unicode=True, sort_keys=False
            ),
            "architecture/WORD_BUDGET.yaml": yaml.safe_dump(
                budgets, allow_unicode=True, sort_keys=False
            ),
            "architecture/STRUCTURAL_CHALLENGE.json": (
                json.dumps(verdict.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
            ),
        }
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations=mutations,
            actor="architect",
            reason="commit challenged post-research book architecture",
        )

        locked_snapshot = store.snapshot()
        dependency_paths = (
            "research/RESEARCH_SYNTHESIS.md",
            "research/CONTRADICTIONS.md",
            "research/RESEARCH_GAPS.md",
            "architecture/MASTER_ARGUMENT.md",
            "architecture/OUTLINE.md",
            "architecture/CHAPTER_FUNCTION_MAP.yaml",
            "architecture/WORD_BUDGET.yaml",
            "architecture/STRUCTURAL_CHALLENGE.json",
        )
        refs = [
            locked_snapshot.artifacts[path]
            for path in dependency_paths
            if path in locked_snapshot.artifacts
        ]
        lock = {
            "gate": "ARCHITECTURE_LOCKED",
            "status": "PASS",
            "dependency_paths": [ref.path for ref in refs],
            "input_fingerprint": dependency_fingerprint(refs),
            "approved_by": "editor",
        }
        state = store.commit(
            expected_revision=state.project_revision,
            mutations={
                "gates/ARCHITECTURE_LOCKED.json": (
                    json.dumps(lock, ensure_ascii=False, indent=2) + "\n"
                ),
                "architecture/LOCK.json": json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
            },
            actor="editor",
            reason="hash-bind architecture lock",
        )
        return ArchitectureRunResult(
            architecture=architecture,
            locked=True,
            project_revision=state.project_revision,
        )
