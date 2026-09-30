"""Auth endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import Depends
from pydantic import BaseModel

from app.auth.jwt import CurrentUser, create_token, get_current_user
from app.auth.users import USERS, authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    username: str


@router.post("/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends()):
    user = authenticate(form.username, form.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_token(user.username, user.role)
    return TokenResponse(
        access_token=token, role=user.role, username=user.username
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(user: CurrentUser = Depends(get_current_user)):
    """Exchange a still-valid token for a fresh one.

    Tokens last 8 hours with no renewal path, so a long analyst session died
    mid-work and lost its place. The frontend can call this periodically to
    stay signed in. The role is re-read from the user store rather than copied
    from the old token, so a role change (or a removed account) takes effect on
    the next refresh instead of persisting until expiry.
    """
    current = USERS.get(user.username)
    if not current:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer exists",
        )
    token = create_token(current.username, current.role)
    return TokenResponse(
        access_token=token, role=current.role, username=current.username
    )
