# 跨进程 SSH 会话劫持观测

用于**观测自动化测试的 SSH 会话（跨进程）**：不修改原有测试业务逻辑，通过 monkey-patch 劫持 paramiko，捕获自动化执行的 SSH 连接、命令、stdout/stderr 输出、会话事件，并在 Web UI 实时展示与历史回放。

## 架构

测试框架与可视化服务**完全分离**：两个进程、两套 Python 环境。

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

## 测试侧（独立 pip 包）

`packages/ssh-monkeypatch/`，独立可安装（`pip install ssh-monkeypatch`），兼容 Python 3.8+，完整文档见该目录的 `README.md`。

```python
import ssh_monkeypatch

# 1. 劫持当前进程所有 SSH 连接入口
ssh_monkeypatch.patch_all()

# 2. 开启 WebSocket 推送（跨进程到可视化服务）
ssh_monkeypatch.start_streamer("ws://127.0.0.1:8765/ws/stream")

# 3. 原有测试业务代码照常执行，不修改任何逻辑：
import paramiko
c = paramiko.SSHClient()
c.connect("192.168.1.10", username="root", password="xxx")

stdin, stdout, stderr = c.exec_command("uname -a")
print(stdout.read())        # 读到的输出同时被采集并推送

c.close()                   # 登记移除 + close 事件
```

特点：

- **读哪儿采哪儿**：只包装流对象的读方法，测试代码读到哪儿采集到哪儿，不与业务逻辑抢数据；`set_capture_unread(True)` 可启用后台兜底采集
- **只加一个轻量依赖**：`websocket-client`（`pip install ssh-monkeypatch[stream]`）
- 断线自动重连；采集与推送均为旁路，不影响原连接行为
- 支持 paramiko 的 `connect` / `exec_command` / `invoke_shell`（含 fabric/scp/pssh 等上层库）与 asyncssh 的连接级事件

## 事件协议（测试侧 → 可视化服务）

每条事件为 JSON，`type` 区分事件种类，`session_id` 区分 SSH 会话：

```json
{"type": "connect",      "session_id": "a1b2c3d4", "host": "192.168.1.10", "port": 22, "username": "root", "kind": "paramiko", "ts": 1757...}
{"type": "command",      "session_id": "a1b2c3d4", "command": "uname -a", "ts": 1757...}
{"type": "output",       "session_id": "a1b2c3d4", "stream": "stdout", "data": "Linux ...", "ts": 1757...}
{"type": "stream_eof",   "session_id": "a1b2c3d4", "stream": "stdout", "ts": 1757...}
{"type": "close",        "session_id": "a1b2c3d4", "ts": 1757...}
```

## 可视化服务侧

| 接口 | 说明 |
|------|------|
| `GET /ws/stream` | 测试进程事件入口，按 `session_id` 区分会话 |
| `GET /ws/external/{session_id}` | 浏览器订阅：先回放历史，再实时转发 |
| `GET /api/external-sessions` | 会话列表（主机/用户/命令数/输出字节/状态） |
| `GET /api/external-sessions/{id}` | 会话详情 + 历史事件 |

前端工具面板新增「🔭 外部会话」tab（`static/external-panel.js`，零依赖原生 JS）：

- 左侧：会话列表（连接中/已关闭标记、命令数、连接时间，自动轮询刷新）
- 右侧：事件流展示（连接/命令/输出/EOF/关闭，时间戳 + 状态颜色），支持历史回放与实时追加

## 发布

推送 `ssh-monkeypatch-v*` 标签到 GitHub 后，`.github/workflows/ssh-monkeypatch-release.yml` 自动构建 wheel/sdist 并发布到 GitHub Release（无需手动登录）：

```bash
git tag ssh-monkeypatch-v0.2.0
git push origin master --tags
```

产物可直接 pip 安装：

```bash
pip install https://github.com/bestenevoy/ssh_web_tool/releases/download/ssh-monkeypatch-v0.2.0/ssh_monkeypatch-0.2.0-py3-none-any.whl
```
