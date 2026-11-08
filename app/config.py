"""Runtime paths and application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def resolve_download_dir(value: str | Path | None = None) -> Path:
    """Resolve the fixed download directory for native and container runtimes."""

    configured = value if value is not None else os.getenv("TMD_DOWNLOAD_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (PROJECT_ROOT / "downloads").resolve()


@dataclass(slots=True)
class RuntimeConfig:
    """Filesystem locations and test/runtime switches."""

    data_dir: Path
    download_dir: Path
    csrf_enabled: bool = True
    start_downloader: bool = True
    network_test_url: str = "https://api.telegram.org"

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        data_dir = Path(os.getenv("TMD_DATA_DIR", PROJECT_ROOT / "data")).resolve()
        download_dir = resolve_download_dir()
        return cls(
            data_dir=data_dir,
            download_dir=download_dir,
            csrf_enabled=os.getenv("TMD_CSRF_ENABLED", "1") != "0",
            start_downloader=os.getenv("TMD_START_DOWNLOADER", "1") != "0",
            network_test_url=os.getenv("TMD_NETWORK_TEST_URL", "https://api.telegram.org"),
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / ".secret.key"

    @property
    def session_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "app.log"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def validate_persistent_storage(self) -> None:
        if os.getenv("TMD_REQUIRE_PERSISTENT_STORAGE", "0") != "1":
            return
        missing = [
            str(path)
            for path in (self.data_dir, self.download_dir)
            if not os.path.ismount(path)
        ]
        if missing:
            joined = "、".join(missing)
            raise RuntimeError(
                f"Docker 必须持久化挂载 data 和 downloads，当前未挂载: {joined}"
            )
