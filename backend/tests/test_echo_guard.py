"""The assistant's own voice must not be answered as a user turn.

Cases are taken from the real device transcript of 2026-08-05, where the
phone speaker fed the mic and the chat filled with lines the user never said
("I'm not afraid to ask anything, no" right after Farry offered "feel free to
ask me anything"), each of which the model then answered — a self-talk loop.
"""

from __future__ import annotations

from app.ai.echo_guard import looks_like_echo

FARRY_ASKED = "Got it. Feel free to ask me anything, I'm here to help. What would you like to know?"
FARRY_EXPLORE = "That's great! Anything on your mind right now that you'd like to explore or talk about?"


def test_garbled_echo_of_the_last_line_is_caught() -> None:
    assert looks_like_echo(
        "I'm not afraid to ask anything, no.", [FARRY_ASKED]
    )


def test_echo_of_an_older_line_is_still_caught() -> None:
    # The guard keeps a couple of lines: an echo can land a turn late.
    assert looks_like_echo("ask me anything", [FARRY_EXPLORE, FARRY_ASKED])


def test_a_real_question_is_not_an_echo() -> None:
    assert not looks_like_echo("what is the time in Dubai", [FARRY_ASKED])


def test_a_real_answer_reusing_a_word_is_not_an_echo() -> None:
    # "anything" alone must not condemn a turn — the overlap has to dominate.
    assert not looks_like_echo(
        "yes, book me a taxi to the airport", [FARRY_ASKED]
    )


def test_short_confirmations_survive() -> None:
    # "yes"/"no"/"ok" are stopwords: nothing meaningful overlaps, so a real
    # confirmation is never swallowed even right after the assistant spoke.
    assert not looks_like_echo("yes", [FARRY_ASKED])
    assert not looks_like_echo("ok", [FARRY_ASKED])


def test_long_fluent_speech_is_never_an_echo() -> None:
    # Above the word cap we trust the user, whatever they repeat back.
    long_turn = (
        "feel free to ask me anything I am here to help what would you like "
        "to know about the weather in Dubai tomorrow morning please"
    )
    assert not looks_like_echo(long_turn, [FARRY_ASKED])


def test_no_assistant_history_means_no_echo() -> None:
    assert not looks_like_echo("open harbor", [])


def test_non_latin_scripts_are_handled() -> None:
    # Hindi/Arabic transcripts must tokenize (the regex is unicode-aware) —
    # an Arabic echo is caught, an unrelated Arabic question is not.
    said = ["هل تريد أن أساعدك في شيء آخر"]
    assert looks_like_echo("تريد أساعدك شيء", said)
    assert not looks_like_echo("كم الساعة الآن في دبي", said)
