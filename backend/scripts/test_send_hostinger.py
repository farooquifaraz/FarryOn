"""Self-test: actually send one email via Hostinger SMTP.

Run on YOUR PC. It asks for the mailbox password (hidden — never stored, never
leaves your machine) and sends a test email to yourself. Proves send works
end-to-end before testing in the app.

    cd D:\\FarryOn\\backend
    .venv\\Scripts\\python scripts\\test_send_hostinger.py
"""

from __future__ import annotations

import getpass
import smtplib
import ssl
from email.message import EmailMessage

ADDRESS = "students@izylrn.com"
SMTP_HOST = "smtp.hostinger.com"
SMTP_PORT = 465  # implicit TLS


def main() -> None:
    print(f"Sending a test email FROM and TO {ADDRESS}")
    password = getpass.getpass("Mailbox password (hidden): ")

    msg = EmailMessage()
    msg["From"] = ADDRESS
    msg["To"] = ADDRESS
    msg["Subject"] = "FarryOn SMTP test"
    msg.set_content("If you got this, sending works! — FarryOn")

    ctx = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=ctx) as s:
            s.login(ADDRESS, password)
            s.send_message(msg)
        print("\nSUCCESS - test email sent. Check the inbox of", ADDRESS)
    except smtplib.SMTPAuthenticationError:
        print("\nLOGIN FAILED - wrong mailbox password (reset it in Hostinger "
              "hPanel -> Emails).")
    except Exception as exc:  # noqa: BLE001
        print(f"\nFAILED - {exc!r}")


if __name__ == "__main__":
    main()
