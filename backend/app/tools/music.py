"""``play_music`` tool: start and steer whatever music app the phone has.

Client-executed. The backend owns no player and streams nothing — it returns a
command and the phone hands it to Android: ``play`` fires the standard
"play from search" intent that Spotify, YouTube Music and the rest implement,
and the transport commands (pause/resume/next/previous/stop) go out as media
keys to whichever player currently holds the audio focus.

Two consequences worth keeping in mind, both deliberate. First, no permission
is required for any of this. Second, we cannot see the result: Android gives no
answer to a media key, so this tool reports what it ASKED for, never what
happened — the model must not claim a song is playing.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext
from app.tools.web_search import WebSearchTool

logger = get_logger(__name__)

#: What the phone can be asked to do. `play` is the only one that takes a query.
_COMMANDS = ("play", "pause", "resume", "next", "previous", "stop")

#: Players we can name in the intent. "default" lets Android pick, which is the
#: right answer unless the user asked for an app by name.
_APPS = ("default", "spotify", "youtube_music")

#: A YouTube video id in any of the URL shapes a search result may use.
_YOUTUBE_ID = re.compile(
    r"(?:youtube\.com/watch\?v=|music\.youtube\.com/watch\?v=|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


async def _youtube_music_link(ctx: ToolContext, query: str) -> str | None:
    """A link that makes YouTube Music actually PLAY the song, or None.

    Device-proven 2026-09-06: the standard "play from search" intent opens
    YouTube Music on its HOME screen and drops the query entirely, so "play
    some Arijit Singh" left the user staring at a home feed. A
    ``music.youtube.com/watch?v=<id>`` link starts playback on its own — so we
    resolve the words to a video id first, using the search provider the app is
    already configured with (and NOT the user's search allowance).

    Best-effort: any failure returns None and the caller falls back to the
    intent, which is still better than nothing.
    """
    try:
        res = await WebSearchTool().search(ctx, f"{query} song official youtube")
    except Exception as exc:  # pragma: no cover - network/provider failure
        logger.warning("play_music.resolve_failed", error=str(exc))
        return None
    for item in res.get("results", []):
        match = _YOUTUBE_ID.search(str(item.get("url") or ""))
        if match:
            return f"https://music.youtube.com/watch?v={match.group(1)}"
    return None


class PlayMusicTool(Tool):
    """Play or control music on the phone's own player."""

    name = "play_music"
    description = (
        "Play or control music on the phone. Use command='play' with a query "
        "for 'play <song/artist/playlist>' — the query is what the user asked "
        "for, e.g. 'Arijit Singh', 'lo-fi beats', 'Bohemian Rhapsody'. Use "
        "pause / resume / next / previous / stop to control whatever is "
        "already playing. Name an app only if the user did. This asks the "
        "phone's music app to act and gets NO confirmation back, so say you've "
        "asked for it — never announce which song is now playing."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": list(_COMMANDS),
                "description": "What to do. Omit it to play — pass one of the "
                "others only to control what is already playing.",
            },
            "query": {
                "type": "string",
                "description": "Song, artist, album, genre or playlist to "
                "play. Only used with command='play'.",
            },
            "app": {
                "type": "string",
                "enum": list(_APPS),
                "description": "Player to use. Leave out unless the user named "
                "one — 'default' lets the phone choose.",
            },
        },
        # Nothing is required: "play some Arijit Singh" is a query and an
        # implied command, and demanding both only turns a good request into a
        # validation error.
        "required": [],
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        command = (kwargs.get("command") or "").strip().lower()
        query = (kwargs.get("query") or "").strip()
        app = (kwargs.get("app") or "default").strip().lower()
        if app not in _APPS:
            app = "default"

        # No command given: a query means play it, silence means carry on.
        if not command:
            command = "play" if query else "resume"
        elif command not in _COMMANDS:
            return {
                "ok": False,
                "message": (
                    "I can play, pause, resume, stop, or skip to the next or "
                    "previous track."
                ),
            }

        # "Play music" with nothing named is a request to carry on, not to pick
        # something at random — resume is what a person means by it.
        if command == "play" and not query:
            command = "resume"

        # Resolve the words to something that plays itself. Skipped when the
        # user named Spotify — honouring the app they asked for beats starting
        # a different one.
        url = ""
        if command == "play" and query and app != "spotify":
            url = await _youtube_music_link(ctx, query) or ""
            if not url and app == "youtube_music":
                # No video id this time (an obscure title, a search that came
                # back without one). The "play from search" intent is NOT a
                # fallback here: YouTube Music ignores it and opens its home
                # feed, which is how "play some Arijit Singh" left the user
                # staring at nothing (device-proven 2026-09-06). Its search URL
                # at least lands on the right results, one tap from playing.
                url = f"https://music.youtube.com/search?q={quote(query)}"

        logger.info(
            "play_music.command",
            command=command,
            app=app,
            # The query itself, so a lookup that finds nothing can be
            # reproduced instead of guessed at.
            query=query[:80],
            resolved=bool(url),
            plays_itself="watch?v=" in url,
        )
        return {
            "ok": True,
            "action": "music_control",
            "command": command,
            "query": query,
            "app": app,
            # When set, the phone opens this and the player starts on its own.
            # Empty means fall back to the "play from search" intent.
            "url": url,
            # Android acknowledges none of this, so the status says what was
            # asked, not what happened.
            "status": "sent_to_player",
            "playing": None,
        }
