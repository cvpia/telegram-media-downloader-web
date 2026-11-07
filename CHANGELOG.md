# Changelog

## 3.0.5

- 切换为原项目使用的 `tangyoha/pyrogram` patch 分支 2.1.22。
- MTProto API Layer 升级到 184，修复 `UPDATE_APP_TO_LOGIN`。
- 使用新版 Pyrogram 的 Unix 时间 `msg_id` 实现，修复首次登录错误。

## 3.0.4

- 修复 Pyrogram 首条消息使用错误参考时钟导致的 `msg_id too low`。

## 3.0.3

- 自动处理 Telegram `msg_id` 时钟同步错误并重试发送验证码。
- 时间同步持续失败时显示明确提示。

## 3.0.2

- 登录步骤可直接测试已保存的代理网络。

## 3.0.1

- 首次配置改为代理优先，并增加 Telegram 网络连通测试。
- 增加可选 Bot Token 配置并加密保存。
- 增加验证码发送与校验过程反馈。
- 完善滚动条样式和移动端自适应布局。

## 3.0.0

- 重写为 FastAPI Web 应用。
- 新增浏览器首次配置向导。
- 新增 Web 端 Telegram 验证码及两步验证登录。
- 新增 SQLite 配置、频道和下载历史存储。
- 新增敏感配置加密、管理员密码和 CSRF 保护。
- 新增频道管理、下载控制和实时状态。
- 新增 YAML 导入导出兼容。
- Web 页面显示版本号。
