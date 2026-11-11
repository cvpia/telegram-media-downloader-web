# Telegram Media Downloader Wiki

自托管 Telegram 媒体下载、筛选、转发和长期存储工具。

- 官方仓库：https://github.com/cvpia/telegram-media-downloader-web
- 站内文档：\`/wiki\`
- 当前版本：3.13.1

## 文档导航

- [[Deployment]] — Docker Compose、预构建镜像、Python 原生运行
- [[Configuration]] — 首次配置、Telegram 登录和下载偏好
- [[Operations-and-Security]] — 数据持久化、升级、备份、安全与故障排查

## 推荐部署路径

1. 安装 Docker Engine 24+ 与 Compose v2。
2. 克隆官方仓库。
3. 执行 \`docker compose up -d --build\`。
4. 打开 \`http://服务器IP:8000/dashboard\` 完成浏览器配置。
