from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .agent_runtime import AgentRequest, AgentResponse, AgentRunner
from .canonical_json import canonical_json_bytes, sha256_bytes
from .methodology import load_method_bundle
from .models import RunManifest
from .runtime import RuntimeInfo
from .store import ProjectStore

_CONTEXT_HASH = re.compile(r"^context_hash:\s*([0-9a-f]{64})$", re.MULTILINE)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temp.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _append_jsonl(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(canonical_json_bytes(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


@dataclass
class RunTracker:
    root: Path
    run_dir: Path
    manifest: RunManifest
    start_artifacts: dict[str, str]
    runtime_info: RuntimeInfo | None = None
    sequence: int = 0
    _candidate_refs: list[str] = field(default_factory=list)

    @classmethod
    def start(
        cls,
        store: ProjectStore,
        *,
        stage: str,
        command: str,
        request_id: str | None,
        runtime_info: RuntimeInfo | None = None,
    ) -> RunTracker:
        snapshot = store.snapshot()
        now = datetime.now(UTC)
        run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}"
        run_dir = store.layout.runs_dir / run_id
        method = load_method_bundle()
        manifest = RunManifest(
            run_id=run_id,
            request_id=request_id,
            project_id=snapshot.state.project_id,
            project_revision_at_start=snapshot.state.project_revision,
            stage=stage,
            command=command,
            upstream_frontieragent_sha=os.environ.get("FRONTIERVSI_UPSTREAM_SHA"),
            frontiervsi_version=__version__,
            models_by_role={},
            prompt_hashes={},
            method_resource_hashes={
                item.name: item.sha256 for item in method.resources
            },
            context_pack_hashes={},
            input_artifact_refs=[
                f"{path}@{ref.sha256}" for path, ref in sorted(snapshot.artifacts.items())
            ],
        )
        tracker = cls(
            root=store.layout.root,
            run_dir=run_dir,
            manifest=manifest,
            start_artifacts={
                path: ref.sha256 for path, ref in snapshot.artifacts.items()
            },
            runtime_info=runtime_info,
        )
        tracker._persist_manifest()
        tracker.progress("RUN_STARTED", {"stage": stage})
        return tracker

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    def progress(self, event: str, data: dict[str, object] | None = None) -> None:
        _append_jsonl(
            self.run_dir / "progress.jsonl",
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "data": data or {},
            },
        )

    def record_request(self, request: AgentRequest) -> int:
        self.sequence += 1
        key = f"{self.sequence:03d}:{request.role_id}"
        prompt_payload = {
            "role_id": request.role_id,
            "instruction": request.instruction,
            "output_contract": request.output_contract,
            "web_policy": request.web_policy,
            "max_turns": request.max_turns,
        }
        prompt_hash = sha256_bytes(canonical_json_bytes(prompt_payload))
        match = _CONTEXT_HASH.search(request.context_markdown)
        context_hash = (
            match.group(1)
            if match
            else sha256_bytes(request.context_markdown.encode("utf-8"))
        )
        prompts = dict(self.manifest.prompt_hashes)
        prompts[key] = prompt_hash
        contexts = dict(self.manifest.context_pack_hashes)
        contexts[key] = context_hash
        models = dict(self.manifest.models_by_role)
        if self.runtime_info is not None:
            models.setdefault(request.role_id, self.runtime_info.model)
        self.manifest = self.manifest.model_copy(
            update={
                "prompt_hashes": prompts,
                "context_pack_hashes": contexts,
                "models_by_role": models,
            }
        )
        self._persist_manifest()
        self.progress(
            "AGENT_REQUEST",
            {
                "sequence": self.sequence,
                "role_id": request.role_id,
                "task_id": request.task_id,
                "prompt_hash": prompt_hash,
                "context_hash": context_hash,
            },
        )
        return self.sequence

    def record_response(self, sequence: int, response: AgentResponse) -> None:
        relative = f"candidates/{sequence:03d}-{response.role_id}.txt"
        path = self.run_dir / relative
        _write_atomic(path, response.final_content.encode("utf-8"))
        ref = f"runs/{self.run_id}/{relative}"
        self._candidate_refs.append(ref)
        self.manifest = self.manifest.model_copy(
            update={"candidate_output_refs": list(self._candidate_refs)}
        )
        self._persist_manifest()
        self.progress(
            "AGENT_RESPONSE",
            {
                "sequence": sequence,
                "role_id": response.role_id,
                "turns_used": response.turns_used,
                "tool_calls_count": response.tool_calls_count,
                "candidate_ref": ref,
            },
        )

    def complete(self, store: ProjectStore, result: dict[str, object]) -> None:
        snapshot = store.snapshot()
        changed = [
            f"{path}@{ref.sha256}"
            for path, ref in sorted(snapshot.artifacts.items())
            if self.start_artifacts.get(path) != ref.sha256
        ]
        _write_atomic(
            self.run_dir / "result.json",
            canonical_json_bytes(result) + b"\n",
        )
        self.manifest = self.manifest.model_copy(
            update={
                "status": "SUCCEEDED",
                "finished_at": datetime.now(UTC),
                "committed_artifact_refs": changed,
                "project_revision_after_commit": snapshot.state.project_revision,
            }
        )
        self._persist_manifest()
        self.progress(
            "RUN_SUCCEEDED",
            {"project_revision": snapshot.state.project_revision},
        )

    def fail(self, exc: BaseException) -> None:
        self.manifest = self.manifest.model_copy(
            update={
                "status": "FAILED",
                "finished_at": datetime.now(UTC),
                "error_class": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        self._persist_manifest()
        self.progress(
            "RUN_FAILED",
            {"error_class": type(exc).__name__, "error": str(exc)},
        )

    def _persist_manifest(self) -> None:
        _write_atomic(
            self.run_dir / "manifest.json",
            canonical_json_bytes(self.manifest.model_dump(mode="json")) + b"\n",
        )


class RecordingRunner:
    def __init__(self, delegate: AgentRunner, tracker: RunTracker) -> None:
        self.delegate = delegate
        self.tracker = tracker

    async def run(self, request: AgentRequest) -> AgentResponse:
        sequence = self.tracker.record_request(request)
        response = await self.delegate.run(request)
        self.tracker.record_response(sequence, response)
        return response
