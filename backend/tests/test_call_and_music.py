"""``make_call`` and ``play_music``: the phone acts, the server only asks.

Both tools are client-executed. The value of these tests is mostly in what they
REFUSE to let the tools claim — a dialer that opened is not a call that
connected, and a media key that was sent is not a song that is playing. An
assistant that reports either as done is worse than one that can't do it.
"""

from __future__ import annotations

import pytest

from app.tools import ratelimit
from app.tools.base import ToolContext
from app.tools.calls import MakeCallTool
from app.tools.music import PlayMusicTool

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    """Each test gets its own send budget."""
    ratelimit._hits.clear()
    yield
    ratelimit._hits.clear()


# --- make_call -----------------------------------------------------------


async def test_a_number_becomes_a_dialer_link(db_session) -> None:
    res = await MakeCallTool().run(
        ToolContext(session=db_session), phone_number="+971500000000"
    )
    assert res["ok"] is True
    assert res["action"] == "open_url"
    assert res["url"] == "tel:+971500000000"


async def test_the_result_never_says_anyone_answered(db_session) -> None:
    """The phone dials; whether a human picks up is not ours to claim."""
    res = await MakeCallTool().run(
        ToolContext(session=db_session), phone_number="+971500000000"
    )
    assert res["status"] == "calling"
    assert res["answered"] is False


async def test_a_device_contact_dials_without_the_server_seeing_a_number(
    db_session,
) -> None:
    """The whole point of the contact round trip: no number reaches us."""
    res = await MakeCallTool().run(ToolContext(session=db_session), contact_id="c7")
    assert res["ok"] is True
    assert res["action"] == "place_call"
    assert res["contact_id"] == "c7"
    assert "url" not in res and "to" not in res


async def test_an_unresolved_name_asks_instead_of_guessing(db_session) -> None:
    res = await MakeCallTool().run(
        ToolContext(session=db_session), contact_name="Nobody I Know"
    )
    assert res["ok"] is False
    assert res["status"] == "not_resolved"


async def test_a_name_just_resolved_on_the_device_is_recovered(db_session) -> None:
    """The model names someone it resolved a turn ago but omits the id."""
    ctx = ToolContext(session=db_session)
    ctx.recall_resolved = lambda name: "c3" if name == "Sara" else None
    res = await MakeCallTool().run(ctx, contact_name="Sara")
    assert res["action"] == "place_call"
    assert res["contact_id"] == "c3"


async def test_no_recipient_asks_who(db_session) -> None:
    res = await MakeCallTool().run(ToolContext(session=db_session))
    assert res["ok"] is False
    assert "who" in res["message"].lower()


async def test_a_half_number_is_refused(db_session) -> None:
    res = await MakeCallTool().run(ToolContext(session=db_session), phone_number="123")
    assert res["ok"] is False
    assert "action" not in res


async def test_a_runaway_loop_cannot_keep_opening_the_dialer(db_session) -> None:
    tool = MakeCallTool()
    ctx = ToolContext(session=db_session, session_id="s1")
    results = [
        await tool.run(ctx, phone_number="+971500000000") for _ in range(12)
    ]
    assert any(r.get("status") == "rate_limited" for r in results)


async def test_sending_messages_does_not_use_up_the_calls(db_session) -> None:
    """A user who just texted several people must still be able to phone one."""
    ratelimit._hits["s9"] = [__import__("time").monotonic()] * 20  # message budget spent
    res = await MakeCallTool().run(
        ToolContext(session=db_session, session_id="s9"),
        phone_number="+971500000000",
    )
    assert res["ok"] is True
    assert res["action"] == "open_url"


# --- play_music ----------------------------------------------------------


async def test_play_carries_the_query_to_the_phone(db_session) -> None:
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), command="play", query="Arijit Singh"
    )
    assert res["ok"] is True
    assert res["action"] == "music_control"
    assert res["command"] == "play"
    assert res["query"] == "Arijit Singh"
    assert res["app"] == "default"


async def test_a_query_alone_is_enough_to_play(db_session) -> None:
    """Device-observed: asked to "play some Arijit Singh", the model sends the
    query and no command. That is a complete request, not a broken one."""
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), query="Arijit Singh"
    )
    assert res["ok"] is True
    assert res["command"] == "play"
    assert res["query"] == "Arijit Singh"


async def test_no_arguments_at_all_resumes(db_session) -> None:
    res = await PlayMusicTool().run(ToolContext(session=db_session))
    assert res["ok"] is True
    assert res["command"] == "resume"


async def test_play_with_nothing_named_means_resume(db_session) -> None:
    """"Play music" is a request to carry on, not to pick something at random."""
    res = await PlayMusicTool().run(ToolContext(session=db_session), command="play")
    assert res["command"] == "resume"


