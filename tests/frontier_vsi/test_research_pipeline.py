import asyncio
from pathlib import Path

from frontier_vsi.agent_runtime import AgentRequest, AgentResponse
from frontier_vsi.layout import initialize_project
from frontier_vsi.research import ResearchCoordinator
from frontier_vsi.store import ProjectStore


class FakeRunner:
    def __init__(self) -> None:
        self.roles: list[str] = []

    async def run(self, request: AgentRequest) -> AgentResponse:
        self.roles.append(request.role_id)
        if request.role_id == "research_director":
            content = '{"focus":"core","tasks":[{"task_id":"T1","question":"q1","objective":"o1"},{"task_id":"T2","question":"q2","objective":"o2"}],"stop_conditions":["counterexample checked"]}'
        elif request.role_id == "researcher":
            task_id = "T1" if "T1" in request.instruction else "T2"
            content = '{"task_id":"%s","question":"q","findings":[],"counterevidence":[],"disagreements":[],"uncertainties":[],"recommended_claims":[],"do_not_claim":[],"followup_questions":[],"source_candidates":[]}' % task_id
        else:
            content = '{"sources":[],"evidence":[],"claims":[],"contradictions":[],"research_gaps":[],"synthesis":"Nothing yet."}'
        return AgentResponse(role_id=request.role_id, final_content=content)


def test_research_pipeline_fans_out_then_curates(tmp_path: Path) -> None:
    root = tmp_path / "book"
    initialize_project(root, project_id="VSI-R", title="Research")
    store = ProjectStore(root)
    store.commit(
        expected_revision=0,
        mutations={"constitution/BOOK_CHARTER.md":"# Charter\n","constitution/AUTHORIAL_CONSTITUTION.md":"# Position\n"},
        actor="test", reason="seed",
    )
    runner = FakeRunner()
    result = asyncio.run(ResearchCoordinator(runner, max_parallel=2).run(store, focus="core"))
    assert [r.task_id for r in result.reports] == ["T1", "T2"]
    assert runner.roles[0] == "research_director"
    assert runner.roles[-1] == "curator"
    assert runner.roles.count("researcher") == 2
