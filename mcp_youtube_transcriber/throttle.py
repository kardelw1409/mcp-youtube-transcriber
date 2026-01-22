import asyncio
import random
import re
import time
from typing import Optional

from .policy import FetchPolicy


class RequestThrottler:
    def __init__(self, policy: FetchPolicy) -> None:
        self._policy = policy
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._cooldown_until = 0.0
        self._consecutive_429 = 0

    async def wait_for_slot(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                await asyncio.sleep(self._cooldown_until - now)

            now = time.monotonic()
            target_gap = self._policy.min_interval_seconds + random.uniform(
                0.0, self._policy.jitter_seconds
            )
            elapsed = now - self._last_request_at
            if elapsed < target_gap:
                await asyncio.sleep(target_gap - elapsed)
            self._last_request_at = time.monotonic()

    def register_success(self) -> None:
        self._consecutive_429 = 0

    def register_rate_limit(self) -> None:
        self._consecutive_429 += 1
        if self._consecutive_429 >= 2:
            self._cooldown_until = time.monotonic() + self._policy.cooldown_seconds


def is_rate_limit_error(exc: Exception) -> bool:
    for attr in ("status_code", "status"):
        if getattr(exc, attr, None) == 429:
            return True
    message = str(exc).lower()
    if "429" in message and ("too many requests" in message or "http" in message):
        return True
    if "rate limit" in message:
        return True
    return False


def parse_retry_after_seconds(message: str) -> Optional[int]:
    match = re.search(r"retry-after[:=]\\s*(\\d+)", message, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None

