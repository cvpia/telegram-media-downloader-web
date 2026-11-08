"""Adaptive request scheduling for Telegram media transfers."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable


MAX_TRANSFER_CONCURRENCY = 10
MIN_REQUEST_INTERVAL = 0.05
SLOW_REQUEST_SECONDS = 5.0
RECOVERY_REQUEST_COUNT = 8


def _bounded_concurrency(value: int) -> int:
    return max(1, min(int(value), MAX_TRANSFER_CONCURRENCY))


class AdaptiveTransferGate:
    """Control GetFile concurrency and back off when Telegram throttles.

    Telegram rate limits media downloads per session. Starting many
    ``upload.GetFile`` requests at once can make every stream enter FloodWait
    at the same time. This gate starts conservatively, spaces request starts,
    immediately falls back to one stream after a slow/throttled request, and
    only raises concurrency after a run of healthy requests.
    """

    def __init__(
        self,
        max_concurrency: int | Callable[[], int],
        *,
        initial_concurrency: int = 1,
        min_request_interval: float = MIN_REQUEST_INTERVAL,
        slow_request_seconds: float = SLOW_REQUEST_SECONDS,
        recovery_request_count: int = RECOVERY_REQUEST_COUNT,
    ) -> None:
        self._max_concurrency = max_concurrency
        self._limit = _bounded_concurrency(initial_concurrency)
        self._min_request_interval = max(0.0, float(min_request_interval))
        self._slow_request_seconds = max(0.0, float(slow_request_seconds))
        self._recovery_request_count = max(1, int(recovery_request_count))
        self._active = 0
        self._healthy_requests = 0
        self._next_request_at = 0.0
        self._cooldown_until = 0.0
        self._condition = asyncio.Condition()

    @property
    def current_limit(self) -> int:
        return self._limit

    def _configured_limit(self) -> int:
        value = (
            self._max_concurrency()
            if callable(self._max_concurrency)
            else self._max_concurrency
        )
        return _bounded_concurrency(value)

    async def _acquire(self) -> float:
        async with self._condition:
            while True:
                configured_limit = self._configured_limit()
                self._limit = min(self._limit, configured_limit)
                now = time.monotonic()
                gate_until = max(self._next_request_at, self._cooldown_until)
                if self._active < self._limit and now >= gate_until:
                    self._active += 1
                    self._next_request_at = now + self._min_request_interval
                    return now

                timeout = gate_until - now if gate_until > now else None
                try:
                    if timeout is None:
                        await self._condition.wait()
                    else:
                        await asyncio.wait_for(self._condition.wait(), timeout)
                except TimeoutError:
                    pass

    async def _release(self, started_at: float, failed: bool) -> None:
        async with self._condition:
            self._active = max(0, self._active - 1)
            elapsed = max(0.0, time.monotonic() - started_at)
            if failed or elapsed >= self._slow_request_seconds:
                self._limit = 1
                self._healthy_requests = 0
                cooldown = min(max(elapsed / 2, 0.25), 3.0)
                self._cooldown_until = max(
                    self._cooldown_until, time.monotonic() + cooldown
                )
            else:
                self._healthy_requests += 1
                configured_limit = self._configured_limit()
                if (
                    self._healthy_requests >= self._recovery_request_count
                    and self._limit < configured_limit
                ):
                    self._limit += 1
                    self._healthy_requests = 0
            self._condition.notify_all()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        started_at = await self._acquire()
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            await self._release(started_at, failed)
