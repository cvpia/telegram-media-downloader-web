from __future__ import annotations

from fastapi.testclient import TestClient

from app.version import APP_VERSION
from tests.conftest import get_csrf, initialize_admin


def test_health_and_version(client: TestClient):
    assert client.get("/health").json() == {"status": "ok", "version": APP_VERSION}
    payload = client.get("/api/version").json()
    assert payload["version"] == APP_VERSION
    assert payload["name"] == "Telegram Media Downloader"


def test_public_landing_page_renders_before_setup(client: TestClient):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200, response.text
    assert f'name="app-version" content="{APP_VERSION}"' in response.text
    assert f"/static/landing.css?v={APP_VERSION}" in response.text
    assert f"/static/landing.js?v={APP_VERSION}" in response.text
    assert 'https://registry.npmmirror.com/react/18.3.1/' in response.text
    assert 'https://registry.npmmirror.com/three/0.160.0/' in response.text
    assert 'https://registry.npmmirror.com/gsap/3.12.5/' in response.text
    assert 'https://registry.npmmirror.com/framer-motion/6.5.1/' in response.text
    assert 'id="root"' in response.text
    assert "把 Telegram" in response.text
    assert "TeleVault" in response.text

    css_response = client.get(f"/static/landing.css?v={APP_VERSION}")
    assert css_response.status_code == 200
    assert css_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "hero-canvas" in css_response.text
    assert "@media (max-width: 620px)" in css_response.text
    assert "width: 16px;" in css_response.text

    js_response = client.get(f"/static/landing.js?v={APP_VERSION}")
    assert js_response.status_code == 200
    assert "new THREE.WebGLRenderer" in js_response.text
    assert "targetFps" in js_response.text
    assert "IntersectionObserver" in js_response.text
    assert "window.gsap" in js_response.text
    assert "window.Motion" in js_response.text
    assert "screenshot-grid" in js_response.text
    assert "appVersion" in js_response.text
    assert client.get("/static/landing/monitor.png").status_code == 200
    dashboard_response = client.get("/dashboard", follow_redirects=False)
    assert dashboard_response.status_code == 303
    assert dashboard_response.headers["location"] == "/setup"


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
    assert 'id="download-dir" type="text" required readonly' in response.text


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


