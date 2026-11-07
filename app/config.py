"""Runtime paths and application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(slots=True)
class RuntimeConfig:
    """Filesystem locations and test/runtime switches."""

    data_dir: Path
    download_dir: Path
    csrf_enabled: bool = True
    start_downloader: bool = True

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        data_dir = Path(os.getenv("TMD_DATA_DIR", PROJECT_ROOT / "data")).resolve()
        download_dir = Path(
            os.getenv("TMD_DOWNLOAD_DIR", PROJECT_ROOT / "downloads")
        ).resolve()
        return cls(
            data_dir=data_dir,
            download_dir=download_dir,
            csrf_enabled=os.getenv("TMD_CSRF_ENABLED", "1") != "0",
            start_downloader=os.getenv("TMD_START_DOWNLOADER", "1") != "0",
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