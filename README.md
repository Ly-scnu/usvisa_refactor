# OpenSands US Visa Refactor

<p align="center">
  <a href="https://github.com/Ly-scnu/usvisa_refactor/stargazers"><img src="https://img.shields.io/github/stars/Ly-scnu/usvisa_refactor?style=social" alt="GitHub Stars" /></a>
  <img src="https://img.shields.io/badge/license-Non--Commercial-red" alt="Non-Commercial License" />
  <img src="https://img.shields.io/badge/backend-FastAPI-009688" alt="FastAPI" />
  <img src="https://img.shields.io/badge/frontend-Vue%203-42b883" alt="Vue 3" />
  <img src="https://img.shields.io/badge/status-active%20refactor-blue" alt="Active Refactor" />
</p>

> 面向本地研究、工程化学习和自动化控制台架构交流的重构项目。项目会持续更新，欢迎 Star、Issue、PR、合作赞助，也欢迎自动化、前端、后端、浏览器工程、分布式调度方向的大佬指导。

<p align="center">
  <img src="picture/1.png" alt="OpenSands US Visa Refactor UI" width="92%" />
</p>

## 项目定位

`usvisa_refactor` 是一个面向本地运行的自动化调度控制台工程。它把多槽位会话、代理路由、页面阶段、实时诊断、票池分析、配置管理和运行清理组织成一个可视化、可维护、可复盘的系统。

这个项目不是一次性脚本，而是一个更偏工程化的本地控制台：

- 前端：Vue 3 + Vite，提供总览、诊断、票池、配置、清理等页面。
- 后端：FastAPI API Gateway，统一暴露状态、控制、配置、分析和清理接口。
- 调度：slot worker、SLA 调度、冷却恢复、代理健康、查询节流。
- 存储：本地 `storage/` 保存运行状态、日志、截图、票池历史和调试证据。
- 安全上传：真实账号、代理、Cookie、浏览器 profile、日志和数据库默认不进入 Git。

## 界面预览

### 总览 / 实时诊断

<p align="center">
  <img src="picture/1.png" alt="总览与实时诊断" width="92%" />
</p>

### 配置 / 策略工作台

<p align="center">
  <img src="picture/2.png" alt="配置与策略工作台" width="92%" />
</p>

### 票池分析 / 运行清理

<p align="center">
  <img src="picture/3.png" alt="票池分析与运行清理" width="92%" />
</p>

## 功能亮点

- 多槽位运行面板：展示每个 slot 的阶段、页面、代理、健康分和恢复动作。
- 实时诊断链路：把关键运行阶段拆成可观察状态，便于定位问题和复盘。
- 票池与结果分析：聚合查询记录、路线表现、失败原因和历史趋势。
- SLA 调度框架：支持候选会话、热会话、冷却、恢复、节流和峰值窗口策略。
- 配置工作台：本地编辑目标、账号、代理、并发、冷却和提交策略。
- 清理中心：支持日志、截图、临时文件、历史 JSONL 的预演和清理。
- GitHub 友好：提交配置示例与源码，默认忽略真实 `config/*.toml` 和 `storage/`。

## 快速开始

### 1. 克隆项目

```powershell
git clone https://github.com/Ly-scnu/usvisa_refactor.git
cd usvisa_refactor
```

如果你还没有改仓库名，也可以先使用当前仓库地址克隆；README 后续会按正式仓库名 `usvisa_refactor` 维护。

### 2. 安装后端依赖

```powershell
python -m pip install -r backend\requirements.txt
```

### 3. 安装前端依赖

```powershell
cd frontend
npm ci
cd ..
```

### 4. 初始化本地私密配置

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_config.ps1
```

该命令会从示例文件生成：

```text
config/app.toml
config/accounts.toml
config/proxy.toml
```

这些文件包含本地私密配置，默认不会提交到 Git。请自行填写：

- `config/accounts.toml`：账号、密码、密保答案等本地信息。
- `config/proxy.toml`：代理服务商、代理账号和路线配置。
- `config/app.toml`：目标地点、目标 ID、槽位数、调度与提交策略。

### 5. 启动服务

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1
```

