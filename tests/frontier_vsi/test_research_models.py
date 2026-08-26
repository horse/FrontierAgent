import pytest

from frontier_vsi.research_models import ResearchPlan, ResearchReport, parse_structured_output


def test_structured_output_parser_accepts_json_fence() -> None:
    plan = parse_structured_output(
        '```json\n{"focus":"x","tasks":[{"task_id":"T1","question":"q","objective":"o"}],"stop_conditions":["counterexample checked"]}\n```',
        ResearchPlan,
    )
    assert plan.tasks[0].task_id == "T1"


def test_structured_output_parser_rejects_unstructured_prose() -> None:
    with pytest.raises(ValueError, match="JSON"):
        parse_structured_output("Here are my findings...", ResearchReport)
