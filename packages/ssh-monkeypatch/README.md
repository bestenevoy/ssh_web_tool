# ssh-monkeypatch

Monkey-patch 劫持当前 Python 进程内所有 paramiko / asyncssh 的 SSH 连接，自动登记连接状态，并**采集 SSH 事件**（连接 / 命令 / stdout/stderr 输出 / 关闭）。

核心场景：**劫持观测自动化测试的 SSH 会话（跨进程）**——测试框架与可视化服务完全分离，测试侧只 patch 采集 + WebSocket 推送，不修改任何原有测试业务逻辑。

## 架构

```
┌─ 测试进程（py3.8+，独立 Python 环境）─────────────┐
│  import ssh_monkeypatch                           │
│  ssh_monkeypatch.patch_all()                      │
│  ssh_monkeypatch.start_streamer("ws://host:8765/ws/stream")  │
│                                                   │
│  原有测试代码照常使用 paramiko / asyncssh：        │
│  connect / exec_command / invoke_shell / close    │
│  → 自动采集 connect/command/output/close 事件      │
│  → WebSocket 推送（websocket-client 轻量依赖）     │
└────────────────────┬──────────────────────────────┘
                     │ WebSocket（跨进程）
┌────────────────────▼──────────────────────────────┐
│ ssh_web_tool 可视化服务                            │
│  /ws/stream      接收事件，按 session_id 区分      │
│  /ws/external/{session_id}  转发给浏览器           │
│  → Web 终端页面实时展示 + 历史回放                 │
└───────────────────────────────────────────────────┘
```

## 安装

```bash
pip install ssh-monkeypatch            # 核心
pip install ssh-monkeypatch[stream]    # + websocket-client（跨进程推送必需）
pip install ssh-monkeypatch[paramiko]  # + paramiko
pip install ssh-monkeypatch[asyncssh]  # + asyncssh
pip install ssh-monkeypatch[all]       # + 两者
pip install ssh-monkeypatch[web]       # + ssh_web_tool 桥接（同进程镜像会话）
```

兼容 Python 3.8+。

## 快速开始（测试侧）

```python
import ssh_monkeypatch

# 1. 劫持当前进程所有 SSH 连接入口
ssh_monkeypatch.patch_all()

# 2. 开启 WebSocket 推送（可选；不推送到包内缓冲也可查）
ssh_monkeypatch.start_streamer("ws://127.0.0.1:8765/ws/stream")

# 3. 原有测试业务代码照常执行，不修改任何逻辑：
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.1.10", username="root", password="xxx")

stdin, stdout, stderr = c.exec_command("uname -a")
print(stdout.read())        # 读到的输出同时被采集并推送

c.close()                   # 登记移除 + close 事件
```

## 事件协议（测试侧 → 可视化服务）

每条事件为 JSON，`type` 区分事件种类，`session_id` 区分 SSH 会话：

```json
{"type": "connect",      "session_id": "a1b2c3d4", "host": "192.168.1.10", "port": 22, "username": "root", "kind": "paramiko", "ts": 1757...}
{"type": "command",      "session_id": "a1b2c3d4", "command": "uname -a", "ts": 1757...}
{"type": "output",       "session_id": "a1b2c3d4", "stream": "stdout", "data": "Linux ...", "ts": 1757...}
{"type": "stream_eof",   "session_id": "a1b2c3d4", "stream": "stdout", "ts": 1757...}
{"type": "close",        "session_id": "a1b2c3d4", "ts": 1757...}
```

## API

| 函数 | 说明 |
| --- | --- |
| `patch_all()` | 一次劫持 paramiko + asyncssh（已安装的生效） |
| `patch_paramiko()` | 劫持 `SSHClient.connect / exec_command / invoke_shell`（含 fabric/scp/pssh 等上层库） |
| `patch_asyncssh()` | 劫持 `asyncssh.connect` |
| `unpatch()` | 恢复原始函数 |
| `start_streamer(ws_url)` | 启动 WebSocket 推送（需 websocket-client），断线自动重连 |
| `stop_streamer()` | 停止推送 |
| `set_capture_unread(True/False)` | 测试代码不读取输出时是否后台自动采集（默认 False：读哪儿采哪儿，不抢数据） |
| `get_events(session_id=None, limit=500)` | 查询采集的事件 |
| `list_events(session_id, limit=500)` | 按会话列出事件（alias） |
| `list_connections()` / `get_connection(id)` / `clear_registry()` | 连接登记查询/清空 |
| `bridge_to_web(True/False)` | 同进程场景：登记同步到 ssh_web_tool 镜像会话 |

## 说明

- 仅对**同一 Python 进程内**的调用生效；subprocess 调外部 `ssh` 命令无法劫持。
- 输出采集默认"读哪儿采哪儿"：只包装流对象的读方法，测试代码读到哪儿采集到哪儿，不与业务逻辑抢数据。若测试代码执行命令后从不读取输出，可 `set_capture_unread(True)` 启用后台采集（会消费输出流）。
- 采集与推送均为旁路：不拦截、不修改命令/输出数据，不影响原连接行为。
