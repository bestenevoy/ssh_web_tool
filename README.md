# SSH Web Tool — 网页版 SSH 终端管理工具

## 一、项目概述

一个用 **Python 后端 + React/TypeScript 前端** 实现的网页版 SSH 终端管理工具，类似 Xshell，但运行在浏览器中。核心设计理念：**本地 Server 统一管理所有 SSH 连接**，前端只负责展示和交互，AI / Python 脚本可通过 HTTP API 或 SDK 复用同一批 SSH 连接，前端可实时观察所有操作过程。

## 二、整体需求

### 核心需求
1. **Python 写后端**，拒绝 Node/其他后端语言
2. **网页 UI 操作**，浏览器访问本地 Server
3. **本地 Server 统一管理所有 SSH 连接**，连接状态由后端维护
4. **AI / Python 可调用**：通过 HTTP REST API、Python SDK、CLI 命令复用 SSH 连接
5. **前端作为观察面板**：实时看到其他程序（CLI/SDK/API）和 Server 交互的过程

### 功能需求
- 多标签终端：一个主机可开多个终端，tab 切换，tab 显示环境类型标签（sh/py/sql/pg）
- 主机列表显示连接状态（绿色圆点 + 终端数徽章），终端计数使用真实连接状态检测
- 前端关闭页面后 SSH 连接不中断，由后端统一维护
- 页面重新打开时自动恢复所有活跃终端（包括 CLI/SDK 创建的终端）
- 前端定期同步后端活跃终端列表，新创建的终端自动显示
- SSH 连接 keepalive：每 30 秒发送保活包，防止空闲超时断开
- 自动重连：连接意外断开后自动重连并恢复 shell，输入失败时自动重连并重发
- 主机持久化保存（JSON 文件）
- 主机分组管理（一级目录，可添加/重命名/删除）
- 主机类型标签（prod/test/dev/web/db/other，带颜色）
- 设备类型区分：普通主机 / 存储阵列（存储阵列支持 DeviceManager 管理页面自动登录）
- 快速复制连接信息（ssh 命令 + 密码）
- 快速复制主机（从现有主机复制，修改 IP 即可）
- 主机搜索（按名称/IP/分组搜索）
- 快捷指令：点击 item 立即执行、点击 ▶ 编辑后执行、点击 ✎ 编辑弹窗、点击 ✕ 删除，按钮只保留 icon
- 快捷指令支持描述字段，添加改为弹窗形式
- 全局命令历史：跨终端跨主机记录，按使用频次排序，Alt+R 弹窗搜索，最多显示20条
- 统一搜索：同时搜索快捷指令和历史命令，快捷指令可直接执行/历史命令可编辑后执行
- SFTP 文件管理：浏览、下载、上传、拖拽上传、查看文件内容
- 活动日志：实时记录所有来源（WEB/API/CLI/SDK）的操作事件
- 终端日志持久化：logs/<session_id>.log，页面加载时自动加载最近 3000 字符
- 主题切换：暗色/亮色主题
- 终端字体和字号调节：8 种等宽字体，8-32px 字号
- 页面底部预留空间，防止被任务栏遮挡
- 完整 HTTP REST API + Swagger 文档

### 技术约束
- 前端用 **React + TypeScript** 编写
- 前端编译为**单个 HTML 文件**，内联所有 JS/CSS
- 后端用 **FastAPI + asyncssh**
- 本地运行，不依赖外部服务

## 三、架构设计

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

### 关键设计点
1. **连接状态后端统一维护**：WebSocket 断开不关闭 SSH 会话，页面重开可恢复
2. **事件总线**：所有关键操作发布事件，通过 `/ws/events` 广播，前端活动日志实时显示
3. **SDK/CLI 复用会话**：`exec_host` 方法自动查找该主机已有会话并复用，不重复建连
4. **单文件前端**：React 构建产物内联为单个 `index.html`，后端零改动直接提供
5. **注入+捕获模式**：CLI 执行命令时注入到已有终端，输出同步显示在 Web 终端，CLI 也能拿到输出

## 四、快速开始

### 环境要求
- Python 3.10+（开发用 3.13）
- Node.js 18+（仅前端开发需要，运行时不需要）
- asyncssh >= 2.20.0（2.17.0 有 SFTP bug）
- 推荐使用 UV 管理 Python 虚拟环境

### 安装依赖（UV 方式，推荐）
```bash
cd ssh-web-tool
uv sync
```

