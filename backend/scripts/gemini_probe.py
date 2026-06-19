"""List the models this GEMINI_API_KEY can use and whether any support Live.

Live/realtime needs a model whose ``supported_actions`` include
``bidiGenerateContent``. This probe makes the key's actual capabilities
explicit so we stop guessing model/version combos.
"""

from __future__ import annotations

import os

from google import genai


def main() -> None:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("NO GEMINI_API_KEY set")
        return

    client = genai.Client(api_key=key)
    live: list[str] = []
    total = 0
    print("=== models available to this key (name -> supported_actions) ===")
    try:
        for m in client.models.list():
            total += 1
            name = getattr(m, "name", "?")
            actions = getattr(m, "supported_actions", None)
            print(f"{name}  ->  {actions}")
            if actions and "bidiGenerateContent" in actions:
                live.append(name)
    except Exception as exc:  # noqa: BLE001 - report and continue
        print(f"models.list() FAILED: {exc!r}")
        return

    print(f"=== total models listed: {total} ===")
    print("=== LIVE-capable (bidiGenerateContent) ===")
    print(live if live else "NONE — this key/project has no realtime Live access")


if __name__ == "__main__":
    main()