def test_settings_update_encrypts_secrets(client: TestClient, monkeypatch):
    token = initialize_admin(client)
    refresh_calls = 0

    async def fake_refresh_concurrency_limit():
        nonlocal refresh_calls
        refresh_calls += 1

    monkeypatch.setattr(
        client.app.state.services.downloader,
        "refresh_concurrency_limit",
        fake_refresh_concurrency_limit,
    )
    response = client.put(
        "/api/settings",
        json={
            "api_id": 123456,
            "api_hash": "super-secret-hash",
            "bot_token": "123456:super-secret-bot-token",
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
    assert payload["download_dir"] == str(
        client.app.state.runtime.download_dir.resolve()
    )
    assert client.app.state.runtime.download_dir.exists()
    assert refresh_calls == 1

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


def test_settings_download_directory_is_fixed(client: TestClient, tmp_path):
    token = initialize_admin(client)
    custom_dir = tmp_path / "custom-downloads"
    fixed_dir = client.app.state.runtime.download_dir.resolve()

    response = client.put(
        "/api/settings",
        json={"download_dir": str(custom_dir)},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 422
    assert "固定" in response.json()["detail"]
    assert not custom_dir.exists()

    client.app.state.services.database.set_setting("download_dir", str(custom_dir))
    assert client.app.state.services.settings.download_dir() == fixed_dir
    assert client.get("/api/settings").json()["download_dir"] == str(fixed_dir)


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

    response = client.patch(
        f"/api/chats/{chat['id']}",
        json={
            "download_filter": "media_type == 'video'",
            "date_from": "2025-02-01",
            "date_to": "2025-11-30",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["download_filter"] == "media_type == 'video'"
    assert response.json()["date_from"] == "2025-02-01"
    assert response.json()["date_to"] == "2025-11-30"

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
    settings = client.get("/api/settings").json()
    assert settings["media_types"] == ["photo", "video"]
    assert settings["download_dir"] == str(
        client.app.state.runtime.download_dir.resolve()
    )

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


def test_download_status_uses_compact_live_payload(client: TestClient):
    initialize_admin(client)
    response = client.get(
        "/api/downloads/status", headers={"Accept-Encoding": "gzip"}
    )
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    payload = response.json()
    assert "recent" not in payload
    assert "completed_count" in payload["summary"]
    assert "retry_wait_count" in payload["summary"]
    assert "total_traffic" in payload["summary"]


def test_retry_failed_download_action(client: TestClient, monkeypatch):
    token = initialize_admin(client)
    downloader = client.app.state.services.downloader

    async def fake_retry_failed():
        return {"state": "running", "requeued": 2, "active": [], "chats": []}

    monkeypatch.setattr(downloader, "retry_failed", fake_retry_failed)
    response = client.post(
        "/api/downloads/retry_failed", headers={"X-CSRF-Token": token}
    )

    assert response.status_code == 200
    assert response.json()["requeued"] == 2


def test_authenticated_incomplete_setup_can_open_monitor(client: TestClient):
    initialize_admin(client)
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "下载监控" in response.text
    assert "配置尚未完成" in response.text
    assert "当前总下载速度" in response.text


def test_static_assets_and_authenticated_pages_render(client: TestClient):
    token = initialize_admin(client)
    assert client.get(f"/static/favicon.svg?v={APP_VERSION}").status_code == 200
    css_response = client.get(
        f"/static/app.css?v={APP_VERSION}", headers={"Accept-Encoding": "gzip"}
    )
    assert css_response.status_code == 200
    assert css_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert css_response.headers["content-encoding"] == "gzip"
    app_js_response = client.get("/static/app.js")
    assert app_js_response.status_code == 200
    app_js = app_js_response.text
    settings_page = client.get("/settings")
    assert settings_page.status_code == 200
    assert "系统设置" in settings_page.text
    assert 'id="download-dir" type="text" required readonly' in settings_page.text
    assert "download_dir: document.getElementById('download-dir').value" not in settings_page.text
    chats_page = client.get("/chats")
    assert chats_page.status_code == 200
    assert "频道管理" in chats_page.text
    assert "账号频道与会话" in chats_page.text
    assert "添加所选频道" in chats_page.text
    assert "点击频道即可查看最近消息" in chats_page.text
    assert 'id="dialog-preview"' in chats_page.text
    assert "加载最近的 30 条消息" in chats_page.text
    assert "上滑到这里自动加载更早消息" in chats_page.text
    assert "IntersectionObserver" in chats_page.text
    assert "rootMargin: '280px 0px 0px 0px'" in chats_page.text
    assert "messageCache" in chats_page.text
    assert "olderLoadFailed" in chats_page.text
    assert "data-retry-messages" in chats_page.text
    assert "timeoutMs: 30000" in chats_page.text
    assert "timeoutMs: 15000" in chats_page.text
    assert "message-media-preview message-media-play" in chats_page.text
    assert 'data-play-video data-media-url="${fullMedia}"' in chats_page.text
    assert 'id="video-dialog"' in chats_page.text
    assert 'id="video-player"' in chats_page.text
    assert "typeof player.load === 'function'" in app_js
    assert "typeof player.play === 'function'" in app_js
    assert "openVideo" in app_js
    assert "openImage" in app_js
    assert "pendingControllers" in app_js
    assert "abortPendingRequests" in app_js
    assert "window.addEventListener('pagehide', abortPendingRequests)" in app_js
    assert 'id="sync-state"' in chats_page.text
    assert "initialDialogCache" in chats_page.text
    assert "localStorage" not in chats_page.text
    assert 'href="/forwarding"' in chats_page.text
    assert "下载监控" in settings_page.text
    assert "配置向导" in settings_page.text
    assert 'href="/setup?edit=1"' in settings_page.text
    assert token


def test_dialog_cache_is_server_rendered_and_lazy_synced(
    client: TestClient, monkeypatch
):
    initialize_admin(client)
    services = client.app.state.services
    cached_dialogs = [
        {
            "id": "123",
            "title": "Cache Channel",
            "username": "cache_channel",
            "type": "channel",
        }
    ]
    services.database.save_dialog_cache(cached_dialogs)

    page = client.get("/chats")
    assert page.status_code == 200
    assert "initialDialogCache" in page.text
    assert "Cache Channel" in page.text
    assert services.database.get_dialog_cache()["dialogs"] == cached_dialogs

    async def fake_list_dialogs():
        return [
            *cached_dialogs,
            {
                "id": "456",
                "title": "Fresh Channel",
                "username": "fresh_channel",
                "type": "channel",
            },
        ]

    monkeypatch.setattr(services.telegram, "list_dialogs", fake_list_dialogs)
    response = client.get("/api/telegram/dialogs")
    assert response.status_code == 200, response.text
    assert [dialog["id"] for dialog in response.json()] == ["123", "456"]
    refreshed_cache = client.get("/api/telegram/dialogs/cache").json()
    assert [dialog["id"] for dialog in refreshed_cache["dialogs"]] == ["123", "456"]
    assert refreshed_cache["cached_at"] > 0

def test_dashboard_monitoring_page_and_edit_wizard(client: TestClient, monkeypatch):
    initialize_admin(client)
    services = client.app.state.services
    monkeypatch.setattr(type(services.settings), "ready", property(lambda _: True))

    dashboard = client.get("/dashboard", headers={"Accept-Encoding": "gzip"})
    assert dashboard.status_code == 200, dashboard.text
    assert dashboard.headers["content-encoding"] == "gzip"
    assert f"/static/favicon.svg?v={APP_VERSION}" in dashboard.text
    assert f"/static/app.css?v={APP_VERSION}" in dashboard.text
    assert f"/static/app.js?v={APP_VERSION}" in dashboard.text
    assert f'<script src="/static/app.js?v={APP_VERSION}"></script>' in dashboard.text
    assert f'<script src="/static/app.js?v={APP_VERSION}" defer></script>' not in dashboard.text
    assert "window.__TMD_BOOTSTRAP__" in dashboard.text
    assert 'data-action="retry_failed"' in dashboard.text
    assert "TMD.api('/api/downloads/status')" not in dashboard.text
    assert "当前总下载速度" in dashboard.text
    assert "存储资源" in dashboard.text
    assert 'id="stat-speed"' in dashboard.text
    assert 'id="stat-eta"' in dashboard.text
    assert 'id="stat-traffic"' in dashboard.text
    assert "累计使用流量" in dashboard.text
    assert 'id="disk-free"' in dashboard.text
    assert 'data-monitor-tab="history"' in dashboard.text
    assert 'id="history-page-info"' in dashboard.text
    assert 'id="history-page-jump"' in dashboard.text
    assert 'data-history-status="success"' in dashboard.text
    assert 'data-history-status="failed"' in dashboard.text
    assert "window.addEventListener('pagehide'" in dashboard.text
    assert "source.close()" in dashboard.text
    assert "window.addEventListener('pageshow'" in dashboard.text
    assert "下载监控" in dashboard.text
    assert "data-play-media" in dashboard.text
    assert "data-preview-image" in dashboard.text
    assert "history-thumbnail" in dashboard.text
    assert "data-lazy-video-thumbnail" in dashboard.text
    assert "videoThumbnailObserver" in dashboard.text
    assert "rootMargin: '180px 0px'" in dashboard.text
    assert "history-type-badge" in dashboard.text
    assert "${TMD.escape(item.media_type)}</span>" not in dashboard.text
    assert "displayFileName" in dashboard.text
    assert "等待 Telegram" in dashboard.text

    forwarding = client.get("/forwarding")
    assert forwarding.status_code == 200, forwarding.text
    assert "消息转发" in forwarding.text
    assert 'id="forward-form"' in forwarding.text
    assert 'id="forward-rule-list"' in forwarding.text
    assert "/listen_forward" in forwarding.text
    assert "补转历史并持续监听新消息" in forwarding.text
    assert "从最早消息开始" in forwarding.text
    assert "重启 Bot" in forwarding.text

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

    streaming_video = b"streaming-video-content"

    async def fake_stream(dialog_id, message_id):
        assert dialog_id == "-100123"
        assert message_id == 9

        def stream_range(start, end):
            async def chunks():
                yield streaming_video[start : end + 1]

            return chunks()

        return stream_range, "video/mp4", len(streaming_video)

    async def fake_thumbnail(dialog_id, message_id):
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

    monkeypatch.setattr(services.telegram, "get_message_media_stream", fake_stream)
    monkeypatch.setattr(
        services.telegram, "get_message_thumbnail_file", fake_thumbnail
    )
    monkeypatch.setattr(services.downloader, "start_direct_download", fake_direct)

    response = client.get(
        "/api/telegram/dialogs/-100123/messages/9/thumbnail"
    )
    assert response.status_code == 200
    assert response.content == b"preview-image"
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "private, max-age=86400"

    response = client.get(
        "/api/telegram/dialogs/-100123/messages/9/media",
        headers={"Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content == streaming_video
    assert response.headers["content-type"].startswith("video/mp4")
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(streaming_video))

    response = client.get(
        "/api/telegram/dialogs/-100123/messages/9/media",
        headers={"Range": "bytes=2-7"},
    )
    assert response.status_code == 206
    assert response.content == streaming_video[2:8]
    assert response.headers["content-range"] == f"bytes 2-7/{len(streaming_video)}"
    assert response.headers["content-length"] == "6"

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
    downloading_id = services.database.create_download(
        chat["id"], "Media Demo", 3, str(first_file), "video", 9
    )
    retrying_id = services.database.create_download(
        chat["id"], "Media Demo", 4, str(first_file), "video", 9
    )
    services.database.update_download(retrying_id, status="retry_wait")
    services.database.update_download(first_id, status="completed", downloaded_size=9)
    services.database.update_download(second_id, status="completed", downloaded_size=9)

    history = client.get("/api/downloads/history?page=1&page_size=1")
    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["total"] == 2
    assert payload["pages"] == 2
    assert len(payload["items"]) == 1
    assert downloading_id not in [item["id"] for item in payload["items"]]
    assert retrying_id not in [item["id"] for item in payload["items"]]
    success_history = client.get(
        "/api/downloads/history?page=1&page_size=10&status=success"
    )
    failed_history = client.get(
        "/api/downloads/history?page=1&page_size=10&status=failed"
    )
    assert success_history.json()["total"] == 2
    assert success_history.json()["counts"]["success"] == 2
    assert failed_history.json()["total"] == 0
    assert failed_history.json()["status"] == "failed"

    media = client.get(
        f"/api/downloads/{first_id}/media", headers={"Accept-Encoding": "gzip"}
    )
    assert media.status_code == 200
    assert "content-encoding" not in media.headers
    assert media.content == b"video-one"
    assert media.headers["content-type"].startswith("video/mp4")


def test_download_record_retry_returns_existing_record(client: TestClient):
    initialize_admin(client)
    services = client.app.state.services
    chat = services.database.upsert_chat("@retry_demo", "Retry Demo")

    first_id = services.database.create_download(
        chat["id"], "Retry Demo", 77, "/tmp/retry.mp4", "video", 1024
    )
    services.database.update_download(first_id, status="failed", error="network")
    assert services.database.resumable_downloads(chat["id"]) == []
    assert services.database.requeue_failed_downloads(10) == 1
    assert [
        item["id"]
        for item in services.database.resumable_downloads(chat["id"])
    ] == [first_id]
    second_id = services.database.create_download(
        chat["id"], "Retry Demo", 77, "/tmp/retry.mp4", "video", 1024
    )

    assert second_id == first_id
    record = services.database.get_download(second_id)
    assert record is not None
    assert record["status"] == "downloading"
    assert record["error"] is None


def test_forwarding_rule_api_and_page(client: TestClient, monkeypatch):
    token = initialize_admin(client)
    headers = {"X-CSRF-Token": token}
    services = client.app.state.services

    async def fake_create_rule(**kwargs):
        return services.database.create_forward_rule(
            source_chat="-100111",
            source_title="Source",
            target_chat="-100222",
            target_title="Target",
            filter_expression=kwargs["filter_expression"],
            enabled=kwargs["enabled"],
            last_message_id=50,
        )

    monkeypatch.setattr(services.bot, "create_rule", fake_create_rule)

    response = client.post(
        "/api/forward-rules",
        json={
            "source_chat": "@source",
            "target_chat": "@target",
            "filter_expression": "media_type == 'video'",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    rule = response.json()
    assert rule["last_message_id"] == 50

    listed = client.get("/api/forward-rules")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [rule["id"]]

    updated = client.patch(
        f"/api/forward-rules/{rule['id']}",
        json={"enabled": False},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] == 0

    page = client.get("/forwarding")
    assert page.status_code == 200
    assert "Source" in page.text
    assert "media_type" in page.text
    assert "video" in page.text

    status = client.get("/api/bot/status")
    assert status.status_code == 200
    assert status.json()["running"] is False

    deleted = client.delete(
        f"/api/forward-rules/{rule['id']}", headers=headers
    )
    assert deleted.status_code == 200
    assert client.get("/api/forward-rules").json() == []
