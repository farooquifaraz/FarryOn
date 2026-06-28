"""Single-connection Telegram login: send code, wait for it via a file, sign in.

Keeps ONE MTProto connection open the whole time (more reliable than splitting
send-code / sign-in across two processes — the login code stays valid).

    python scripts/tg_login_live.py +9715xxxxxxxx

It sends the code, then polls a code file. Write the 5-digit code to:
    <scratchpad>/tg_code.txt        (line 1 = code, line 2 = 2FA password if any)
It then signs in and writes the StringSession to:
    <scratchpad>/tg_session.txt
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

_DIR = Path(os.environ.get("TG_SCRATCH", "."))
_CODE = _DIR / "tg_code.txt"
_SESSION = _DIR / "tg_session.txt"


async def main(phone: str) -> None:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    _CODE.unlink(missing_ok=True)
    _SESSION.unlink(missing_ok=True)

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    sent = await client.send_code_request(phone)
    print(f"CODE SENT to {phone}. Waiting for {_CODE} ...", flush=True)

    # Wait up to 5 minutes for the code file to appear.
    for _ in range(300):
        if _CODE.exists():
            break
        await asyncio.sleep(1)
    else:
        await client.disconnect()
        sys.exit("Timed out waiting for the code.")

    lines = _CODE.read_text().strip().splitlines()
    code = lines[0].strip()
    password = lines[1].strip() if len(lines) > 1 else None
    _CODE.unlink(missing_ok=True)

    try:
        await client.sign_in(
            phone=phone, code=code, phone_code_hash=sent.phone_code_hash
        )
    except SessionPasswordNeededError:
        if not password:
            await client.disconnect()
            sys.exit("2FA_NEEDED: write code on line 1 AND password on line 2.")
        await client.sign_in(password=password)

    me = await client.get_me()
    _SESSION.write_text(client.session.save())
    await client.disconnect()
    print(f"LOGGED IN as {me.first_name} (@{me.username}, id={me.id})", flush=True)
    print(f"SESSION written to {_SESSION}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
