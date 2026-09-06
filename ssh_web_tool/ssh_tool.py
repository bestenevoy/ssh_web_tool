"""
SSH Web Tool - 可嵌入 Python 库

两种使用模式：
1. 嵌入式模式：直接 import，在同一进程内管理 SSH 连接，可选启动 Web UI
2. 客户端模式：通过 HTTP API 连接到独立运行的 server（见 ssh_client.py）

示例：
    # 嵌入式模式
    from ssh_tool import SSHWebTool
    tool = SSHWebTool(web_ui=True)  # 启动 Web UI
    session = tool.connect("192.168.1.1", "root", "password")
    result = tool.run_command(session, "ls -la")
    print(result)

    # 劫持 paramiko（让已有代码自动注册到管理器）
    # 前提：pip install paramiko
    from ssh_tool import patch_paramiko
    patch_paramiko()
    # 之后所有 paramiko.SSHClient().connect() 都会注册为镜像会话（出现在会话列表，
    # 无交互 shell；client.close() 时自动移除）
"""

import asyncio
import threading
import time
from typing import Optional, Dict, List, Any

from .sessions import session_manager, SSHSession
from .storage import storage


class SSHWebTool:
    """
    SSH Web Tool 嵌入式管理器

    在其他 Python 项目中直接使用，管理 SSH 连接，可选启动 Web UI 观察。
    所有连接由本实例统一维护，关闭 Python 进程前连接保持活跃。
    """

    def __init__(self, web_ui: bool = False, host: Optional[str] = None, port: Optional[int] = None):
        """
        初始化管理器

        Args:
            web_ui: 是否启动 Web UI 服务器（在后台线程运行）
            host: Web UI 监听地址（不传则读取 config.json，默认 127.0.0.1）
            port: Web UI 监听端口（不传则读取 config.json，默认 8765；
                  端口被占用时按 config.json 的 auto_find_free_port 自动切换）
        """
        from .config import load_config

        server_cfg = load_config().get("server", {})
        self._web_host = host if host is not None else str(server_cfg.get("host", "127.0.0.1"))
        self._web_port = int(port if port is not None else server_cfg.get("port", 8765))

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._web_ui_thread = None
        self._uvicorn_server = None

        if web_ui:
            self.start_web_ui(self._web_host, self._web_port)

    def _run_loop(self):
        """在后台线程运行事件循环"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_async(self, coro):
        """同步等待异步任务完成"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    # ============ 连接管理 ============

    def connect(self, host: str, username: str, password: str = "", port: int = 22, private_key: str = "", terminal_name: str = "", host_id: str = "") -> str:
        """
        创建 SSH 连接并启动交互式终端

        Args:
            host: 主机地址
            username: 用户名
            password: 密码
            port: 端口
            private_key: 私钥内容
            terminal_name: 终端名称（如"终端1"），不指定则自动生成
            host_id: 关联的主机 ID（用于在 Web UI 中归组显示）

        Returns:
            session_id: 会话 ID，用于后续操作
        """
        session_id = session_manager.create_session(host, port, username, host_id, terminal_name)
        session = session_manager.get_session(session_id)

        async def _connect():
            await session.connect(
                password=password or None,
                private_key=private_key or None,
            )
            await session.start_interactive_shell()

        self._run_async(_connect())
        return session_id

    def connect_without_shell(self, host: str, username: str, password: str = "", port: int = 22, private_key: str = "") -> str:
        """
        创建 SSH 连接但不启动交互式终端（仅用于执行命令）

        Returns:
            session_id: 会话 ID
        """
        session_id = session_manager.create_session(host, port, username)
        session = session_manager.get_session(session_id)

        async def _connect():
            await session.connect(
                password=password or None,
                private_key=private_key or None,
            )

        self._run_async(_connect())
        return session_id

    def get_session(self, session_id: str) -> Optional[SSHSession]:
        """获取会话对象"""
        return session_manager.get_session(session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话"""
        return session_manager.list_sessions()

    def close_session(self, session_id: str) -> bool:
        """关闭并移除会话"""
        return self._run_async(session_manager.remove_session(session_id))

    def is_connected(self, session_id: str) -> bool:
        """检查会话是否已连接"""
        session = session_manager.get_session(session_id)
        return session.is_connected if session else False

    # ============ 命令执行 ============

    def run_command(self, session_id: str, command: str, timeout: int = 30, inject: bool = True) -> Dict[str, Any]:
        """
        在会话中执行命令

        Args:
            session_id: 会话 ID
            command: 要执行的命令
            timeout: 超时时间（秒）
            inject: 是否注入到交互式终端（True=在终端中执行并显示，False=独立执行）

        Returns:
            {"output": str, "exit_code": int, "error": str}
        """
        session = session_manager.get_session(session_id)
        if not session:
            return {"output": "", "exit_code": -1, "error": "会话不存在"}

        async def _run():
            if inject and session.has_shell:
                # 注入到交互式终端
                # inject_and_capture 返回 (退出码, stdout, stderr)
                exit_code, stdout, stderr = await session.inject_and_capture(command, total_timeout=timeout)
                return {"output": stdout, "exit_code": exit_code, "error": stderr}
            else:
                # 独立执行命令
                # run_command 返回 (退出码, stdout, stderr)
                exit_code, stdout, stderr = await session.run_command(command, timeout=timeout)
                return {"output": stdout, "exit_code": exit_code, "error": stderr}

        return self._run_async(_run())

    def send_input(self, session_id: str, data: str):
        """
        向交互式终端发送原始输入（不等待结果）

        用于发送特殊字符、控制序列等。
        """
        session = session_manager.get_session(session_id)
        if session and session.has_shell:

            async def _send():
                await session.send_input(data)

            self._run_async(_send())

    def get_terminal_state(self, session_id: str) -> Dict[str, Any]:
        """
        获取终端当前状态（提示符类型、是否忙碌等）

        Returns:
            {"prompt_type": str, "is_busy": bool, "last_output": str}
        """
        session = session_manager.get_session(session_id)
        if not session:
            return {"prompt_type": "unknown", "is_busy": False, "last_output": ""}
        return session.detect_state()

    # ============ Web UI ============

    def start_web_ui(self, host: Optional[str] = None, port: Optional[int] = None):
        """
        在后台线程启动 Web UI 服务器

        启动后可以在浏览器中打开 http://host:port 观察和操作所有 SSH 连接。
        端口被占用时，按 config.json 的 server.auto_find_free_port 自动切换。
        """
        if self._web_ui_thread and self._web_ui_thread.is_alive():
            return  # 已在运行

        from .config import load_config, resolve_server_config

        cfg = load_config()
        cfg["server"]["host"] = host if host is not None else self._web_host
        cfg["server"]["port"] = int(port if port is not None else self._web_port)
        try:
            resolved = resolve_server_config(cfg)
        except (RuntimeError, ValueError) as e:
            print(f"[SSHWebTool] Web UI 启动失败: {e}")
            raise
        host, port = resolved["host"], resolved["port"]
        self._web_host, self._web_port = host, port

        def _run_server():
            import uvicorn
            from main import app

            config = uvicorn.Config(app, host=host, port=port, log_level="warning", workers=1)
            self._uvicorn_server = uvicorn.Server(config)
            self._uvicorn_server.run()

        self._web_ui_thread = threading.Thread(target=_run_server, daemon=True)
        self._web_ui_thread.start()
        time.sleep(1)  # 等待服务器启动
        print(f"[SSHWebTool] Web UI 已启动: http://{host}:{port}")

    def stop_web_ui(self):
        """停止 Web UI 服务器"""
        if self._uvicorn_server:
            self._uvicorn_server.should_exit = True
            self._uvicorn_server = None
        if self._web_ui_thread:
            self._web_ui_thread.join(timeout=3)
            self._web_ui_thread = None

    # ============ 主机配置（持久化） ============

    def save_host(self, name: str, host: str, username: str, password: str = "", port: int = 22, group: str = "", device_type: str = "linux") -> dict:
        """保存主机配置到 data.json（重启不丢失）"""
        return storage.add_host(
            {
                "name": name,
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "group": group,
                "device_type": device_type,
            }
        )

    def list_hosts(self) -> List[dict]:
        """列出所有已保存的主机"""
        return storage.list_hosts()

    def connect_saved_host(self, host_id: str, terminal_name: str = "") -> str:
        """使用已保存的主机配置创建连接"""
        host = storage.get_host(host_id)
        if not host:
            raise ValueError(f"主机不存在: {host_id}")
        return self.connect(host=host["host"], username=host["username"], password=host.get("password", ""), port=host.get("port", 22), terminal_name=terminal_name, host_id=host_id)

    # ============ 清理 ============

    def close_all(self):
        """关闭所有连接并清理资源"""
        for session_id in list(session_manager._sessions.keys()):
            try:
                self.close_session(session_id)
            except Exception:
                pass
        self.stop_web_ui()
        self._loop.call_soon_threadsafe(self._loop.stop)


# ============ Monkey-Patch：劫持 paramiko / asyncssh ============

_original_paramiko_connect = None
_original_asyncssh_connect = None
_patched = False


def patch_paramiko(manager: Optional[SSHWebTool] = None):
    """
    劫持 paramiko.SSHClient.connect，让已有代码的 SSH 连接自动注册到管理器

    使用后，所有 paramiko.SSHClient().connect() 创建的连接都会：
    1. 正常执行原有连接逻辑（不重复建连）
    2. 在全局会话管理器中注册一个镜像会话（出现在会话列表 /api/sessions 中）
    3. 调用 client.close() 时镜像会话自动移除

    注意：
    - 镜像会话没有交互式 shell，不会出现在 Web UI 的活跃终端中（/api/sessions/active）。
      如需在 Web 终端中交互操作，请使用 SSHWebTool.connect() 创建会话。
    - 注册始终写入全局 session_manager（Web UI 与所有 SSHWebTool 实例共享）；
      当前架构不支持实例级隔离，manager 参数仅为 API 兼容保留。

    Args:
        manager: SSHWebTool 实例（保留参数，当前注册到全局会话管理器）
    """
    global _original_paramiko_connect, _patched

    if manager is None:
        manager = _get_default_manager()

    try:
        import paramiko
    except ImportError:
        print("[SSHWebTool] paramiko 未安装，无法劫持（可先执行 pip install paramiko）")
        return False

    if _original_paramiko_connect is None:
        _original_paramiko_connect = paramiko.SSHClient.connect

    def patched_connect(self, hostname, port=22, username=None, password=None, pkey=None, key_filename=None, timeout=None, allow_agent=True, look_for_keys=True, compress=False, sock=None, **kwargs):
        # 执行原有连接（失败会正常向上抛异常，不会注册镜像会话）
        result = _original_paramiko_connect(
            self,
            hostname,
            port=port,
            username=username,
            password=password,
            pkey=pkey,
            key_filename=key_filename,
            timeout=timeout,
            allow_agent=allow_agent,
            look_for_keys=look_for_keys,
            compress=compress,
            sock=sock,
            **kwargs,
        )

        # 在管理器中注册镜像会话（仅注册，不重复连接）
        session_id = None
        try:
            session_id = session_manager.create_session(
                hostname, port, username or "", terminal_name=f"paramiko-{hostname}"
            )
            session = session_manager.get_session(session_id)
            session._external = True       # 外部镜像会话：is_alive 基于真实 client，不参与空闲清理
            session._paramiko_client = self
            session._connected = True      # is_connected 是只读 property，直接设置内部字段
            session.last_active = time.time()
            print(f"[SSHWebTool] 已注册 paramiko 连接: {username}@{hostname}:{port} (session={session_id})")

            # hook client.close：连接关闭时同步移除镜像会话，避免残留"已连接"僵尸会话
            if not getattr(self, '_ssh_web_tool_close_hooked', False):
                original_close = self.close

                def patched_close():
                    try:
                        ret = original_close()
                    finally:
                        try:
                            if session_manager.remove_session_sync(session_id):
                                print(f"[SSHWebTool] paramiko 连接已关闭，移除镜像会话 {session_id}")
                        except Exception:
                            pass
                    return ret

                self.close = patched_close
                self._ssh_web_tool_close_hooked = True
        except Exception as e:
            # 注册失败：清理半成品会话，避免残留未连接状态
            if session_id:
                session_manager.remove_session_sync(session_id)
            print(f"[SSHWebTool] 注册 paramiko 连接失败: {e}")

        return result

    paramiko.SSHClient.connect = patched_connect
    _patched = True
    print("[SSHWebTool] paramiko 已劫持，所有连接将自动注册到管理器")
    return True


def patch_asyncssh(manager: Optional[SSHWebTool] = None):
    """
    劫持 asyncssh.connect，让已有代码的 SSH 连接自动注册到管理器

    注意：
    - 镜像会话没有交互式 shell，不会出现在 Web UI 的活跃终端中。
    - 注册写入全局 session_manager；manager 参数仅为 API 兼容保留。

    Args:
        manager: SSHWebTool 实例（保留参数，当前注册到全局会话管理器）
    """
    global _original_asyncssh_connect

    if manager is None:
        manager = _get_default_manager()

    try:
        import asyncssh
    except ImportError:
        print("[SSHWebTool] asyncssh 未安装，无法劫持")
        return False

    if _original_asyncssh_connect is None:
        _original_asyncssh_connect = asyncssh.connect

    async def patched_connect(host, port=22, username=None, password=None, client_keys=None, known_hosts=None, **kwargs):
        # 工具自身连接（SSHSession.connect 内部调用）：直接放行，不注册镜像
        from .sessions import _patch_guard

        if getattr(_patch_guard, 'active', False):
            return await _original_asyncssh_connect(
                host, port=port, username=username, password=password,
                client_keys=client_keys, known_hosts=known_hosts, **kwargs
            )

        # 执行原有连接
        conn = await _original_asyncssh_connect(host, port=port, username=username, password=password, client_keys=client_keys, known_hosts=known_hosts, **kwargs)

        # 在管理器中注册镜像会话
        session_id = None
        try:
            session_id = session_manager.create_session(
                host, port, username or "", terminal_name=f"asyncssh-{host}"
            )
            session = session_manager.get_session(session_id)
            session._external = True       # 外部镜像会话：is_alive 基于真实 conn，不参与空闲清理
            session._asyncssh_conn = conn
            session._connected = True      # is_connected 是只读 property，直接设置内部字段
            session.last_active = time.time()
            print(f"[SSHWebTool] 已注册 asyncssh 连接: {username}@{host}:{port} (session={session_id})")

            # hook conn.close：连接关闭时同步移除镜像会话（尽力而为，失败不影响连接）
            if not getattr(conn, '_ssh_web_tool_close_hooked', False):
                original_close = conn.close

                def patched_close():
                    try:
                        ret = original_close()
                    finally:
                        try:
                            if session_manager.remove_session_sync(session_id):
                                print(f"[SSHWebTool] asyncssh 连接已关闭，移除镜像会话 {session_id}")
                        except Exception:
                            pass
                    return ret

                conn.close = patched_close
                conn._ssh_web_tool_close_hooked = True
        except Exception as e:
            # 注册失败：清理半成品会话，避免残留未连接状态
            if session_id:
                session_manager.remove_session_sync(session_id)
            print(f"[SSHWebTool] 注册 asyncssh 连接失败: {e}")

        return conn

    asyncssh.connect = patched_connect
    print("[SSHWebTool] asyncssh 已劫持，所有连接将自动注册到管理器")
    return True


def patch_all(manager: Optional[SSHWebTool] = None) -> Optional[SSHWebTool]:
    """
    一次调用，劫持当前进程内所有主流 SSH 连接入口：
    - paramiko（SSHClient.connect）—— fabric / scp / pssh 等基于 paramiko 的库自动覆盖
    - asyncssh（asyncssh.connect）

    之后当前进程中发起的 SSH 连接都会注册为镜像会话（连接状态可见，
    client 关闭时自动移除）。工具自身内部的 asyncssh 连接不会被重复注册。

    注意：
    - 仅对"同一 Python 进程内"的调用生效；通过 subprocess 调外部 ssh 命令
      （openssh 客户端）属于跨进程，无法劫持。
    - 镜像会话只登记连接（无交互 shell）；如需命令/输出观测请另行开启。

    Args:
        manager: SSHWebTool 实例（保留参数，当前注册到全局会话管理器）

    Returns:
        传入的 manager（未传入时为全局默认实例），便于链式使用
    """
    if manager is None:
        manager = _get_default_manager()

    results = []
    try:
        results.append(patch_paramiko(manager))
    except Exception as e:
        print(f"[SSHWebTool] 劫持 paramiko 失败: {e}")
        results.append(False)
    try:
        results.append(patch_asyncssh(manager))
    except Exception as e:
        print(f"[SSHWebTool] 劫持 asyncssh 失败: {e}")
        results.append(False)

    ok = sum(1 for r in results if r)
    if ok == len(results) and ok > 0:
        print("[SSHWebTool] 已劫持当前进程所有 SSH 连接入口（paramiko + asyncssh）")
    elif ok > 0:
        print(f"[SSHWebTool] 部分劫持成功（{ok}/{len(results)}），请查看上方提示")
    else:
        print("[SSHWebTool] 劫持失败：paramiko 与 asyncssh 均不可用")
    return manager


def unpatch():
    """恢复所有被劫持的函数"""
    global _original_paramiko_connect, _original_asyncssh_connect, _patched

    if _original_paramiko_connect:
        try:
            import paramiko

            paramiko.SSHClient.connect = _original_paramiko_connect
        except ImportError:
            pass
        _original_paramiko_connect = None

    if _original_asyncssh_connect:
        try:
            import asyncssh

            asyncssh.connect = _original_asyncssh_connect
        except ImportError:
            pass
        _original_asyncssh_connect = None

    _patched = False
    print("[SSHWebTool] 已恢复所有被劫持的函数")


# ============ 全局默认管理器 ============

_default_manager = None


def _get_default_manager() -> SSHWebTool:
    """获取或创建全局默认管理器"""
    global _default_manager
    if _default_manager is None:
        _default_manager = SSHWebTool(web_ui=False)
    return _default_manager


def get_manager() -> SSHWebTool:
    """获取全局默认管理器（如果不存在则创建）"""
    return _get_default_manager()


# ============ 便捷函数 ============


def quick_connect(host: str, username: str, password: str = "", port: int = 22, web_ui: bool = False) -> tuple:
    """
    快速连接（使用全局默认管理器）

    Returns:
        (manager, session_id)
    """
    manager = _get_default_manager()
    if web_ui:
        manager.start_web_ui()
    session_id = manager.connect(host, username, password, port)
    return manager, session_id
