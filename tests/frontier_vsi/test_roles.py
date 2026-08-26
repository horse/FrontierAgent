from frontier_vsi.roles import ROLE_SPECS, tools_for_role


def test_author_and_reviewers_cannot_write_or_browse() -> None:
    forbidden = {"write_file", "create_file", "file_editor_create", "web_search", "web_fetch"}
    for role in ("author", "fact_reviewer", "structural_reviewer", "authorial_reviewer"):
        assert forbidden.isdisjoint(tools_for_role(role, web_policy="search_only"))


def test_researcher_web_policy_is_explicit() -> None:
    assert "web_search" not in tools_for_role("researcher", web_policy="off")
    tools = tools_for_role("researcher", web_policy="search_only")
    assert "web_search" in tools and "web_fetch" in tools
    assert "write_file" not in tools
    assert set(ROLE_SPECS) >= {"editor", "research_director", "researcher", "curator", "author"}
