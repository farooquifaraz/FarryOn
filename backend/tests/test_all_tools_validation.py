"""Full end-to-end validation of EVERY registered tool.

For each tool in :func:`build_default_tools` this exercises the real dispatch
path (schema validation -> execute) through the :class:`ToolEngine` with valid
arguments and asserts the tool runs without crashing AND returns the expected
payload shape. Tools that depend on external resources not present in the
offline test env (email creds, a live camera frame, a vision/web key) are
validated on their *graceful* path — they must return a well-formed
"not available" payload, never raise.

A printed PASS/FAIL table makes a regression obvious at a glance. Run with
``-s`` to see it: ``pytest tests/test_all_tools_validation.py -s``.
"""

from __future__ import annotations

import time

import pytest

from app.agent.tool_engine import ToolEngine
from app.tools import build_default_tools
from app.tools.base import ToolContext

pytestmark = pytest.mark.asyncio


async def test_every_registered_tool_dispatches(db_session) -> None:
    engine = ToolEngine.from_tools(build_default_tools())
    ctx = ToolContext(session=db_session, user_id=None)

    report: list[tuple[str, str, str]] = []
    failures: list[str] = []

    async def run(name: str, args: dict) -> dict:
        res = await engine.dispatch(name, args, ctx)
        await db_session.commit()
        if not res.ok:  # engine-level crash / timeout / bad schema
            report.append((name, "FAIL", f"dispatch error: {res.error}"))
            failures.append(f"{name}: {res.error}")
            return {}
        return res.result or {}

    def ok(name: str, cond: bool, detail: str) -> None:
        report.append((name, "PASS" if cond else "FAIL", detail))
        if not cond:
            failures.append(f"{name}: unexpected payload -> {detail}")

    # --- notes / tasks lifecycle (offline, fully exercisable) -------------
    r = await run("create_note", {"text": "Buy milk"})
    ok("create_note", r.get("text") == "Buy milk" and "id" in r, str(r))

    r = await run("list_notes", {})
    notes = r.get("notes") or r.get("items") or []
    ok("list_notes", any("Buy milk" in str(n) for n in notes), f"{len(notes)} notes")

    r = await run("delete_note", {"text": "Buy milk"})
    ok("delete_note", r.get("ok") is True and r.get("deleted") is True, str(r))

    r = await run("create_task", {"title": "Call mom"})
    ok("create_task", r.get("title") == "Call mom" and "id" in r, str(r))

    r = await run("create_task", {"title": "Pay bill", "remind_in_seconds": 120})
    ok("create_task(reminder)", "id" in r, str(r))

    r = await run("list_tasks", {})
    tasks = r.get("tasks") or r.get("items") or []
    ok("list_tasks", len(tasks) >= 2, f"{len(tasks)} tasks")

    r = await run("update_task", {"task": "Call mom", "new_title": "Call mum"})
    ok("update_task", r.get("ok") is True, str(r))

    r = await run("complete_task", {"task": "Call mum"})
    ok("complete_task", r.get("ok") is True and r.get("done") is True, str(r))

    r = await run("delete_task", {"task": "Pay bill"})
    ok("delete_task", r.get("ok") is True and r.get("deleted") is True, str(r))

    # --- device / camera controls (client-executed acks) -----------------
    r = await run("mute_mic", {"muted": True})
    ok("mute_mic", r.get("applied") is True and r.get("muted") is True, str(r))

    r = await run("set_camera", {"on": False})
    ok("set_camera", r.get("applied") is True and r.get("on") is False, str(r))

    r = await run("rotate_camera", {})
    ok("rotate_camera", r.get("applied") is True, str(r))

    r = await run("set_camera_zoom", {"level": 3.0})
    ok("set_camera_zoom", r.get("applied") is True and r.get("zoom") == 3.0, str(r))

    r = await run("end_session", {})
    ok("end_session", r.get("applied") is True, str(r))

    # --- messaging --------------------------------------------------------
    r = await run(
        "send_message", {"text": "hi", "phone_number": "+971501234567"}
    )
    ok(
        "send_message(sms)",
        r.get("ok") is True and r.get("action") == "open_url"
        and r.get("url", "").startswith("sms:+971501234567"),
        str(r),
    )
    r = await run("send_message", {"text": "hi", "contact_name": "Unknown"})
    ok(
        "send_message(name->device)",
        r.get("action") == "resolve_contact" and r.get("platform") == "sms",
        str(r),
    )

    r = await run(
        "send_whatsapp", {"message": "hi", "phone_number": "+971501234567"}
    )
    ok(
        "send_whatsapp(number)",
        r.get("ok") is True and r.get("action") == "open_url"
        and r.get("url", "").startswith("https://wa.me/971501234567"),
        str(r),
    )

    r = await run("send_whatsapp", {"message": "hi", "contact_name": "Zoya"})
    ok(
        "send_whatsapp(name->device)",
        r.get("ok") is True and r.get("action") == "resolve_contact"
        and r.get("name") == "Zoya",
        str(r),
    )

    r = await run("send_telegram", {"message": "hi", "username": "@neo"})
    ok(
        "send_telegram(deeplink)",
        r.get("ok") is True and r.get("action") == "open_url"
        and r.get("url") == "https://t.me/neo",
        str(r),
    )

    r = await run("save_contact", {"name": "Zoya", "phone_number": "+971500000000"})
    ok("save_contact", r.get("ok") is True and r.get("phone"), str(r))

    # After saving, the name now resolves to the saved number (open_url).
    r = await run("send_whatsapp", {"message": "hi", "contact_name": "Zoya"})
    ok(
        "send_whatsapp(saved-name)",
        r.get("action") == "open_url" and "971500000000" in r.get("url", ""),
        str(r),
    )

    # --- web search (mock provider in test env) ---------------------------
    r = await run("web_search", {"query": "latest news"})
    ok(
        "web_search",
        isinstance(r.get("results"), list) and len(r["results"]) > 0,
        f"{len(r.get('results', []))} results",
    )

    # --- location (with + without a cached fix) ---------------------------
    r = await run("get_location", {})
    ok("get_location(none)", r.get("ok") is False, "no fix -> graceful")
    ctx.location = {"lat": 25.2, "lng": 55.27, "address": "Dubai"}
    r = await run("get_location", {})
    ok("get_location(fix)", r.get("ok") is True and r.get("lat") == 25.2, str(r))

    # --- identify_image (graceful when no live frame) ---------------------
    r = await run("identify_image", {"kind": "auto"})
    ok("identify_image(no-frame)", r.get("ok") is False and "error" in r, "graceful")

    # --- email (graceful when no creds configured) ------------------------
    r = await run("read_emails", {})
    ok("read_emails(no-cfg)", r.get("ok") is False and "message" in r, "graceful")
    r = await run("read_email", {"query": "boss"})
    ok("read_email(no-cfg)", r.get("ok") is False, "graceful")
    r = await run("send_email", {"to": "a@b.com", "body": "hi"})
    ok("send_email(no-cfg)", r.get("ok") is False, "graceful")

    # ---- report ----------------------------------------------------------
    width = max(len(n) for n, _, _ in report)
    print("\n\n=== TOOL VALIDATION REPORT ===")
    for name, status, detail in report:
        mark = "OK " if status == "PASS" else "XX "
        print(f"  [{mark}] {name.ljust(width)}  {detail}")
    passed = sum(1 for _, s, _ in report if s == "PASS")
    print(f"=== {passed}/{len(report)} checks passed ===\n")

    assert not failures, "Tool validation failures:\n" + "\n".join(failures)
