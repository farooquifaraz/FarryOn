"""Keep a broken machine-level CA setting from taking the backend down.

``requests`` — which google-auth, authlib and our own tool HTTP calls all sit on
— reads ``REQUESTS_CA_BUNDLE`` / ``CURL_CA_BUNDLE`` from the environment and
uses whatever path it finds as the TLS trust store, *without checking that the
file exists*. If it doesn't, every single HTTPS call raises::

    OSError: Could not find a suitable TLS CA certificate bundle,
             invalid path: <that path>

This is not hypothetical. The PostgreSQL 18 Windows installer sets
``CURL_CA_BUNDLE=C:\\Program Files\\PostgreSQL\\18\\ssl\\certs\\ca-bundle.crt``
in the user environment, and does not ship that file. Every process the user
launches afterwards inherits it — so ``pip`` breaks, and so does Google
sign-in, which surfaces to the user as "Couldn't reach Google to verify your
sign-in" with nothing on the phone to explain it. It cost a full debugging
session to find, twice.

Dropping a pointer to a file that isn't there cannot weaken anything: requests
falls back to certifi's public CA bundle, which is the normal default, and the
alternative isn't "stricter checking" — it's a hard crash on every request. A
path that *does* exist is left alone: a corporate proxy CA is a legitimate
setup and none of our business.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.logging_conf import get_logger

logger = get_logger(__name__)

#: The env vars requests consults for a CA bundle, in its own precedence order.
_CA_ENV_VARS = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE")


def sanitize_ca_env() -> list[str]:
    """Unset any CA-bundle env var pointing at a path that doesn't exist.

    Returns the names of the vars dropped, so a caller can assert on it. Safe to
    call repeatedly — a var is only dropped once, and a valid one is never
    touched.
    """
    dropped: list[str] = []
    for var in _CA_ENV_VARS:
        value = os.environ.get(var)
        if not value or not value.strip():
            continue
        if Path(value).is_file():
            continue
        os.environ.pop(var, None)
        dropped.append(var)
        logger.warning(
            "tls.ca_bundle_env_ignored",
            variable=var,
            path=value,
            reason="path does not exist; falling back to certifi's CA bundle",
        )
    return dropped
