from pathlib import Path

import pytest

from frontier_vsi.context import ContextBuildError, build_context_pack
from frontier_vsi.layout import initialize_project
from frontier_vsi.store import ProjectStore


def _seed(root: Path) -> ProjectStore:
    initialize_project(root, project_id="VSI-CTX", title="Context Book")
    store = ProjectStore(root)
    store.commit(
        expected_revision=0,
        mutations={
            "constitution/BOOK_CHARTER.md": "# Charter\nPromise.\n",
            "constitution/AUTHORIAL_CONSTITUTION.md": "# Position\nJudgement.\n",
            "constitution/STYLE_PROFILE.yaml": "language: zh-CN\nregister: public-nonfiction\n",
            "architecture/MASTER_ARGUMENT.md": "# Argument\nA -> B.\n",
            "architecture/OUTLINE.md": "# Outline\n1. One\n",
            "chapters/C01/BRIEF.md": "# Brief\nExplain one move.\n",
            "chapters/C01/EVIDENCE_PACKET.md": "# Evidence\nSRC-1.\n",
        },
        actor="test",
        reason="seed",
    )
    return store


def test_author_context_is_deterministic_and_scoped(tmp_path: Path) -> None:
    store = _seed(tmp_path / "book")
    snapshot = store.snapshot()
    first = build_context_pack(snapshot, role_id="author", chapter_id="C01")
    second = build_context_pack(snapshot, role_id="author", chapter_id="C01")

    assert first.pack_hash == second.pack_hash
    assert "chapters/C01/BRIEF.md" in first.artifacts
    assert "chapters/C01/EVIDENCE_PACKET.md" in first.artifacts
    assert "architecture/MASTER_ARGUMENT.md" in first.artifacts
    assert "research/SOURCE_REGISTRY.jsonl" not in first.artifacts
    assert first.render_markdown().startswith("# FrontierVSI Context Pack")


def test_context_hash_changes_when_dependency_changes(tmp_path: Path) -> None:
    store = _seed(tmp_path / "book")
    before = build_context_pack(store.snapshot(), role_id="author", chapter_id="C01")
    rev = store.snapshot().state.project_revision
    store.commit(
        expected_revision=rev,
        mutations={"chapters/C01/BRIEF.md": "# Brief\nChanged move.\n"},
        actor="editor",
        reason="change brief",
    )
    after = build_context_pack(store.snapshot(), role_id="author", chapter_id="C01")
    assert before.pack_hash != after.pack_hash


def test_author_context_requires_chapter_id(tmp_path: Path) -> None:
    store = _seed(tmp_path / "book")
    with pytest.raises(ContextBuildError, match="chapter_id"):
        build_context_pack(store.snapshot(), role_id="author")
