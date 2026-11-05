"""Supabase JWT verification (JWKS), dev bypass, and the invite allowlist (TDD §10.3, §11).

- Production: `Authorization: Bearer <Supabase access token>`; verified against the project's JWKS
  (ES256/RS256), or the legacy HS256 secret if SUPABASE_JWT_SECRET is set. `sub` → user_id.
- Dev: DEV_AUTH_BYPASS=1 (ignored when APP_ENV=prod) accepts `Bearer dev:<uuid>[:<email>]`, or
  plain `Bearer dev` for a fixed, auto-invited local dev user (dev@localhost, admin).
- First login: a profile is created only if the email is on the invite list; otherwise 403.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import jwt
from fastapi import HTTPException, Request, WebSocket

from app.config.settings import Settings

DEV_USER_ID = UUID("00000000-0000-4000-8000-000000000001")
DEV_EMAIL = "dev@localhost"


class AuthError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class CurrentUser:
    user_id: UUID
    email: str | None
    role: str
    profile: dict[str, Any]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class JwtVerifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None, ttl_s: int = 600):
        self.s = settings
        self.client = client
        self.ttl_s = ttl_s
        self._keys: dict[str, Any] = {}
        self._fetched = 0.0

    async def _jwks(self, force: bool = False) -> dict[str, Any]:
        if not force and self._keys and time.time() - self._fetched < self.ttl_s:
            return self._keys
        url = self.s.jwks_url
        if not url:
            raise AuthError(503, "auth_not_configured")
        client = self.client or httpx.AsyncClient(timeout=10)
        try:
            r = await client.get(url)
            r.raise_for_status()
            keys = {}
            for k in r.json().get("keys", []):
                keys[k.get("kid", "")] = jwt.PyJWK.from_json(json.dumps(k))
        except (httpx.HTTPError, ValueError, jwt.PyJWKError) as e:
            raise AuthError(503, "jwks_unavailable") from e
        finally:
            if self.client is None:
                await client.aclose()
        self._keys, self._fetched = keys, time.time()
        return keys

    async def verify(self, token: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise AuthError(401, "invalid_token") from e
        alg = header.get("alg")
        opts = {"require": ["exp", "sub"]}
        try:
            if alg == "HS256":
                if not self.s.supabase_jwt_secret:
                    raise AuthError(401, "invalid_token")
                return jwt.decode(
                    token,
                    self.s.supabase_jwt_secret,
                    algorithms=["HS256"],
                    audience=self.s.supabase_jwt_audience,
                    options=opts,
                )
            if alg not in ("RS256", "ES256"):
                raise AuthError(401, "invalid_token")
            keys = await self._jwks()
            key = keys.get(header.get("kid", ""))
            if key is None:
                key = (await self._jwks(force=True)).get(header.get("kid", ""))
            if key is None:
                raise AuthError(401, "unknown_key")
            return jwt.decode(
                token, key.key, algorithms=[alg], audience=self.s.supabase_jwt_audience, options=opts
            )
        except jwt.ExpiredSignatureError as e:
            raise AuthError(401, "token_expired") from e
        except jwt.PyJWTError as e:
            raise AuthError(401, "invalid_token") from e


async def identity_from_token(
    token: str, settings: Settings, verifier: JwtVerifier
) -> tuple[UUID, str | None]:
    if token == "dev":  # W-F dev shortcut: a fixed local dev user (dev bypass only)
        token = f"dev:{DEV_USER_ID}:{DEV_EMAIL}"
    if token.startswith("dev:"):
        if not settings.dev_bypass_enabled:
            raise AuthError(401, "invalid_token")
        parts = token.split(":", 2)
        try:
            return UUID(parts[1]), (parts[2].lower() if len(parts) > 2 and parts[2] else None)
        except (ValueError, IndexError) as e:
            raise AuthError(401, "invalid_token") from e
    claims = await verifier.verify(token)
    try:
        uid = UUID(str(claims["sub"]))
    except ValueError as e:
        raise AuthError(401, "invalid_token") from e
    email = claims.get("email")
    return uid, email.lower() if email else None


async def resolve_user(app_state: Any, token: str | None) -> CurrentUser:
    if not token:
        raise AuthError(401, "missing_token")
    uid, email = await identity_from_token(token, app_state.settings, app_state.verifier)
    db = app_state.db
    async with db.user_scope(uid) as s:
        prof = await s.profiles.me()
    if prof is None:
        if not email:
            raise AuthError(403, "not_invited")
        async with db.system_scope() as s:
            if email == DEV_EMAIL and app_state.settings.dev_bypass_enabled:
                await s.invites.upsert(email=email, role="admin")  # local dev user is pre-invited
            inv = await s.invites.get(email)
            if inv is None:
                raise AuthError(403, "not_invited")
            await s.execute(
                "update invites set accepted_at = coalesce(accepted_at, now()) where email = %s", [email]
            )
        async with db.user_scope(uid) as s:
            prof = await s.profiles.upsert(user_id=uid, email=email, role=inv["role"], status="onboarding")
    return CurrentUser(user_id=uid, email=prof["email"], role=prof["role"], profile=prof)


def bearer(request: Request | WebSocket) -> str | None:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return None


def http_error(e: AuthError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.code)
