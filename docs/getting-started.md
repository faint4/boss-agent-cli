# 快速上手

这份文档只保留最短可验证路径。完整能力说明见 [README.md](../README.md)，Agent 接入见 [agent-quickstart.md](agent-quickstart.md)。

## 1. 安装

```bash
uv tool install boss-agent-cli
patchright install chromium
```

源码开发环境：

```bash
git clone https://github.com/faint4/boss-agent-cli.git
cd boss-agent-cli
uv sync --all-extras
uv run patchright install chromium
```

源码中的 Developer Preview Web 外壳可用一条命令启动：

```bash
cd web
pnpm install --frozen-lockfile
pnpm run build
cd ..
uv run python scripts/developer_preview_gate.py source --repo-root .
uv run boss-web
```

该命令由 Python 在随机的 `127.0.0.1` 端口托管已构建的 React UI，并在服务就绪后打开浏览器。启动凭据仅用于本次进程且只保存在内存中。求职与招聘工作区使用完全分离的数据库、缓存、运行目录和平台会话；Windows 上的平台会话由当前用户的 DPAPI 加密保存。连接操作只会打开 BOSS 官方登录窗口，不读取日常浏览器凭据；退出只删除当前工作区的平台凭据并保留本地工作流数据。Browser Bridge 仍不属于这条启动路径，也不会自动执行 BOSS 写入。

修改 `web/` 下的前端源码后，使用 `cd web && pnpm install --frozen-lockfile && pnpm run build` 更新随 Python 包发布的生产资源。

连接真实 BOSS 前还需运行 `uv run patchright install chromium`。真实账号只能使用上述 Python 同源托管的生产构建，不能使用 Vite 开发或预览服务器。完整自动化矩阵、人工验收边界和脱敏证据格式见 [Developer Preview 验收](developer-preview.md)。

## 2. 本地自检

```bash
boss doctor
boss status
boss schema --format native
```

期望结果：

- `boss doctor` 返回 `ok:true` 或带有明确 `recovery_action` 的 `ok:false`。
- `boss status` 能说明当前登录态是否可用。
- `boss schema --format native` 返回 JSON 信封，并列出当前 CLI 能力。

真人直接运行 `boss` 或 `boss wizard` 进入角色、平台和目标向导。非 TTY / Agent 调用使用结构化输入，不会等待交互：

```bash
boss --json wizard --input-json '{"role":"candidate","platform":"zhipin","goal":"shortlist","inputs":{}}'
boss --json wizard --status <run_id>
```

## 3. 第一个平台命令

登录后先用搜索和详情验证平台链路，再继续投递、沟通或招聘者 workflow。

```bash
boss search "Golang" --city 广州 --welfare "双休"
boss detail <security_id>
```

`security_id` 来自 `search` 返回的 JSON 数据。提交 Issue 时必须脱敏，不要粘贴真实 `security_id`、Cookie、Token、手机号、微信号、姓名或公司内部信息。

## 4. JSON 信封契约

所有 Agent 可读输出都应是单个 JSON 信封：

```json
{
	"ok": true,
	"schema_version": "1.0",
	"command": "schema",
	"data": {},
	"pagination": null,
	"error": null,
	"hints": null
}
```

失败时：

```json
{
	"ok": false,
	"schema_version": "1.0",
	"command": "status",
	"data": null,
	"pagination": null,
	"error": {
		"code": "AUTH_REQUIRED",
		"message": "未登录",
		"recoverable": true,
		"recovery_action": "boss login"
	},
	"hints": null
}
```

## 5. 开发者验证

修改代码前后使用同一组命令验证：

```bash
uv run pytest tests/ -q
uv run ruff check src/ tests/
uv run mypy src/boss_agent_cli
uv run boss --help
uv run boss schema --format native
cd web && pnpm test && pnpm run typecheck && pnpm run build
```

如果只改文档，至少运行：

```bash
uv run pytest tests/test_agent_docs.py tests/test_open_source_docs.py -q
git diff --check
```

## 6. 提交问题前

提交 Bug 时请提供：

- `boss --version`
- Python 版本
- 操作系统
- 平台：`zhipin` 或 `zhilian`
- 角色：`candidate` 或 `recruiter`
- 完整 JSON 信封，已脱敏
- `boss doctor` 输出，已脱敏

平台接口变化、登录失效、风控、Cookie/CDP、浏览器自动化问题，先阅读 [platform-risk.md](platform-risk.md)。
