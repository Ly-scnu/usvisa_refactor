# 99_error_recovery

职责：已知错误 -> 恢复组件映射。遇到 1015/429/CF/login/session/network/page_stuck 等问题时，流水线应调用这里的组件，而不是只做简单重启。

当前已实现：

- `ban_1015` / `rate_limit_1015` -> `Ban1015Recovery`
  - 保存官方错误页面截图/状态证据
  - 关闭当前浏览器上下文
  - 清空当前代理材料
  - 让调度器下一轮用新代理/新 profile
  - 不在同页反复刷新
- `rate_limit_429` -> `RateLimit429Recovery`
  - 保留会话，退避降频
- `cf_challenge` -> `CfChallengeRecovery`
  - 交回 CF Gate 组件处理 interactiveBegin + CDP 点击
- `access_denied` -> `access_denied/AccessDeniedResetRecovery`
  - 识别 Access denied / Sorry, you have been blocked / 1020
  - 保存官方错误页状态和截图证据
  - 不刷新原页，不继续 API
  - 立即关闭当前 CloakBrowser 上下文，清空代理材料
  - 让调度器下一轮新代理/新 profile 重新开始

后续扩展：

- `login_redirect`
- `session_expired`
- `network_error`
- `page_stuck`

