import pytest

from frontier_vsi.style import StyleProfile


def test_style_profile_keeps_book_voice_variable() -> None:
    profile = StyleProfile(
        language="zh-CN",
        register="1990s-2000s-magazine-nonfiction",
        sentence_length="long",
        paragraph_length="long",
        sample_paths=["samples/user_01.md"],
        prohibited_patterns=["AI summary voice"],
    )
    assert profile.language == "zh-CN"
    assert profile.prose_register == "1990s-2000s-magazine-nonfiction"
    assert profile.sample_paths == ["samples/user_01.md"]


def test_style_profile_rejects_empty_language() -> None:
    with pytest.raises(ValueError):
        StyleProfile(language="", register="public")
