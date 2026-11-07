from __future__ import annotations

from fastapi.testclient import TestClient

from app.version import APP_VERSION
from tests.conftest import get_csrf, initialize_admin


def test_health_and_version(client: TestClient):
    assert client.get("/health").json() == {"status": "ok", "version": APP_VERSION}
    payload = client.get("/api/version").json()
    assert payload["version"] == APP_VERSION
    assert payload["name"] == "Telegram Media Downloader"


def test_root_redirects_to_setup_on_first_run(client: TestClient):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


def test_setup_page_renders_version_and_csrf(client: TestClient):
    response = client.get("/setup")
    assert response.status_code == 200
    assert f"v{APP_VERSION}" in response.text
    assert 'name="csrf-token"' in response.text
    assert "一次配置，直接开始下载" in response.text
    assert "设置网络代理" in response.text
    assert '<li data-progress="1"><span>1</span>网络代理</li>' in response.text
    assert 'data-progress="6"' not in response.text
    assert '添加下载频道' not in response.text
    assert '保存并前往频道管理' in response.text
    assert '测试代理并继续' in response.text
    assert 'id="api-hash"' in response.text
    assert 'id="bot-token"' in response.text
    assert 'test-saved-network' in response.text


def test_csrf_is_required(client: TestClient):
    token = get_csrf(client)
    assert token
    response = client.post(
        "/api/setup/admin",
        json={"password": "strong-password", "confirm_password": "strong-password"},
    )
    assert response.status_code == 403


