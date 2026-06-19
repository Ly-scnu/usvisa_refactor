# usvisa_refactor_v1

这是一个整理后的本地自动化/调度控制台版本。上传 GitHub 前已经按“可分享代码、不分享个人运行数据”的原则拆分：代码与样例配置可提交，真实账号、代理、Cookie、浏览器画像、日志和运行数据库全部留在本机并被 `.gitignore` 排除。

> 建议：先创建 **private repository**，确认没有敏感信息后再决定是否开放。

## 默认端口

- Backend API: <http://127.0.0.1:18890/docs>
- Frontend UI: <http://127.0.0.1:18891>

使用 18890/18891 是为了避免和其他本地实例端口冲突。

## 目录结构

```text
backend/
  00_infrastructure/     配置模型、事件总线、运行状态、调度策略基础设施
  01_proxy_management/   代理路由与代理连接信息构造
  02_session_context/    会话上下文、槽位状态和页面上下文组织
  03_browser_management/ 浏览器启动、CloakBrowser/Chrome 入口、Playwright 连接
  05_stage_components/   主流水线阶段：代理、CF/等待室、登录、业务查询、提交
  07_scheduler/          调度/任务入口相关模块
  09_api_gateway/        FastAPI 接口、WebSocket 状态推送、配置保存接口
  99_error_recovery/     失败分类、恢复动作和重试/回收策略
frontend/                Vue + Vite 控制台
config/                  仅提交 *.example.toml；真实 *.toml 不提交
scripts/                 初始化、启动、敏感信息检查脚本
tests/                   测试与验证脚本
UI/                      UI 设计/静态参考资源
```

## 快速开始

### 1. 安装后端依赖

```powershell
cd D:\path\to\usvisa_refactor_v1
python -m pip install -r backend\requirements.txt
```

### 2. 安装前端依赖

```powershell
cd frontend
npm ci
cd ..
```

### 3. 生成本地配置

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\init_config.ps1
```

这会从样例生成：

```text
config/app.toml
config/accounts.toml
config/proxy.toml
```

这些文件包含账号、代理、目标 ID 等本地私密信息，已经被 `.gitignore` 排除，**不要提交**。

### 4. 填写配置

- `config/accounts.toml`：填写你自己的账号、密码、密保答案。
- `config/proxy.toml`：填写你自己的代理服务商、账号、密码、路由。
- `config/app.toml`：填写目标地点、目标 application/post id、槽位数、调度和提交策略。
- `producer.cloak_browser_root`：可留空使用默认，也可设置为自己的 CloakBrowser/Chrome 兼容浏览器目录。

样例配置默认：

- `booking.armed = false`，避免刚 clone 后误触发提交。
- `standalone_smoke = true`、`real_browser_probe = false`，偏向本地冒烟验证。
- API/UI 端口为 18890/18891。

### 5. 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_backend.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_frontend.ps1
```

或同时启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_all.ps1
```

## 上传 GitHub 前检查

强烈建议每次 push 前执行：

```powershell
git init
powershell -ExecutionPolicy Bypass -File .\scripts\check_no_secrets.ps1
git add -n .
```

重点确认不会被提交：

- `config/app.toml`
- `config/accounts.toml`
- `config/proxy.toml`
- `storage/`
- `frontend/node_modules/`
- `frontend/dist/`
- `*.log`, `*.sqlite`, `*.db`, `*.har`, `*.pcap`
- 浏览器 profile、Cookie、Local Storage、Session Storage、IndexedDB

## 推荐 GitHub 上传方式

```powershell
git init
git branch -M main
powershell -ExecutionPolicy Bypass -File .\scripts\check_no_secrets.ps1
git add .
git commit -m "Initial sanitized refactor release"
gh repo create usvisa_refactor_v1 --private --source . --remote origin --push
```

如果远程仓库已存在：

```powershell
git remote add origin https://github.com/<your-user>/usvisa_refactor_v1.git
git push -u origin main
```

## 维护原则

1. **配置与代码分离**：真实 `config/*.toml` 永不提交，只提交 `*.example.toml`。
2. **运行数据与代码分离**：`storage/` 只在本机保存日志、截图、票池、Cookie/会话等，不进入 Git。
3. **端口隔离**：v1 使用 18890/18891，避免影响旧项目。
4. **提交默认关闭**：公开/分享版本默认 `booking.armed = false`，需要使用者自己开启。
5. **提交前扫一遍**：先跑 `scripts/check_no_secrets.ps1`，再 `git add -n .` 人工复核。

