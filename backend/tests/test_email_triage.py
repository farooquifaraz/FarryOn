"""The importance scorer: evidence in, a level and speakable reasons out."""

from __future__ import annotations

from app.tools.email_triage import rank, score_importance


def _item(**over):
    base = {
        "subject": "Lunch?", "snippet": "Want to grab lunch on Friday?",
        "from_email": "friend@x.com", "to": ["me@gmail.com"], "cc": [],
        "unread": False, "flagged": False, "gmail_important": False,
        "bulk": False, "priority": "normal", "in_reply_to": "",
    }
    base.update(over)
    return base


def test_plain_mail_is_normal() -> None:
    level, reasons, score = score_importance(_item(), "me@gmail.com")
    assert level == "normal"
    assert score <= 2


def test_urgent_subject_from_a_person_is_critical() -> None:
    level, reasons, _ = score_importance(
        _item(subject="URGENT: server down", unread=True), "me@gmail.com"
    )
    assert level == "critical"
    assert any("urgent" in r for r in reasons)
    assert "sent directly to you" in reasons


def test_urgent_newsletter_is_not_critical() -> None:
    """Bulk headers pull a shouting promo back down."""
    level, reasons, _ = score_importance(
        _item(
            subject="URGENT: sale ends tonight!", bulk=True,
            from_email="noreply@shop.com", to=["undisclosed@shop.com"],
        ),
        "me@gmail.com",
    )
    assert level in ("normal", "low")
    assert "it's a newsletter or bulk mail" in reasons


def test_gmail_important_plus_deadline_is_high_or_more() -> None:
    level, reasons, _ = score_importance(
        _item(subject="Deadline for the visa documents", gmail_important=True),
        "me@gmail.com",
    )
    assert level in ("high", "critical")
    assert "Gmail marked it important" in reasons


def test_starred_unread_reply_is_critical() -> None:
    level, _r, score = score_importance(
        _item(subject="Re: contract", flagged=True, unread=True, in_reply_to="<x>"),
        "me@gmail.com",
    )
    assert level == "critical", score


def test_hinglish_urgency_words_count() -> None:
    level, reasons, _ = score_importance(
        _item(subject="Zaroori: kal ka meeting", unread=True), "me@gmail.com"
    )
    assert level in ("high", "critical")
    assert any("zaroori" in r for r in reasons)


def test_word_matching_is_whole_word() -> None:
    """'due' must not fire inside 'residue'; 'abhi' not inside 'abhishek'."""
    level, reasons, _ = score_importance(
        _item(subject="Residue report from Abhishek"), "me@gmail.com"
    )
    assert level == "normal"
    assert not any("due" in r or "abhi" in r for r in reasons)


def test_noreply_low_when_nothing_else() -> None:
    level, _r, _ = score_importance(
        _item(from_email="no-reply@service.com", to=["me@gmail.com", "a@b", "c@d", "e@f"]),
        "me@gmail.com",
    )
    assert level == "low"


def test_rank_puts_critical_first_then_newest() -> None:
    items = [
        {"uid": "1", "importance": "high", "_score": 3, "date": "2026-09-12T09:00:00"},
        {"uid": "2", "_score": 0, "date": "2026-09-12T11:00:00"},
        {"uid": "3", "importance": "critical", "_score": 7, "date": "2026-09-11T08:00:00"},
        {"uid": "4", "_score": 1, "date": "2026-09-12T12:00:00"},
    ]
    assert [e["uid"] for e in rank(items)] == ["3", "1", "4", "2"]
