"""The glasses photo that arrives after its question gave up waiting.

A glasses photo comes over Bluetooth, and in a live voice session that link
shares the radio with the headset audio. Measured on the live server
(2026-09-22): the same question took 5.7 s one time and 16 s the next. Waiting
out the slow case in silence is not an answer — the user hears nothing for
twenty seconds and then, too often, "the glasses didn't take the photo", while
the photo lands a moment later.

So a vision tool waits a short, fixed patience. If the photo is not there by
then, the tool hands back "it is on its way" (the model says so in one
sentence) and registers the question here. When the photo does land, the
session describes it and tells the model, which answers the question then.
If it never lands, the model is told that too — every question ends with an
answer or an honest failure, exactly once.

Why not the Live API's own non-blocking functions: on the native-audio model
the model answers from its own guess before the result arrives (googleapis/
python-genai#1894, closed "not planned"). For "what is this?" a guess is worse
than a pause. Here the model is told plainly that there is no picture yet.

The tracker holds no knowledge of the model or the transport — the session
injects ``answer`` and ``fail`` — so it can be tested with plain coroutines.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.logging_conf import get_logger

logger = get_logger(__name__)

#: Failure codes after which the photo may still come. The app reports
#: ``capture_timeout`` when the glasses did not confirm the shot in time — but
#: a slow link can deliver it afterwards (device-seen 2026-09-23: "the photo
#: was taken, and Farry said it wasn't"). Every other code is final.
STILL_COMING = frozenset({"capture_timeout"})


@dataclass(slots=True)
class _Pending:
    question: str | None
    deadline: float


class LatePhoto:
    """At most one vision question whose glasses photo is still on its way."""

    def __init__(
        self,
        *,
        window_seconds: float,
        answer: Callable[[bytes, str | None], Awaitable[None]],
        fail: Callable[[str | None], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            window_seconds: How long after :meth:`defer` a photo still counts
                as the answer to that question.
            answer: ``await answer(jpeg, question)`` — describe the photo and
                have the model answer the question with it.
            fail: ``await fail(reason)`` — tell the model the photo never came.
        """
        self._window = window_seconds
        self._answer = answer
        self._fail = fail
        self._clock = clock
        self._pending: _Pending | None = None
        self._expiry: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def defer(self, question: str | None) -> None:
        """Register a question whose photo is still coming. A newer question
        replaces an older one: its photo is the one now being taken."""
        self._clear()
        self._pending = _Pending(question, self._clock() + self._window)
        self._expiry = asyncio.get_running_loop().create_task(
            self._expire_after(self._window), name="late_photo_expiry"
        )
        self._tasks.add(self._expiry)
        self._expiry.add_done_callback(self._tasks.discard)
        logger.info("late_photo.deferred", window_s=self._window)

    def on_frame(self, jpeg: bytes) -> bool:
        """A photo arrived with no tool waiting for it. Returns whether it was
        taken as the late answer."""
        pending = self._pending
        if pending is None or not jpeg:
            return False
        self._clear()
        if self._clock() > pending.deadline:
            return False
        logger.info("late_photo.arrived", bytes=len(jpeg))
        self._spawn(self._answer(jpeg, pending.question))
        return True

    def on_failure(self, reason: str | None) -> None:
        """The app gave up on the capture. A final reason ends the question
        now; a ``capture_timeout`` keeps waiting — the photo can still come."""
        if self._pending is None or reason in STILL_COMING:
            return
        self._clear()
        logger.info("late_photo.failed", reason=reason)
        self._spawn(self._fail(reason))

    def cancel(self) -> None:
        """Forget the question (a newer one is waiting for its own photo, or
        the session is closing). Says nothing."""
        self._clear()

    async def close(self) -> None:
        """Cancel everything in flight (session teardown)."""
        self._clear()
        for task in list(self._tasks):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # -- internals --------------------------------------------------------

    def _clear(self) -> None:
        self._pending = None
        expiry, self._expiry = self._expiry, None
        if expiry is not None and not expiry.done():
            expiry.cancel()

    async def _expire_after(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self._pending is None:
            return
        self._pending = None
        self._expiry = None
        logger.info("late_photo.expired", window_s=self._window)
        # Run inline: this task is already the background task for it.
        with contextlib.suppress(Exception):
            await self._fail("capture_timeout")

    def _spawn(self, coro: Awaitable[None]) -> None:
        async def run() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never break the session
                logger.warning("late_photo.handler_failed", error=repr(exc))

        task = asyncio.get_running_loop().create_task(run(), name="late_photo")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
