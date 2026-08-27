from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

from .agent_runtime import FrontierAgentRunner
from .roles import ensure_frontieragent_roles


@dataclass(frozen=True)
class RuntimeInfo:
    provider: str
    model: str
    tool_names: tuple[str, ...]


class StandaloneRuntime:
    """Scoped FrontierAgent runtime used by the standalone FrontierVSI CLI.

    FrontierVSI calls FrontierAgent's agent loop directly, so it does not need
    to bootstrap the scheduler or FrontierAgent's no-op OSS EventStore. The
    canonical book state remains owned by ProjectStore.
    """

    def __init__(self) -> None:
        self._registry_snapshot: dict[type, Any] | None = None
        self.runner: FrontierAgentRunner | None = None
        self.info: RuntimeInfo | None = None

    def __enter__(self) -> StandaloneRuntime:
        from frontier_agent.components.middleware.llm import (
            LLMMiddlewareChain,
            SummarizationMiddleware,
        )
        from frontier_agent.core.runtime import registry
        from frontier_agent.core.runtime.registries.agents import AgentRegistry
        from frontier_agent.core.runtime.resources.manager import ResourceManager
        from frontier_agent.infra.config import get_config
        from frontier_agent.infra.llm_adapter import create_llm
        from plugins.tools import get_builtin_tools

        if self._registry_snapshot is not None:
            raise RuntimeError("StandaloneRuntime already entered")

        self._registry_snapshot = registry.snapshot()
        try:
            config = get_config()
            agent_registry = AgentRegistry()
            registry.register(AgentRegistry, agent_registry)

            llm = create_llm(config)
            tools = dict(get_builtin_tools())
            registry.register(ResourceManager, ResourceManager(llm=llm, tools=tools))

            middleware = LLMMiddlewareChain()
            middleware.add(SummarizationMiddleware(threshold=80_000, keep_recent=10))
            registry.register(LLMMiddlewareChain, middleware)

            ensure_frontieragent_roles()
            self.runner = FrontierAgentRunner()
            provider = str(config.llm_provider)
            model = _configured_model(config, provider)
            self.info = RuntimeInfo(
                provider=provider,
                model=model,
                tool_names=tuple(sorted(tools)),
            )
        except Exception:
            registry.restore(self._registry_snapshot)
            self._registry_snapshot = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        from frontier_agent.core.runtime import registry

        if self._registry_snapshot is not None:
            registry.restore(self._registry_snapshot)
        self._registry_snapshot = None
        self.runner = None
        self.info = None


def _configured_model(config: Any, provider: str) -> str:
    attr = {
        "openai": "openai_model",
        "anthropic": "anthropic_model",
        "qwen": "qwen_model",
        "deepseek": "deepseek_model",
    }.get(provider, "openai_model")
    return str(getattr(config, attr, ""))
