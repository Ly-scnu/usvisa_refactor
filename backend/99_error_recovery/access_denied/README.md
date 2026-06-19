# access_denied

Cloudflare `Access denied` / `Sorry, you have been blocked` 专用恢复组件。

策略：

1. 不刷新当前页面，不继续打 API。
2. 保存官方错误页状态和截图证据。
3. 立即关闭当前 CloakBrowser context/browser/profile。
4. 清空当前代理材料。
5. 返回 `reset_proxy_profile_restart_round`，让调度器下一轮用新代理/新 profile 重新开始。

这个错误和 CF managed challenge 不同：不是同页面 CDP 点击可恢复状态。
