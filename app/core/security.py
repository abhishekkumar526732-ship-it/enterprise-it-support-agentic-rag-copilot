"""
JWT / OIDC / Role-Based Access Control (RBAC) module.

Roles
─────
  admin   – full access: chat, ingest, evaluate, manage users
  analyst – chat, view traces, run evaluations
  user    – chat only
"""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ───────────────────────────── password hashing ──────────────────
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ───────────────────────────── role enum ─────────────────────────

class Role(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    USER = "user"


ROLE_HIERARCHY: dict[Role, int] = {
    Role.ADMIN: 100,
    Role.ANALYST: 50,
    Role.USER: 10,
}

# ───────────────────────────── token models ──────────────────────

class TokenPayload(BaseModel):
    sub: str
    role: Role = Role.USER
    exp: Optional[datetime] = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class UserRecord(BaseModel):
    """Minimal user record for in-memory demo store."""
    username: str
    hashed_password: str
    role: Role = Role.USER
    disabled: bool = False


# ─── In-memory user store (replace with DB in production) ───────
_USERS: dict[str, UserRecord] = {
    "admin": UserRecord(
        username="admin",
        hashed_password=pwd_context.hash("admin123"),
        role=Role.ADMIN,
    ),
    "analyst": UserRecord(
        username="analyst",
        hashed_password=pwd_context.hash("analyst123"),
        role=Role.ANALYST,
    ),
    "user": UserRecord(
        username="user",
        hashed_password=pwd_context.hash("user123"),
        role=Role.USER,
    ),
}

# ───────────────────────────── helpers ───────────────────────────

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def get_user(username: str) -> Optional[UserRecord]:
    try:
        from app.db.session import get_session_factory
        from app.db.models import User as DBUser
        factory = get_session_factory()
        with factory() as session:
            db_user = session.query(DBUser).filter(DBUser.username == username).first()
            if db_user:
                return UserRecord(
                    username=db_user.username,
                    hashed_password=db_user.hashed_password,
                    role=Role(db_user.role),
                    disabled=not db_user.is_active,
                )
    except Exception as exc:
        logger.debug("Database user lookup failed, falling back to memory: %s", exc)
    return _USERS.get(username)


def authenticate_user(username: str, password: str) -> Optional[UserRecord]:
    user = get_user(username)
    if user and not user.disabled and verify_password(password, user.hashed_password):
        return user
    return None


def create_user(username: str, password: str, role: Role = Role.USER) -> UserRecord:
    hashed = hash_password(password)
    # Attempt to persist in PostgreSQL / SQLAlchemy database
    try:
        from app.db.session import get_session_factory
        from app.db.models import User as DBUser
        factory = get_session_factory()
        with factory() as session:
            existing = session.query(DBUser).filter(DBUser.username == username).first()
            if existing or username in _USERS:
                raise ValueError(f"User '{username}' already exists")
            new_user = DBUser(
                username=username,
                hashed_password=hashed,
                role=role.value,
                is_active=True,
            )
            session.add(new_user)
            session.commit()
    except ValueError:
        raise
    except Exception as exc:
        logger.debug("Database not reachable during user creation, persisting to memory store: %s", exc)
        if username in _USERS:
            raise ValueError(f"User '{username}' already exists")

    record = UserRecord(
        username=username,
        hashed_password=hashed,
        role=role,
    )
    _USERS[username] = record
    return record


# ───────────────────────────── JWT creation ──────────────────────

def _create_token(data: dict, expires_delta: timedelta) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + expires_delta
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_token_pair(user: UserRecord) -> TokenPair:
    access = _create_token(
        {"sub": user.username, "role": user.role.value, "type": "access"},
        timedelta(minutes=settings.jwt_access_token_expire_minutes),
    )
    refresh = _create_token(
        {"sub": user.username, "role": user.role.value, "type": "refresh"},
        timedelta(minutes=settings.jwt_refresh_token_expire_minutes),
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


def decode_token(token: str) -> TokenPayload:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return TokenPayload(
            sub=payload["sub"],
            role=Role(payload.get("role", "user")),
            exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ───────────────────────────── OIDC Discovery ───────────────────

_oidc_config_cache: dict | None = None


async def fetch_oidc_config() -> dict:
    """Fetch and cache the OIDC provider configuration."""
    global _oidc_config_cache
    if _oidc_config_cache:
        return _oidc_config_cache
    if not settings.oidc_discovery_url:
        raise HTTPException(status_code=500, detail="OIDC discovery URL not configured")
    async with httpx.AsyncClient() as client:
        resp = await client.get(settings.oidc_discovery_url, timeout=10)
        resp.raise_for_status()
        _oidc_config_cache = resp.json()
        return _oidc_config_cache


async def exchange_oidc_code(code: str) -> dict:
    """Exchange authorization code for tokens via the OIDC provider."""
    config = await fetch_oidc_config()
    token_endpoint = config["token_endpoint"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


async def get_oidc_userinfo(access_token: str) -> dict:
    """Get userinfo from the OIDC provider."""
    config = await fetch_oidc_config()
    userinfo_endpoint = config["userinfo_endpoint"]
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()


# ───────────────────────────── FastAPI dependencies ──────────────

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> TokenPayload:
    """Dependency: extract and validate JWT from Authorization header."""
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(creds.credentials)


def require_role(minimum_role: Role):
    """Factory that returns a dependency requiring at least *minimum_role*."""
    async def _check(user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        user_level = ROLE_HIERARCHY.get(user.role, 0)
        required_level = ROLE_HIERARCHY.get(minimum_role, 0)
        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.value}' insufficient; need at least '{minimum_role.value}'",
            )
        return user
    return _check


# convenience shortcuts
require_admin = require_role(Role.ADMIN)
require_analyst = require_role(Role.ANALYST)
require_user = require_role(Role.USER)
