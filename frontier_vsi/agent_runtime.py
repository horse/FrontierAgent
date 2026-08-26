from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .roles import ROLE_SPECS, ensure_frontieragent_roles, tools_for_role


@dataclass(frozen=True)
class AgentRequest:
    role_id: str
    instruction: str
    context_markdown: str = ""
    task_id: str = "frontiervsi"
    web_policy: str = "off"
    max_turns: int = 30
    output_contract: str = ""


@dataclass(frozen=True)
class AgentResponse:
    role_id: str
    final_content: str
    turns_used: int = 0
    tool_calls_count: int = 0
    metadata: dict[str, object] = field(default_factory=dict)


class AgentRunner(Protocol):
    async def run(self, request: AgentRequest) -> AgentResponse: ...


class FrontierAgentRunner:
    """Thin adapter over FrontierAgent's domain-neutral agent loop."""

    async def run(self, request: AgentRequest) -> AgentResponse:
        if request.role_id not in ROLE_SPECS:
            raise KeyError(request.role_id)
        ensure_frontieragent_roles()
        from frontier_agent.core.loop_types import LoopConfig, LoopPolicy
        from frontier_agent.core.runtime import registry
        from frontier_agent.core.runtime.loop.agent_loop import run_agent_loop
        from frontier_agent.core.runtime.resources.manager import ResourceManager

        spec = ROLE_SPECS[request.role_id]
        manager = registry.get(ResourceManager)
        allowed = set(tools_for_role(request.role_id, web_policy=request.web_policy))
        tools = [tool for name, tool in manager.all_tools.items() if name in allowed]
        system_prompt = spec.system_prompt
        if request.output_contract:
            system_prompt += "\n\nOUTPUT CONTRACT:\n" + request.output_contract
        user_message = request.context_markdown
        if user_message:
            user_message += "\n\n"
        user_message += "# Assigned task\n" + request.instruction
        result = await run_agent_loop(
            system_prompt=system_prompt,
            user_message=user_message,
            llm=manager.get_llm(request.role_id),
            tools=tools,
            config=LoopConfig(
                max_turns=request.max_turns,
                task_id=request.task_id,
                role_id=request.role_id,
                loop_policy=LoopPolicy(no_tool_behavior="stop"),
            ),
        )
        return AgentResponse(
            role_id=request.role_id,
            final_content=result.final_content,
            turns_used=result.turns_used,
            tool_calls_count=result.tool_calls_count,
            metadata={str(k): v for k, v in result.metadata.items()},
        )
