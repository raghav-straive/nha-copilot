"""Auth endpoints.

Login issues the token two ways:

  * an **httpOnly cookie** — not readable by page scripts, so an injected
    script cannot steal it. This is the preferred mechanism and works whenever
    the frontend and backend share an origin, which is LAYOUT A in
    deploy/nginx.conf.example (the recommended setup: nginx serves the built
    frontend and proxies /auth, /chat, ... to the backend on the same host).

  * the token **in the response body**, as before. Needed for cross-origin
    deployments (LAYOUT B, and the GitHub Pages demo), where the cookie would
    be a third-party cookie and is blocked by default in Safari and
    increasingly in Chrome. Also what API clients use.

The frontend probes which mode it has (see frontend/src/api.ts): if the cookie
works it keeps no token in browser storage at all.

CSRF: the cookie is SameSite=Lax, so browsers do not attach it to cross-site
POSTs, which is what would otherwise let another site act as the signed-in
user. State-changing endpoints are all POSTs, so Lax covers them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from app.auth.jwt import AUTH_COOKIE, CurrentUser, create_token, get_current_user
from app.auth.users import USERS, authenticate
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


def _set_auth_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        AUTH_COOKIE,
        token,
        httponly=True,          # invisible to page scripts
        samesite="lax",         # not sent on cross-site POSTs (CSRF defence)
        secure=s.cookie_secure,  # HTTPS only unless explicitly relaxed for local dev
        max_age=s.jwt_expire_minutes * 60,
        path="/",
    )


@router.post("/login", response_model=TokenResponse)
def login(response: Response, form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate(form.username, form.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_token(user.username, user.role)
    _set_auth_cookie(response, token)
    return TokenResponse(
        access_token=token, role=user.role, username=user.username
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(response: Response, user: CurrentUser = Depends(get_current_user)):
    """Exchange a still-valid token for a fresh one.

    Tokens last 8 hours with no renewal path, so a long analyst session died
    mid-work. The frontend also calls this on load to restore a session from
    the cookie without ever holding the token in browser storage.

    The role is re-read from the user store rather than copied from the old
    token, so a role change — or a removed account — takes effect at the next
    refresh instead of persisting until expiry.
    """
    current = USERS.get(user.username)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer exists",
        )
    token = create_token(current.username, current.role)
    _set_auth_cookie(response, token)
    return TokenResponse(
        access_token=token, role=current.role, username=current.username
    )


@router.post("/logout")
def logout(response: Response, request: Request):
    """Clear the auth cookie.

    Deliberately unauthenticated: signing out must work even with an expired or
    malformed token, and it only ever removes a cookie. A bearer token held by
    a client cannot be revoked server-side without a token blocklist, so the
    frontend also drops its copy.
    """
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"status": "signed out"}
