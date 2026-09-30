"""Shared rate limiter (slowapi).

Keyed on the authenticated user rather than the client address. Behind the
reverse proxy every request arrives from 127.0.0.1 unless uvicorn runs with
--proxy-headers, so an IP key silently collapses the documented "60/min per
user" into a single bucket shared by everyone at once. The JWT subject is the
right unit for a per-request-cost LLM endpoint anyway, and it needs no proxy
configuration to be correct.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def user_key(request: Request) -> str:
    """Rate-limit bucket for a request: the logged-in user, else the client IP.

    Keying on the `sub` claim rather than the raw token means re-logging in does
    not reset the bucket, and no token material reaches limiter storage or logs.
    """
    # Imported lazily: main.py imports this module early, and app.auth.jwt
    # pulls in config.
    from app.auth.jwt import decode_token, token_from_request

    auth = request.headers.get("authorization", "")
    header_token = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else None
    # Same precedence as the auth dependency: cookie first, then header. Without
    # the cookie here, every cookie-authenticated user would share the IP bucket.
    token = token_from_request(request, header_token)
    if token:
        try:
            sub = decode_token(token).get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:  # noqa: BLE001 - unauthenticated/expired: fall back to IP
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=user_key)
