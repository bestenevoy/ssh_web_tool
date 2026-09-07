# 架构设计

## 整体架构

```
┌─────────────────────────────────────────────────────┐
│                    浏览器 (React UI)                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐ │
│  │ 主机列表  │ │ 终端标签  │ │ SFTP面板  │ │活动日志│ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └───┬────┘ │
│       │              │             │             │      │
│       └──────────────┴─────────────┴─────────────┘      │
│                         │                                  │
│         HTTP REST API  │  WebSocket (/ws/ssh, /ws/events)│
└─────────────────────────┼──────────────────────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────┐
│              Python FastAPI Server (main.py)                │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  主机管理API  │  │  会话管理API   │  │  SFTP/事件API    │  │
│  └──────┬──────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                  │                     │            │
│  ┌──────▼──────────────────▼─────────────────────▼────────┐ │
│  │              SSH 会话池 (sessions.py)                    │ │
│  │   统一创建、持有、销毁所有 SSH 连接，后端维护连接状态      │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                             │ asyncssh                         │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │              持久化存储 (storage.py)                      │ │
│  │   主机/分组/类型/快捷指令/命令历史 → data.json             │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                          ▲
                          │ HTTP API
┌─────────────────────────┴──────────────────────────────────┐
│              外部调用方 (AI / Python 脚本)                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │ Python SDK    │  │ CLI 命令行    │  │ 直接 HTTP 请求    │ │
│  │ (ssh_client)  │  │ (ssh_client)  │  │ (curl/requests)  │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 关键设计点

1. **连接状态后端统一维护**：WebSocket 断开不关闭 SSH 会话，页面重开可恢复
2. **事件总线**：所有关键操作发布事件，通过 `/ws/events` 广播，前端活动日志实时显示
3. **SDK/CLI 复用会话**：`exec_host` 方法自动查找该主机已有会话并复用，不重复建连
4. **单文件前端**：React 构建产物内联为单个 `index.html`，后端零改动直接提供
5. **注入+捕获模式**：CLI 执行命令时注入到已有终端，输出同步显示在 Web 终端，CLI 也能拿到输出

## 项目结构

```
ssh-web-tool/
├── main.py                  # FastAPI 后端入口（API + WebSocket + 静态文件）
├── ssh_client.py            # Python SDK + CLI 二合一
├── pyproject.toml           # UV 项目配置（包配置、依赖、CLI 入口点）
├── uv.lock                  # UV 依赖锁定文件
├── build_exe.bat            # Windows 一键打包脚本入口（调用 build.ps1）
├── build.ps1                # 打包脚本：前端构建 + PyInstaller 打包 EXE + 打包劫持包（wheel）
├── data.json                # 运行时生成的主机配置文件
├── logs/                    # 终端历史日志（按会话 ID 分文件）
├── docs/                    # 文档（本目录）
├── ssh_web_tool/            # 核心 Python 包
│   ├── __init__.py          # 包入口（导出 SSHWebTool / patch_all 等）
│   ├── sessions.py          # SSH 会话池（统一管理所有 SSH 连接）
│   ├── storage.py           # JSON 持久化存储（主机/分组/类型/快捷指令/命令历史）
│   ├── external_sessions.py # 外部会话中心（跨进程观测：接收 ssh-monkeypatch 事件）
│   ├── ssh_tool.py          # 可嵌入的 Python 库入口（SSHWebTool / patch_paramiko / patch_asyncssh / patch_all）
│   └── playwright_mgmt.py   # 存储阵列管理页面自动登录（基于 Playwright）
├── static/
│   ├── index.html           # React 构建产物（单文件，约 560KB）
│   └── external-panel.js    # 外部会话面板（原生 JS，展示跨进程观测事件流）
├── packages/
│   └── ssh-monkeypatch/     # 独立 pip 包：劫持 paramiko/asyncssh 连接并推送事件
│       └── README.md        # 该包独立文档
├── frontend/                # React + TypeScript 源码
│   ├── src/
│   │   ├── components/      # React 组件（主机列表/终端/快捷指令/SFTP/分组管理等）
│   │   ├── lib/             # API 封装 + 自定义 hooks（useTerminals/useEvents/useSettings）
│   │   ├── types/           # TypeScript 类型定义
│   │   ├── App.tsx          # 主应用
│   │   ├── main.tsx         # 入口
│   │   └── index.css        # 样式（CSS 变量主题）
│   ├── vite.config.ts       # Vite 配置（单文件构建 + 开发代理）
│   ├── package.json
│   └── index.html
├── .github/
│   └── workflows/
│       ├── ci.yml                            # CI workflow（多平台多 Python 版本测试）
│       ├── release.yml                       # Release workflow（推送 tag 自动构建 EXE 并发布）
│       └── ssh-monkeypatch-release.yml       # 推送 ssh-monkeypatch-v* 标签自动构建并发布 pip 包
└── dist/
    ├── SSHWebTool.exe       # 打包生成的 EXE 文件（双击运行）
    ├── wheel/               # 劫持包（ssh_web_tool-*.whl，pip install 后 patch_all() 劫持 SSH 连接）
    ├── release/             # ssh-monkeypatch 独立包发布产物（wheel + sdist）
    ├── example_embed.py     # 嵌入式使用示例（含 patch_all）
    └── README.md / requirements.txt
```
