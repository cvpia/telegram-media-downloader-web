from __future__ import annotations

from pathlib import Path

from app.version import APP_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE_NAME = "xxoo520zls/telegram-media-downloader-web"


def test_docker_build_uses_aliyun_apt_mirror() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    mirror_rewrite = "sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g'"
    assert mirror_rewrite in dockerfile
    assert dockerfile.index(mirror_rewrite) < dockerfile.index("&& apt-get update")


def test_release_version_is_consistent_across_distribution_metadata() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"当前版本：**{APP_VERSION}**" in readme
    assert f"{IMAGE_NAME}:{APP_VERSION}" in readme
    assert f"image: {IMAGE_NAME}:{APP_VERSION}" in compose
    assert f"## {APP_VERSION}" in changelog