def test_admin_setup_login_and_logout(client: TestClient):
    token = initialize_admin(client)
    assert client.get("/api/setup/status").json()["admin_configured"] is True
    assert client.get("/api/settings").status_code == 200

    response = client.post(
        "/api/auth/logout", headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 200
    assert client.get("/api/settings").status_code == 401

    login_token = get_csrf(client, "/login")
    response = client.post(
        "/api/auth/login",
        json={"password": "wrong-password"},
        headers={"X-CSRF-Token": login_token},
    )
    assert response.status_code == 401

    response = client.post(
        "/api/auth/login",
        json={"password": "strong-password"},
        headers={"X-CSRF-Token": login_token},
    )
    assert response.status_code == 200
    assert client.get("/api/settings").status_code == 200


def test_settings_update_encrypts_secrets(client: TestClient, tmp_path):
    token = initialize_admin(client)
    download_dir = tmp_path / "custom-downloads"
    response = client.put(
        "/api/settings",
        json={
            "api_id": 123456,
            "api_hash": "super-secret-hash",
            "bot_token": "123456:super-secret-bot-token",
            "download_dir": str(download_dir),
            "media_types": ["photo", "video"],
            "folder_parts": ["chat_title", "date", "media_type"],
            "filename_template": "{date}_{message_id}",
            "max_concurrent_downloads": 3,
            "proxy_url": "socks5://user:pass@127.0.0.1:1080",
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["api_id"] == 123456
    assert payload["api_hash_configured"] is True
    assert "super-secret-hash" not in response.text
    assert "super-secret-bot-token" not in response.text
    assert payload["bot_token_configured"] is True
    assert payload["media_types"] == ["photo", "video"]
    assert download_dir.exists()

    raw_api_hash = client.app.state.services.database.get_setting("api_hash")
    raw_bot_token = client.app.state.services.database.get_setting("bot_token")
    raw_proxy = client.app.state.services.database.get_setting("proxy_url")
    assert raw_api_hash.startswith("fernet:")
    assert raw_bot_token.startswith("fernet:")
    assert raw_proxy.startswith("fernet:")
    assert "super-secret-hash" not in raw_api_hash


def test_settings_validation_rejects_unknown_values(client: TestClient):
    token = initialize_admin(client)
    response = client.put(
        "/api/settings",
        json={"media_types": ["video", "executable"]},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 422


def test_chat_crud(client: TestClient):
    token = initialize_admin(client)
    headers = {"X-CSRF-Token": token}
    response = client.post(
        "/api/chats",
        json={
            "chat_ref": "@example_channel",
            "title": "Example",
            "date_from": "2025-01-01",
            "date_to": "2025-12-31",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    chat = response.json()
    assert chat["chat_ref"] == "@example_channel"
    assert chat["enabled"] == 1

    response = client.patch(
        f"/api/chats/{chat['id']}", json={"enabled": False}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["enabled"] == 0

    chats = client.get("/api/chats").json()
    assert len(chats) == 1

    response = client.delete(f"/api/chats/{chat['id']}", headers=headers)
    assert response.status_code == 200
    assert client.get("/api/chats").json() == []


def test_yaml_import_and_export(client: TestClient):
    token = initialize_admin(client)
    yaml_content = """
api_id: 7654321
api_hash: yaml-secret
media_types:
  - photo
  - video
save_path: /tmp/from-yaml
max_download_task: 2
chat:
  - chat_id: '@yaml_channel'
    title: YAML Channel
    last_read_message_id: 25
    download_filter: media_type == r'(video|audio)'
""".encode("utf-8")
    response = client.post(
        "/api/settings/import.yaml",
        files={"file": ("config.yaml", yaml_content, "application/x-yaml")},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["chats_imported"] == 1

    chats = client.get("/api/chats").json()
    assert chats[0]["last_message_id"] == 25
    assert client.get("/api/settings").json()["media_types"] == ["photo", "video"]

    exported = client.get("/api/settings/export.yaml")
    assert exported.status_code == 200
    assert "yaml_channel" in exported.text
    assert "attachment" in exported.headers["content-disposition"]


def test_download_start_requires_telegram_and_chat(client: TestClient):
    token = initialize_admin(client)
    response = client.post(
        "/api/downloads/start", headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 422
    assert "Telegram" in response.json()["detail"]


def test_authenticated_incomplete_setup_can_open_monitor(client: TestClient):
    initialize_admin(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "下载监控" in response.text
    assert "配置尚未完成" in response.text
    assert "当前总下载速度" in response.text


def test_static_assets_and_authenticated_pages_render(client: TestClient):
    token = initialize_admin(client)
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    settings_page = client.get("/settings")
    assert settings_page.status_code == 200
    assert "系统设置" in settings_page.text
    chats_page = client.get("/chats")
    assert chats_page.status_code == 200
    assert "频道管理" in chats_page.text
    assert "账号频道与会话" in chats_page.text
    assert "添加所选频道" in chats_page.text
    assert "点击频道即可查看最近消息" in chats_page.text
    assert 'id="dialog-preview"' in chats_page.text
    assert "加载最近的 30 条消息" in chats_page.text
    assert 'id="sync-state"' in chats_page.text
    assert "tmd:dialogs:v2" in chats_page.text
    assert "localStorage" in chats_page.text
    assert "下载监控" in settings_page.text
    assert "配置向导" in settings_page.text
    assert 'href="/setup?edit=1"' in settings_page.text
    assert token

def test_dashboard_monitoring_page_and_edit_wizard(client: TestClient, monkeypatch):
    initialize_admin(client)
    services = client.app.state.services
    monkeypatch.setattr(type(services.settings), "ready", property(lambda _: True))

    dashboard = client.get("/")
    assert dashboard.status_code == 200, dashboard.text
    assert "当前总下载速度" in dashboard.text
    assert "存储资源" in dashboard.text
    assert 'id="stat-speed"' in dashboard.text
    assert 'id="stat-eta"' in dashboard.text
    assert 'id="disk-free"' in dashboard.text
    assert 'data-monitor-tab="history"' in dashboard.text
    assert 'id="history-page-info"' in dashboard.text
    assert "下载监控" in dashboard.text

    setup = client.get("/setup?edit=1")
    assert setup.status_code == 200, setup.text
    assert "配置向导" in setup.text
    assert "下载偏好" in setup.text


def test_telegram_dialog_messages_before_id_api(client: TestClient, monkeypatch):
    initialize_admin(client)
    telegram = client.app.state.services.telegram

    async def fake_messages(dialog_id, limit, before_id):
        assert dialog_id == "-100123"
        assert limit == 50
        assert before_id == 60
        return [{"id": 59, "text": "Older", "has_media": False}]

    monkeypatch.setattr(telegram, "list_dialog_messages", fake_messages)
    response = client.get(
        "/api/telegram/dialogs/-100123/messages?limit=50&before_id=60"
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["id"] == 59


def test_channel_filter_validation_api(client: TestClient):
    token = initialize_admin(client)
    headers = {"X-CSRF-Token": token}

    response = client.post(
        "/api/filters/validate",
        json={"expression": "caption == r'.*电影.*' and file_size >= 10MB"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is True

    response = client.post(
        "/api/filters/validate",
        json={"expression": "caption > 'invalid'"},
        headers=headers,
    )
    assert response.status_code == 422


def test_proxy_is_first_setup_step_and_can_be_skipped(client: TestClient):
    token = get_csrf(client)
    status = client.get("/api/setup/status").json()
    assert status["proxy_step_completed"] is False

    response = client.post(
        "/api/setup/proxy",
        json={"proxy_url": "socks5://user:pass@127.0.0.1:1080"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/setup/status").json()["proxy_step_completed"] is True
    raw_proxy = client.app.state.services.database.get_setting("proxy_url")
    assert raw_proxy.startswith("fernet:")


def test_proxy_can_be_skipped_without_redirect_loop(client: TestClient):
    token = get_csrf(client)
    response = client.post(
        "/api/setup/proxy",
        json={"proxy_url": ""},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200
    assert response.json()["proxy_configured"] is False
    assert client.get("/api/setup/status").json()["proxy_step_completed"] is True
    assert client.get("/setup").status_code == 200

def test_network_probe_must_pass_before_proxy_step_is_completed(client: TestClient, monkeypatch):
    token = get_csrf(client)

    async def fake_probe(proxy_url, target_url):
        assert proxy_url == "socks5://127.0.0.1:1080"
        assert target_url.startswith("https://")
        return {"ok": True, "latency_ms": 87, "status_code": 200, "mode": "proxy"}

    monkeypatch.setattr("app.main.test_network_connection", fake_probe)
    response = client.post(
        "/api/setup/proxy/test",
        json={"proxy_url": "socks5://127.0.0.1:1080"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["latency_ms"] == 87
    assert client.get("/api/setup/status").json()["proxy_step_completed"] is True


def test_network_probe_failure_does_not_advance_setup(client: TestClient, monkeypatch):
    token = get_csrf(client)

    async def failed_probe(proxy_url, target_url):
        from app.network import NetworkTestError

        raise NetworkTestError("代理网络测试失败: timeout")

    monkeypatch.setattr("app.main.test_network_connection", failed_probe)
    response = client.post(
        "/api/setup/proxy/test",
        json={"proxy_url": "socks5://127.0.0.1:1080"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 422
    assert client.get("/api/setup/status").json()["proxy_step_completed"] is False
def test_authenticated_user_can_retest_saved_proxy(client: TestClient, monkeypatch):
    token = initialize_admin(client)
    client.put(
        "/api/settings",
        json={"proxy_url": "socks5://127.0.0.1:1080"},
        headers={"X-CSRF-Token": token},
    )

    async def fake_probe(proxy_url, target_url):
        assert proxy_url == "socks5://127.0.0.1:1080"
        return {"ok": True, "latency_ms": 45, "status_code": 200, "mode": "proxy"}

    monkeypatch.setattr("app.main.test_network_connection", fake_probe)
    response = client.post(
        "/api/setup/proxy/test",
        json={"proxy_url": None},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    assert response.json()["latency_ms"] == 45
def test_bulk_chat_creation(client: TestClient):
    token = initialize_admin(client)
    response = client.post(
        "/api/chats/bulk",
        json={
            "chats": [
                {"chat_ref": "@channel_one", "title": "Channel One"},
                {"chat_ref": "-1001234567890", "title": "Channel Two"},
            ]
        },
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 2
    assert len(client.get("/api/chats").json()) == 2


def test_telegram_dialog_messages_and_avatar_api(client: TestClient, monkeypatch):
    initialize_admin(client)
    telegram = client.app.state.services.telegram

    async def fake_messages(dialog_id, limit):
        assert dialog_id == "-1001234567890"
        assert limit == 12
        return [
            {
                "id": 9,
                "date": "2026-11-07T12:30:00+00:00",
                "sender": "Example",
                "text": "Hello channel",
                "media_type": "",
                "media_label": "",
                "media_name": "",
            }
        ]

    async def fake_avatar(dialog_id):
        assert dialog_id == "-1001234567890"
        return b"\x89PNG\r\n\x1a\n", "image/png"

    monkeypatch.setattr(telegram, "list_dialog_messages", fake_messages)
    monkeypatch.setattr(telegram, "get_dialog_avatar", fake_avatar)

    response = client.get(
        "/api/telegram/dialogs/-1001234567890/messages?limit=12"
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["text"] == "Hello channel"

    response = client.get("/api/telegram/dialogs/-1001234567890/avatar")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert response.content.startswith(b"\x89PNG")


def test_media_preview_direct_download_and_history_api(
    client: TestClient, monkeypatch, tmp_path
):
    token = initialize_admin(client)
    headers = {"X-CSRF-Token": token}
    services = client.app.state.services
    chat = services.database.upsert_chat("@media_demo", "Media Demo")

    preview_file = tmp_path / "preview.jpg"
    preview_file.write_bytes(b"preview-image")

    async def fake_preview(dialog_id, message_id):
        assert dialog_id == "-100123"
        assert message_id == 9
        return preview_file, "image/jpeg"

    async def fake_direct(dialog_id, message_id, chat_title):
        assert dialog_id == "-100123"
        assert message_id == 9
        assert chat_title == "Media Demo"
        return {
            "ok": True,
            "status": "queued",
            "chat_title": chat_title,
            "file_name": "image.jpg",
            "relative_path": "Media Demo/image.jpg",
        }

    monkeypatch.setattr(services.telegram, "get_message_media_file", fake_preview)
    monkeypatch.setattr(services.downloader, "start_direct_download", fake_direct)

    response = client.get(
        "/api/telegram/dialogs/-100123/messages/9/media"
    )
    assert response.status_code == 200
    assert response.content == b"preview-image"
    assert response.headers["content-type"].startswith("image/jpeg")

    response = client.post(
        "/api/telegram/dialogs/-100123/messages/9/download",
        json={"chat_title": "Media Demo"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"

    first_file = services.settings.download_dir() / "Media Demo" / "one.mp4"
    second_file = services.settings.download_dir() / "Media Demo" / "two.mp4"
    first_file.parent.mkdir(parents=True, exist_ok=True)
    first_file.write_bytes(b"video-one")
    second_file.write_bytes(b"video-two")
    first_id = services.database.create_download(
        chat["id"], "Media Demo", 1, str(first_file), "video", 9
    )
    second_id = services.database.create_download(
        chat["id"], "Media Demo", 2, str(second_file), "video", 9
    )
    services.database.update_download(first_id, status="completed", downloaded_size=9)
    services.database.update_download(second_id, status="completed", downloaded_size=9)

    history = client.get("/api/downloads/history?page=1&page_size=1")
    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["total"] == 2
    assert payload["pages"] == 2
    assert len(payload["items"]) == 1

    media = client.get(f"/api/downloads/{first_id}/media")
    assert media.status_code == 200
    assert media.content == b"video-one"
    assert media.headers["content-type"].startswith("video/mp4")
