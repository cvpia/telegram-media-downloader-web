# Operations and Security

## 数据目录

- \`./data\`：SQLite、密钥、Telegram 会话、日志与数据库备份
- \`./downloads\`：下载的媒体文件
- \`./data/.secret.key\`：敏感配置解密密钥

## 更新

\`\`\`bash
git pull
docker compose pull
docker compose up -d
docker compose logs -f --tail=100
\`\`\`

不要执行 \`docker compose down -v\`，该命令会删除持久化数据卷。

## 备份

\`\`\`bash
docker compose stop
tar -czf telegram-media-backup-$(date +%F).tar.gz data downloads
docker compose start
\`\`\`

## 安全基线

- 公网访问必须启用 HTTPS。
- 不要提交 \`data/\`、\`downloads/\`、会话文件或日志到 Git。
- 限制后台入口来源，使用强管理员密码。
- 定期升级并保留可恢复的离线备份。

## 故障排查

1. 容器退出：检查 \`docker compose logs\` 与持久化挂载。
2. Telegram 无法连接：重新测试代理并确认账号状态。
3. 下载持续失败：检查磁盘空间、文件引用和日志中的重试原因。
4. SSE 不更新：关闭代理响应缓冲并延长读取超时。
