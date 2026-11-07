from __future__ import annotations

import asyncio

import pytest

from app.telegram_transfer import AdaptiveTransferGate


@pytest.mark.asyncio
async def test_transfer_gate_limits_concurrency_while_saturated():
    gate = AdaptiveTransferGate(4, min_request_interval=0)
    active = 0
    max_active = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def worker():
        nonlocal active, max_active
        async with gate.slot():
            active += 1
            max_active = max(max_active, active)
            entered.set()
            await release.wait()
            active -= 1

    tasks = [asyncio.create_task(worker()) for _ in range(3)]
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.sleep(0.02)

    assert max_active == 1

    release.set()
    await asyncio.gather(*tasks)
    assert max_active == 1


@pytest.mark.asyncio
async def test_transfer_gate_recovers_and_backs_off_after_slow_request():
    gate = AdaptiveTransferGate(
        4,
        min_request_interval=0,
        slow_request_seconds=0.05,
        recovery_request_count=2,
    )

    for _ in range(6):
        async with gate.slot():
            pass

    assert gate.current_limit == 4

    async with gate.slot():
        await asyncio.sleep(0.06)

    assert gate.current_limit == 1
