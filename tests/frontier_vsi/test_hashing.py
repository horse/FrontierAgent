from frontier_vsi.canonical_json import canonical_json_bytes, sha256_bytes


def test_canonical_json_hash_is_independent_of_mapping_order() -> None:
    left = {"b": 2, "a": {"y": 2, "x": 1}}
    right = {"a": {"x": 1, "y": 2}, "b": 2}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_bytes(canonical_json_bytes(left)) == sha256_bytes(canonical_json_bytes(right))
