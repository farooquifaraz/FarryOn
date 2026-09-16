"""Affective-dialog control tags never reach the chat or the speaker."""

from app.ai.gemini import strip_affect_tags


def test_tags_and_labels_are_removed_and_words_kept() -> None:
    raw = "emotion_user calm<ctrl95>emotion_model warm<ctrl95> Hello! How can I help?"
    assert strip_affect_tags(raw) == "Hello! How can I help?"


def test_a_bare_control_token_goes_too() -> None:
    assert strip_affect_tags("<ctrl100>नमस्ते, बताइए") == "नमस्ते, बताइए"


def test_plain_text_is_untouched() -> None:
    assert strip_affect_tags("I feel an emotion today") == "I feel an emotion today"
    assert strip_affect_tags("") == ""


def test_a_tag_split_across_deltas_is_caught_once_complete() -> None:
    # The buffer accumulates raw deltas; only what is EMITTED is filtered,
    # so a tag that arrives in two pieces is whole by the time it is judged.
    buf = "emotion_user ca"
    assert strip_affect_tags(buf) == ""
    buf += "lm<ctrl95> Hi"
    assert strip_affect_tags(buf) == "Hi"
