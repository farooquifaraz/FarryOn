"""One-time Telegram USER-account login (Telethon / MTProto).

Produces a StringSession so FarryOn can send Telegram messages AS THE USER
(to anyone in their contacts, no bot /start needed).

Two steps (run separately so the code from Telegram can be entered between):

    python scripts/tg_login.py send-code  +9715xxxxxxxx
    python scripts/tg_login.py sign-in    <code>  [2fa_password]

api_id/api_hash are read from the environment (TELEGRAM_API_ID / _API_HASH).
The intermediate state (session + phone_code_hash) is kept in a temp file; the
final StringSession is printed for you to paste into TELEGRAM_SESSION in .env.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

_STATE = Path(tempfile.gettempdir()) / "farryon_tg_login.json"


def _creds() -> tuple[int, str]:
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        sys.exit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in the environment.")
    return int(api_id), api_hash


async def send_code(phone: str) -> None:
    api_id, api_hash = _creds()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    sent = await client.send_code_request(phone)
    _STATE.write_text(json.dumps({
        "session": client.session.save(),
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash,
    }))
    await client.disconnect()
    print(f"OK: a login code was sent to {phone} on Telegram.")
    print("Now run:  python scripts/tg_login.py sign-in <code> [2fa_password]")


async def sign_in(code: str, password: str | None) -> None:
    api_id, api_hash = _creds()
    state = json.loads(_STATE.read_text())
    client = TelegramClient(StringSession(state["session"]), api_id, api_hash)
    await client.connect()
    try:
        await client.sign_in(
            phone=state["phone"], code=code,
            phone_code_hash=state["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        if not password:
            await client.disconnect()
            sys.exit("2FA is on — re-run: sign-in <code> <your_2fa_password>")
        await client.sign_in(password=password)
    me = await client.get_me()
    final = client.session.save()
    await client.disconnect()
    _STATE.unlink(missing_ok=True)
    print(f"OK: logged in as {me.first_name} (@{me.username}, id={me.id})")
    print("\n=== TELEGRAM_SESSION (paste into backend/.env) ===")
    print(final)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "send-code" and len(sys.argv) >= 3:
        asyncio.run(send_code(sys.argv[2]))
    elif cmd == "sign-in" and len(sys.argv) >= 3:
        pw = sys.argv[3] if len(sys.argv) >= 4 else None
        asyncio.run(sign_in(sys.argv[2], pw))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
