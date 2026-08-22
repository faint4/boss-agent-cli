# Windows delivery path: Developer Preview to First Product Release

> **结论先行**：首个 Windows 产品版本采用 **React/Vite 生产构建 + Python 同源本地服务 + PyInstaller `onedir` + Inno Setup 用户级安装器 + GitHub Releases 全量升级包**。Developer Preview 先从源码启动；打包预演阶段发布 `onedir` ZIP；First Product Release 再发布带开始菜单快捷方式、卸载器和 Authenticode 签名的单个 Setup EXE。首版不采用 PyInstaller `onefile`、Briefcase、Nuitka、WiX/MSI 或 MSIX，也不实现静默后台自更新。
>
> 调研问题：[GitHub Issue #8](https://github.com/faint4/boss-agent-cli/issues/8)
> 调研日期：2026-08-22
> 范围：Windows x64、单机单用户、Python 本地服务、React + TypeScript + Vite 前端、仅监听 `127.0.0.1`，从 Developer Preview 演进到 First Product Release。

## 1. 当前仓库事实与约束

- 项目当前是使用 Hatchling 的 Python 包，`pyproject.toml` 声明 Python `>=3.10`，入口为 `boss` 和 `boss-mcp`；没有桌面 GUI 运行时，也没有 Web 前端目录或 Vite 构建配置。[项目配置](../../pyproject.toml)
- Web 产品已决定由同一个 Python 进程托管 React 生产构建和版本化 API，只绑定精确的 `127.0.0.1:<port>`，每次启动生成随机会话，Platform Write 必须逐项确认。[本地 Web 安全基线](local-web-security-baseline.md)
- 求职与招聘工作区、平台会话和数据根物理隔离；停止、升级或崩溃后只允许恢复安全读取状态，不允许恢复旧的写授权。[工作区与会话设计](../design/workspace-storage-and-session-lifecycle.md)
- Developer Preview 从源码运行可以接受；First Product Release 必须让普通 Windows 用户无需命令行即可安装、启动、升级和卸载。

这些约束意味着交付链应当继续把 Python 包作为应用核心，而不是为了安装器改写现有构建后端；React 产物是被 Python 服务托管的静态数据，不需要在用户机器上安装 Node.js 或运行 Vite。Vite 官方说明 `vite build` 默认输出 `dist`，且 `vite preview` 只用于本地预览而不是生产服务器。[Vite 静态部署](https://vite.dev/guide/static-deploy.html) [Vite 生产构建](https://vite.dev/guide/build)

## 2. 最终选择

| 交付面 | 决策 |
| --- | --- |
| Developer Preview | 从源码安装 Python 与前端依赖，构建 React `dist`，通过 Python 的窗口式启动入口启动；允许受控测试者手动创建快捷方式，不承诺安装/升级体验 |
| 打包预演 | Windows CI 使用固定的 CPython 3.14 x64 补丁版本和锁定依赖，生成 PyInstaller `onedir`，压缩为 ZIP 放入 GitHub prerelease |
| First Product Release | 将同一个 `onedir` 目录交给 Inno Setup，生成当前用户安装的 Setup EXE、开始菜单快捷方式和卸载项 |
| 监听 | 运行时只监听 `127.0.0.1` 的系统分配端口；不使用固定端口、`localhost`、`0.0.0.0`、Windows 服务或 LAN 模式 |
| 启动 | 无控制台的单实例启动器；主进程内运行 API 与静态资源服务；就绪后才调用默认浏览器打开一次性 bootstrap URL |
| 更新 | 应用只检查并提示 GitHub 最新正式 Release；用户明确同意后下载并验证完整的下一版签名安装器，再退出并运行安装器 |
| 签名 | Developer Preview 可暂时不签名但必须标明；First Product Release 必须用稳定的 RSA 发布者身份签署所有需要加载的 PE 文件、安装器和卸载器，并做 RFC 3161 时间戳与安装前后验证 |
| 卸载 | 删除程序文件、快捷方式、卸载项、运行时 token/临时文件；平台凭据默认清除；用户明确创建的工作区数据默认保留，并提供“同时删除本地数据”的准确范围选项 |

发布用 Python 不是项目支持矩阵：源码仍可支持 `3.10–3.14`，但 Windows 安装包只嵌入一个在 CI 中锁定并完整测试的 x64 CPython。PyInstaller 会携带解释器和依赖，用户无需预装 Python；它必须在目标操作系统上构建，不能从 Linux 交叉生成受支持的 Windows 包。[PyInstaller 手册](https://pyinstaller.org/en/stable/) 首次实现时使用 CPython 3.14 x64，并在每次正式发布中钉死补丁版本、PyInstaller 和 `pyinstaller-hooks-contrib`；PyInstaller 官方要求这两个包大致同步。[PyInstaller 安装说明](https://www.pyinstaller.org/en/stable/installation.html)

## 3. 为什么选择 PyInstaller `onedir`

### 3.1 与候选 Python 打包器的比较

| 方案 | 官方能力 | 本项目判断 | 结论 |
| --- | --- | --- | --- |
| PyInstaller `onedir` | 收集解释器、模块和数据文件到自包含目录；默认模式，易于检查和调试；支持无控制台 Windows 可执行文件。[运行模式](https://www.pyinstaller.org/en/stable/operating-mode.html) [命令选项](https://pyinstaller.org/en/stable/usage.html) | 保留 Hatchling 作为源码包后端；只需新增一个发布入口和 `.spec`；可直接把 Vite `dist` 作为数据目录加入 | **采用** |
| PyInstaller `onefile` | 启动时解压到随机 `_MEI` 临时目录，启动稍慢；异常终止可能留下目录；官方建议先让 `onedir` 工作，并警告不要给 Windows one-file 程序管理员权限。[运行模式](https://www.pyinstaller.org/en/stable/operating-mode.html) | 已经需要安装器，单文件对用户没有额外价值；临时解压增加启动、杀毒扫描、进程与故障诊断变量 | 不采用 |
| Nuitka standalone/onefile | 编译为 C，需要 C11 编译器；standalone/onefile 可独立分发，官方同样建议先验证 standalone。[用户手册](https://nuitka.net/user-documentation/user-manual.html) [用例](https://nuitka.net/user-documentation/use-cases.html) | 引入 MSVC/MinGW 编译链和更长构建面，却没有已证明的性能、兼容或源码保护需求 | 暂不采用；仅当 PyInstaller 的动态依赖无法稳定收集时重新评估 |
| Briefcase | 可生成 Windows App 与 MSI/ZIP，MSI 使用 WiX；也能把 PyInstaller 目录作为 external app 包装。[Windows 文档](https://briefcase.readthedocs.io/_/downloads/en/stable/pdf/) [external app](https://briefcase.readthedocs.io/en/v0.3.24/how-to/external-apps.html) | 更适合由 Briefcase 管理完整应用生命周期的项目；对当前 Hatchling CLI 加本地 Web 启动器，会叠加模板和 WiX，而不是减少边界 | 不采用 |

`onedir` 的 `.spec` 必须显式包含 Vite 生产产物、Python 包数据、平台端点数据和需要的浏览器自动化资源。PyInstaller 官方允许在 `.spec` 的 `Analysis(datas=...)` 中复制完整目录，并推荐通过运行时 `__file__` 定位打包资源。[Spec 文件](https://pyinstaller.org/en/stable/spec-files.html) [运行时资源](https://pyinstaller.org/en/stable/runtime-information.html)

构建默认使用 `--windowed`/`--noconsole`，发布构建使用 `--noupx`。后者是项目判断：避免额外压缩变换，使签名、差异定位和杀毒误报调查更简单；PyInstaller 官方确认 Windows 上可用 `--noupx` 完全禁用 UPX。[PyInstaller 命令选项](https://pyinstaller.org/en/stable/usage.html)

### 3.2 必须先通过的兼容性探针

PyInstaller 选择成立的前提不是“成功生成 EXE”，而是以下真实路径在一台无 Python、无 Node、无源码的干净 Windows 虚拟机中全部通过：

1. 导入并启动所有 Web 应用服务依赖；
2. 从包内读取并同源返回 Vite hashed assets；
3. 打开 BOSS 官方登录窗口并完成登录/恢复路径；
4. 加载 `patchright` 所需的动态模块、浏览器资源和辅助进程；
5. 两个核心旅程的真实只读路径与逐项确认 Platform Write；
6. 中文路径、含空格用户名、标准用户权限和离线启动；
7. 安装目录只读时，所有可变数据仍只写入当前用户应用数据目录。

如果缺失模块或资源，应优先补充项目拥有的 PyInstaller hook/`.spec`，而不是在运行时从网络下载代码。只有在连续两个发布候选中无法稳定收集 Patchright/浏览器依赖时，才启动 Nuitka standalone 对照探针；不因可执行文件大小单独换工具。

## 4. 为什么选择 Inno Setup，而不是 WiX/MSI 或 MSIX

| 方案 | 优点 | 当前代价 | 结论 |
| --- | --- | --- | --- |
| Inno Setup EXE | 原生支持当前用户无提权安装、开始菜单/桌面快捷方式、卸载器、应用互斥、关闭占用程序和外部 SignTool。[权限](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm) [快捷方式](https://jrsoftware.org/ishelp/topic_iconssection.htm) [AppMutex](https://jrsoftware.org/ishelp/topic_setup_appmutex.htm) [SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm) | 不是 Windows Installer/MSIX；自动更新需应用自行编排 | **采用** |
| WiX/MSI | Windows Installer 语义、企业部署友好；WiX 可创建快捷方式、升级和卸载。[WiX 指南](https://docs.firegiant.com/wix3/howtos/) | 组件/GUID/升级规则和工具链复杂；当前没有企业 MSI、组策略或 Intune 验收需求；Briefcase 自己也需 WiX 才生成 MSI | 后续企业分发需要明确后再评估 |
| MSIX | Microsoft 提供可靠安装/卸载、差分更新和包身份；full-trust desktop app 可以打包。[MSIX 概览](https://learn.microsoft.com/en-us/windows/msix/overview) [packaged desktop app](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes) | 包文件只读且受保护，文件/注册表行为存在虚拟化；需要先稳定包身份、数据迁移、签名与外部浏览器自动化行为 | 首版不采用；商店、企业管理或差分更新成为产品目标后再评估 |

Inno Setup 使用固定、与显示名称无关的 `AppId`。后续产品更名、版本升级都不得改变它；相同 `AppId`、相同安装模式和位数会被视为同一应用，下一版可沿用安装目录和卸载日志。[AppId](https://jrsoftware.org/ishelp/topic_setup_appid.htm) [Same Application](https://jrsoftware.org/ishelp/topic_sameappnotes.htm) [UsePreviousAppDir](https://jrsoftware.org/ishelp/topic_setup_usepreviousappdir.htm)

### 4.1 安装布局

首版是当前用户安装，不请求管理员权限：

```text
%LOCALAPPDATA%\Programs\<StableProductId>\
  <Product>.exe
  _internal\...

%LOCALAPPDATA%\<StableProductId>\
  app\
  workspaces\job-seeking\
  workspaces\recruiting\
```

Inno 设置 `PrivilegesRequired=lowest`，安装目录使用会映射到当前用户 Program Files 的 `{autopf}`，开始菜单使用 `{group}`/`{autoprograms}`。官方文档说明非管理员模式不会触发 UAC，相关 auto 常量映射到当前用户位置，并把卸载信息写入 HKCU。[PrivilegesRequired](https://jrsoftware.org/ishelp/topic_setup_privilegesrequired.htm) [Non Administrative Install Mode](https://jrsoftware.org/ishelp/topic_admininstallmode.htm) [目录常量](https://jrsoftware.org/ishelp/topic_consts.htm)

程序目录和数据目录必须分离。升级器可以完整替换 `onedir`，绝不能把数据库、简历、日志或平台会话装进/写回安装目录。

### 4.2 快捷方式与可见入口

安装器创建：

- 开始菜单：`<Product>`，指向唯一启动器 EXE；
- 开始菜单：`卸载 <Product>`，指向 Inno 卸载器；
- 可选桌面快捷方式，默认不强制创建；
- 不创建开机自启动、Windows 服务、计划任务或常驻托盘自启动。

Inno 的 `[Icons]` 可创建开始菜单、桌面和卸载快捷方式。[Icons section](https://jrsoftware.org/ishelp/topic_iconssection.htm)

## 5. 启动器与进程生命周期

### 5.1 正常启动

启动器是 PyInstaller 生成的无控制台 EXE，按以下顺序执行：

1. 获取当前登录会话范围、带用户 ACL 的命名 mutex；失败表示已有实例。
2. 创建只允许当前 logon SID 访问的本地 named pipe，用于第二次点击快捷方式时请求主实例“重新打开 UI”；不得使用 named pipe 默认 ACL，因为 Microsoft 说明默认 ACL 还给 Everyone 与匿名账户读权限。[Named Pipe Security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
3. 在主进程内创建 API/静态资源服务，明确绑定 `127.0.0.1` 与端口 `0`，读取操作系统实际分配的端口；端口绑定失败时退出，不扫描或连接未知本地服务。
4. 生成本次进程的 256-bit 启动秘密和单次 bootstrap token；token 不写磁盘、不放命令行。
5. 完成数据库打开、工作区验证和 HTTP 就绪探针后，调用 Python `webbrowser.open_new_tab()` 打开 `http://127.0.0.1:<port>/#<bootstrap>`。Python 标准库说明该 API 会使用默认浏览器打开新标签。[Python `webbrowser`](https://docs.python.org/3/library/webbrowser.html)
6. 浏览器完成单次 token 交换并从 URL fragment 清除 token；后续遵守本地 Web 安全基线。

mutex 使用 `Local\<StableProductId>`，明确表示“每个 Windows 登录会话一个实例”。Windows 官方说明 `Local\` 是会话命名空间，`Global\` 才跨所有会话；本产品不是系统服务，不需要全局对象。[Kernel object namespaces](https://learn.microsoft.com/en-us/windows/win32/termserv/kernel-object-namespaces) mutex 和 named pipe 都必须显式限制到当前 logon SID；不能把“同一机器”误当成“同一用户”。

第二次点击快捷方式时，辅助实例只通过受 ACL 保护的 named pipe 发送“open-ui”命令；主实例生成新的单次 bootstrap token并自行调用默认浏览器。端口、长期会话 token 和平台凭据都不写入发现文件。

### 5.2 退出与子进程

- 浏览器标签关闭**不**自动终止服务，因为可恢复运行可能仍在进行；UI 提供明确的“退出应用”。
- 退出先拒绝新任务并使所有 Write Intent 失效，再取消进行中的只读任务、保存可恢复检查点、清除敏感内存、关闭平台会话和监听 socket，最后释放 mutex/pipe。
- Windows 注销、关闭和进程信号走同一有时限的优雅关闭；超过时限才由系统结束进程。下一次启动不得自动恢复 Platform Write。
- 产品拥有的浏览器自动化/辅助进程加入 Windows Job Object，并使用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` 作为最终清理边界；默认浏览器不加入该 Job Object。Microsoft 说明 Job Object 能把进程组作为一个单元管理，关闭最后句柄时可终止其关联进程树。[Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- 不把本地 Web 后端安装成 Windows 服务。服务会改变用户/桌面会话、DPAPI、浏览器和文件 ACL 边界，也会制造与“用户主动启动、明确退出”冲突的常驻进程。

### 5.3 安装、升级和卸载时的运行实例

应用创建与 Inno `AppMutex` 相同名称的 mutex，安装器/卸载器检测到运行实例时要求用户先退出，不能强杀正在处理状态的进程。Inno 官方说明 `AppMutex` 用于阻止在应用运行时安装新版本或卸载。[Inno AppMutex](https://jrsoftware.org/ishelp/topic_setup_appmutex.htm)

作为辅助，Inno 保留 `CloseApplications=yes`、`RestartApplications=no`：Restart Manager 可以检测占用待更新文件的应用，但安装器不自动重启本产品，避免在升级后悄悄恢复旧运行。[CloseApplications](https://jrsoftware.org/ishelp/topic_setup_closeapplications.htm) 不使用 `force`，防止丢失尚未写盘的状态。

## 6. 签名、SmartScreen 与供应链

### 6.1 分阶段签名

**Developer Preview**：受控源码测试不要求 Authenticode。若发布未签名 prerelease ZIP，Release 页面与启动说明必须明确“仅供已知测试者”；同时发布 SHA-256 摘要，不能宣称 SmartScreen 信任。

**First Product Release**：代码签名是阻断项。

1. 优先使用 Microsoft Artifact Signing（原 Trusted Signing）；如果主体/地区不符合资格，使用 Windows 信任根体系内 CA 签发的 OV 代码签名证书。Microsoft 当前把 Artifact Signing 作为非 Store 分发的推荐方式。[SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
2. 使用稳定的、RSA-based 发布者身份；Smart App Control 当前不接受 ECC 签名。[Smart App Control signing](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control)
3. 在 CI 的受保护签名阶段完成：先构建前端和 `onedir`；盘点所有 EXE/DLL；验证有效的上游签名，签署所有项目生成或未签名且会被加载的 PE；再由 Inno SignTool 签署安装器、卸载器和需要的源文件。Inno 能调用外部 SignTool，并在启用时生成已签名卸载器。[Inno SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm) [SignedUninstaller](https://jrsoftware.org/ishelp/topic_setup_signeduninstaller.htm)
4. 使用 SHA-256 文件摘要和 RFC 3161 时间戳；无时间戳时，证书过期后 Windows 会把签名视为无效。Microsoft SignTool 支持签名、验证和时间戳。[SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool) [Authenticode timestamp](https://learn.microsoft.com/en-us/windows/win32/seccrypto/time-stamping-authenticode-signatures)
5. 打包后再次验证 Setup EXE、uninstaller 和所有运行时 PE；在 Smart App Control 测试机上覆盖安装、启动、登录、浏览器自动化、升级和卸载所有代码路径。Microsoft 明确要求测试安装/卸载二进制及所有会加载的代码路径。[Smart App Control test](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/test-your-app-with-smart-app-control)

签名不等于立即没有 SmartScreen 警告。Microsoft 说明新签名文件仍可能在发布初期显示“未识别”提示，稳定证书身份可积累发布者信誉；未签名版本则每个新哈希都从零开始。[SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) 发布说明必须如实告知早期用户，不承诺“签名后零警告”。

### 6.2 Release 完整性

- Windows 构建只在受控 Windows runner 上进行；依赖、Node lockfile、Python lock、构建工具版本和 Python 补丁版本全部固定。
- Release 先作为 draft 创建，上传签名安装器、可选 debug symbols、SBOM、SHA-256 清单和发布说明，验证完再发布。
- 启用 GitHub immutable releases。GitHub 官方说明不可变 Release 会锁定 tag 和 assets，并自动生成覆盖 tag、commit 与 assets 的 release attestation。[GitHub immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- CI 与本地验收使用 `gh release verify`/`verify-asset` 或等价验证检查 attestation；安装器自身仍必须验证 Authenticode，不能把 GitHub attestation 当作 Windows 发布者签名的替代品。[GitHub CLI release verification](https://cli.github.com/manual/gh_release_verify)

## 7. 升级路径

### 7.1 Developer Preview

Developer Preview 不做自更新。测试者通过 Git 拉取明确 commit/tag，重新安装锁定依赖并重建前端；每次数据 schema 变更都运行迁移测试。源码预览的目标是验证产品和打包边界，不是模拟消费者安装器。

### 7.2 First Product Release

首版采用**提示式全量升级**，不是后台静默升级：

1. 应用在用户主动点击“检查更新”时（未来可增加低频、可关闭的自动检查）调用公开仓库的 `GET /repos/{owner}/{repo}/releases/latest`；GitHub 说明该端点返回最新非 draft、非 prerelease Release 及 assets 和 SHA-256 digest。[GitHub Releases API](https://docs.github.com/en/rest/releases/releases)
2. UI 展示版本、发布日期、变更摘要、下载大小和 Release 页面；不自动下载或安装。
3. 用户明确选择下载后，下载完整 Setup EXE，校验 GitHub asset digest，再验证 Authenticode 链、时间戳与预期发布者身份。任一检查失败即删除临时文件并停止。
4. 用户再次确认安装后，应用停止新任务、使 Write Intent 失效、保存安全检查点、退出主进程，再启动下载的安装器。
5. 安装器使用固定 `AppId` 覆盖程序目录并沿用卸载日志；不迁移或覆盖工作区数据。Inno 官方说明同一应用的新安装会追加到已有卸载日志，并在卸载时逆向撤销各次安装变更。[Appending uninstall logs](https://jrsoftware.org/ishelp/topic_appendnotes.htm)
6. 安装完成后不自动恢复浏览器任务；用户从完成页或快捷方式显式重新启动，新进程使用新 token，并将需要人工恢复的 run 展示出来。

不在首版实现：差分补丁、热替换运行文件、常驻 updater 服务、无人值守自动重启、回滚后自动降级数据库。若升级失败，保留下载的旧正式安装器供人工重新安装；schema 迁移前生成经过范围验证的本地备份，并保证迁移可重新进入或给出明确恢复指引。

GitHub Release 直接下载链接和 latest 链接可作为下载通道；GitHub 官方提供 `/releases/latest/download/<asset>` 形式。[Linking to releases](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases) 但应用不得只信任 URL/HTTPS，仍执行 digest 与 Authenticode 验证。

## 8. 卸载与数据生命周期

卸载器执行以下明确顺序：

1. 用 `AppMutex` 阻止在应用运行时卸载；用户正常退出后继续。
2. 删除安装器拥有的程序目录、开始菜单/桌面快捷方式和卸载注册项。
3. 始终删除运行时 bootstrap/token 文件（如果未来实现）、临时更新包、崩溃中间文件和平台凭据；凭据清理由应用的受限 `--prepare-uninstall` 路径在程序文件删除前执行。
4. 默认保留用户明确创建的偏好、shortlist、备注、导入简历和工作区数据库，并在卸载页显示准确数据根。
5. 提供未默认勾选的“同时删除所有本地工作区数据”选项；选择后由同一受限清理入口验证固定根、workspace marker 和当前用户归属后删除，不能在 Inno 脚本里对可变路径使用宽泛通配符。

Inno 官方强烈警告卸载时不要用通配符删除整个目录，因为可能删除用户数据或错误目录。[UninstallDelete](https://jrsoftware.org/ishelp/topic_uninstalldeletesection.htm) 因此卸载只自动删除安装器记录的程序文件；用户数据删除由应用服务层对固定、已验证的数据根执行。卸载完成页要说明保留或删除了什么，以及重装是否可恢复。

## 9. 分阶段质量门

### Gate A — Developer Preview from source

- [ ] `npm ci && npm run build` 生成 Vite `dist`；Python 服务托管该目录，不运行 `vite preview`。
- [ ] 单个 Python 启动入口完成 `127.0.0.1:0` 绑定、随机会话、就绪后打开默认浏览器、明确退出。
- [ ] 单实例、第二次点击 reopen、端口被占用、无默认浏览器、启动失败均有可理解的恢复提示。
- [ ] 两个工作区真实核心旅程、逐项确认门、恢复后重新确认通过。
- [ ] 真实账号模式满足本地 Web 安全基线；Vite 开发服务器仅可使用模拟数据。
- [ ] 前端不存在时，此 Gate 首先创建前端构建与 Python 静态资源边界；不能直接跳到安装器。

### Gate B — Packaged Developer Preview (`onedir` ZIP prerelease)

- [ ] Windows CI 在固定 CPython 3.14 x64 和锁定工具链上可重复生成 `onedir`。
- [ ] 干净 VM 无 Python、Node、Git 或源码仍可启动和完成两个核心旅程。
- [ ] Patchright、BOSS 登录窗口、动态模块、数据文件、中文路径、空格用户名全部通过。
- [ ] 只监听 `127.0.0.1`；安装目录/解压目录只读时业务数据仍写入固定用户数据根。
- [ ] 启动、退出、异常崩溃、Windows 注销后无遗留产品辅助进程；默认浏览器不被误杀。
- [ ] ZIP 标明 Developer Preview；提供 SHA-256；未签名时明确 SmartScreen 预期。
- [ ] 连续两次候选构建在干净 VM 通过，才锁定 PyInstaller；否则触发 Nuitka 对照探针。

### Gate C — First Product Release candidate

- [ ] Inno 当前用户安装无需 UAC；安装、覆盖升级、修复重装和卸载均无需命令行。
- [ ] 开始菜单、可选桌面快捷方式、Add/Remove Programs 版本和发布者信息正确。
- [ ] 运行中升级/卸载不会强杀；安全退出后安装器可继续；升级后不自动恢复旧写操作。
- [ ] Setup、uninstaller、所有会加载的 EXE/DLL 的签名链、发布者、SHA-256 和时间戳验证通过。
- [ ] Smart App Control/SmartScreen 测试覆盖安装、启动、浏览器自动化、升级与卸载代码路径。
- [ ] GitHub draft Release 包含签名 Setup、SBOM、SHA-256、变更说明；验证后发布为 immutable release 并验证 attestation。
- [ ] 应用更新检查只显示正式 Release；prerelease/draft/降级不可自动安装；下载后同时校验 asset digest 与 Authenticode。
- [ ] 卸载准确删除程序、快捷方式、运行时秘密和平台凭据；保留/删除用户数据两条路径均有自动化测试，且不会触及另一工作区或其他目录。
- [ ] 全新安装、从上一正式版升级、升级中断恢复、重装后数据兼容四条矩阵全部通过。

### Gate D — First Product Release 发布后

- [ ] 每个新版本沿用同一 AppId、安装模式、位数和签名身份。
- [ ] 证书/Artifact Signing 续期、时间戳服务故障和签名密钥撤销有书面应急流程。
- [ ] 发布资产不可变；发现构建错误必须发新版本，禁止替换同 tag 资产。
- [ ] SmartScreen 初期信誉状态如实记录，不通过绕过或自签证书伪装信任。
- [ ] 只有出现明确的 Store、Intune/企业 MSI 或高下载量差分更新目标时，才重新评估 MSIX/WiX。

## 10. 建议拆分的实现票

1. 建立 React/Vite production build，并由 Python 同源托管静态资源。
2. 实现 Windows launcher：loopback port 0、ready/open-browser、session-local mutex、logon-SID named pipe 与可理解错误页。
3. 实现应用退出控制器与 Job Object 子进程清理，覆盖 Windows 注销和崩溃恢复。
4. 建立 PyInstaller `onedir` `.spec` 与 Patchright/动态资源 hook，添加干净 VM 冒烟。
5. 建立 Inno Setup 用户级安装、稳定 AppId、快捷方式、AppMutex、升级与卸载数据选择。
6. 建立 Windows 签名阶段、PE inventory、时间戳、Smart App Control 和签名验证门。
7. 建立 GitHub immutable Release、SBOM/digest/attestation 和应用内提示式更新。
8. 建立安装/升级/卸载/保留数据/删除数据的 Windows 自动化矩阵。

## 11. 一手来源索引

- [Vite — Building for Production](https://vite.dev/guide/build)
- [Vite — Deploying a Static Site](https://vite.dev/guide/static-deploy.html)
- [PyInstaller — Manual](https://pyinstaller.org/en/stable/)
- [PyInstaller — Operating Mode](https://www.pyinstaller.org/en/stable/operating-mode.html)
- [PyInstaller — Spec Files](https://pyinstaller.org/en/stable/spec-files.html)
- [PyInstaller — Run-time Information](https://pyinstaller.org/en/stable/runtime-information.html)
- [Nuitka — User Manual](https://nuitka.net/user-documentation/user-manual.html)
- [Nuitka — Use Cases](https://nuitka.net/user-documentation/use-cases.html)
- [Briefcase — Windows packaging](https://briefcase.readthedocs.io/_/downloads/en/stable/pdf/)
- [Briefcase — Packaging external apps](https://briefcase.readthedocs.io/en/v0.3.24/how-to/external-apps.html)
- [Inno Setup — Help contents](https://jrsoftware.org/ishelp/contents.htm)
- [Inno Setup — AppMutex](https://jrsoftware.org/ishelp/topic_setup_appmutex.htm)
- [Inno Setup — SignTool](https://jrsoftware.org/ishelp/topic_setup_signtool.htm)
- [Microsoft — What is MSIX?](https://learn.microsoft.com/en-us/windows/msix/overview)
- [Microsoft — SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)
- [Microsoft — SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)
- [Microsoft — Smart App Control signing test](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/test-your-app-with-smart-app-control)
- [Microsoft — Named Pipe Security](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights)
- [Microsoft — Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- [GitHub — REST API endpoints for releases](https://docs.github.com/en/rest/releases/releases)
- [GitHub — Immutable releases](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