### 安装依赖（pip 方式）
```bash
cd ssh-web-tool
pip install -e .
```

### 启动服务
```bash
# UV 方式
uv run python main.py

# 或直接运行
python main.py
```
浏览器打开 **http://127.0.0.1:8765**

### 配置文件（config.json）
服务监听端口等启动参数通过 `config.json` 配置（首次启动自动生成，放在 EXE/脚本同级目录）：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8765,
    "auto_find_free_port": true
  },
  "open_browser": true
}
```

| 字段 | 说明 |
|------|------|
| `server.host` | 服务监听地址 |
| `server.port` | 服务监听端口 |
| `server.auto_find_free_port` | `true`（默认）：端口被占用时自动向上寻找空闲端口并切换；`false`：被占用时直接报错退出 |
| `open_browser` | 启动后是否自动打开浏览器 |

- 修改配置后**重启程序生效**；启动时会打印实际使用的端口。
- 前端页面、API 均使用相对路径，端口变化后浏览器地址自动跟随，无需改前端。
- `config.json` 为本地配置（不入库），模板见 `config.example.json`。

### 添加主机
点击右上角「+ 新建主机」，填写：
- 别名（显示名称，如"阿里云生产"）
- 主机 IP、端口
- 用户名、密码
- 类型（prod/test/dev/web/db/other）
- 分组（如"生产环境"，可自定义）
- 设备类型（普通主机 / 存储阵列）

点击主机即可建立 SSH 连接。

### 作为 Python 包使用
```bash
# clone 后安装到当前 Python 环境
pip install -e .

# 然后在任何 Python 脚本中导入使用
from ssh_client import SSHClient
client = SSHClient("http://127.0.0.1:8765")
```

## 五、快捷键

| 快捷键 | 功能 |
|--------|------|
| `Alt+R` | 打开全局命令历史搜索弹窗（跨终端跨主机，按频次排序） |
| `↑/↓` | 在搜索弹窗中选择历史命令 |
| `Enter` | 选中的命令输入到终端（可编辑后再执行） |
| `Ctrl+R` | 终端内反向搜索（bash 原生） |

> 注意：Alt+空格在 Windows 上被系统拦截（打开窗口菜单），所以改用 Alt+R。

## 六、前端开发指南

### 项目位置
```
ssh-web-tool/frontend/
```

### 开发模式（热更新，推荐）
```bash
# 终端1：启动后端
cd ssh-web-tool
python main.py

# 终端2：启动前端开发服务器
cd ssh-web-tool/frontend
npm install   # 首次需要
npm run dev
```
浏览器打开 **http://localhost:5173**

- 修改 React/TS 代码保存后页面自动刷新
- `/api` 和 `/ws` 请求自动代理到后端 8765
- 不需要编译

### 生产构建（单文件）
```bash
cd ssh-web-tool/frontend
npm run build
```
输出到 `../static/index.html`（单个文件，约 530KB，gzip 后约 141KB）

构建配置了 `vite-plugin-singlefile`，所有 JS/CSS 内联到 HTML 中。构建后访问 **http://127.0.0.1:8765** 即可看到最新版本。

### 前端技术栈
- React 19 + TypeScript
- Vite 构建工具
- xterm.js + @xterm/addon-fit 终端组件
- 无 UI 组件库，纯 CSS 样式（暗色/亮色主题）

### 前端目录结构
```
frontend/src/
├── components/              # React 组件
│   ├── HostList.tsx         # 主机列表（分组、状态、操作）
│   ├── TerminalTabs.tsx     # 终端标签栏
│   ├── TerminalView.tsx     # 终端视图（xterm 实例管理）
│   ├── QuickCommands.tsx    # 快捷指令面板
│   ├── QuickCommandModal.tsx# 快捷指令添加/编辑弹窗
│   ├── HistorySearchModal.tsx# 历史命令搜索弹窗（Alt+R）
│   ├── SftpPanel.tsx        # SFTP 文件管理
│   ├── EventLog.tsx         # 活动日志
│   ├── SettingsBar.tsx      # 顶部设置栏（主题/字体/字号）
│   └── HostModal.tsx        # 主机新增/编辑弹窗
├── lib/
│   ├── api.ts               # HTTP API 调用封装
│   ├── useTerminals.ts      # 终端状态管理 hook（WebSocket/xterm）
│   ├── useEvents.ts         # 事件订阅 hook
│   └── useSettings.ts       # 设置管理 hook（主题/字体/字号）
├── types/index.ts           # TypeScript 类型定义
├── App.tsx                  # 主应用
├── App.css                  # 历史搜索弹窗样式
├── main.tsx                 # 入口
└── index.css                # 全局样式（CSS 变量主题）
```

## 七、Python SDK 使用

`ssh_client.py` 同时是 SDK 和 CLI。作为 SDK 导入时，所有操作通过 HTTP API 与后端通信，自动复用已有 SSH 会话。

```python
from ssh_client import SSHClient