也可以一键启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1
```

默认地址：

- API 文档：<http://127.0.0.1:18890/docs>
- Web UI：<http://127.0.0.1:18891>

## 推荐运行流程

1. 运行 `scripts/init_config.ps1` 生成本地配置。
2. 填写自己的账号、代理、目标地点和目标日期。
3. 初期保持 `booking.armed = false`，先做 dry-run 和链路验证。
4. 打开 UI，检查总览、诊断、票池、配置、清理页面是否正常。
5. 根据自己的合规边界调整调度、冷却、并发和提交策略。
6. 每次运行后先在清理页预演，再清理日志、截图和临时产物。

## 目录结构

```text
backend/
  00_infrastructure/     配置模型、事件总线、运行状态、调度策略基础设施
  01_proxy_management/   代理路由与代理连接信息构造
  02_session_context/    会话上下文、槽位状态和页面上下文组织
  03_browser_management/ 浏览器启动、浏览器选项、页面分类
  05_stage_components/   主流水线阶段：代理、页面、登录、业务查询、提交
  07_scheduler/          调度、worker、drain 策略
  09_api_gateway/        FastAPI 接口、WebSocket 状态推送、配置与清理接口
  99_error_recovery/     失败分类、恢复动作和重试/回收策略
frontend/                Vue + Vite 控制台
config/                  仅提交 *.example.toml，真实 *.toml 不提交
scripts/                 初始化、启动、敏感信息检查脚本
picture/                 README 展示图片与交流群二维码
storage/                 本地运行数据，默认不提交
tests/                   单元测试
docs/                    架构与排障文档
```

## 上传前安全检查

上传 GitHub 前建议执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check_no_secrets.ps1
git add -n .
```

请不要提交：

- `config/app.toml`
- `config/accounts.toml`
- `config/proxy.toml`
- `storage/` 内的任何运行数据
- `frontend/node_modules/`
- `frontend/dist/`
- `*.log`, `*.sqlite`, `*.db`, `*.har`, `*.pcap`
- Cookie、Local Storage、Session Storage、IndexedDB
- 浏览器 profile、截图、真实账号、真实代理凭证

## 合作与交流

如果你觉得项目有帮助，欢迎给一个 Star。Star 是最直接的支持，也方便后续更新被更多同方向的人看到。

欢迎这些方向的朋友参与：

- 自动化控制台、任务调度、SLA 编排
- Vue 前端、可视化、交互体验优化
- FastAPI 后端、工程结构、测试体系
- Playwright、浏览器工程、本地调试工具链
- 日志分析、票池分析、数据可观测性
- 安全合规、授权协议、项目文档完善
- 赞助测试资源、工程开发时间或基础设施

QQ群交流讨论：

<p align="center">
  <img src="picture/qrcode_1781936976460.jpg" alt="QQ群二维码" width="260" />
</p>

## 非商用授权

本项目当前仅允许用于：

- 个人学习
- 技术研究
- 本地实验
- 代码审阅
- 非商业交流展示

未经作者明确书面授权，禁止：

- 商业售卖、打包转卖、付费分发
- 用作收费 SaaS、代抢、代预约、代运营服务
- 去除作者信息后重新发布
- 以本项目为核心进行商业化推广
- 将本项目集成到任何商业产品、课程、培训或企业内训

如需商业使用、课程使用、企业内训、二次发行、项目集成或其他超出非商业范围的用途，请先联系作者取得书面授权。

## 免责声明

本项目仅用于工程化学习、自动化系统研究、前端/后端架构交流与本地实验。使用者必须自行确保其行为符合所在国家/地区法律法规、目标网站服务条款以及相关平台规则。

作者不鼓励、不支持、也不授权任何形式的非法用途，包括但不限于：

- 未经授权访问、测试、扫描或干扰第三方系统
- 绕过平台规则、风控策略或服务条款从事牟利行为
- 破坏服务稳定性或影响其他用户正常使用
- 批量滥用账号、代理、接口或第三方资源
- 侵犯他人权益、泄露隐私数据或传播敏感信息

本项目不承诺任何预约、查询、提交、抢占或业务结果，不提供任何官方服务，不代表任何政府机构、签证平台、代理服务商或第三方网站。

因使用、修改、分发、部署或参考本项目产生的任何风险、损失、账号问题、服务限制、法律责任或第三方纠纷，均由使用者自行承担。作者和贡献者不承担任何直接或间接责任。

如果你不理解或不同意以上限制，请不要运行、传播或二次开发本项目。

## Star History

如果项目对你有帮助，欢迎 Star 支持，也欢迎加入群交流、提交 Issue、发 PR 或提供合作建议。

<p align="center">
  <a href="https://star-history.com/#Ly-scnu/usvisa_refactor&Date">
    <img src="https://api.star-history.com/svg?repos=Ly-scnu/usvisa_refactor&type=Date" alt="Star History Chart" width="92%" />
  </a>
</p>

## 当前状态

- 项目仍在持续重构和迭代。
- README、UI、配置样例、测试和清理中心会继续完善。
- 欢迎有经验的朋友一起把它做成更稳定、更可维护、更专业的本地自动化控制台。
