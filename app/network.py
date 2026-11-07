"""Outbound network connectivity checks used by the setup wizard."""

from __future__ import annotations

import time

import httpx

from app.version import APP_VERSION


class NetworkTestError(RuntimeError):
    """Raised when the Telegram connectivity probe fails."""


async def test_network_connection(
    proxy_url: str,
    target_url: str,
    timeout: float = 12.0,
) -> dict[str, object]:
    """Check whether the configured proxy can reach Telegram."""

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url or None,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": f"Telegram-Media-Downloader/{APP_VERSION}"},
        ) as client:
            response = await client.get(target_url)
    except Exception as exc:
        mode = "代理" if proxy_url else "直连"
        raise NetworkTestError(f"{mode}网络测试失败: {exc}") from exc

    latency_ms = round((time.perf_counter() - started) * 1000)
    if response.status_code >= 500:
        raise NetworkTestError(f"目标服务器返回 HTTP {response.status_code}")
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "status_code": response.status_code,
        "mode": "proxy" if proxy_url else "direct",
    }