client = SSHClient("http://127.0.0.1:8765")

# 1. 列出所有主机（含连接状态）
hosts = client.list_hosts()
for h in hosts:
    print(f"{h['name']} {h['host']} 在线={h.get('is_connected', False)}")

# 2. 最常用：在指定主机执行命令（自动复用已有会话，没有则新建）
# 输出会同步显示在 Web 终端中，CLI 也能拿到输出
result = client.exec_host("117d5a13", "df -h && free -m && uptime")
print(result['stdout'])
print("返回码:", result['returncode'])

# 3. 直接用 IP 执行命令（自动匹配该主机第1个活跃终端）
result = client.exec_host("192.168.1.100", "uptime")
result = client.exec_host("192.168.1.100:22", "df -h")

# 4. 连续执行命令（状态保持，cd 等生效）
client.exec_host("117d5a13", "cd /tmp")
client.exec_host("117d5a13", "pwd")  # 输出 /tmp

# 5. 管理会话
sessions = client.list_sessions()
client.close_session("session_id")

# 6. SFTP
client.sftp_list("session_id", "/var/log")
client.sftp_download("session_id", "/etc/hosts", "./local_hosts")
client.sftp_upload("session_id", "./local_file.txt", "/tmp/remote_file.txt")
client.sftp_delete("session_id", "/tmp/remote_file.txt")

# 7. 主机管理
client.add_host(name="测试机", host="192.168.1.100", port=22,
                username="root", password="xxx", type="test", group="测试环境")
client.update_host("host_id", {"name": "新名称"})
client.delete_host("host_id")
```

### SDK 核心方法

| 方法 | 说明 |
|------|------|
| `list_hosts()` | 列出所有主机（含连接状态、终端数） |
| `add_host(...)` | 新增主机 |
| `update_host(host_id, data)` | 更新主机 |
| `delete_host(host_id)` | 删除主机 |
| `list_sessions()` | 列出所有活跃会话 |
| `list_active_terminals()` | 获取所有活跃终端（含主机信息） |
| `connect(host, port, username, password)` | 手动创建 SSH 会话 |
| `connect_host(host_id, terminal_name)` | 从保存的主机创建会话 |
| `exec_host(host_id, command)` | ⭐ 在指定主机执行命令（自动复用会话，输出同步到 Web） |
| `run(session_id, command)` | 在指定会话执行命令 |
| `close_session(session_id)` | 关闭会话 |
| `sftp_list(session_id, path)` | SFTP 列目录 |
| `sftp_download(session_id, remote, local)` | SFTP 下载文件 |
| `sftp_upload(session_id, local, remote)` | SFTP 上传文件 |
| `sftp_delete(session_id, path)` | SFTP 删除文件 |

## 八、CLI 命令行使用

```bash
cd ssh-web-tool

# 查看帮助
python ssh_client.py --help

# 列出所有主机（含连接状态和终端数）
python ssh_client.py hosts

# 列出所有活跃终端
python ssh_client.py terminals

# ⭐ 在指定主机执行命令（自动复用已有会话，输出同步显示在 Web 终端）
python ssh_client.py exec 117d5a13 "uptime && df -h"

# ⭐ 直接用 IP 执行命令（自动匹配该主机第1个活跃终端，没有则新建）
python ssh_client.py exec 192.168.1.100 "uptime"
python ssh_client.py exec 192.168.1.100:22 "df -h"

# 从保存的主机创建新会话（指定终端名称）
python ssh_client.py connect 117d5a13 --name "部署终端"

# 在指定会话执行命令
python ssh_client.py run <session_id> "ls -la"

# SFTP 列目录
python ssh_client.py ls <session_id> /var/log

# SFTP 下载
python ssh_client.py download <session_id> /etc/hosts ./hosts

# SFTP 上传
python ssh_client.py upload <session_id> ./file.txt /tmp/file.txt