@pytest.mark.parametrize("command", ["pause", "resume", "next", "previous", "stop"])
async def test_every_transport_command_passes_through(db_session, command) -> None:
    res = await PlayMusicTool().run(ToolContext(session=db_session), command=command)
    assert res["ok"] is True
    assert res["command"] == command


async def test_a_song_resolves_to_a_link_that_plays_itself(
    db_session, monkeypatch
) -> None:
    """Device-proven: the play-from-search intent opens YouTube Music on its
    HOME screen and drops the query, while a watch link starts playing."""
    from app.tools import music as music_mod

    async def fake_search(self, ctx, query):
        assert "Arijit" in query
        return {"results": [
            {"url": "https://example.com/lyrics"},
            {"url": "https://www.youtube.com/watch?v=P4HYIWfceBQ"},
        ]}

    monkeypatch.setattr(music_mod.WebSearchTool, "search", fake_search)
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), command="play", query="Arijit Singh"
    )
    assert res["url"] == "https://music.youtube.com/watch?v=P4HYIWfceBQ"


async def test_a_failed_lookup_still_returns_the_search_fallback(
    db_session, monkeypatch
) -> None:
    """A dead search provider must not cost the user their music."""
    from app.tools import music as music_mod

    async def boom(self, ctx, query):
        raise RuntimeError("provider down")

    monkeypatch.setattr(music_mod.WebSearchTool, "search", boom)
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), command="play", query="Arijit Singh"
    )
    assert res["ok"] is True
    assert res["url"] == ""
    assert res["query"] == "Arijit Singh"


async def test_youtube_music_falls_back_to_its_search_not_the_intent(
    db_session, monkeypatch
) -> None:
    """YouTube Music ignores the play-from-search intent and opens its home
    feed, so an unresolved song must still arrive somewhere useful."""
    from app.tools import music as music_mod

    async def nothing(self, ctx, query):
        return {"results": [{"url": "https://example.com/lyrics"}]}

    monkeypatch.setattr(music_mod.WebSearchTool, "search", nothing)
    res = await PlayMusicTool().run(
        ToolContext(session=db_session),
        command="play",
        query="kala pathar",
        app="youtube_music",
    )
    assert res["url"] == "https://music.youtube.com/search?q=kala%20pathar"


async def test_naming_spotify_is_honoured_over_youtube(
    db_session, monkeypatch
) -> None:
    """Asked for Spotify, we don't quietly start a different app instead."""
    from app.tools import music as music_mod

    async def fake_search(self, ctx, query):  # pragma: no cover - must not run
        raise AssertionError("should not search when an app was named")

    monkeypatch.setattr(music_mod.WebSearchTool, "search", fake_search)
    res = await PlayMusicTool().run(
        ToolContext(session=db_session),
        command="play",
        query="Arijit Singh",
        app="spotify",
    )
    assert res["url"] == ""
    assert res["app"] == "spotify"


async def test_finding_a_song_costs_no_web_search_allowance(
    db_session, monkeypatch
) -> None:
    """Resolving a link is our implementation detail, not a search they made."""
    from app.tools import music as music_mod
    from app.tools import quota

    async def refuse(ctx, metric):  # pragma: no cover - must not run
        raise AssertionError("play_music must not meter web_searches")

    monkeypatch.setattr(quota, "check_quota", refuse)

    async def fake_search(self, ctx, query):
        return {"results": [{"url": "https://youtu.be/P4HYIWfceBQ"}]}

    monkeypatch.setattr(music_mod.WebSearchTool, "search", fake_search)
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), command="play", query="lo-fi"
    )
    assert res["url"].endswith("P4HYIWfceBQ")


async def test_the_result_never_claims_music_is_playing(db_session) -> None:
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), command="play", query="lo-fi"
    )
    assert res["status"] == "sent_to_player"
    assert res["playing"] is None


async def test_an_unknown_command_is_refused_kindly(db_session) -> None:
    res = await PlayMusicTool().run(ToolContext(session=db_session), command="rewind")
    assert res["ok"] is False
    assert "action" not in res


async def test_an_unknown_app_falls_back_to_the_phones_choice(db_session) -> None:
    res = await PlayMusicTool().run(
        ToolContext(session=db_session), command="play", query="jazz", app="winamp"
    )
    assert res["app"] == "default"


async def test_music_costs_no_send_budget(db_session) -> None:
    """It is a control, like the camera — it must not eat the message limit."""
    tool = PlayMusicTool()
    ctx = ToolContext(session=db_session, session_id="s2")
    for _ in range(20):
        assert (await tool.run(ctx, command="next"))["ok"] is True
    assert ratelimit.allow("s2") is True
