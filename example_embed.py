"""
SSH Web Tool 嵌入式使用示例

展示如何在其他 Python 项目中导入并使用这个库。
"""

# ============================================================
# 示例1：基本使用 - 创建连接、执行命令
# ============================================================
def example_basic():
    from ssh_web_tool import SSHWebTool

    # 创建管理器（不启动 Web UI）
    tool = SSHWebTool(web_ui=False)

    # 连接到远程主机
    session_id = tool.connect(
        host="192.168.1.100",
        username="root",
        password="your_password",
        port=22
    )
    print(f"已连接，会话ID: {session_id}")

    # 执行命令（注入到交互式终端，结果会显示在终端中）
    result = tool.run_command(session_id, "ls -la /tmp")
    print(f"输出:\n{result['output']}")
    print(f"退出码: {result['exit_code']}")

    # 连续执行命令（在同一个 shell 进程中）
    tool.run_command(session_id, "cd /var/log")
    result = tool.run_command(session_id, "pwd")
    print(f"当前目录: {result['output'].strip()}")  # /var/log

    # 检查终端状态
    state = tool.get_terminal_state(session_id)
    print(f"终端状态: {state}")

    # 关闭连接
    tool.close_session(session_id)
    tool.close_all()


# ============================================================
# 示例2：启动 Web UI - 在浏览器中观察和操作
# ============================================================
def example_with_web_ui():
    from ssh_web_tool import SSHWebTool
    import time

    # 创建管理器并启动 Web UI
    tool = SSHWebTool(web_ui=True, host="127.0.0.1", port=8765)

    # 连接多个主机
    s1 = tool.connect("192.168.1.100", "root", "pass1", terminal_name="Web服务器")
    s2 = tool.connect("192.168.1.101", "root", "pass2", terminal_name="数据库")

    print("打开浏览器访问 http://127.0.0.1:8765 查看终端")

    # 在代码中执行命令，Web UI 中会实时显示
    tool.run_command(s1, "uptime")
    tool.run_command(s2, "mysql --version")

    # 保持运行，让用户可以在 Web UI 中操作
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        tool.close_all()


# ============================================================
# 示例3：劫持 paramiko - 让已有代码自动注册到管理器
# ============================================================
def example_patch_paramiko():
    from ssh_web_tool import SSHWebTool, patch_paramiko, unpatch
    import paramiko

    # 创建管理器并启动 Web UI
    tool = SSHWebTool(web_ui=True)

    # 劫持 paramiko
    patch_paramiko(tool)

    # 之后所有 paramiko 连接都会自动注册到管理器
    # 并在 Web UI 中显示
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("192.168.1.100", username="root", password="pass")

    # 执行命令（使用 paramiko 原生方式）
    stdin, stdout, stderr = client.exec_command("ls -la")
    print(stdout.read().decode())

    # 同时可以在 Web UI 中看到这个连接
    sessions = tool.list_sessions()
    print(f"当前活跃会话数: {len(sessions)}")

    # 恢复
    unpatch()
    client.close()
    tool.close_all()


# ============================================================
# 示例4：使用已保存的主机配置
# ============================================================
def example_saved_hosts():
    from ssh_web_tool import SSHWebTool

    tool = SSHWebTool(web_ui=True)

    # 保存主机配置（持久化到 data.json）
    tool.save_host(
        name="生产服务器",
        host="192.168.1.100",
        username="root",
        password="pass",
        group="生产环境",
        device_type="linux"
    )

    # 列出所有已保存主机
    hosts = tool.list_hosts()
    for h in hosts:
        print(f"{h['name']} - {h['host']}:{h['port']}")

    # 使用已保存的主机配置连接
    if hosts:
        session_id = tool.connect_saved_host(hosts[0]["id"], terminal_name="主终端")
        result = tool.run_command(session_id, "whoami")
        print(result["output"])

    tool.close_all()


# ============================================================
# 示例5：快速连接（便捷函数）
# ============================================================
def example_quick_connect():
    from ssh_web_tool import quick_connect

    # 一行代码连接并启动 Web UI
    tool, session_id = quick_connect(
        host="192.168.1.100",
        username="root",
        password="pass",
        web_ui=True
    )

    result = tool.run_command(session_id, "uname -a")
    print(result["output"])

    tool.close_all()


# ============================================================
# 示例6：与自动化脚本集成 - 连续执行命令
# ============================================================
def example_automation():
    from ssh_web_tool import SSHWebTool

    tool = SSHWebTool(web_ui=True)
    session_id = tool.connect("192.168.1.100", "root", "pass")

    # 自动化部署脚本 - 连续执行命令
    commands = [
        "cd /opt",
        "mkdir -p myapp",
        "cd myapp",
        "git clone https://github.com/example/myapp.git .",
        "pip install -r requirements.txt",
        "systemctl restart myapp",
        "systemctl status myapp",
    ]

    for cmd in commands:
        print(f"\n>>> {cmd}")
        result = tool.run_command(session_id, cmd, timeout=60)
        print(result["output"])
        if result["exit_code"] != 0:
            print(f"命令失败，退出码: {result['exit_code']}")
            break

    tool.close_all()


if __name__ == "__main__":
    print("SSH Web Tool 嵌入式使用示例")
    print("请根据需要取消注释对应示例函数")
    # example_basic()
    # example_with_web_ui()
    # example_patch_paramiko()
    # example_saved_hosts()
    # example_quick_connect()
    # example_automation()