# 关闭会话
python ssh_client.py close <session_id>
```

### exec 命令的智能 ID 识别
`exec` 命令会按优先级自动识别传入的 ID：
1. **session_id** — 如果匹配到活跃终端的会话 ID，直接使用该终端
2. **host_id** — 如果匹配到保存主机的 ID，复用该主机的第1个活跃终端
3. **IP 地址** — 如果是 `ip` 或 `ip:port` 格式，按 IP 查找主机，使用第1个活跃终端
4. **都不匹配** — 按 host_id 处理，没有则新建连接

### CLI 自动化示例
```bash
# 批量在多台主机执行命令（用 IP 更方便）
for ip in 192.168.1.100 192.168.1.101 192.168.1.102; do
  echo "=== $ip ==="
  python ssh_client.py exec $ip "uptime"
done

# 定时收集服务器状态
python ssh_client.py exec 192.168.1.100 "free -m && df -h && uptime" > server_status_$(date +%Y%m%d).txt
```

## 九、HTTP REST API

API 文档（Swagger UI）：**http://127.0.0.1:8765/docs**

### 会话管理
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建 SSH 会话（手动输入连接信息） |
| POST | `/api/sessions/from-host` | 从保存的主机创建 SSH 会话 |
| GET | `/api/sessions` | 列出所有活动会话 |
| GET | `/api/sessions/active` | 获取所有活跃终端（用于页面恢复） |
| GET | `/api/sessions/{id}/state` | 获取终端状态（shell 类型检测） |
| GET | `/api/sessions/{id}/logs` | 获取终端历史日志（分页） |
| DELETE | `/api/sessions/{id}` | 关闭并删除会话 |
| POST | `/api/sessions/{id}/run` | 在会话中执行命令（AI 调用） |

### 主机管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/hosts` | 获取所有保存的主机（含连接状态） |
| POST | `/api/hosts` | 新增主机 |
| PUT | `/api/hosts/{id}` | 更新主机 |
| DELETE | `/api/hosts/{id}` | 删除主机 |

### 快捷指令
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/quick-commands` | 获取快捷指令列表 |
| POST | `/api/quick-commands` | 新增快捷指令 |
| PUT | `/api/quick-commands/{id}` | 更新快捷指令 |
| DELETE | `/api/quick-commands/{id}` | 删除快捷指令 |

### 全局命令历史
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/history/record` | 记录命令执行（自动调用） |
| GET | `/api/history/search` | 搜索历史命令（按频次排序） |
| GET | `/api/history/recent` | 获取最近使用的命令 |

### 统一搜索
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/search?q=xxx` | 同时搜索快捷指令和历史命令 |

### 终端持久化
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/terminals/saved` | 获取已保存的终端列表 |
| POST | `/api/terminals/{id}/restore` | 恢复已保存的终端 |

### SFTP
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sftp/{id}/list` | SFTP 列出目录 |
| GET | `/api/sftp/{id}/download` | SFTP 下载文件 |
| POST | `/api/sftp/{id}/write` | SFTP 上传/写入文件 |
| POST | `/api/sftp/{id}/delete` | SFTP 删除文件 |

### WebSocket
| 路径 | 说明 |
|------|------|
| `/ws/ssh/{session_id}` | 交互式终端（输入/输出/调整大小） |
| `/ws/events` | 事件订阅（实时观察所有操作） |

### API 调用示例（curl）
```bash
# 执行命令
curl -X POST http://127.0.0.1:8765/api/sessions/from-host \
  -H "Content-Type: application/json" \
  -d '{"host_id":"117d5a13"}'

