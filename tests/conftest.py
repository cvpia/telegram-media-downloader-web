from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import RuntimeConfig
from app.main import create_app


CSRF_PATTERN = re.compile(r'<meta name="csrf-token" content="([^"]+)"')


@pytest.fixture
def client(tmp_path: Path):
    runtime = RuntimeConfig(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        csrf_enabled=True,
        start_downloader=False,
    )
    application = create_app(runtime)
    with TestClient(application) as test_client:
        yield test_client


def get_csrf(test_client: TestClient, path: str = "/setup") -> str:
    response = test_client.get(path, follow_redirects=False)
    assert response.status_code == 200, response.text
    match = CSRF_PATTERN.search(response.text)
    assert match, "CSRF meta tag missing"
    return match.group(1)


def initialize_admin(test_client: TestClient, password: str = "strong-password") -> str:
    token = get_csrf(test_client)
    response = test_client.post(
        "/api/setup/admin",
        json={"password": password, "confirm_password": password},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 200, response.text
    return token