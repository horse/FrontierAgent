from __future__ import annotations

import json

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_runtime import AgentRequest, AgentRunner
from .chapter_pipeline import ChapterCoordinator
from .context import build_context_pack
from .control_chapter import ControlChapterCoordinator
from .issues import IssueRecord
from .research_gap import FocusedResearchGapResolver
from .store import ProjectStore
from .structured import parse_structured_output

_PLAN_CONTRACT = (
    "Return exactly one JSON object with actions[{chapter_id,issue_ids,directive}]. "
    "Every supplied issue_id must appear exactly once. Do not create new issues."
)


class DeltaRepairAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(pattern=r"^C\d{2,3}$")
    issue_ids: list[str] = Field(min_length=1)
    directive: str = Field(min_length=10)


class DeltaRepairPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[DeltaRepairAction] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_issue_routing(self) -> DeltaRepairPlan:
        routed = [issue_id for action in self.actions for issue_id in action.issue_ids]
        if len(routed) != len(set(routed)):
            raise ValueError("each full-book issue may be routed only once")
        return self


class EditorialDeltaRepairer:
    """Route full-book issues back through chapter-level editorial gates."""

    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner
        self.gap_resolver = FocusedResearchGapResolver(runner)

    async def repair(
        self,
        store: ProjectStore,
        *,
        issues: tuple[IssueRecord, ...],
    ) -> int:
        if not issues:
            return store.snapshot().state.project_revision
        snapshot = store.snapshot()
        context = build_context_pack(
            snapshot,
            role_id="editor",
            book_level=True,
            extra_paths=("manuscript/MANUSCRIPT.md", "architecture/CHAPTER_FUNCTION_MAP.yaml"),
        )
        response = await self.runner.run(
            AgentRequest(
                role_id="editor",
                instruction=(
                    "Route these full-book issues to the smallest responsible chapter. Do not rewrite "
                    "prose. Produce bounded revision directives that preserve supported claims and "
                    "send evidence gaps back to research when needed.\n\n"
                    + json.dumps(
                        [issue.model_dump(mode="json") for issue in issues],
                        ensure_ascii=False,
                    )
                ),
                context_markdown=context.render_markdown(),
                task_id=f"{snapshot.state.project_id}:delta-repair-plan",
                output_contract=_PLAN_CONTRACT,
            )
        )
        plan = parse_structured_output(response.final_content, DeltaRepairPlan)
        supplied = {issue.issue_id for issue in issues}
        routed = {issue_id for action in plan.actions for issue_id in action.issue_ids}
        if routed != supplied:
            missing = sorted(supplied - routed)
            unknown = sorted(routed - supplied)
            raise ValueError(
                f"delta repair routing mismatch; missing={missing}, unknown={unknown}"
            )

        chapter_map = yaml.safe_load(
            store.snapshot().read_text("architecture/CHAPTER_FUNCTION_MAP.yaml")
        ) or {}
        chapter_ids = [str(item["chapter_id"]) for item in chapter_map.get("chapters", [])]
        if not chapter_ids:
            raise ValueError("chapter function map contains no chapters")
        control_chapter_id = chapter_ids[0]
        known = set(chapter_ids)

        for action in plan.actions:
            if action.chapter_id not in known:
                raise ValueError(f"delta repair references unknown chapter: {action.chapter_id}")
            snapshot = store.snapshot()
            directive_path = f"chapters/{action.chapter_id}/REVISION_DIRECTIVE.md"
            directive = (
                f"# Revision Directive — {action.chapter_id}\n\n"
                f"Issue IDs: {', '.join(action.issue_ids)}\n\n"
                + action.directive.strip()
                + "\n"
            )
            store.commit(
                expected_revision=snapshot.state.project_revision,
                mutations={directive_path: directive},
                actor="editor",
                reason=f"route full-book audit issues to {action.chapter_id}",
            )

            if action.chapter_id == control_chapter_id:
                result = await ControlChapterCoordinator(self.runner).run(
                    store, chapter_id=action.chapter_id
                )
                if not result.passed:
                    raise ValueError(
                        f"control chapter delta repair failed: {action.chapter_id}"
                    )
            else:
                result = await ChapterCoordinator(
                    self.runner,
                    research_gap_resolver=self.gap_resolver,
                ).run(store, chapter_id=action.chapter_id)
                if result.status != "APPROVED":
                    raise ValueError(
                        f"chapter delta repair did not approve {action.chapter_id}: {result.status}"
                    )

            snapshot = store.snapshot()
            if directive_path in snapshot.artifacts:
                store.commit(
                    expected_revision=snapshot.state.project_revision,
                    mutations={directive_path: None},
                    actor="editor",
                    reason=f"clear consumed delta directive for {action.chapter_id}",
                )

        return store.snapshot().state.project_revision
