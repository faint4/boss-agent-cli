# Developer Preview 验收

Developer Preview 是源码交付的 Windows 里程碑。它使用 React 生产构建和同一个 Python 本地服务，不是 Vite 开发服务器，也不是 First Product Release 安装包。

## 干净检出与启动

在 Windows 11 x64 的全新目录中执行：

```powershell
git clone https://github.com/faint4/boss-agent-cli.git
Set-Location boss-agent-cli
uv sync --all-extras
corepack enable
Set-Location web
pnpm install --frozen-lockfile
pnpm test
pnpm run typecheck
pnpm run build
Set-Location ..
uv run patchright install chromium
uv run python scripts/developer_preview_gate.py source --repo-root .
uv run boss-web
```

最后一条命令必须在服务完成 `127.0.0.1:0` 绑定后才打开默认浏览器。浏览器地址使用一次性 fragment；页面认证后 fragment 会立即消失。按 `Ctrl+C` 明确关闭源码预览服务。真实账号不得连接 Vite 的 `dev` 或 `preview` 服务。

## 自动化门禁

提交前运行：

```powershell
uv run pytest tests/ -q
uv run ruff check src/ tests/ mcp-server/
uv run mypy src/boss_agent_cli
Set-Location web
pnpm test
pnpm run typecheck
pnpm run build
Set-Location ..
uv run python scripts/developer_preview_gate.py source --repo-root .
```

保存该命令输出的 `source_build_sha256`。它对 Python/Web 锁文件和实际由 Python 服务提供的生产资源生成确定性指纹；人工证据里的同名字段必须填写这个值。

GitHub 的 `Developer Preview (Windows)` 门禁会在 Windows 上重新安装锁定依赖、重建生产资源、安装固定 Patchright Chromium，并运行应用合同、本地 Web、安全负向、恢复、隐私和真实浏览器测试。浏览器测试使用 fake BOSS 边界，不访问真实账号，但会从生产 Python 服务完成两个 Core Journey、逐项 Confirmation Gate、窄屏检查和浏览器存储检查。

以下行为由自动化覆盖：

- 精确 IPv4 loopback、Host、启动认证、Origin、Fetch Metadata、JSON Content-Type、默认拒绝 CORS 和安全响应头；
- 两个 Workspace 的路径、数据库、Platform Session、Run、清除和导出隔离；
- Write Intent 的修改、过期、重放、双击、重启和不确定结果；
- 取消、认证过期、限流、平台风控和手动恢复；
- 凭据、Cookie、Token、简历、联系方式、聊天和草稿 canary 不进入日志、错误、浏览器存储或默认导出；
- Web 生产运行时不导入、调用或公开 Browser Bridge。

## 真实 BOSS 人工门禁

真实平台验收只允许人工完成以下四个动作：

1. 在求职 Workspace 点击“连接 BOSS”，通过官方窗口建立该 Workspace 专属 Platform Session，完成一次只读 Core Journey；
2. 准备一条低影响招呼或申请，核对目标和最终内容，在 Confirmation Gate 只确认一次；
3. 切换到招聘 Workspace，重新通过官方窗口建立独立 Platform Session，完成一次 Inbound Applicants 只读 Journey；
4. 准备一条低影响回复，核对目标和最终内容，在 Confirmation Gate 只确认一次。

出现认证过期、限流、风控或不确定结果时立即停止，不重试、不绕过，也不能把该项记录为 `pass`。不得把 Cookie、Token、`security_id`、姓名、电话、微信、公司内部信息、简历、聊天或消息正文写入证据。

复制 [脱敏模板](developer-preview-evidence.template.json) 到仓库外的受控位置，只填写版本、commit、Windows/浏览器版本、上方门禁输出的 `source_build_sha256` 和枚举结果。然后验证：

```powershell
uv run python scripts/developer_preview_gate.py validate-evidence C:\path\to\developer-preview-evidence.json
```

验证器严格拒绝额外字段和自由文本，且错误输出不会回显输入值。证据文件不提交到公开仓库；发布负责人把验证通过的文件作为受限的发布证据保存。

## 完成判定

只有 Windows、Python、Web、浏览器和安全自动化全部通过，且两个 Workspace 的真实只读 Journey 与各一次人工确认低影响 Platform Write 都有有效脱敏证据时，Issue #25 才能关闭。任何未确认/重复 Platform Write、跨 Workspace 泄漏、凭据或个人信息泄漏、非 loopback 监听、自动重试风控或不确定结果都会阻断 Developer Preview。
