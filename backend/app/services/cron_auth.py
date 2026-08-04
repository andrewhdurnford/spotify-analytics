"""Cron bearer-token validation.

Shared by the cron endpoint and its rate-limit dependency so both agree on
what "authenticated" means without duplicating the comparison.
"""

import secrets

from ..config import settings


def cron_token_is_valid(authorization: str | None) -> bool:
    """Return True only for an `Authorization` header matching CRON_SECRET.

    A blank CRON_SECRET never authenticates anything, regardless of what's
    sent -- otherwise a misconfigured (empty) secret would let an empty or
    `Bearer ` token through. The comparison is constant-time to avoid leaking
    the secret's value/length via response timing, and operates on bytes:
    `secrets.compare_digest` raises TypeError on non-ASCII `str` inputs, and
    Starlette decodes headers as latin-1, so a header like `Bearer é` would
    otherwise turn a 401 into an unhandled 500.
    """
    configured_secret = settings.cron_secret
    if not configured_secret:
        return False

    provided = (authorization or "").encode("utf-8", "surrogateescape")
    expected = f"Bearer {configured_secret}".encode()
    return secrets.compare_digest(provided, expected)
