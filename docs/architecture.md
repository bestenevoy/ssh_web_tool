# 架构设计

## 整体架构

```
┌───────────────────────────────────────────────────────────────┐
│                 桌面壳 (pywebview + WebView2 + 系统托盘)          │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │              浏览器 (React 单文件 index.html)              │  │
│  │  主机列表/分组 · 终端标签+分屏 · 工具面板(会话/指令/SFTP)     │  │
│  │  SFTP 工作台+传输列表 · 文件编辑器 · 设置页(日志/主题)        │  │
│  └────────────────────────┬────────────────────────────────┘  │
└───────────────────────────┼───────────────────────────────────┘
                            │ HTTP REST (/api/*) + WebSocket (/ws/*)
┌───────────────────────────▼───────────────────────────────────┐
│           Python FastAPI Server (main.py = 组合根)              │
│  api/routers/ 按域拆分: sessions hosts groups quick_commands    │
│  history sftp files config storage_auto external ws            │
│ ┌───────────────────────────────────────────────────────────┐ │
│ │            会话池 SSHSession (sessions.py)                  │ │
│ │   远端 shell (asyncssh) ←→ 本机 shell (WinPTY) 同一终端互切   │ │
│ │   每会话编排: OutputBus(有界广播) · EchoParser(命令解析)       │ │
│ │             SessionLog + TerminalMirror(日志转录)            │ │
│ └──────────────┬──────────────────────────┬─────────────────┘ │
│    SFTP 传输 → transfers.py 任务注册表      │ JSON 持久化        │
│                                            ▼                  │
│   event_bus(事件广播) · storage.py(主机/分组/设置→data.json)     │
│   history_db.py(命令历史→SQLite) · config.py(~/.ai4one/sshtool) │
└───────────────────────────▲───────────────────────────────────┘
                            │ HTTP API / WS 推送
┌───────────────────────────┴───────────────────────────────────┐
│        外部调用方: Python SDK / CLI (ssh_client) ·              │
│        被劫持进程 (packages/ssh-monkeypatch → /ws/stream)       │
└───────────────────────────────────────────────────────────────┘
```

## 关键设计点

1. **连接状态后端统一维护**：WebSocket 断开不关闭 SSH 会话，页面重开可恢复（restore 有抢占保护，多 tab/多页面场景已修）。
2. **本机/远端同一终端互切**：任何 SSH 断开（exit 或传输层掉线）自动切回本机 shell（WinPTY 跑 cmd/PowerShell/pwsh），终端始终可用；重连复用同一会话与同一份终端记录。本机终端输入 `ssh user:pass@host[:port]` 由服务端拦截直连远端。
3. **会话日志 = 终端镜像转录**：`SessionLog` + `TerminalMirror`（pyte 虚拟屏幕）保证日志内容与终端显示构造性一致（进度条只留终态、退格/`\r` 覆盖收敛、alt-screen 不混入）。默认不记录；未开启记录时输出只存有界近期环（256KB），首次开启回放保连接 banner；文件名 `{host}_{start}_running_{session_id}.log`，关闭时补全结束时间。
4. **事件循环阻塞纪律**：所有路由均为 `async def`，因此任何同步阻塞调用必须 `asyncio.to_thread` 移出循环——已知四处：Ctrl+C 控制台注入（Win32 SendMessageTimeout，进程级 AttachConsole 用全局锁串行）、本机 PTY terminate（内含 time.sleep）、本机 PTY 读取轮询、编辑器文件全量读写。
5. **输出广播有界**：`OutputBus` 每监听器约 100 块（≈400KB）队列，慢消费者丢最旧保最新，前端断流不拖垮内存。
6. **SFTP 传输任务化**：上传/下载在 `transfers.py` 注册表登记，支持进度轮询（`GET /api/sftp/transfers`）、取消（半成品清理）、打开所在目录；本机终端禁用 SFTP；断点续传未实现（已排期）。
7. **事件总线**：关键操作发布事件经 `/ws/events` 广播，前端「活动日志」实时显示；采集在模块级外部 store（见 frontend-dev），不挂 React 状态。
8. **SDK/CLI 复用会话**：`exec_host` 自动查找该主机已有会话复用，注入+捕获模式让 CLI 命令与 Web 终端共享输出。
9. **单文件前端**：React 构建产物内联为 `static/index.html`（vite-plugin-singlefile），后端零改动直接提供；改前端必须重建该文件。
10. **跨进程观测**：`packages/ssh-monkeypatch` 劫持 paramiko/asyncssh 连接，事件推 `/ws/stream`，`external_sessions.py` 汇聚后可供浏览器订阅回放。

