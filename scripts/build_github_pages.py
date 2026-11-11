"""Build the public website for GitHub Pages under docs/."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from app.version import APP_NAME, APP_VERSION

REPO_URL = "https://github.com/cvpia/telegram-media-downloader-web"
PAGES_URL = "https://cvpia.github.io/telegram-media-downloader-web/"
STATIC_ASSET_REPLACEMENTS = {
    "/static/favicon.svg": "assets/favicon.svg",
    "/static/landing.css": "assets/landing.css",
    "/static/landing.js": "assets/landing.js",
    "/static/landing/": "assets/landing/",
    "/static/wiki.css": "assets/wiki.css",
    "/static/wiki.js": "assets/wiki.js",
}


def _common_replacements(content: str) -> str:
    content = content.replace("{{ app_name }}", APP_NAME)
    content = content.replace("{{ app_version }}", APP_VERSION)
    for source, target in STATIC_ASSET_REPLACEMENTS.items():
        content = content.replace(source, target)
    return content


def render_landing(template: str) -> str:
    content = _common_replacements(template)
    content = content.replace(
        '<a href="/dashboard" style="color: #22d3ee;">/dashboard</a>',
        '<a href="' + REPO_URL + '" style="color: #22d3ee;">GitHub</a>',
    )
    content = content.replace(
        '<meta name="theme-color" content="#080c18">',
        '<meta name="theme-color" content="#080c18">\n  <meta name="site-mode" content="github-pages">\n  <meta property="og:url" content="' + PAGES_URL + '">',
    )
    return content


def render_wiki(template: str) -> str:
    content = _common_replacements(template)
    content = content.replace(
        '<a class="docs-brand" href="/" aria-label="返回产品官网">',
        '<a class="docs-brand" href="./" aria-label="返回产品官网">',
    )
    content = content.replace(
        '<a href="/">官网</a>',
        '<a href="./">官网</a>',
    )
    content = content.replace(
        '<a href="/dashboard">工作台</a>',
        '<a href="#quick-start">快速开始</a>',
    )
    content = content.replace(
        '<a href="/api/docs" target="_blank"><span>接口文档</span><strong>FastAPI OpenAPI ↗</strong></a>',
        '<a href="#quick-start"><span>部署入口</span><strong>查看快速开始 →</strong></a>',
    )
    content = content.replace(
        '<a href="/dashboard"><span>开始使用</span><strong>进入下载工作台 →</strong></a>',
        '<a href="' + REPO_URL + '" target="_blank" rel="noreferrer"><span>获取源码</span><strong>打开 GitHub 仓库 ↗</strong></a>',
    )
    content = content.replace(
        '<a href="/">返回官网</a>',
        '<a href="./">返回官网</a>',
    )
    return content


def _copy_assets(project_root: Path, output_dir: Path) -> None:
    source_static = project_root / "app" / "static"
    assets_dir = output_dir / "assets"
    if assets_dir.exists():
        shutil.rmtree(assets_dir)
    assets_dir.mkdir(parents=True, exist_ok=True)

    for filename in ("favicon.svg", "landing.css", "landing.js", "wiki.css", "wiki.js"):
        shutil.copy2(source_static / filename, assets_dir / filename)
    shutil.copytree(source_static / "landing", assets_dir / "landing")


def build_site(project_root: Path | None = None, output_dir: Path | None = None) -> Path:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    destination = (output_dir or root / "docs").resolve()
    destination.mkdir(parents=True, exist_ok=True)

    _copy_assets(root, destination)
    landing_template = (root / "app" / "templates" / "landing.html").read_text(encoding="utf-8")
    wiki_template = (root / "app" / "templates" / "wiki.html").read_text(encoding="utf-8")
    (destination / "index.html").write_text(render_landing(landing_template), encoding="utf-8")
    (destination / "wiki.html").write_text(render_wiki(wiki_template), encoding="utf-8")
    (destination / ".nojekyll").write_text("", encoding="utf-8")
    (destination / "404.html").write_text(
        """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta http-equiv=\"refresh\" content=\"0;url=./\"><title>TeleVault</title></head><body style=\"background:#080c18;color:#dceefa;font-family:sans-serif;padding:40px\"><p>正在返回 TeleVault 官网…</p></body></html>""",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Output directory (default: docs/)")
    args = parser.parse_args()
    destination = build_site(output_dir=args.output)
    print(f"GitHub Pages site generated at {destination}")
    print(f"Public URL: {PAGES_URL}")


if __name__ == "__main__":
    main()
