"""Self-test: send one email from any provider (Gmail, Hostinger, Outlook...).

Run on YOUR PC. Asks for the email address + password (hidden, never stored,
never leaves your machine), auto-detects the SMTP host, and sends a test email
to yourself.

    cd D:\\FarryOn\\backend
    .venv\\Scripts\\python scripts\\test_send.py
"""

from __future__ import annotations

import getpass
import smtplib
import ssl
from email.message import EmailMessage

# domain -> (smtp_host, port)  [465 = implicit TLS, 587 = STARTTLS]
PRESETS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp.office365.com", 587),
    "hotmail.com": ("smtp.office365.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 465),
    "izylrn.com": ("smtp.hostinger.com", 465),
}


def main() -> None:
    address = input("Your email address: ").strip()
    domain = address.split("@")[-1].lower()
    host, port = PRESETS.get(domain, ("", 0))
    if not host:
        host = input(f"SMTP host for {domain}: ").strip()
        port = int(input("SMTP port (465 or 587): ").strip() or "587")
    print(f"Using {host}:{port}")
    note = "16-digit App Password" if "gmail" in host else "mailbox password"
    password = getpass.getpass(f"Password ({note}, hidden): ")

    msg = EmailMessage()
    msg["From"] = address
    msg["To"] = address
    msg["Subject"] = "FarryOn SMTP test"
    msg.set_content("If you got this, sending works! - FarryOn")

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ctx) as s:
                s.login(address, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(address, password)
                s.send_message(msg)
        print(f"\nSUCCESS - test email sent. Check the inbox of {address}")
    except smtplib.SMTPAuthenticationError:
        extra = ("Use a 16-digit App Password, not your login password."
                 if "gmail" in host else "Check the mailbox password.")
        print(f"\nLOGIN FAILED - {extra}")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED - {exc!r}")


if __name__ == "__main__":
    main()
