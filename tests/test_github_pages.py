from __future__ import annotations

from pathlib import Path

from app.version import APP_VERSION
from scripts.build_github_pages import PAGES_URL, build_site


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECKED_IN_FILES = (
    "index.html",
    "wiki.html",
    ".nojekyll",
    "404.html",
    "assets/favicon.svg",
    "assets/landing.css",
    "assets/landing.js",
    "assets/wiki.css",
    "assets/wiki.js",
    "assets/landing/monitor.png",
    "assets/landing/filter.png",
    "assets/landing/setup.png",
)


def test_github_pages_build_is_self_contained_and_current(tmp_path: Path) -> None:
    generated = build_site(PROJECT_ROOT, tmp_path / "site")

    landing = (generated / "index.html").read_text(encoding="utf-8")
    wiki = (generated / "wiki.html").read_text(encoding="utf-8")
    assert '<meta name="site-mode" content="github-pages">' in landing
    assert f"assets/landing.css?v={APP_VERSION}" in landing
    assert f"assets/landing.js?v={APP_VERSION}" in landing
    assert "/static/" not in landing
    assert "{{" not in landing
    assert f"assets/wiki.css?v={APP_VERSION}" in wiki
    assert f"assets/wiki.js?v={APP_VERSION}" in wiki
    assert "/static/" not in wiki
    assert "{{" not in wiki
    assert "docker compose up -d --build" in wiki
    assert "https://github.com/cvpia/telegram-media-downloader-web" in landing
    assert PAGES_URL in (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    for relative_path in CHECKED_IN_FILES:
        assert (PROJECT_ROOT / "docs" / relative_path).is_file()
        assert (PROJECT_ROOT / "docs" / relative_path).read_bytes() == (
            generated / relative_path
        ).read_bytes()
