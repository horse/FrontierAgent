from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from .agent_runtime import AgentRequest, AgentRunner
from .context import build_context_pack
from .research_commit import commit_curated_research
from .research_models import CuratedResearch, ResearchPlan, ResearchReport, parse_structured_output
from .store import ProjectStore

_PLAN_CONTRACT = "Return exactly one JSON object matching ResearchPlan: focus, tasks[{task_id,question,objective,search_terms,required_source_types,counterexample_target}], stop_conditions."
_REPORT_CONTRACT = "Return exactly one JSON object matching ResearchReport. Every source candidate must distinguish discovered vs actually read. Record counterevidence, uncertainty and do_not_claim explicitly."
_CURATOR_CONTRACT = "Return exactly one JSON object matching CuratedResearch with local source/evidence keys. Evidence must point only to sources actually read and must include a precise locator."


@dataclass(frozen=True)
class ResearchRunResult:
    plan: ResearchPlan
    reports: tuple[ResearchReport, ...]
    curated: CuratedResearch
    project_revision: int


class ResearchCoordinator:
    def __init__(self, runner: AgentRunner, *, max_parallel: int = 4, web_policy: str = "search_only") -> None:
        self.runner = runner
        self.max_parallel = max(1, max_parallel)
        self.web_policy = web_policy

    async def run(self, store: ProjectStore, *, focus: str) -> ResearchRunResult:
        snapshot = store.snapshot()
        director_pack = build_context_pack(snapshot, role_id="research_director")
        director = await self.runner.run(
            AgentRequest(
                role_id="research_director",
                instruction=f"Design a bounded research sprint for this focus: {focus}. Include active counterexample tests and stop conditions.",
                context_markdown=director_pack.render_markdown(),
                task_id=f"{snapshot.state.project_id}:research-plan",
                output_contract=_PLAN_CONTRACT,
            )
        )
        plan = parse_structured_output(director.final_content, ResearchPlan)
        plan_doc = "# Research Plan\n\n```json\n" + plan.model_dump_json(indent=2) + "\n```\n"
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={"research/RESEARCH_PLAN.md": plan_doc},
            actor="research_director",
            reason="approve research plan candidate for sprint execution",
        )

        semaphore = asyncio.Semaphore(self.max_parallel)

        async def run_task(task) -> ResearchReport:
            async with semaphore:
                pack = build_context_pack(store.snapshot(), role_id="researcher")
                response = await self.runner.run(
                    AgentRequest(
                        role_id="researcher",
                        instruction="Research this task only:\n" + json.dumps(task.model_dump(), ensure_ascii=False),
                        context_markdown=pack.render_markdown(),
                        task_id=f"{state.project_id}:research:{task.task_id}",
                        web_policy=self.web_policy,
                        output_contract=_REPORT_CONTRACT,
                    )
                )
                report = parse_structured_output(response.final_content, ResearchReport)
                if report.task_id != task.task_id:
                    raise ValueError(f"research report task mismatch: {report.task_id} != {task.task_id}")
                return report

        reports = list(await asyncio.gather(*(run_task(task) for task in plan.tasks)))
        reports.sort(key=lambda report: report.task_id)
        curator_pack = build_context_pack(store.snapshot(), role_id="curator")
        curator_instruction = (
            "Normalize these independent research reports. Preserve disagreements and gaps; do not vote models into truth.\n"
            + json.dumps([report.model_dump() for report in reports], ensure_ascii=False)
        )
        curator = await self.runner.run(
            AgentRequest(
                role_id="curator",
                instruction=curator_instruction,
                context_markdown=curator_pack.render_markdown(),
                task_id=f"{state.project_id}:research-curate",
                output_contract=_CURATOR_CONTRACT,
            )
        )
        curated = parse_structured_output(curator.final_content, CuratedResearch)
        committed = commit_curated_research(
            store,
            curated,
            expected_revision=store.snapshot().state.project_revision,
            actor="curator",
        )
        return ResearchRunResult(plan=plan, reports=tuple(reports), curated=curated, project_revision=committed.project_revision)
