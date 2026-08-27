from __future__ import annotations

from .agent_runtime import AgentRunner
from .issues import IssueRecord
from .research import ResearchCoordinator
from .store import ProjectStore


class FocusedResearchGapResolver:
    """Route a chapter evidence gap through the normal typed research system."""

    def __init__(
        self,
        runner: AgentRunner,
        *,
        max_parallel: int = 3,
        web_policy: str = "search_only",
    ) -> None:
        self.coordinator = ResearchCoordinator(
            runner,
            max_parallel=max_parallel,
            web_policy=web_policy,
        )

    async def resolve(
        self,
        store: ProjectStore,
        *,
        chapter_id: str,
        issues: tuple[IssueRecord, ...],
    ) -> int:
        if not issues:
            return store.snapshot().state.project_revision
        focus = "Resolve only these chapter research gaps without broadening scope:\n" + "\n".join(
            f"- {issue.issue_id} [{issue.code}] {issue.message}" for issue in issues
        )
        result = await self.coordinator.run(store, focus=focus)
        unresolved = result.curated.research_gaps
        if unresolved:
            raise ValueError(
                "focused research returned unresolved gaps: " + "; ".join(unresolved)
            )
        snapshot = store.snapshot()
        summary = (
            f"# Research Gap Resolution — {chapter_id}\n\n"
            "Resolved issues:\n"
            + "\n".join(f"- {issue.issue_id}: {issue.message}" for issue in issues)
            + "\n\n## Curated synthesis\n\n"
            + result.curated.synthesis.strip()
            + "\n"
        )
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={f"chapters/{chapter_id}/RESEARCH_GAP_RESOLUTION.md": summary},
            actor="research_director",
            reason=f"commit focused research-gap resolution for {chapter_id}",
        )
        return state.project_revision
