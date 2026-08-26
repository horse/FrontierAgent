from __future__ import annotations

import json
from dataclasses import dataclass

from .agent_runtime import AgentRequest, AgentRunner
from .context import build_context_pack
from .editorial_models import ReviewPacket
from .gates import dependency_fingerprint
from .structured import parse_structured_output
from .store import ProjectStore

_SAMPLE_KINDS = ("opening", "core_explanation", "high_risk")
_REVIEW_CONTRACT = "Return one JSON object: pass_gate boolean, score 0-100, issues[]."


@dataclass(frozen=True)
class StyleCalibrationResult:
    sample_paths: tuple[str, ...]
    locked: bool
    project_revision: int


class StyleCalibrationCoordinator:
    def __init__(self, runner: AgentRunner) -> None:
        self.runner = runner

    async def run(self, store: ProjectStore) -> StyleCalibrationResult:
        snapshot = store.snapshot()
        required = (
            "constitution/STYLE_PROFILE.yaml",
            "constitution/VOICE_SPEC.md",
            "architecture/MASTER_ARGUMENT.md",
        )
        missing = [path for path in required if path not in snapshot.artifacts]
        if missing:
            raise ValueError(f"style calibration missing prerequisites: {missing}")

        samples: dict[str, str] = {}
        reviews: dict[str, object] = {}
        for kind in _SAMPLE_KINDS:
            author_pack = build_context_pack(
                store.snapshot(), role_id="author", book_level=True
            )
            response = await self.runner.run(
                AgentRequest(
                    role_id="author",
                    instruction=(
                        f"Write a short {kind.replace('_', ' ')} calibration sample using real book "
                        "subject matter. This is a style test, not canonical manuscript prose."
                    ),
                    context_markdown=author_pack.render_markdown(),
                    task_id=f"{snapshot.state.project_id}:style:{kind}",
                )
            )
            if not response.final_content.strip():
                raise ValueError(f"empty style calibration sample: {kind}")

            reviewer_pack = build_context_pack(
                store.snapshot(), role_id="authorial_reviewer", book_level=True
            )
            review_response = await self.runner.run(
                AgentRequest(
                    role_id="authorial_reviewer",
                    instruction=(
                        f"Independently review this {kind} calibration sample against Style Profile, "
                        "Voice Spec and the five Authorial Presence dimensions.\n\n"
                        + response.final_content
                    ),
                    context_markdown=reviewer_pack.render_markdown(),
                    task_id=f"{snapshot.state.project_id}:style-review:{kind}",
                    output_contract=_REVIEW_CONTRACT,
                )
            )
            verdict = parse_structured_output(review_response.final_content, ReviewPacket)
            if not verdict.pass_gate:
                raise ValueError(f"style calibration failed: {kind}")

            samples[f"style/calibration/{kind}.md"] = response.final_content.rstrip() + "\n"
            reviews[kind] = verdict.model_dump(mode="json")

        state = store.commit(
            expected_revision=store.snapshot().state.project_revision,
            mutations={
                **samples,
                "style/calibration/REVIEWS.json": (
                    json.dumps(reviews, ensure_ascii=False, indent=2) + "\n"
                ),
            },
            actor="editor",
            reason="commit three-part style calibration",
        )

        calibrated = store.snapshot()
        dependency_paths = (
            "constitution/STYLE_PROFILE.yaml",
            "constitution/VOICE_SPEC.md",
            "style/calibration/opening.md",
            "style/calibration/core_explanation.md",
            "style/calibration/high_risk.md",
            "style/calibration/REVIEWS.json",
        )
        refs = [calibrated.artifacts[path] for path in dependency_paths]
        lock = {
            "gate": "STYLE_LOCKED",
            "status": "PASS",
            "dependency_paths": [ref.path for ref in refs],
            "input_fingerprint": dependency_fingerprint(refs),
            "approved_by": "editor",
        }
        state = store.commit(
            expected_revision=state.project_revision,
            mutations={
                "gates/STYLE_LOCKED.json": json.dumps(lock, ensure_ascii=False, indent=2)
                + "\n",
                "constitution/STYLE_LOCK.md": (
                    "# Style Lock\n\n"
                    "Status: LOCKED\n"
                    f"Fingerprint: {lock['input_fingerprint']}\n"
                    "Required calibration samples: opening, core explanation, high risk.\n"
                ),
            },
            actor="editor",
            reason="hash-bind book-specific style lock",
        )
        return StyleCalibrationResult(
            sample_paths=tuple(samples),
            locked=True,
            project_revision=state.project_revision,
        )
