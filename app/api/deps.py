"""FastAPI dependencies: the authenticated user (with per-user rate limit) and app services."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.api.auth import AuthError, CurrentUser, bearer, http_error, resolve_user


class RateLimiter:
    """Sliding one-minute window per user (TDD §23: 60 requests a minute). In-process only."""

    def __init__(self, per_min: int):
        self.per_min = per_min
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_min:
            return False
        q.append(now)
        return True


async def current_user(request: Request) -> CurrentUser:
    try:
        user = await resolve_user(request.app.state, bearer(request))
    except AuthError as e:
        raise http_error(e) from e
    if not request.app.state.rate_limiter.check(str(user.user_id)):
        raise HTTPException(status_code=429, detail="rate_limited")
    return user


async def admin_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:  # noqa: B008
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin_only")
    return user