# 列出主机
curl http://127.0.0.1:8765/api/hosts
```

## 十、活动日志（事件总线）

所有关键操作都会发布事件，通过 `/ws/events` WebSocket 广播。前端「活动日志」面板实时显示，可用于观察 CLI/SDK/API 与 Server 的交互过程。

### 事件类型
| 事件类型 | 颜色 | 说明 |
|----------|------|------|
| `session_create` | 绿色 | 创建 SSH 会话 |
| `session_close` | 红色 | 关闭 SSH 会话 |
| `command_run` | 蓝色 | 执行命令 |
| `sftp_list` | 紫色 | SFTP 列目录 |
| `sftp_download` | 橙色 | SFTP 下载 |
| `sftp_upload` | 青色 | SFTP 上传 |
| `host_add` | 浅绿 | 新增主机 |
| `host_update` | 黄色 | 更新主机 |
| `host_delete` | 粉红 | 删除主机 |

### 事件来源
- `WEB` — 前端网页操作
- `API` — HTTP API 调用
- `CLI` — 命令行工具
- `SDK` — Python SDK

### 观察自动化过程
1. 打开网页，切换到「📊 活动日志」面板
2. 在另一个终端运行 CLI 命令，如 `python ssh_client.py exec 117d5a13 "echo test"`
3. 日志面板会实时显示 `session_create`（如果是新会话）和 `command_run` 事件
4. 事件包含时间、来源、详细信息

## 十一、终端尺寸排错专题（重要！）

### 症状
输入几个字符后，光标突然跳回行首并覆盖原来的内容；换行时甚至覆盖提示符行。粘贴长文本或快捷指令执行却完全正常。

### 根本原因
**asyncssh 的 `change_terminal_size` 参数顺序和文档不一致！**

| API | 文档写的 | 实际行为 |
|-----|---------|---------|
| `create_process(term_size=...)` | `(cols, rows)` | `(cols, rows)` ✓ |
| `change_terminal_size(...)` | `(rows, cols)` | `(cols, rows)` ✗ |

如果按照文档写 `change_terminal_size(rows=17, cols=96)`，实际会被解释成 **17 列、96 行**，导致 `$COLUMNS=17`，输入超过 17 列就回行首覆盖。

### 验证方法
在终端中执行：
```bash
echo $COLUMNS    # 应该是 96 左右，如果是 17 就说明参数顺序反了
stty size         # 输出格式是 "rows cols"，应该是 "17 96"，如果是 "96 17" 就反了
```

### 正确写法（sessions.py）
```python
# 创建 PTY：term_size=(cols, rows)
self.process = await self.conn.create_process(
    term_type="xterm-256color",
    term_size=(cols, rows),   # ✓ 正确
)

