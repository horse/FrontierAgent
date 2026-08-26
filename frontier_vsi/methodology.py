from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

from .canonical_json import canonical_json_bytes, sha256_bytes

METHOD_VERSION = "1.1"
_METHOD_FILES = (
    "VSI_CORE.md",
    "EVIDENCE_PROTOCOL.md",
    "AUTHORIAL_PRESENCE.md",
    "REVIEW_RUBRIC.md",
    "CHAPTER_PROTOCOL.md",
    "PUBLICATION_GATE.md",
    "STYLE_PROTOCOL.md",
)


@dataclass(frozen=True)
class MethodResource:
    name: str
    content: str
    sha256: str


@dataclass(frozen=True)
class MethodBundle:
    version: str
    resources: tuple[MethodResource, ...]
    bundle_hash: str

    def render_markdown(self) -> str:
        sections = [f"# FrontierVSI Method Bundle v{self.version}"]
        for item in self.resources:
            sections.append(f"<!-- {item.name} sha256={item.sha256} -->\n{item.content.strip()}")
        return "\n\n".join(sections) + "\n"


def load_method_bundle() -> MethodBundle:
    root = resources.files("frontier_vsi").joinpath("resources", "method")
    loaded: list[MethodResource] = []
    for name in _METHOD_FILES:
        content = root.joinpath(name).read_text(encoding="utf-8")
        loaded.append(MethodResource(name=name, content=content, sha256=sha256_bytes(content.encode())))
    manifest = {
        "version": METHOD_VERSION,
        "resources": [{"name": item.name, "sha256": item.sha256} for item in loaded],
    }
    return MethodBundle(
        version=METHOD_VERSION,
        resources=tuple(loaded),
        bundle_hash=sha256_bytes(canonical_json_bytes(manifest)),
    )