## 项目结构

```
ssh-web-tool/
├── main.py                  # FastAPI 后端入口（组合根：装配路由/静态页/生命周期 + pywebview 桌面壳 + 单实例锁）
├── ssh_client.py            # Python SDK + CLI 二合一
├── pyproject.toml / uv.lock # UV 项目配置与依赖锁定
├── build_exe.bat / build.ps1# 打包：前端构建 + PyInstaller EXE + 劫持包 wheel
├── docs/                    # 文档（本目录）
├── ssh_web_tool/            # 核心 Python 包
│   ├── __init__.py          # 包入口（导出 SSHWebTool / patch_all 等）
│   ├── sessions.py          # SSHSession + 会话池：远端/本机 shell、断开自动切回、SSH 命令拦截
│   ├── session_log.py       # 会话日志：命名/管理 + 聚合缓冲延迟落盘 + 历史读取
│   ├── terminal_mirror.py   # pyte 终端镜像：输出回放虚拟屏幕，转录"新显示"的行
│   ├── output_bus.py        # 终端输出广播（有界队列）+ 清洗缓冲区
│   ├── echo_parser.py       # 多 shell 提示符识别，从输出流捕获用户最终命令
│   ├── ps_history.py        # PSReadLine 历史文件跟随（Tab 补全后的真实命令）
│   ├── prompt_detect.py     # 终端状态分析（提示符/运行中判定）
│   ├── transfers.py         # SFTP 传输任务注册表（进度/取消/历史）
│   ├── storage.py           # JSON 持久化（主机/分组/类型/快捷指令/设置 → data.json）
│   ├── history_db.py        # 全局命令历史（SQLite）
│   ├── config.py            # 配置加载（~/.ai4one/sshtool/config.json）
│   ├── event_bus.py         # 事件总线（/ws/events 数据源）
│   ├── external_sessions.py # 外部会话中心（跨进程观测）
│   ├── ws_protocol.py       # WS 消息类型双端契约（与 frontend types/ws.ts 同步，有协议测试）
│   ├── ssh_tool.py          # 可嵌入库入口（SSHWebTool / patch_paramiko / patch_asyncssh）
│   ├── playwright_mgmt.py   # 存储阵列管理页自动登录（Playwright）
│   ├── tray.py              # Windows 系统托盘（纯 ctypes）
│   ├── updater.py / version.py
│   └── api/
│       ├── models.py        # Pydantic 请求/响应模型
│       ├── deps.py          # 单例注入（经 `import main` 取绑定，测试可替换）
│       └── routers/         # 按域拆分的路由：sessions/hosts/groups/quick_commands/
│                            # history/sftp/files/config/storage_auto/external/ws
├── static/
│   ├── index.html           # React 构建产物（单文件，git 跟踪，改前端须重建）
│   └── external-panel.js    # 外部会话面板（原生 JS，跨进程观测事件流）
├── packages/
│   └── ssh-monkeypatch/     # 独立 pip 包：劫持 paramiko/asyncssh 连接并推送事件
├── frontend/                # React + TypeScript 源码（详见 frontend-dev.md）
├── tests/                   # pytest（unit + protocol 一致性）
├── .github/workflows/       # ci.yml / release.yml / ssh-monkeypatch-release.yml
└── dist/                    # 打包产物（EXE / wheel / release）
```

运行时数据统一在 `~/.ai4one/sshtool/`：`config.json`、`data.json`、`logs/`（会话日志 + server.log）。
