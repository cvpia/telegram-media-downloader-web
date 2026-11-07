# Telegram Media Downloader Web

基于 FastAPI 重写的 Telegram 媒体下载器。目标是把原项目需要手工编辑 YAML、进入终端完成首次登录的流程，改成浏览器内一次完成。

当前版本：**3.5.2**

## 功能

- 首次访问自动进入配置向导
- Web 端设置管理密码
- Web 端填写 Telegram API ID / API Hash
- 网络代理连接测试通过后才进入下一步
- Web 端完成手机号、验证码和两步验证登录
- 可选配置 Telegram Bot Token
- 登录会话保存在服务器本地
- 频道管理使用本地缓存优先展示，随后在后台同步账号会话和群头像
- 点击频道或群组优先显示本地缓存，并复用 Telegram 会话 Peer 快速加载最近消息
- 上滑到顶部自动连续加载更早的 50 条消息，并保持当前阅读位置
- 频道消息使用轻量缩略图预览，点击后可查看原图或播放视频，并直接保存到 downloads/频道名称
- 下载监控使用选项卡切换当前任务、下载历史和下载来源
- 下载历史支持分页，并可查看图片或播放视频
- 下载监控显示实时速度、进度、剩余时间和磁盘资源
- 苹果风格浅色界面与配置向导导航
- 支持搜索、多选和批量添加频道
- Web 端添加、启停和删除下载频道
- 支持音频、文档、图片、视频、语音、视频消息和动画
- 支持媒体格式过滤、目录模板、文件名模板和并发数
- 支持 HTTP / SOCKS 代理
- FastAPI 实时状态推送（SSE）
- SQLite 持久化配置、频道和下载历史
- API Hash、Bot Token 和代理密码使用本地密钥加密
- 支持导入和导出旧版 YAML 配置
- Web 页面显示当前应用版本

## 频道下载规则

每个下载频道可配置独立的 `download_filter`，不影响其他频道。留空时使用全局媒体类型和文件格式规则。规则编辑器提供媒体类型、扩展名、大小、日期、关键词、消息 ID、分辨率和时长等点选条件，并自动生成表达式。

支持字段：

- `message_date` / `message_date_time`
- `message_id` / `id`
- `message_caption` / `caption`
- `media_file_size` / `file_size`
- `media_file_name` / `file_name`
- `media_width`、`media_height`、`media_duration`
- `media_type`、`file_extension`

支持 `and` / `or` / `not`、括号、比较运算符、KB/MB/GB/TB 单位和正则表达式。示例：

```text
file_size >= 10MB and (caption == r'.*电影.*' or file_name == r'.*1080p.*')
```

```text
message_date >= 2025.01.01 and media_type == r'(video|audio)'
```

规则采用安全解析器执行，不会运行任意 Python 代码。

## Docker 启动

```bash
docker compose up -d --build
```

打开：

```text
http://localhost:8000
```

首次访问会进入配置向导。

数据目录：

- `./data`：SQLite、加密密钥、Telegram 会话和日志
- `./downloads`：默认下载目录

备份时至少需要保留 `./data`。

## 本地开发

需要 Python 3.12 或 3.13。Python 3.12 / 3.13 均安装 TgCrypto 加速：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

## 测试

```bash
pytest
```

覆盖率：

```bash
pytest --cov=app --cov-report=term-missing
```

## 目录结构

```text
app/
  main.py              FastAPI 路由与应用生命周期
  database.py          SQLite 数据访问
  downloader.py        下载服务
  telegram_manager.py  Telegram 登录和客户端管理
  settings_service.py  配置读写、加密与 YAML 兼容
  security.py          密码、加密和 CSRF
  templates/           Jinja2 页面
  static/              前端样式和 JavaScript
tests/                 自动化测试
```

## 安全说明

- 管理密码使用 scrypt 哈希保存。
- API Hash 和代理地址中的凭据使用 Fernet 加密。
- Telegram 验证码和两步验证密码只用于当前登录请求，不写入数据库。
- 默认服务监听 `0.0.0.0:8000`。如果暴露到公网，请在反向代理层启用 HTTPS。
- `data/.secret.key` 用于解密敏感配置，丢失后需要重新填写相关配置。

## 与上游项目的关系

功能设计参考 `tangyoha/telegram_media_downloader`。本项目是独立重写，不沿用其 Flask Web 层和手工 YAML 启动流程。上游项目的 MIT 许可和署名保留在 `LICENSE` 与 `NOTICE` 中。
