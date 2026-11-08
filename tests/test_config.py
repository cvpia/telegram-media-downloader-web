from __future__ import annotations

from pathlib import Path

import pytest

from app.config import PROJECT_ROOT, RuntimeConfig, resolve_download_dir


def test_download_dir_defaults_to_project_root(monkeypatch):
    monkeypatch.delenv("TMD_DOWNLOAD_DIR", raising=False)

    assert resolve_download_dir() == (PROJECT_ROOT / "downloads").resolve()
    assert RuntimeConfig.from_env().download_dir == (PROJECT_ROOT / "downloads").resolve()


def test_download_dir_accepts_container_mount_path(monkeypatch, tmp_path: Path):
    mount = tmp_path / "downloads"
    monkeypatch.setenv("TMD_DOWNLOAD_DIR", str(mount))

    assert resolve_download_dir() == mount.resolve()
    assert RuntimeConfig.from_env().download_dir == mount.resolve()


def test_persistent_storage_check_requires_mounts(monkeypatch, tmp_path: Path):
    runtime = RuntimeConfig(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        start_downloader=False,
    )
    monkeypatch.setenv("TMD_REQUIRE_PERSISTENT_STORAGE", "1")
    monkeypatch.setattr("app.config.os.path.ismount", lambda _: False)

    with pytest.raises(RuntimeError, match="持久化挂载"):
        runtime.validate_persistent_storage()

    monkeypatch.setattr("app.config.os.path.ismount", lambda _: True)
    runtime.validate_persistent_storage()
