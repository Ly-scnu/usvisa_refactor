# 05_stage_components

实际一条龙业务组件层。每个阶段只负责自己的事情，供 `pipeline.py` 编排调用。

- `stage00_proxy/`: 生成 711Proxy sticky 代理材料。
- `stage01_cf_gate/`: 无头 CloakBrowser + CDP 方式处理 Cloudflare/Turnstile gate，可被登录/业务阶段反复调用恢复。
- `stage02_waiting_room/`: 等待室检测与策略；`slot_01` 可占一个等待 lane，`slot_02` direct-only 命中等待室立即回收。
- `stage03_login/`: B2C 登录、密保问题填充、登录回跳恢复。
- `stage04_query/`: 同浏览器上下文内协议请求 page-view、family、posts、days、entries，按目标日期窗口筛选。
- `stage05_booking/`: 命中目标后按 booking 配置并发提交。
- `pipeline.py`: 代理 → CloakBrowser → CF → 等待室 → 登录 → 安排预约/协议查票 → 命中提交；遇到 CF/登录回退会调用恢复组件。
