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
    assert '测试代理并继续' in response.text
    assert 'id="api-hash"' in response.text
    assert 'id="bot-token"' in response.text


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
def test_authenticated_incomplete_setup_redirects_to_wizard(client: TestClient):
    initialize_admin(client)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/setup"


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
    assert token

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
