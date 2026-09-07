# SSH Web Tool — 网页版 SSH 终端管理工具

一个用 **Python 后端 + React/TypeScript 前端** 实现的网页版 SSH 终端管理工具，类似 Xshell，但运行在浏览器中。核心设计理念：**本地 Server 统一管理所有 SSH 连接**，前端只负责展示和交互，AI / Python 脚本可通过 HTTP API 或 SDK 复用同一批 SSH 连接，前端可实时观察所有操作过程。

## 功能亮点

- **多标签 Web 终端**：xterm.js，支持多主机多标签、主题/字体/字号调节，页面关闭连接不中断
- **后端统一维护连接**：keepalive 保活、断线自动重连、页面重开自动恢复全部活跃终端
- **主机管理**：分组、类型标签、搜索、复制、拖拽排序，快捷指令（含预操作/参数替换）与全局命令历史（Alt+R）
- **多端复用**：Web UI / HTTP REST API / Python SDK / CLI 共用同一批 SSH 连接，前端实时观察所有操作（活动日志）
- **SFTP 文件管理**：浏览、上传（含拖拽）、下载、在线查看
- **存储阵列支持**：DeviceManager 管理页自动登录（Playwright）
- **跨进程 SSH 劫持观测**：`ssh-monkeypatch` 独立 pip 包劫持 paramiko/asyncssh 连接，采集命令/输出经 WebSocket 推送，Web 面板实时展示与历史回放（详见 [跨进程劫持观测](docs/external-observation.md)）

## 快速开始

```bash
cd ssh-web-tool
uv sync            # 或 pip install -e .
uv run python main.py
```

浏览器打开 **http://127.0.0.1:8765**（端口等参数通过 `config.json` 配置，详见文档）。

## 文档导航

| 文档 | 内容 |
|------|------|
| [快速开始](docs/quickstart.md) | 环境要求、安装、启动、config.json 配置、快捷键 |
| [架构设计](docs/architecture.md) | 整体架构、关键设计点、项目结构 |
| [Python SDK 与 CLI](docs/sdk-cli.md) | SDK 用法、CLI 命令、智能 ID 识别 |
| [HTTP REST API](docs/api.md) | 全部 API 接口、WebSocket、活动日志事件 |
| [跨进程劫持观测](docs/external-observation.md) | ssh-monkeypatch 独立包：劫持 paramiko/asyncssh、事件推送、外部会话面板 |
| [前端开发指南](docs/frontend-dev.md) | React 开发模式、构建、目录结构 |
| [终端尺寸排错专题](docs/troubleshooting-terminal-size.md) | asyncssh 参数顺序坑、修复清单 |
| [已知问题](docs/troubleshooting.md) | asyncssh 版本、密码安全、保活重连等 |
| [打包发布](docs/release.md) | EXE/劫持包打包、GitHub Actions 自动发布 |

## 项目结构（概览）

```
ssh-web-tool/
├── main.py                  # FastAPI 后端入口（API + WebSocket + 静态文件）
├── ssh_client.py            # Python SDK + CLI 二合一
├── ssh_web_tool/            # 核心 Python 包（会话池/存储/外部会话中心等）
├── static/                  # 前端单文件产物 + 外部会话面板
├── packages/ssh-monkeypatch/# 独立 pip 包（劫持 SSH 连接并推送事件）
├── frontend/                # React + TypeScript 源码
├── docs/                    # 文档（本仓库详细说明）
├── .github/workflows/       # CI + Release + 独立包发布
└── dist/                    # 打包产物（EXE / wheel / release）
```

详细结构见 [架构设计](docs/architecture.md)。
