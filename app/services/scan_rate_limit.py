import logging
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)


class ScanRateLimiter:
    def __init__(
        self,
        *,
        start_limit: int = 10,
        frame_limit: int = 400,
        window_seconds: int = 60,
        max_clients: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(start_limit, frame_limit, window_seconds, max_clients) < 1:
            raise ValueError(
                "Rate limits, window, and client capacity must be positive."
            )
        self._limits = {"start": start_limit, "frame": frame_limit}
        self._window_seconds = window_seconds
        self._max_clients = max_clients
        self._clock = clock
        self._events: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def try_acquire(self, client: str, scope: str) -> int | None:
        if scope not in self._limits:
            raise ValueError(f"Unsupported scan rate-limit scope: {scope}")

        now = self._clock()
        cutoff = now - self._window_seconds
        key = (client, scope)
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_clients:
                    self._events.popitem(last=False)
                events = deque()
                self._events[key] = events
            else:
                self._events.move_to_end(key)

            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limits[scope]:
                return max(1, math.ceil(self._window_seconds - (now - events[0])))

            events.append(now)
            return None


_initialization_lock = threading.Lock()


def _client_key(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def _enforce_limit(request: Request, scope: str) -> None:
    limiter = getattr(request.app.state, "scan_rate_limiter", None)
    if limiter is None:
        with _initialization_lock:
            limiter = getattr(request.app.state, "scan_rate_limiter", None)
            if limiter is None:
                settings = get_settings()
                limiter = ScanRateLimiter(
                    start_limit=settings.scan_start_limit_per_minute,
                    frame_limit=settings.scan_frame_limit_per_minute,
                )
                request.app.state.scan_rate_limiter = limiter

    retry_after = limiter.try_acquire(_client_key(request), scope)
    if retry_after is not None:
        logger.warning("Kiosk scan rate limit exceeded: scope=%s", scope)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many scan requests. Please wait before trying again.",
            headers={"Retry-After": str(retry_after)},
        )


def limit_scan_start(request: Request) -> None:
    _enforce_limit(request, "start")


def limit_scan_frame(request: Request) -> None:
    _enforce_limit(request, "frame")
