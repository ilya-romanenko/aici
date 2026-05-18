from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class _SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def try_acquire(self, key: str) -> tuple[bool, float, int, float]:
        """Attempt to record a hit and return rate limit metadata."""

        now = time.monotonic()
        async with self._lock:
            queue = self._hits[key]
            window = self.window_seconds

            while queue and now - queue[0] >= window:
                queue.popleft()

            if len(queue) >= self.limit:
                retry_after = window - (now - queue[0])
                return False, max(retry_after, 0.0), 0, max(retry_after, 0.0)

            queue.append(now)
            remaining = max(self.limit - len(queue), 0)
            reset_in = window - (now - queue[0]) if queue else window
            return True, 0.0, remaining, max(reset_in, 0.0)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory sliding window limiter for API routes."""

    def __init__(self, app, *, limit: int = 120, window_seconds: int = 60) -> None:
        super().__init__(app)
        self._limiter = _SlidingWindowLimiter(limit, window_seconds)

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: D401
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        client = request.client.host if request.client else "anonymous"
        key = f"{client}:{path}"

        allowed, retry_after, remaining, reset_in = await self._limiter.try_acquire(key)
        if not allowed:
            retry_after_header = max(int(retry_after), 1)
            return JSONResponse(
                {"detail": "rate_limit_exceeded", "retry_after": round(retry_after, 2)},
                status_code=429,
                headers={
                    "Retry-After": str(retry_after_header),
                    "X-RateLimit-Limit": str(self._limiter.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        epoch_reset = int(time.time() + reset_in)
        response.headers.setdefault("X-RateLimit-Limit", str(self._limiter.limit))
        response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
        response.headers.setdefault("X-RateLimit-Reset", str(epoch_reset))
        return response


__all__ = ["RateLimitMiddleware"]
