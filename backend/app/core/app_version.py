"""Which app build a client is, and whether it is too old to serve.

The Android app is sideloaded (no Play Store), so nothing can update it
behind the user's back. What the server can do is refuse a build older than
``Settings.min_app_build`` with a message that says where the new one is —
every shipped build already sends its ``version+versionCode`` in ``hello``.

Build numbers: the release build is made with ``--build-number=N`` (the
"base" build, shown on the website as ``build``) and ``--split-per-abi``,
which gives each APK its own ``versionCode``: N + 1000 (armeabi-v7a),
N + 2000 (arm64-v8a), N + 4000 (x86_64). The phone reports the versionCode,
so the base is recovered with the ABI — sent in ``hello`` by builds that
know to, or read off the versionCode's thousands for older ones (sound while
N stays in 2000–2999, which covers every build shipped so far).
"""

from __future__ import annotations

#: versionCode offset per ABI (Flutter's --split-per-abi).
ABI_OFFSETS: dict[str, int] = {"arm32": 1000, "arm64": 2000, "x86_64": 4000}

#: Older builds send no ABI: their versionCode's thousands give it away.
_THOUSANDS_TO_OFFSET: dict[int, int] = {3: 1000, 4: 2000, 6: 4000}


def base_build(app_version: str | None, abi: str | None = None) -> int | None:
    """The base build of ``"1.0.0+4531"`` (-> 2531), or ``None`` when the
    string carries no build number (a test client, a web page)."""
    if not app_version or "+" not in app_version:
        return None
    try:
        code = int(app_version.rsplit("+", 1)[1].strip())
    except ValueError:
        return None
    if code <= 0:
        return None
    offset = ABI_OFFSETS.get((abi or "").strip().lower())
    if offset is None:
        offset = _THOUSANDS_TO_OFFSET.get(code // 1000, 0)
    return code - offset


def is_outdated(
    app_version: str | None, abi: str | None, platform: str | None, min_build: int
) -> bool:
    """Whether this client must update before it is served.

    Off when ``min_build`` is 0. Only Android is checked (the only app
    shipped), and a client that names no build is let through rather than
    locked out on a guess.
    """
    if min_build <= 0 or (platform or "").lower() != "android":
        return False
    build = base_build(app_version, abi)
    return build is not None and build < min_build
