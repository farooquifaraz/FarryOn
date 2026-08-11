"""End-to-end tests for ``hello.mode == "translate"`` on ``/ws/live``.

Runs entirely against :class:`~app.ai.mock_translate.MockTranslateGateway` — no
network, no key. ``conftest.py`` forces ``AI_PROVIDER=mock``, and the translate
provider follows that switch, so these tests exercise the real session code
path without knowing the feature has a provider at all.

The two things worth protecting here are the isolation promises: a translate
session must not reach the tool engine or the camera, and it must never quietly
fall back to the assistant.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.ai.mock_translate import MockTranslateGateway
from app.config import get_settings
from app.main import create_app
from app.ws import session as session_module
from app.ws.frames import FrameTag, encode_frame

from .test_ws_live import _collect_until


@pytest.fixture(autouse=True)
def _dev_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Connect token-less via the dev-mode anonymous fallback, as
    ``test_ws_live`` does — the dev .env carries a real secret."""
    monkeypatch.setattr(get_settings(), "jwt_secret", "dev-insecure-change-me")


def _translate_handshake(ws, *, target: str = "hi", echo: bool = False) -> dict:
    """Send a translate-mode hello and return the parsed ``ready``."""
    ws.send_json(
        {
            "type": "hello",
            "protocolVersion": 1,
            "client": {"platform": "android", "appVersion": "1.0.0"},
            "device": {
                "kind": "phone",
                "id": "dev-1",
                "capabilities": ["audio_in", "audio_out"],
            },
            "session": {},
            "mode": "translate",
            "translate": {
                "targetLanguage": target,
                "echoTargetLanguage": echo,
            },
        }
    )
    return ws.receive_json()


def _speak(ws) -> None:
    """Send one chunk of input audio (one utterance to the mock)."""
    ws.send_json({"type": "audio_start"})
    ws.send_bytes(encode_frame(FrameTag.INPUT_AUDIO, b"\x00\x01" * 160))


def _jsons(received) -> list[dict]:
    return [obj for kind, obj in received if kind == "json"]


def test_translate_handshake_reports_the_mode_it_accepted() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ready = _translate_handshake(ws, target="hi")
            assert ready["type"] == "ready"
            assert ready["mode"] == "translate"
            assert ready["targetLanguage"] == "hi"


def test_agent_mode_is_the_default_and_says_so() -> None:
    """A client that has never heard of `mode` must be unaffected."""
    from .test_ws_live import _handshake

    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ready = _handshake(ws)
            assert ready["mode"] == "agent"
            assert "targetLanguage" not in ready


def test_speech_produces_heard_and_translated_transcripts() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _translate_handshake(ws, target="hi")
            _speak(ws)
            received = _collect_until(
                ws,
                lambda m: m[0] == "json"
                and m[1].get("type") == "transcript"
                and m[1].get("role") == "assistant",
            )
            transcripts = [
                m for m in _jsons(received) if m.get("type") == "transcript"
            ]
            heard = [t for t in transcripts if t["role"] == "user"]
            translated = [t for t in transcripts if t["role"] == "assistant"]

            assert heard and translated
            # The source language is DETECTED, so it has to come down the wire;
            # the client cannot work it out for itself.
            assert heard[-1]["lang"] == "en"
            assert translated[-1]["lang"] == "hi"
            # Partial then final, which is what the UI firms up on.
            assert heard[0]["final"] is False
            assert heard[-1]["final"] is True


def test_speech_already_in_the_target_language_is_answered_with_silence() -> None:
    """The model stays silent on the third utterance of the mock's script.

    This is the case that reads as a broken feature, so it is pinned: no
    assistant transcript and no audio, only the heard text tagged with the
    target language — which is exactly what the UI needs to explain it.
    """
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _translate_handshake(ws, target="hi")
            ws.send_json({"type": "audio_start"})
            # Utterances 1 and 2 translate; the 3rd is already Hindi.
            for _ in range(3):
                ws.send_bytes(encode_frame(FrameTag.INPUT_AUDIO, b"\x00\x01" * 160))

            # Drain until the turn ENDS (turn_complete → state "listening")
            # after the Hindi utterance was heard. Stopping at the heard text
            # would pass even if the model spoke immediately afterwards, since
            # the assistant transcript follows the final user one.
            heard_hi_final = False
            spoke_after = False
            audio_after = False
            for _ in range(120):
                data = ws.receive()
                if data.get("bytes") is not None:
                    if heard_hi_final:
                        audio_after = True
                    continue
                text = data.get("text")
                if text is None:
                    continue
                msg = json.loads(text)
                mtype = msg.get("type")
                if mtype == "transcript":
                    if (
                        msg["role"] == "user"
                        and msg.get("lang") == "hi"
                        and msg["final"]
                    ):
                        heard_hi_final = True
                    elif msg["role"] == "assistant" and heard_hi_final:
                        spoke_after = True
                elif mtype == "state" and heard_hi_final:
                    break  # the silent turn completed

            assert heard_hi_final, "the Hindi utterance was never heard"
            assert not spoke_after, (
                "the model spoke over speech already in the target language"
            )
            assert not audio_after, "audio was played for a silent turn"


