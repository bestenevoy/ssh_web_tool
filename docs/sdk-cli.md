# Python SDK 与 CLI 使用

## Python SDK

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

## CLI 命令行

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
