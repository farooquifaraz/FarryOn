"""The download line on the site: /download/info carries the build the CI
published beside the APKs, so a phone's Settings → Version can be checked
against what the site serves."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.web import router as web

pytestmark = pytest.mark.asyncio


async def test_download_info_carries_the_published_build(tmp_path, monkeypatch) -> None:
    (tmp_path / "build-info.json").write_text(json.dumps({
        "version": "1.0.0", "build": 2419,
        "versionCode": {"arm64": 4419, "arm32": 3419},
        "commit": "08d9309", "builtAt": "2026-09-15T17:00:00Z",
    }))
    monkeypatch.setattr(web, "_apk_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(web.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/download/info")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "1.0.0"
    assert body["build"]["build"] == 2419
    assert body["build"]["versionCode"]["arm64"] == 4419
    assert body["arm64"] == {"available": False, "size": None}


async def test_download_info_without_the_sidecar_is_unchanged(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(web, "_apk_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(web.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/download/info")
    assert "build" not in r.json()
