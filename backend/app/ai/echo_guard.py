"""Detect a 'user turn' that is really the assistant's own voice coming back.

Provider-independent by design: it works on the two normalized transcript
streams every gateway already produces, so OpenAI, Gemini, and any future
adapter share one implementation.

The client mutes its mic while the assistant's audio plays, which is the
primary defence. This is the backstop for what leaks through anyway — a
speaker louder than the echo canceller, a device whose playback starts late,
a headset that feeds the room. What leaks does not arrive verbatim: the mic
hears a muffled, clipped version and the transcriber writes something close
but wrong ("feel free to ask me anything" came back as "I'm not afraid to ask
anything, no" — device-proven 2026-08-05). So we compare word OVERLAP, not
equality, and only for short turns: a long, fluent sentence is a real person
talking, whatever it echoes.
"""

from __future__ import annotations

import re

# Words that carry no identity — an overlap made only of these means nothing.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by can do does for from had has have he her his
    i if in is it its me my no not of on or our so that the their them then
    there these they this to too us was we were what when where which who will
    with would you your yes ok okay hmm uh um
    """.split()
)

# Above this many words we treat the turn as real speech regardless of overlap:
# echoes that survive the mic gate are short fragments, not paragraphs, and a
# false positive here would silently swallow a genuine question.
_MAX_ECHO_WORDS = 12

# Share of the user's meaningful words that must appear in what the assistant
# just said. High on purpose — the cost of a false positive (ignoring a real
# user) is worse than the cost of a miss (one more echo turn).
_OVERLAP_THRESHOLD = 0.6


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[^\W\d_]+", text.lower(), flags=re.UNICODE)
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


def looks_like_echo(user_text: str, assistant_texts: list[str]) -> bool:
    """Whether ``user_text`` looks like the assistant's own recent speech.

    Args:
        user_text: The transcript just attributed to the user.
        assistant_texts: The assistant's recent utterances, newest first.

    Returns:
        True when the turn is short and most of its meaningful words came from
        what the assistant just said.
    """
    user_words = _content_words(user_text)
    if not user_words or len(user_words) > _MAX_ECHO_WORDS:
        return False
    said: set[str] = set()
    for t in assistant_texts:
        said.update(_content_words(t))
    if not said:
        return False
    hits = sum(1 for w in user_words if w in said)
    return hits / len(user_words) >= _OVERLAP_THRESHOLD
