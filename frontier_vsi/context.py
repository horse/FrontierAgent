from __future__ import annotations

from dataclasses import dataclass

from .canonical_json import canonical_json_bytes, sha256_bytes
from .methodology import load_method_bundle
from .store import ProjectSnapshot


class ContextBuildError(ValueError):
    pass


@dataclass(frozen=True)
class ContextArtifact:
    path: str
    sha256: str
    content: str


@dataclass(frozen=True)
class ContextPack:
    project_id: str
    project_revision: int
    role_id: str
    chapter_id: str | None
    method_bundle_hash: str
    artifacts: dict[str, ContextArtifact]
    pack_hash: str

    def render_markdown(self) -> str:
        lines = [
            "# FrontierVSI Context Pack",
            f"role: {self.role_id}",
            f"project: {self.project_id}",
            f"revision: {self.project_revision}",
            f"method_bundle: {self.method_bundle_hash}",
            f"context_hash: {self.pack_hash}",
        ]
        if self.chapter_id:
            lines.append(f"chapter: {self.chapter_id}")
        for path in sorted(self.artifacts):
            item = self.artifacts[path]
            lines.append(
                f"\n## Artifact: {path}\nsha256: {item.sha256}\n\n{item.content.rstrip()}"
            )
        return "\n".join(lines) + "\n"


_BASE = (
    "constitution/BOOK_CHARTER.md",
    "constitution/AUTHORIAL_CONSTITUTION.md",
)
_ROLE_PATHS: dict[str, tuple[str, ...]] = {
    "editor": (*_BASE, "architecture/MASTER_ARGUMENT.md", "architecture/OUTLINE.md"),
    "research_director": (
        *_BASE,
        "research/RESEARCH_PLAN.md",
        "architecture/PROVISIONAL_OUTLINE.md",
    ),
    "researcher": (*_BASE, "research/RESEARCH_PLAN.md", "research/SOURCE_POLICY.md"),
    "curator": (
        *_BASE,
        "research/RESEARCH_PLAN.md",
        "research/CONTRADICTIONS.md",
        "research/RESEARCH_GAPS.md",
    ),
    "architect": (
        *_BASE,
        "research/RESEARCH_SYNTHESIS.md",
        "research/CONTRADICTIONS.md",
        "research/RESEARCH_GAPS.md",
    ),
    "author": (
        *_BASE,
        "constitution/STYLE_PROFILE.yaml",
        "constitution/VOICE_SPEC.md",
        "constitution/VOICE_ANCHORS.md",
        "architecture/MASTER_ARGUMENT.md",
        "architecture/OUTLINE.md",
        "architecture/CHAPTER_FUNCTION_MAP.yaml",
    ),
    "claim_extractor": (*_BASE, "architecture/MASTER_ARGUMENT.md"),
    "fact_reviewer": (*_BASE, "research/RESEARCH_SYNTHESIS.md"),
    "structural_reviewer": (
        *_BASE,
        "architecture/MASTER_ARGUMENT.md",
        "architecture/OUTLINE.md",
    ),
    "authorial_reviewer": (
        *_BASE,
        "constitution/STYLE_PROFILE.yaml",
        "constitution/VOICE_SPEC.md",
    ),
    "public_reader_reviewer": ("constitution/BOOK_CHARTER.md",),
    "blind_reviewer": (
        "constitution/BOOK_CHARTER.md",
        "publication/PUBLICATION_RUBRIC.md",
    ),
}
_CHAPTER_ROLES = {
    "author",
    "claim_extractor",
    "fact_reviewer",
    "structural_reviewer",
    "authorial_reviewer",
    "public_reader_reviewer",
}


def build_context_pack(
    snapshot: ProjectSnapshot,
    *,
    role_id: str,
    chapter_id: str | None = None,
    extra_paths: tuple[str, ...] = (),
    book_level: bool = False,
) -> ContextPack:
    if role_id not in _ROLE_PATHS:
        raise ContextBuildError(f"unknown FrontierVSI role: {role_id}")
    if role_id in _CHAPTER_ROLES and not chapter_id and not book_level:
        raise ContextBuildError(f"chapter_id is required for role {role_id}")

    paths = list(_ROLE_PATHS[role_id])
    if chapter_id:
        chapter_root = f"chapters/{chapter_id}"
        if role_id == "author":
            paths += [f"{chapter_root}/BRIEF.md", f"{chapter_root}/EVIDENCE_PACKET.md"]
        elif role_id == "claim_extractor":
            paths += [f"{chapter_root}/DRAFT.md"]
        else:
            paths += [f"{chapter_root}/DRAFT.md", f"{chapter_root}/CLAIMS.jsonl"]
    paths.extend(extra_paths)

    artifacts: dict[str, ContextArtifact] = {}
    for path in dict.fromkeys(paths):
        ref = snapshot.artifacts.get(path)
        if ref is None:
            continue
        try:
            content = snapshot.read_text(path)
        except UnicodeDecodeError as exc:
            raise ContextBuildError(f"context artifact must be UTF-8 text: {path}") from exc
        artifacts[path] = ContextArtifact(path=path, sha256=ref.sha256, content=content)

    if role_id == "author" and chapter_id:
        required_paths = (
            f"chapters/{chapter_id}/BRIEF.md",
            f"chapters/{chapter_id}/EVIDENCE_PACKET.md",
        )
        for required in required_paths:
            if required not in artifacts:
                raise ContextBuildError(f"required author context artifact missing: {required}")

    method = load_method_bundle()
    manifest = {
        "project_id": snapshot.state.project_id,
        "project_revision": snapshot.state.project_revision,
        "role_id": role_id,
        "chapter_id": chapter_id,
        "method_bundle_hash": method.bundle_hash,
        "artifacts": [
            {"path": path, "sha256": artifacts[path].sha256}
            for path in sorted(artifacts)
        ],
    }
    return ContextPack(
        project_id=snapshot.state.project_id,
        project_revision=snapshot.state.project_revision,
        role_id=role_id,
        chapter_id=chapter_id,
        method_bundle_hash=method.bundle_hash,
        artifacts=artifacts,
        pack_hash=sha256_bytes(canonical_json_bytes(manifest)),
    )
