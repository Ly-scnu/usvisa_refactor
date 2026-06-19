# callback_not_found

处理 `signin-aad-b2c_1` / B2C 回跳后出现的 `Page Not Found · Customer Self-Service`。

策略：

- 不把该页面继续当作 `idp_loading` 等待；
- 保存当前截图和页面证据；
- 立即关闭当前 CloakBrowser 上下文、代理和画像；
- 交给调度器重新产出票，避免坏会话占用业务查询闸门。
