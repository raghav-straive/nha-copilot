"""JWT issue/verify and the FastAPI current-user dependency.

Uses PyJWT. The project previously used python-jose, which has had no release
in years and calls a date function Python has deprecated and scheduled for
removal — an unmaintained library is a poor place to keep authentication.

`InvalidTokenError` is the base class for every PyJWT verification failure
(expired, bad signature, malformed, wrong algorithm), so catching it covers the
same ground the old `JWTError` did.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jwt import InvalidTokenError

from app.config import get_settings

# auto_error=False so a request carrying the auth cookie instead of a header is
# not rejected before we get a chance to look at the cookie.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# Name of the httpOnly cookie that carries the token on same-origin deployments.
AUTH_COOKIE = "nha_token"


def create_token(username: str, role: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": username, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    # algorithms is a whitelist: without it a token could name its own
    # algorithm (including "none") and bypass signature verification.
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


class CurrentUser:
    def __init__(self, username: str, role: str) -> None:
        self.username = username
        self.role = role


def token_from_request(request: Request, header_token: str | None = None) -> str | None:
    """The caller's token: cookie first, then the Authorization header.

    Cookie first because that is the safer mechanism — an httpOnly cookie is
    not readable by injected scripts — so a same-origin deployment that sets it
    should win over any stale header. The header remains supported for
    cross-origin deployments (where the cookie would be a third-party cookie
    and is blocked by default in several browsers) and for API clients.
    """
    cookie = request.cookies.get(AUTH_COOKIE)
    if cookie:
        return cookie
    return header_token


def get_current_user(
    request: Request,
    header_token: str | None = Depends(oauth2_scheme),
) -> CurrentUser:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = token_from_request(request, header_token)
    if not token:
        raise credentials_exception
    try:
        payload = decode_token(token)
        username = payload.get("sub")
        role = payload.get("role")
        if not username or not role:
            raise credentials_exception
    except InvalidTokenError:
        raise credentials_exception
    return CurrentUser(username=username, role=role)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
