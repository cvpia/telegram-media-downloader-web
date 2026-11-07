from __future__ import annotations

from types import SimpleNamespace

import pytest
from pyrogram.errors import BadMsgNotification

from app.telegram_manager import TelegramManager


class FakeClient:
    def __init__(self, failures: int = 0):
        self.failures = failures
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.send_code_calls = 0

    async def connect(self):
        self.connect_calls += 1
        return True

    async def disconnect(self):
        self.disconnect_calls += 1

    async def send_code(self, phone_number):
        self.send_code_calls += 1
        if self.send_code_calls <= self.failures:
            raise BadMsgNotification(16)
        return SimpleNamespace(phone_code_hash="phone-hash", type="app")



def test_clock_sync_codes_are_detected():
    assert TelegramManager.is_clock_sync_error(BadMsgNotification(16))
    assert TelegramManager.is_clock_sync_error(BadMsgNotification(17))
    assert TelegramManager.is_clock_sync_error(BadMsgNotification(20))
    assert not TelegramManager.is_clock_sync_error(BadMsgNotification(18))


@pytest.mark.asyncio
async def test_send_login_code_retries_after_clock_sync_error(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    fake_client = FakeClient(failures=1)
    monkeypatch.setattr(manager, "_new_client", lambda *args: fake_client)

    client, sent_code = await manager._send_login_code(
        "login_test", "+8613800000000", 1, "hash"
    )

    assert client is fake_client
    assert sent_code.phone_code_hash == "phone-hash"
    assert fake_client.connect_calls == 2
    assert fake_client.disconnect_calls == 1
    assert fake_client.send_code_calls == 2


@pytest.mark.asyncio
async def test_send_login_code_reports_persistent_clock_error(tmp_path, monkeypatch):
    manager = TelegramManager(settings=None, session_dir=tmp_path)
    fake_client = FakeClient(failures=2)
    monkeypatch.setattr(manager, "_new_client", lambda *args: fake_client)

    with pytest.raises(ValueError, match="时间不同步"):
        await manager._send_login_code("login_test", "+8613800000000", 1, "hash")

    assert fake_client.connect_calls == 2
    assert fake_client.disconnect_calls == 2
    assert fake_client.send_code_calls == 2
