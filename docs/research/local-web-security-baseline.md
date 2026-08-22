# 本地 Web 安全基线

> **结论先行**：`127.0.0.1` 绑定只是第一道网络边界，不是认证机制。Developer Preview 在连接真实 BOSS 账号之前，必须同时具备精确 Host/Origin 校验、每次启动随机认证、非环境凭据防护、默认拒绝 CORS、服务端写操作确认门，以及默认不记录凭据/个人信息。First Product Release 则必须再补齐 Windows 用户级密钥保护、数据生命周期、安装包与依赖供应链、自动化负向安全测试。任何一项核心门禁缺失，都不应连接真实账号发布。
>
> 调研问题：[GitHub Issue #4](https://github.com/faint4/boss-agent-cli/issues/4)  
> 调研日期：2026-08-22  
> 范围：仅绑定 `127.0.0.1`、单机单用户、React + TypeScript + Vite 前端、Python 本地 API、可发起 Platform Write 并处理登录态和个人信息的 Windows 产品。

## 1. 威胁模型与边界

本基线至少防御以下来源：

1. 用户访问的恶意公网网页尝试调用本地 API、打开 WebSocket 或读取返回数据；Chrome 已明确把公网网页访问本地/回环服务视为需要权限控制的 CSRF 与指纹风险，但浏览器保护仍在演进，WebSocket 等通道并非在所有阶段都由该机制完整覆盖，因此产品不能依赖浏览器弹窗作为唯一防线。[Chrome Local Network Access](https://developer.chrome.com/blog/local-network-access?hl=en)
2. 攻击者控制 DNS，让其域名解析到 `127.0.0.1`，从而以攻击者域名作为 Host/Origin 访问本地服务（DNS rebinding）。Vite 官方因此明确警告不得把 `server.allowedHosts` 设为 `true`。[Vite server options](https://vite.dev/config/server-options)
3. 本机其他普通进程扫描固定端口并直接调用 API。回环绑定并不区分“本产品 UI”和“同一台机器上的其他进程”，所以仍需不可猜测的会话认证。
4. 前端 XSS、依赖投毒或错误的动态 HTML 渲染窃取本地会话并发起平台写操作。CSP 和安全 DOM API 能降低风险，但写操作还必须有独立、服务端强制的交易确认边界。[OWASP CSP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html) [OWASP HTML5 Security](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html)
5. 本地文件、日志、崩溃报告、浏览器缓存或备份泄露 BOSS Cookie、AI API Key、简历、聊天和候选人资料。OWASP 建议首先减少敏感信息存储，并使用操作系统提供的安全存储机制保护密钥。[OWASP Cryptographic Storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html)

不在本地 Web 层能独立解决的风险：已完全控制当前 Windows 用户会话或应用进程的恶意软件、BOSS 平台自身被攻破、用户主动复制并外泄数据。这些剩余风险必须在产品说明中明确，不能宣称“本地即绝对安全”。

## 2. 强制架构决策

### 2.1 单一精确 Origin

- 后端只绑定 IPv4 `127.0.0.1`，不得绑定 `0.0.0.0`、`::`、LAN 地址或由用户输入的主机名；启动后验证实际监听地址，发现非回环监听立即退出。Vite 官方说明 `0.0.0.0`/`true` 会监听所有地址。[Vite server.host](https://vite.dev/config/server-options#server-host)
- 浏览器始终打开 `http://127.0.0.1:<随机或已分配端口>`；同一次运行不得混用 `localhost`、`127.0.0.1` 和 `::1`，因为 scheme/host/port 共同构成 Origin。[RFC 6454: The Web Origin Concept](https://datatracker.ietf.org/doc/html/rfc6454)
- UI 的生产构建产物和 API 由同一个 Python 服务、同一个 Origin 提供。正常产品运行不启用 CORS，不使用 Vite 开发服务器，也不从 CDN 加载脚本、字体或样式。OWASP 的 REST 指南建议在不需要跨域调用时不发送 CORS 许可头。[OWASP REST Security](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html#cors)
- `Host` 必须精确匹配本次监听的 `127.0.0.1:<port>`；拒绝其他域名、其他端口、空 Host、多个 Host，以及未经配置的 `Forwarded`/`X-Forwarded-Host`。如果采用 Starlette/FastAPI，可使用显式 allowlist 的 `TrustedHostMiddleware`，但仍要测试端口和代理头的实际行为。[Starlette middleware](https://www.starlette.io/middleware/#trustedhostmiddleware)
- Developer Preview 若为前端开发必须使用 Vite：固定 `host: "127.0.0.1"`，使用显式 `allowedHosts`，不得设 `allowedHosts: true`，CORS 仅允许精确 API Origin；此模式只能使用测试数据，不能连接真实 BOSS 登录态。[Vite server options](https://vite.dev/config/server-options)

### 2.2 每次启动的本地会话认证

- 服务每次进程启动用密码学安全随机源生成至少 32 字节（256 bit）启动秘密；Python 标准库明确把 `secrets` 用于安全 token，并指出 32 字节适合典型用途。[Python `secrets`](https://docs.python.org/3/library/secrets.html#how-many-bytes-should-tokens-use)
- 该秘密不得写入配置、数据库、日志、命令行参数、URL query 或浏览器持久存储。启动器可把**单次 bootstrap token** 放入 URL fragment；前端读取后立即 `history.replaceState` 清除 fragment，并通过一次性交换接口换取内存会话。bootstrap token 首次成功交换或超时即失效。
- 所有 `/api/**`，包括读取状态、搜索结果和健康详情，都要求会话认证；只有静态 UI 和不含 PID、路径、版本细节的最小存活检查可以匿名。认证失败统一返回 401/403，不泄露技术细节。OWASP 要求 API 错误避免向客户端返回堆栈或内部提示。[OWASP REST Security: Error handling](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html#error-handling)
- SPA 使用 `Authorization: Bearer <session-token>` 或专用自定义请求头；token 只保存在 JS 内存，不进入 `localStorage`、`sessionStorage`、IndexedDB 或 URL。退出、服务重启、闲置超时后立即失效。
- 比较 token 使用恒定时间比较；认证中间件必须包住所有路由和所有 HTTP 方法，默认拒绝未知路由/方法。

这种 bearer 设计不依赖浏览器自动附带的 Cookie，因此削弱了传统 CSRF 的条件。如果未来改用 Cookie，会话 Cookie 必须为 host-only、`HttpOnly`、显式 `SameSite=Strict`、最小 Path，并额外使用同步器 CSRF token；OWASP 明确把 SameSite 视为纵深防御而非通用 CSRF 替代品。[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html) [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)

### 2.3 Origin、Fetch Metadata 与 CSRF

对所有非安全方法（`POST/PUT/PATCH/DELETE`）实行以下**叠加**检查：

1. 认证 token 有效；
2. `Origin` 必须精确等于本次 UI Origin；有 Origin 但不匹配、`null` Origin一律拒绝；
3. `Sec-Fetch-Site` 存在时只接受 `same-origin`；`cross-site`、`same-site` 拒绝。缺失时不能放行替代 Origin 校验；
4. 只接受声明的 `Content-Type`（写 API 通常为 `application/json`）和显式自定义认证头，拒绝 `text/plain`、form 和未声明内容类型；
5. `GET/HEAD/OPTIONS` 不得改变本地或平台状态。

OWASP 推荐用 `Sec-Fetch-Site` 阻断明显跨站请求，但同时要求对不发送 Fetch Metadata 的客户端保留标准 Origin/Referer 回退；也明确要求安全方法不产生状态变化。[OWASP CSRF Prevention: Fetch Metadata](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#use-fetch-metadata-headers-to-verify-nature-of-the-request) 这里进一步采用“本产品只支持现代受控浏览器”的更严格策略：浏览器写请求缺失 Origin 时默认拒绝。

CORS 响应不应出现 `Access-Control-Allow-Origin: *`、动态回显 Origin 或 `Access-Control-Allow-Credentials: true`。预检只允许精确 UI Origin、实际使用的方法和头；任何其他 Origin 的预检返回拒绝，且实际请求仍执行相同认证与 Origin 检查。CORS 只是浏览器读响应权限，不是服务端授权。[OWASP HTML5 Security](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html)

### 2.4 Platform Write 必须是服务端交易确认

“页面上出现确认弹窗”不够。每一次投递、打招呼、回复、请求简历、职位上下架等 Platform Write 都使用两阶段协议：

1. UI 提交草稿；服务端规范化并保存不可变 write intent，内容至少包含工作区、平台账号、动作、目标、最终文本/影响、payload hash、过期时间。
2. 服务端返回短时、单次 `confirmation_id`，UI 从**服务端 intent** 渲染确认页，不从可被篡改的旧客户端状态拼装。
3. 用户明确点击确认后，UI 只提交 `confirmation_id`；服务端重新验证会话、Origin、intent 未变更、未过期、未使用、仍属于当前工作区，随后执行 intent 中绑定的精确动作。
4. 成功、失败或不确定结果都会消费该 confirmation；重试必须生成新的 intent。使用幂等键防止双击和网络重放。
5. 首版一个确认只对应一个目标和一个平台写操作；不得以“一次确认”授权批量、未来或后台无人值守写入。

OWASP Transaction Authorization 要求授权在服务端强制、关键交易数据由服务端保存并由用户核对、状态按顺序推进，交易数据变化后旧授权立即失效；这正是上述设计的依据。[OWASP Transaction Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html)

### 2.5 实时进度通道

- Developer Preview 优先使用同源、已认证 HTTP + SSE 提供单向进度；用户操作仍走普通受保护 HTTP，减少 WebSocket 攻击面。
- 如果必须使用 WebSocket：握手必须校验精确 `Host` 和 `Origin`，连接建立后在允许任何业务消息前用短时单次 ticket 或首条消息认证；不得把 token 放进 query；每条消息重新做动作授权和 schema 校验，并设置消息大小、速率、连接数、空闲超时和取消机制。RFC 6455 规定浏览器握手携带 Origin；OWASP 要求 WebSocket 显式 Origin allowlist、消息级授权和资源限制。[RFC 6455](https://datatracker.ietf.org/doc/html/rfc6455) [OWASP WebSocket Security](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)
- 会话退出、工作区切换或服务停止时，立即关闭相关流和 WebSocket；断线不得自动恢复并执行待确认 Platform Write。

## 3. 浏览器响应与前端基线

至少返回以下头，并在自动化测试中固定：

```text
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()
Cache-Control: no-store
```

- CSP 不允许 `unsafe-inline`、`unsafe-eval`、远程脚本或宽泛 `connect-src`；如果构建工具确需内联启动代码，使用构建期 hash/nonce，不降低整个策略。CSP 的 `connect-src` 同时约束 fetch、XHR、EventSource 和 WebSocket。[OWASP CSP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
- 所有 API JSON 设置准确 `Content-Type: application/json`；敏感 UI/API 返回 `Cache-Control: no-store`，防止浏览器和中间缓存保存响应。OWASP REST 指南明确推荐 `no-store`、`frame-ancestors 'none'` 与 `nosniff`。[OWASP REST Security: Security Headers](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html#security-headers)
- 不注册 Service Worker，不做离线缓存；不把简历、聊天、候选人资料、平台标识符或 token 放入浏览器存储。退出时清空内存状态。
- 渲染平台或用户文本时使用框架默认转义/`textContent`，禁止未经严格净化的 `dangerouslySetInnerHTML`。OWASP HTML5 指南建议使用 `textContent` 而不是将数据直接放入 `innerHTML`。[OWASP HTML5 Security](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html)
- `http://127.0.0.1` 可被 Secure Contexts 规范视为“potentially trustworthy”，因此在严格回环边界内可接受不自签 TLS；一旦支持 LAN、公网、反向代理或非回环主机，HTTPS/WSS 就成为阻断项。[W3C Secure Contexts](https://www.w3.org/TR/secure-contexts/#potentially-trustworthy-origin)

## 4. 凭据与本地数据保护

### 4.1 凭据

- BOSS Cookie、stoken、AI API Key 和本地数据加密 key 不得进入前端、浏览器存储、日志、错误详情、崩溃报告或导出文件；前端只看到“已连接/过期/需重新登录”等状态。
- Windows 上使用当前用户作用域 DPAPI（不使用 `CRYPTPROTECT_LOCAL_MACHINE`）或等价 OS credential vault。Microsoft 说明 `CryptProtectData` 默认通常只有同一登录凭据、同一电脑可以解密；`CRYPTPROTECT_LOCAL_MACHINE` 会让同机任何用户都可解密，因此本产品不得启用该标志。[Microsoft `CryptProtectData`](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- OS 安全存储不可用时 fail closed：要求重新登录或禁用真实平台能力，不得回退到硬编码 key、环境变量、MachineGuid/主机名派生 key 或明文文件。OWASP 建议使用 OS 提供的安全存储，且不得把 key 放在源码、版本库或环境变量中。[OWASP Cryptographic Storage: Key storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html#key-storage)
- 登录必须由用户点击“连接 BOSS”启动官方页面/专用浏览器窗口；不得收集 BOSS 用户名和密码，不得无提示扫描日常浏览器 Cookie。登出和“清除账号数据”必须删除受保护凭据并使当前 Web 会话失效。

### 4.2 数据最小化与隔离

- 求职者和招聘者工作区使用物理独立数据目录/数据库和独立平台会话，只共享无敏感性的应用设置。每个请求和后台任务都携带服务端解析的 workspace id，禁止客户端路径拼接。
- 求职工作区只持久保存用户明确选择的偏好、候选岗位和本地简历；招聘工作区默认只保存必要引用与用户备注，候选人简历、联系方式和聊天按需读取且默认不落盘。
- 数据位于当前 Windows 用户的应用数据目录，不位于源码树、临时目录或浏览器下载目录；创建时应用当前用户专属 ACL。备份/导出必须显式选择、默认脱敏，并清楚提示包含的个人信息。
- First Product Release 对确需持久化的敏感字段使用随机数据加密 key，key 由当前用户 DPAPI 包裹；设计 key 轮换、损坏恢复和删除路径。OWASP 强调最有效的保护是不存储敏感信息，并建议把加密 key 与数据分离。[OWASP Cryptographic Storage](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html)
- 为缓存、运行记录、日志和临时文件定义默认保留期以及可见的“查看占用/立即清除”入口；清除应覆盖数据库、文件、内存队列和浏览器态。任何遥测或崩溃上报默认关闭，开启前说明字段并取得明确同意。

## 5. 日志、诊断与审计

采用字段 allowlist，而不是只靠字段名/正则黑名单：

- 可记录：时间、随机 request/run id、工作区类型（不含账号）、动作类别、结果类别、耗时、错误代码、确认 intent 的不可逆短 hash。
- 不记录：请求/响应 body、完整 URL/query、Authorization、Cookie、会话/CSRF/bootstrap/confirmation token、BOSS `security_id`、平台账号、姓名、电话、微信、邮箱、简历、聊天文本、职位沟通草稿、候选人标识、AI prompt/response、绝对本地路径、浏览器控制消息。
- Platform Write 记录“谁（本地会话匿名 id）在何时确认了哪类动作及结果”，但不记录消息正文或凭据；确认失败、Origin/Host 拒绝、token 校验失败、数据清除等安全事件要有结构化审计。
- 对换行、分隔符等日志输入做编码/净化以防日志注入；日志轮转、总量上限、最短必要保留期和用户一键清除为必需项。

OWASP Logging Cheat Sheet 明确要求会话标识、访问 token、密码、密钥和敏感个人数据通常不得直接记录，并要求净化 CR/LF 等事件数据、限制日志访问且不得超期保留。[OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)

现有 `src/boss_agent_cli/output.py` 的递归字段名与文本正则脱敏可作为最后一道防线，但不能替代 Web 层字段 allowlist；它不会自动识别所有姓名、简历、聊天或语义型个人信息。[项目源码：`output.py`](../../src/boss_agent_cli/output.py)

## 6. 现有代码的复用审计

| 现状 | 结论 |
| --- | --- |
| `bridge/protocol.py` 固定 `BRIDGE_HOST = "127.0.0.1"` | 可复用“仅回环绑定”原则。[项目源码](../../src/boss_agent_cli/bridge/protocol.py) |
| `bridge/daemon.py` 的 `/command`、`/status`、`/ext` 没有认证、Host/Origin/Fetch Metadata 校验 | **禁止把 Bridge daemon 直接暴露给 Web UI，也禁止复制其安全模型。** `/command` 可转发 `exec`，风险远高于普通本地状态接口。[项目源码](../../src/boss_agent_cli/bridge/daemon.py) |
| WebSocket `/ext` 接受任意浏览器 Origin，后连接者可覆盖 `ext_ws` | 必须在 Web 产品开发前单独加固或隔离 Bridge；OWASP 要求 WebSocket handshake 使用精确 Origin allowlist。[OWASP WebSocket Security](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html#origin-header-validation) |
| 扩展发来的 `log` 消息被原样写入 `daemon.log` | 不满足日志字段 allowlist；可能产生凭据/个人信息泄露和日志注入。[项目源码](../../src/boss_agent_cli/bridge/daemon.py) |
| `auth/token_store.py` 用同目录 salt + MachineGuid/主机指纹派生 Fernet key | 有加密与完整性保护，但 MachineGuid/主机指纹不是用户秘密，也不提供 Windows 用户级密钥边界；Web 产品应迁移到当前用户 DPAPI，且不允许 fallback。[项目源码](../../src/boss_agent_cli/auth/token_store.py) [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) |
| CLI 求职者和招聘者默认共享一个 `data_dir`，平台仅在 auth 子目录部分区分 | 不满足已确定的双工作区物理隔离，需要新的数据根与迁移策略。[项目源码：`main.py`](../../src/boss_agent_cli/main.py) [项目源码：`auth/manager.py`](../../src/boss_agent_cli/auth/manager.py) |

这不是对既有 CLI/Bridge 的漏洞公告；它是 Web 产品威胁模型下的复用判定。Bridge 仍应另开安全修复票，因为网页可主动连接回环端口，且浏览器厂商的 Local Network Access 保护不能替代服务自身鉴权。[Chrome Local Network Access](https://developer.chrome.com/blog/local-network-access?hl=en)

## 7. 分阶段发布门槛

### 7.1 Developer Preview：连接真实账号前的阻断项

- [ ] 生产构建 UI 与 API 同源；服务仅监听 `127.0.0.1`，启动测试证明未监听 LAN/IPv6 wildcard。
- [ ] 精确 Host allowlist；错误 Host、代理 Host、DNS-rebinding 域名全部 400/403。
- [ ] 每次启动 256-bit 随机会话；bootstrap 单次/短时；API 无 token 全部拒绝；token 不落盘、不进 URL query/日志/浏览器存储。
- [ ] 所有写请求同时验证 bearer、精确 Origin、`Sec-Fetch-Site` 与 JSON Content-Type；默认无 CORS；GET 无副作用。
- [ ] 所有 Platform Write 使用服务端不可变 intent + 短时单次确认；修改、过期、重放、双击均不能执行。
- [ ] BOSS/AI 凭据使用当前 Windows 用户 DPAPI 或等价 OS vault；无安全存储时禁用真实账号。
- [ ] 两个工作区数据目录和登录会话物理隔离；招聘侧候选人简历、联系方式、聊天默认不落盘。
- [ ] CSP、安全响应头、默认转义、无 Service Worker/远程资源；敏感响应 `no-store`。
- [ ] 日志字段 allowlist、轮转/上限；canary 测试证明 token、Cookie、`security_id`、姓名、电话、简历和聊天不出现于任何日志或错误响应。
- [ ] Vite 开发模式只能使用模拟数据；真实登录必须运行后端托管的生产构建。
- [ ] 现有 Bridge 未完成独立认证/Origin 加固前，Web 后端不调用或暴露其 `/command`、`/ext`。

### 7.2 First Product Release：面向普通 Windows 用户的附加阻断项

- [ ] 安装器/启动器无需命令行，单实例启动；端口占用时 fail closed，不连接未知现有服务；退出时销毁会话并取消所有任务。
- [ ] 发布构建关闭 debug、源码映射公开、交互式 API 文档和详细异常；只携带已构建静态资源和必要后端路由。
- [ ] DPAPI 凭据迁移、轮换、损坏/重装恢复、登出/清除均有测试；不会回退到 MachineGuid 派生 key。
- [ ] 敏感持久数据 key 由 DPAPI 包裹；具备保留策略、存储占用展示、按工作区清除、脱敏导出与卸载清理说明。
- [ ] 对安装包和更新产物做 Windows 代码签名；更新清单与包做签名校验；依赖锁定并在 CI 中做漏洞/许可证审计。
- [ ] 自动化安全回归覆盖下节矩阵，并在支持的 Windows/Chrome/Edge 版本上做安装后冒烟验证。
- [ ] 完成隐私说明：存什么、为何存、存多久、是否发送给 AI/遥测、如何删除；AI 和遥测均按需启用，未配置时核心流程不受影响。
- [ ] 对 Bridge 安全加固另行验收，或者正式版本完全不包含/启动 Bridge。

## 8. 必须自动化的负向测试

| 测试 | 预期结果 |
| --- | --- |
| 从恶意 Origin 发起 form、`text/plain`、JSON fetch | 均不得改变本地或平台状态；403 |
| `Host: attacker.example` 但 TCP 目标为 `127.0.0.1` | 在路由和静态文件之前拒绝 |
| 无 token、错误 token、旧进程 token、bootstrap 重放 | 401/403；响应无细节 |
| `Sec-Fetch-Site: cross-site/same-site` 写请求 | 403 |
| CORS 预检来自任意非 UI Origin | 不返回许可头 |
| GET/HEAD/OPTIONS 遍历全部路由 | 无本地/平台状态变化 |
| confirmation 过期、重复、payload/工作区/账号被替换、双击 | 零次或最多一次精确执行，不可换目标 |
| 未确认任务断线/恢复/重启 | 不自动执行 Platform Write |
| WebSocket（如有）错误/缺失 Origin、未认证首条消息、超大消息 | 拒绝/关闭；无业务执行 |
| HTML/简历/聊天中注入脚本与事件属性 | 只作为文本显示；CSP 不报告可执行路径 |
| 日志注入 CR/LF 与 canary 凭据/PII | 日志结构不破坏，敏感 canary 不出现 |
| 同时打开求职/招聘工作区并构造越权 id/path | 无跨工作区读取、写入或任务串线 |
| 服务端口已被第三方占用 | 启动失败并解释恢复，不向占用者发送 token/凭据 |

## 9. 建议拆分的后续实现票

1. 本地 Web 安全外壳：loopback bind、Host/Origin/Fetch Metadata、启动会话、统一安全头与测试夹具。
2. Platform Write intent/confirmation 状态机与幂等测试。
3. Windows DPAPI credential store 与现有 `TokenStore` 迁移。
4. 求职/招聘工作区物理隔离和数据保留/清除模型。
5. Bridge daemon 独立安全加固或从 Web 产品中隔离移除。
6. Windows 签名安装器、更新验证和安装后安全冒烟。

## 10. 一手来源索引

- [RFC 6454 — The Web Origin Concept](https://datatracker.ietf.org/doc/html/rfc6454)
- [RFC 6455 — The WebSocket Protocol](https://datatracker.ietf.org/doc/html/rfc6455)
- [W3C Secure Contexts](https://www.w3.org/TR/secure-contexts/)
- [Chrome for Developers — Local Network Access](https://developer.chrome.com/blog/local-network-access?hl=en)
- [Vite — Server Options](https://vite.dev/config/server-options)
- [Starlette — Middleware](https://www.starlette.io/middleware/)
- [Python — `secrets`](https://docs.python.org/3/library/secrets.html)
- [Microsoft — `CryptProtectData`](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)
- [OWASP — CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP — REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html)
- [OWASP — WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)
- [OWASP — Transaction Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Transaction_Authorization_Cheat_Sheet.html)
- [OWASP — Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
- [OWASP — Cryptographic Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html)
- [OWASP — Content Security Policy Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html)
- [OWASP — HTML5 Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html)
- 项目源码：[`bridge/daemon.py`](../../src/boss_agent_cli/bridge/daemon.py)、[`bridge/protocol.py`](../../src/boss_agent_cli/bridge/protocol.py)、[`auth/token_store.py`](../../src/boss_agent_cli/auth/token_store.py)、[`output.py`](../../src/boss_agent_cli/output.py)

