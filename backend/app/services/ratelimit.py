import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.errors import ApiError


class RateLimiter:
    """프로세스 메모리 기반 고정 창 제한. 워커 1개 구성(요구사항 5장)이라 이걸로 충분하다."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                raise ApiError(429, "RATE_LIMITED", "요청이 너무 많습니다. 잠시 후 다시 시도하세요.")
            hits.append(now)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
