from __future__ import annotations

from dataclasses import dataclass

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .agent_runtime import AgentRequest, AgentRunner
from .editorial_models import ReviewPacket
from .gates import dependency_fingerprint
from .methodology import load_method_bundle
from .models import GateRecord, GateStatus
from .store import ProjectStore
from .structured import parse_structured_output

_COMMISSION_CONTRACT = (
    "Return exactly one JSON object with book_charter_md, authorial_constitution_md, "
    "provisional_outline_md, source_policy_md, style_profile object, and voice_spec_md. "
    "style_profile must include language and register."
)
_FRAMING_REVIEW_CONTRACT = (
    "Return exactly one JSON object: pass_gate boolean, score 0-100, issues[]."
)


class CommissionPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    book_charter_md: str = Field(min_length=20)
    authorial_constitution_md: str = Field(min_length=20)
    provisional_outline_md: str = Field(min_length=20)
    source_policy_md: str = Field(min_length=20)
    style_profile: dict[str, object]
    voice_spec_md: str = Field(min_length=20)

    @model_validator(mode="after")
    def validate_style_profile(self) -> CommissionPackage:
        language = str(self.style_profile.get("language", "")).strip()
        register = str(self.style_profile.get("register", "")).strip()
        if not language or not register:
            raise ValueError("style_profile requires language and register")
        return self


@dataclass(frozen=True)
class CommissionResult:
    passed: bool
    project_revision: int


def _sample_context(store: ProjectStore, *, max_chars: int = 40_000) -> str:
    snapshot = store.snapshot()
    paths = [
        path
        for path in sorted(snapshot.artifacts)
        if path.startswith("samples/") and path.endswith((".md", ".txt"))
    ]
    chunks: list[str] = []
    used = 0
    for path in paths:
        text = snapshot.read_text(path)
        remaining = max_chars - used
        if remaining <= 0:
            break
        clipped = text[:remaining]
        chunks.append(f"## Sample: {path}\n\n{clipped}")
        used += len(clipped)
    return "\n\n".join(chunks)


def _publication_rubric() -> str:
    method = load_method_bundle()
    for resource in method.resources:
        if resource.name == "REVIEW_RUBRIC.md":
            return resource.content.rstrip() + "\n"
    raise RuntimeError("REVIEW_RUBRIC.md missing from method bundle")


class CommissionCoordinator:
    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    async def run(self, store: ProjectStore, *, brief: str) -> CommissionResult:
        if not brief.strip():
            raise ValueError("commission brief must not be empty")
        snapshot = store.snapshot()
        samples = _sample_context(store)
        context = "# Commission Brief\n\n" + brief.strip() + "\n"
        if samples:
            context += "\n# User Style Samples\n\n" + samples + "\n"

        response = await self.runner.run(
            AgentRequest(
                role_id="editor",
                instruction=(
                    "Commission this VSI-style nonfiction book. Fix the central promise, reader, "
                    "scope boundaries, authorial stance, provisional cognitive route, source policy, "
                    "and a book-specific voice configuration. Samples are style evidence, never a "
                    "phrase bank. The outline is provisional and must be rebuilt after research."
                ),
                context_markdown=context,
                task_id=f"{snapshot.state.project_id}:commission",
                output_contract=_COMMISSION_CONTRACT,
            )
        )
        package = parse_structured_output(response.final_content, CommissionPackage)
        style_yaml = yaml.safe_dump(
            package.style_profile,
            allow_unicode=True,
            sort_keys=False,
        )
        mutations = {
            "constitution/BOOK_CHARTER.md": package.book_charter_md.rstrip() + "\n",
            "constitution/AUTHORIAL_CONSTITUTION.md": (
                package.authorial_constitution_md.rstrip() + "\n"
            ),
            "constitution/STYLE_PROFILE.yaml": style_yaml,
            "constitution/VOICE_SPEC.md": package.voice_spec_md.rstrip() + "\n",
            "architecture/PROVISIONAL_OUTLINE.md": (
                package.provisional_outline_md.rstrip() + "\n"
            ),
            "research/SOURCE_POLICY.md": package.source_policy_md.rstrip() + "\n",
            "publication/PUBLICATION_RUBRIC.md": _publication_rubric(),
        }
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations=mutations,
            actor="editor",
            reason="commit book commissioning candidate",
        )

        framed = store.snapshot()
        review_response = await self.runner.run(
            AgentRequest(
                role_id="structural_reviewer",
                instruction=(
                    "Audit the commissioning package only. Fail if the promise is diffuse, the "
                    "reader is undefined, scope boundaries are missing, or the provisional outline "
                    "is already pretending to be a final researched architecture."
                ),
                context_markdown=(
                    framed.read_text("constitution/BOOK_CHARTER.md")
                    + "\n\n"
                    + framed.read_text("architecture/PROVISIONAL_OUTLINE.md")
                ),
                task_id=f"{state.project_id}:commission-review",
                output_contract=_FRAMING_REVIEW_CONTRACT,
            )
        )
        verdict = parse_structured_output(review_response.final_content, ReviewPacket)
        review_path = "commission/FRAMING_REVIEW.json"
        state = store.commit(
            expected_revision=store.snapshot().state.project_revision,
            mutations={review_path: verdict.model_dump_json(indent=2) + "\n"},
            actor="editor",
            reason="record independent framing review",
        )
        if not verdict.pass_gate:
            return CommissionResult(passed=False, project_revision=state.project_revision)

        snapshot = store.snapshot()
        dependency_paths = [*mutations, review_path]
        refs = [snapshot.artifacts[path] for path in dependency_paths]
        gate = GateRecord(
            gate="FRAMING_READY",
            status=GateStatus.PASS,
            dependency_paths=dependency_paths,
            input_fingerprint=dependency_fingerprint(refs),
            approved_by="editor",
        )
        state = store.commit(
            expected_revision=snapshot.state.project_revision,
            mutations={
                "gates/FRAMING_READY.json": gate.model_dump_json(indent=2) + "\n"
            },
            actor="editor",
            reason="hash-bind framing-ready commissioning package",
        )
        return CommissionResult(passed=True, project_revision=state.project_revision)
