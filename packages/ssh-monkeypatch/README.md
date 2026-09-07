# ssh-monkeypatch

Monkey-patch 劫持当前 Python 进程内所有 paramiko / asyncssh 的 SSH 连接，自动登记连接状态（主机、端口、用户、连接时间、存活状态），连接对象 close 时自动移除登记。

独立可用的轻量包，无需依赖 `ssh_web_tool` 主体；可选桥接到 `ssh_web_tool` 的 Web UI 镜像会话。

## 安装

```bash
pip install ssh-monkeypatch            # 核心（paramiko / asyncssh 按需劫持）
pip install ssh-monkeypatch[paramiko]  # 附带 paramiko
pip install ssh-monkeypatch[asyncssh]  # 附带 asyncssh
pip install ssh-monkeypatch[all]       # 附带两者
pip install ssh-monkeypatch[web]       # 附带 ssh_web_tool 桥接（Web UI 镜像会话）
```

## 快速开始

```python
import ssh_monkeypatch
ssh_monkeypatch.patch_all()

# 之后现有代码发起 SSH 连接（paramiko / asyncssh 均可），自动登记：
import paramiko
c = paramiko.SSHClient()
c.connect("192.168.1.10", username="root", password="xxx")

# 查看已登记的连接：
print(ssh_monkeypatch.list_connections())
# [{'id': 'a1b2c3d4', 'host': '192.168.1.10', 'port': 22, 'username': 'root',
#   'kind': 'paramiko', 'connected_at': 1757..., 'is_alive': True}]

c.close()   # 登记自动移除
```

## 桥接到 Web UI（可选）

```python
import ssh_monkeypatch
ssh_monkeypatch.bridge_to_web(True)   # 需要 pip install ssh_web_tool
ssh_monkeypatch.patch_all()
```

开启后，被劫持的连接除了登记在本包 registry，还会同步注册为 `ssh_web_tool` 的镜像会话，出现在 Web UI 的会话列表（`/api/sessions`）中；连接关闭时同步移除。

## API

| 函数 | 说明 |
| --- | --- |
| `patch_all()` | 一次劫持 paramiko + asyncssh（已安装的生效） |
| `patch_paramiko()` | 仅劫持 `paramiko.SSHClient.connect`（含 fabric/scp/pssh 等上层库） |
| `patch_asyncssh()` | 仅劫持 `asyncssh.connect` |
| `unpatch()` | 恢复原始函数，停止劫持（已登记连接保留） |
| `list_connections()` | 列出所有已登记的连接（dict 列表） |
| `get_connection(id)` | 按 id 查询单条连接 |
| `clear_registry()` | 清空登记（不关闭真实连接） |
| `bridge_to_web(True/False)` | 开关 ssh_web_tool Web 镜像会话桥接 |

## 说明

- 仅对**同一 Python 进程内**的调用生效；通过 subprocess 调外部 `ssh` 命令（openssh 客户端）属于跨进程，无法劫持。
- 登记的是连接状态（镜像会话），不拦截命令/输出数据，不影响原连接的任何行为。
- 支持 `paramiko` / `asyncssh` 任一或同时安装：未安装的库自动跳过，不会报错。
