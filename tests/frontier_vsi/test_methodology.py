from frontier_vsi.methodology import load_method_bundle


def test_method_bundle_is_versioned_and_hashed() -> None:
    bundle = load_method_bundle()
    assert bundle.version == "1.1"
    assert len(bundle.resources) >= 6
    assert all(len(item.sha256) == 64 for item in bundle.resources)
    assert bundle.bundle_hash == load_method_bundle().bundle_hash
    assert "Authorial Presence" in bundle.render_markdown()