# 调整尺寸：change_terminal_size(cols, rows) —— 注意不是 (rows, cols)！
self.process.change_terminal_size(cols, rows)   # ✓ 正确（和文档相反）
```

### 其他可能导致输入错乱的原因
1. **xterm.js 自动换行被禁用**：`term.reset()` 可能重置 DECSET 模式，需要发送 `\x1b[?7h` 重新启用
2. **PTY 创建时用默认尺寸**：WebSocket 连接时先等待前端的 resize 消息，再启动 shell
3. **前端容器 display:none 时 fit 计算错误**：终端激活后重新 fit + resync

### 修复检查清单
- [ ] `create_process(term_size=(cols, rows))` 参数顺序正确
- [ ] `change_terminal_size(cols, rows)` 参数顺序正确（和文档相反！）
- [ ] WebSocket 连接时先等待 resize 消息再启动 shell
- [ ] `term.reset()` 后发送 `\x1b[?7h` 启用自动换行
- [ ] 终端激活后重新 `fitAddon.fit()` + `resyncTerminal`
- [ ] `echo $COLUMNS` 输出正常值（90+）
- [ ] `stty size` 输出 "rows cols"（行数在前，列数在后）

## 十二、项目结构

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
├── ssh_web_tool/            # 核心 Python 包
│   ├── __init__.py          # 包入口（导出 SSHWebTool / patch_all 等）
│   ├── sessions.py          # SSH 会话池（统一管理所有 SSH 连接）
│   ├── storage.py           # JSON 持久化存储（主机/分组/类型/快捷指令/命令历史）
│   ├── ssh_tool.py          # 可嵌入的 Python 库入口（SSHWebTool / patch_paramiko / patch_asyncssh / patch_all）
│   └── playwright_mgmt.py   # 存储阵列管理页面自动登录（基于 Playwright）
├── static/
│   └── index.html           # React 构建产物（单文件，约 560KB）
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
│       ├── ci.yml           # CI workflow（多平台多 Python 版本测试）
│       └── release.yml      # Release workflow（推送 tag 自动构建 EXE 并发布）
└── dist/
    ├── SSHWebTool.exe       # 打包生成的 EXE 文件（双击运行）
    ├── wheel/               # 劫持包（ssh_web_tool-*.whl，pip install 后 patch_all() 劫持 SSH 连接）
    ├── example_embed.py     # 嵌入式使用示例（含 patch_all）
    └── README.md / requirements.txt
```

### 打包（同时产出 EXE 和劫持包）

双击 `build_exe.bat`（或执行 `powershell -ExecutionPolicy Bypass -File build.ps1`），自动完成：

1. **构建前端**：`npm run build`（产出单文件 `static/index.html`）
2. **打包独立 EXE**：PyInstaller 将后端 + 前端 + 依赖打包为 `dist/SSHWebTool.exe`，双击即用
3. **打包劫持包**：`pip wheel` 产出 `dist/wheel/ssh_web_tool-*.whl`，`pip install` 后调用 `patch_all()` 可劫持当前进程所有 paramiko / asyncssh 连接（含 fabric/scp 等基于 paramiko 的库）

产物结构见上方 `dist/` 目录。

## 十三、已知问题和注意事项

### asyncssh 版本
- 必须使用 **asyncssh >= 2.20.0**
- 2.17.0 有多个 bug：SFTPAttrs 无法设置 size、SFTPClientFile.write() 不接受 bytes、conn.run() 返回值缺少字段
- 2.24.0 已验证全部功能正常

### asyncssh 参数顺序坑（重要！）
- `create_process(term_size=(cols, rows))` —— 参数顺序是 (cols, rows)
- `change_terminal_size(cols, rows)` —— **参数顺序是 (cols, rows)，和文档写的 (rows, cols) 相反！**
- 详见「终端尺寸排错专题」

### Windows 环境
- pip 命令可能不可用，用 `python -m pip` 代替
- 中文显示问题：确保 data.json 用 UTF-8 编码，PowerShell 输出可能乱码但不影响功能

### 终端关闭行为
- 点击终端标签的 ✕ 时，会弹出确认框：
  - **确定**：同时关闭后端 SSH 连接
  - **取消**：仅关闭前端标签，后端连接保持，页面重开时自动恢复
- 设计目的：避免误操作断开正在运行的任务（如编译、部署）

### 连接超时
- 后端会话 24 小时无活动自动清理
- 可在 `sessions.py` 中修改 `SESSION_TIMEOUT` 常量

### 密码安全
- 主机密码明文存储在 `data.json` 中，仅适合本地使用
- 生产环境建议改用密钥认证或加密存储

### 前端单文件构建
- `vite-plugin-singlefile` 将所有 JS/CSS 内联到 HTML
- 构建产物约 530KB（xterm.js 占大部分），gzip 后约 141KB
- 如果需要拆分为多个文件，移除 `viteSingleFile()` 插件即可

### CLI 执行命令的输出同步
- CLI 执行命令时使用「注入+捕获」模式：命令注入到已有终端，输出同步显示在 Web 终端
- CLI 也能拿到输出和返回码
- 使用独立的输出监听器捕获新输出，不受历史输出和缓冲区清理影响
- 支持多提示符检测（shell/python/mysql/sqlite/redis/node 等），不会干扰交互程序
- 等待时间约 0.28 秒（多提示符检测 + 空闲超时兜底）

### SSH 连接保活与自动重连
- 连接时设置 `keepalive_interval=30`，每 30 秒发送保活包，防止空闲超时断开
- `keepalive_count_max=3`，3 次无响应则认为连接断开
- 自动重连机制：连接意外断开后自动重连并恢复 shell，最多重连 5 次
- WebSocket 连接时自动检测连接状态，如果断开自动重连
- 输入时如果检测到 shell 已死，自动重连并重发输入
- 后台每 15 秒检测一次连接状态，发现断开自动重连
- 重连成功/失败都会在终端中显示提示

### 前端自动同步活跃终端
- 前端每 5 秒同步一次后端活跃终端列表
- CLI/SDK/Python 包创建的新终端会自动显示在 Web 页面的终端标签中
- 已连接的终端不会重复连接，只连接新创建的终端
- 主机列表每 5 秒自动刷新，终端计数及时更新
- 终端计数使用 `is_alive()` 检测真实连接状态，而非 `is_connected` 标志位

## 十四、常用命令速查

```bash
# 启动服务
cd ssh-web-tool && uv run python main.py

# 前端开发（热更新）
cd ssh-web-tool/frontend && npm run dev

# 前端构建（单文件）
cd ssh-web-tool/frontend && npm run build

# CLI 执行命令（输出同步到 Web 终端）
uv run ./ssh_client.py exec <host_id或IP> "command"

# CLI 列出主机
uv run ./ssh_client.py hosts

# CLI 列出活跃终端
uv run ./ssh_client.py terminals

# 打包成 EXE
build_exe.bat

# 查看 API 文档
# 浏览器打开 http://127.0.0.1:8765/docs
```
