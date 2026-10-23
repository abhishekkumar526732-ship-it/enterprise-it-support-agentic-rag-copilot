"""
Authentication API — JWT / OIDC endpoints
──────────────────────────────────────────
POST /api/auth/token     → Local login (username + password → JWT pair)
POST /api/auth/refresh   → Refresh access token
GET  /api/auth/me        → Current user info
POST /api/auth/register  → Register new user (admin only)
GET  /api/auth/oidc/login    → Redirect to OIDC provider
GET  /api/auth/oidc/callback → Handle OIDC callback
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import (
    Role,
    TokenPair,
    TokenPayload,
    authenticate_user,
    create_token_pair,
    create_user,
    decode_token,
    get_current_user,
    get_user,
    require_admin,
    exchange_oidc_code,
    fetch_oidc_config,
    get_oidc_userinfo,
)

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ───────────────────────────── Request / Response models ─────────

class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=4, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6, max_length=200)
    role: str = Field(default="user", pattern="^(admin|analyst|user)$")


class UserInfo(BaseModel):
    username: str
    role: str


# ───────────────────────────── Local JWT endpoints ───────────────

@router.post("/token", response_model=TokenPair)
def login(payload: LoginRequest):
    """Authenticate with username + password, receive a JWT token pair."""
    user = authenticate_user(payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    tokens = create_token_pair(user)
    logger.info("Login successful: %s (role=%s)", user.username, user.role.value)
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest):
    """Exchange a refresh token for a new token pair."""
    token_data = decode_token(payload.refresh_token)
    user = get_user(token_data.sub)
    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return create_token_pair(user)


@router.get("/me", response_model=UserInfo)
def me(current_user: TokenPayload = Depends(get_current_user)):
    """Return the current authenticated user's identity and role."""
    return UserInfo(username=current_user.sub, role=current_user.role.value)


@router.post("/register", response_model=UserInfo)
def register(
    payload: RegisterRequest,
    _admin: TokenPayload = Depends(require_admin),
):
    """Register a new user (admin only)."""
    try:
        user = create_user(payload.username, payload.password, Role(payload.role))
        logger.info("User registered: %s (role=%s) by admin=%s", user.username, user.role.value, _admin.sub)
        return UserInfo(username=user.username, role=user.role.value)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ───────────────────────────── OIDC endpoints ────────────────────

@router.get("/oidc/login")
async def oidc_login():
    """Redirect the user to the OIDC provider's authorization endpoint."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")
    config = await fetch_oidc_config()
    auth_url = config["authorization_endpoint"]
    params = (
        f"?client_id={settings.oidc_client_id}"
        f"&redirect_uri={settings.oidc_redirect_uri}"
        f"&response_type=code"
        f"&scope=openid email profile"
    )
    return RedirectResponse(url=auth_url + params)


@router.get("/oidc/callback")
async def oidc_callback(code: str):
    """Handle the OIDC callback: exchange code → tokens → local JWT."""
    if not settings.oidc_enabled:
        raise HTTPException(status_code=400, detail="OIDC is not enabled")
    try:
        token_response = await exchange_oidc_code(code)
        userinfo = await get_oidc_userinfo(token_response["access_token"])
        email = userinfo.get("email", userinfo.get("sub", "unknown"))

        # auto-provision user if not exists
        user = get_user(email)
        if not user:
            user = create_user(email, "oidc-managed", Role.USER)
            logger.info("OIDC auto-provisioned user: %s", email)

        tokens = create_token_pair(user)
        logger.info("OIDC login successful: %s", email)
        return tokens

    except Exception as exc:
        logger.error("OIDC callback failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"OIDC authentication failed: {exc}")