def test_translate_session_has_no_tools() -> None:
    """No tool_call ever reaches a translate session.

    The agent mock calls ``create_note`` on every turn, so if the translate
    path were wired to the wrong gateway — or kept its orchestrator — this
    would catch it immediately.
    """
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _translate_handshake(ws)
            _speak(ws)
            received = _collect_until(
                ws,
                lambda m: m[0] == "json"
                and m[1].get("type") == "transcript"
                and m[1].get("role") == "assistant",
            )
            assert not [
                m for m in _jsons(received) if m.get("type") == "tool_call"
            ]


def test_translate_session_never_forwards_a_video_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A camera frame is dropped by the SESSION, not merely ignored downstream.

    Asserting through the mock would prove nothing — its ``send_video`` is a
    no-op either way. So the gateway is swapped for one that records the call:
    the only way this passes is if the frame never left ``_handle_binary``.
    """
    seen: list[bytes] = []

    class _RecordingGateway(MockTranslateGateway):
        async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
            seen.append(jpeg)

    monkeypatch.setattr(
        session_module,
        "build_translate_gateway",
        lambda *a, **k: _RecordingGateway(
            target_language=k.get("target_language", "hi")
        ),
    )

    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _translate_handshake(ws)
            ws.send_bytes(encode_frame(FrameTag.INPUT_VIDEO, b"\xff\xd8\xff\xd9"))
            # Speak afterwards so we know the frame was processed before the
            # assertion runs, rather than still sitting in the read pump.
            _speak(ws)
            _collect_until(
                ws,
                lambda m: m[0] == "json"
                and m[1].get("type") == "transcript"
                and m[1].get("role") == "assistant",
            )
            assert seen == [], "a camera frame reached the translate model"


def test_an_unknown_target_language_falls_back_rather_than_failing() -> None:
    """A code outside the allow-list must not reach the upstream setup."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ready = _translate_handshake(ws, target="klingon")
            assert ready["mode"] == "translate"
            assert ready["targetLanguage"] == "en"


def test_a_missing_translate_provider_is_answered_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A translate provider that cannot start must say so, not go quiet.

    Originally this covered a `gemini_translate` module that did not exist yet.
    It does now, so the case it pins is the one that outlives that: the adapter
    is present but cannot open — here because the test environment has no
    `GEMINI_API_KEY`, in production because a key expired, a model id changed,
    or an operator set `TRANSLATE_PROVIDER` to something unrecognised.

    All of those must reach the user as a sentence. The gateway is built
    OUTSIDE the try that guards `connect()`, so before this was fixed the
    failure fell through to the top-level handler and the socket died with
    nothing said — the one outcome a user can do nothing about.
    """
    monkeypatch.setattr(get_settings(), "translate_provider", "gemini_translate")
    monkeypatch.setattr(get_settings(), "gemini_api_key", None)

    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ready = _translate_handshake(ws)
            assert ready["type"] == "error"
            assert ready["code"] == "translate_unavailable"
            assert ready["fatal"] is True


def test_an_unrecognised_translate_provider_is_answered_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo in TRANSLATE_PROVIDER must not present as a dead socket."""
    monkeypatch.setattr(get_settings(), "translate_provider", "gemini-translate")

    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            msg = _translate_handshake(ws)
            assert msg["type"] == "error"
            assert msg["code"] == "translate_unavailable"


def test_an_unsupported_mode_is_refused_loudly() -> None:
    """Downgrading to the assistant would answer the room instead of
    translating it, which is worse than failing."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json(
                {
                    "type": "hello",
                    "protocolVersion": 1,
                    "client": {"platform": "android", "appVersion": "1.0.0"},
                    "device": {"kind": "phone", "id": "d", "capabilities": []},
                    "session": {},
                    "mode": "karaoke",
                }
            )
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "unsupported_mode"
