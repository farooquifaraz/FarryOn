"""The assistant's transcriber is told what language its owner speaks.

Left to detect the language per utterance, it transcribed one Hinglish speaker
into five scripts in an afternoon (2026-09-06) — English words spelt in
Devanagari, a contact's name mangled past recognition. The hint is a setting so
it can be switched off in one line if it ever makes things worse, and so the
pre-hint behaviour stays one env var away.
"""

from __future__ import annotations

import pytest

from app.ai.gemini import GeminiGateway
from app.config import Settings, get_settings


def _hints_on_the_wire(cfg) -> list[str] | None:
    tx = getattr(cfg, "input_audio_transcription", None)
    hints = getattr(tx, "language_hints", None)
    return None if hints is None else list(hints.language_codes or [])


def test_default_is_auto_detection() -> None:
    """Measured 2026-09-07: hints changed scripts a little and words not at
    all. Off by default until a larger measurement says otherwise."""
    cfg = GeminiGateway(system_prompt="sys", tools=[])._build_config()
    assert _hints_on_the_wire(cfg) is None


def test_setting_hints_puts_them_on_the_wire(monkeypatch) -> None:
    settings = get_settings().model_copy(deep=True)
    settings.assistant_language_hints = ["hi-IN", "en-IN"]
    monkeypatch.setattr("app.ai.gemini.get_settings", lambda: settings)
    cfg = GeminiGateway(system_prompt="sys", tools=[])._build_config()
    assert _hints_on_the_wire(cfg) == ["hi-IN", "en-IN"]


def test_the_models_own_speech_gets_no_hint() -> None:
    """Farry chose the language she is speaking; nothing to guess."""
    cfg = GeminiGateway(system_prompt="sys", tools=[])._build_config()
    out = getattr(cfg, "output_audio_transcription", None)
    assert out is not None
    assert getattr(out, "language_hints", None) is None


def test_empty_setting_restores_auto_detection(monkeypatch) -> None:
    """The switch: empty means the recogniser is on its own, as it shipped."""
    settings = get_settings().model_copy(deep=True)
    settings.assistant_language_hints = []
    monkeypatch.setattr("app.ai.gemini.get_settings", lambda: settings)
    cfg = GeminiGateway(system_prompt="sys", tools=[])._build_config()
    assert _hints_on_the_wire(cfg) is None


def test_hints_and_auto_are_never_sent_together(monkeypatch) -> None:
    """The SDK forbids LanguageHints alongside LanguageAuto; we set only one."""
    settings = get_settings().model_copy(deep=True)
    settings.assistant_language_hints = ["hi-IN"]
    monkeypatch.setattr("app.ai.gemini.get_settings", lambda: settings)
    cfg = GeminiGateway(system_prompt="sys", tools=[])._build_config()
    tx = cfg.input_audio_transcription
    assert getattr(tx, "language_auto", None) is None


def test_the_env_var_is_a_comma_list() -> None:
    """Operators set it as text; a stray space must not become a language."""
    s = Settings(assistant_language_hints="hi-IN, en-IN ,ur-IN")
    assert s.assistant_language_hints == ["hi-IN", "en-IN", "ur-IN"]


def test_translate_mode_is_untouched() -> None:
    """The translator hears strangers in any language; its own setting stays
    empty and its own gateway builds its own config."""
    assert get_settings().translate_language_hints == []


@pytest.mark.parametrize("bad", ["", " , ,"])
def test_blank_env_means_off_not_a_blank_language(bad) -> None:
    s = Settings(assistant_language_hints=bad)
    assert s.assistant_language_hints == []
