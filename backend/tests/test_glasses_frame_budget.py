"""The glasses frame budget has to outlast a real BLE thumbnail transfer.

Set against 10-12 s in July 2026. Re-measured on an L802 mid-session on
2026-08-21: 2.4 s to take the picture, then 19 BLE chunks about 830 ms apart —
28 s from command to photo. The 18 s budget in force that night gave up ten
seconds early, ``identify_image`` returned "I couldn't get a fresh look just
now", and the model told the wearer it could not see. The photo arrived
afterwards and went to the gallery, so the camera had visibly worked and the
assistant had visibly lied.

The mobile side keeps the matching half of this in
``mobile/test/glasses_capture_budget_test.dart``; the Dart backstop must stay
above the number here.
"""

from app.config import Settings


def test_glasses_budget_outlasts_a_measured_transfer() -> None:
    measured_seconds = 28.0
    assert Settings().glasses_frame_wait_seconds > measured_seconds, (
        "a photo the glasses genuinely deliver must not be reported as a "
        "failure because the wait was shorter than the hardware"
    )


def test_glasses_budget_is_longer_than_the_phone_camera_budget() -> None:
    # The phone streams at 1 fps, so a frame is always seconds away. The glasses
    # shoot on demand and then trickle the result over BLE — the two budgets are
    # not interchangeable and the glasses one must be the larger.
    settings = Settings()
    assert settings.glasses_frame_wait_seconds > settings.frame_wait_seconds


def test_the_ingest_pause_stays_short() -> None:
    # Unrelated to the transfer, and easy to inflate by accident while tuning
    # the budget above: this pause happens AFTER the photo arrives, with the
    # model waiting, so it is paid on every successful capture.
    assert Settings().frame_ingest_seconds <= 2.0
