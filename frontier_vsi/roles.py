from __future__ import annotations

from dataclasses import dataclass

_WEB_TOOLS = ("web_search", "web_fetch", "download_file")
_READ_TOOLS = ("read_file", "grep_search", "glob_search")


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    display_name: str
    system_prompt: str
    allowed_tools: tuple[str, ...]
    temperature: float = 0.2
    max_tokens: int = 8192


ROLE_SPECS: dict[str, RoleSpec] = {
    "editor": RoleSpec("editor", "Editor-in-Chief", "Govern the book. Decide routing and gates; do not invent evidence or silently rewrite canonical prose.", _READ_TOOLS),
    "research_director": RoleSpec("research_director", "Research Director", "Turn the book problem into bounded research questions, counterexample tests and explicit stop conditions. Return structured output only when requested.", _READ_TOOLS),
    "researcher": RoleSpec("researcher", "Source Researcher", "Research one bounded question. Separate discovery from reading, record locators, counterevidence, uncertainty and do-not-claim boundaries. Never write book prose.", (*_READ_TOOLS, *_WEB_TOOLS)),
    "curator": RoleSpec("curator", "Knowledge Curator", "Normalize research reports into sources, evidence, claims, contradictions and gaps. Never strengthen a claim beyond its evidence.", _READ_TOOLS),
    "architect": RoleSpec("architect", "Book Architect", "Design the shortest non-distorting cognitive route. Give every chapter one irreplaceable job and preserve counterevidence.", _READ_TOOLS),
    "author": RoleSpec("author", "Author", "Write canonical public nonfiction in this book's locked voice. Use only supplied evidence; do not invent research conclusions or workflow language.", ("read_file",), temperature=0.35, max_tokens=16384),
    "claim_extractor": RoleSpec("claim_extractor", "Claim Extractor", "Extract checkable claims from prose without editing it. Return structured output.", ("read_file",)),
    "fact_reviewer": RoleSpec("fact_reviewer", "Fact Reviewer", "Independently audit claims against evidence and provenance. Fail critical factual or quotation errors.", _READ_TOOLS),
    "structural_reviewer": RoleSpec("structural_reviewer", "Structural Reviewer", "Independently audit chapter function, argument movement, repetition and handoff. Do not line-edit prose.", _READ_TOOLS),
    "authorial_reviewer": RoleSpec("authorial_reviewer", "Authorial Presence Reviewer", "Independently judge Position, Interpretation, Architecture, Judgement and Voice. This gate cannot be offset by aggregate score.", _READ_TOOLS),
    "public_reader_reviewer": RoleSpec("public_reader_reviewer", "Public Reader Reviewer", "Test intelligibility for an intelligent non-specialist reader: explanation order, jargon load, examples and hidden prerequisites.", ()),
    "blind_reviewer": RoleSpec("blind_reviewer", "Fresh Blind Reviewer", "Judge the final candidate without prior review history, issue dispositions or scores. Apply the fixed publication rubric and hard failures.", _READ_TOOLS),
}


def tools_for_role(role_id: str, *, web_policy: str = "off") -> tuple[str, ...]:
    if role_id not in ROLE_SPECS:
        raise KeyError(role_id)
    tools = ROLE_SPECS[role_id].allowed_tools
    if web_policy not in {"off", "search_only"}:
        raise ValueError(f"unsupported web policy: {web_policy}")
    if web_policy == "off":
        return tuple(tool for tool in tools if tool not in _WEB_TOOLS)
    return tools


def ensure_frontieragent_roles() -> None:
    """Register typed FrontierVSI roles into the active FrontierAgent runtime."""
    from frontier_agent.core.runtime import registry
    from frontier_agent.core.runtime.registries.agents import AgentRegistry
    from frontier_agent.models.agent_definition import AgentDefinition

    agents = registry.get(AgentRegistry)
    for spec in ROLE_SPECS.values():
        agents.register(
            AgentDefinition(
                role_id=spec.role_id,
                display_name=spec.display_name,
                system_prompt=spec.system_prompt,
                allowed_tools=list(spec.allowed_tools),
                temperature=spec.temperature,
                max_tokens=spec.max_tokens,
                description=spec.system_prompt,
            )
        )
