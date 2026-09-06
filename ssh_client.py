"""
SSH Web Tool Python Client
============================
既可作为 SDK 导入使用，也可作为 CLI 命令行工具使用。

SDK 用法:
    from ssh_client import SSHClient
    client = SSHClient("http://127.0.0.1:8765")
    result = client.exec_host("host_id", "ls -la")
    print(result['stdout'])

CLI 用法:
    python ssh_client.py hosts              # 列出所有主机
    python ssh_client.py terminals          # 列出活跃终端
    python ssh_client.py connect <host_id>  # 连接主机
    python ssh_client.py run <session_id> "cmd"  # 在指定会话执行命令（默认注入到Web终端）
    python ssh_client.py exec <id> "cmd"    # 在指定主机/会话执行（自动识别ID，默认注入到Web终端）
    python ssh_client.py state <session_id> # 查看终端当前状态（前台进程/Python/MySQL等）
    python ssh_client.py ls <session_id> /path    # SFTP 列目录
    python ssh_client.py download <session_id> remote local  # 下载
    python ssh_client.py upload <session_id> local remote     # 上传
"""
import json
import sys
import argparse
from typing import Optional, Dict, List, Any
from urllib.request import Request, urlopen
from urllib.parse import quote
from urllib.error import HTTPError


class SSHClient:
    """SSH Web Tool Python SDK - 连接本地 Server 复用 SSH 连接"""

    def __init__(self, base_url: str = "http://127.0.0.1:8765", timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout

    def _request(self, method: str, path: str, json_data: Optional[dict] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(json_data).encode('utf-8') if json_data else None
        req = Request(url, data=data, method=method)
        if data:
            req.add_header('Content-Type', 'application/json')
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                return json.loads(body.decode('utf-8')) if body else None
        except HTTPError as e:
            try:
                detail = json.loads(e.read().decode('utf-8')).get('detail', str(e))
            except Exception:
                detail = str(e)
            raise Exception(f"API Error {e.code}: {detail}")

    # ============ 会话管理 ============

    def list_sessions(self) -> List[Dict]:
        """列出所有活动会话"""
        return self._request('GET', '/api/sessions')['sessions']

    def list_active_terminals(self) -> List[Dict]:
        """列出所有活跃终端（有交互式 shell 的会话）"""
        return self._request('GET', '/api/sessions/active')['terminals']

    def create_session(self, host: str, port: int = 22, username: str = "root",
                       password: Optional[str] = None) -> Dict:
        """手动创建 SSH 会话"""
        data = {"host": host, "port": port, "username": username}
        if password:
            data["password"] = password
        return self._request('POST', '/api/sessions', data)

    def get_or_create_session(self, host: str, port: int = 22, username: str = "root",
                              password: Optional[str] = None) -> str:
        """
        获取或创建 SSH 会话（智能复用）
        - 如果该主机已有活跃终端，直接复用第1个（按创建时间排序取最老的）
        - 如果没有活跃终端，则创建新的 SSH 连接
        - 返回 session_id

        这样可以避免重复创建连接，所有操作都在同一个终端中进行
        """
        # 1. 列出所有活跃终端
        terminals = self.list_active_terminals()

        # 2. 按 host 和 port 匹配
        matched = []
        for t in terminals:
            if t.get('host') == host and t.get('port', 22) == port:
                matched.append(t)

        # 3. 如果找到，返回第1个（按创建时间排序取最老的）
        if matched:
            matched.sort(key=lambda x: x.get('created_at', 0))
            session_id = matched[0]['session_id']
            print(f"[复用] 已有活跃终端 {session_id}，直接复用")
            return session_id

        # 4. 没有找到活跃终端，创建新的会话
        print(f"[新建] 没有找到 {host}:{port} 的活跃终端，创建新连接")

        # 4.1 先按 IP 查找保存的主机，如果找到就用 connect_host（会设置 host_id）
        # 这样创建的终端会关联到保存的主机，前端能正确显示名称/类型/分组，也能统计终端数量
        try:
            hosts_data = self.list_hosts()
            hosts = hosts_data.get('hosts', []) if isinstance(hosts_data, dict) else hosts_data
            matched_host = None
            for h in hosts:
                if h.get('host') == host and h.get('port', 22) == port:
                    matched_host = h
                    break

            if matched_host:
                host_id = matched_host.get('id', '')
                host_name = matched_host.get('name', host)
                print(f"[关联] 找到保存的主机「{host_name}」(id={host_id})，使用 connect_host 创建会话")
                result = self.connect_host(host_id)
                return result.get('session_id', '')
        except Exception as e:
            print(f"[提示] 查找保存主机失败: {e}，使用 create_session 创建")

        # 4.2 没有找到保存的主机，使用 create_session（没有 host_id）
        result = self.create_session(host, port, username, password)
        return result.get('session_id', '')


    def connect_host(self, host_id: str, terminal_name: Optional[str] = None) -> Dict:
        """从保存的主机创建 SSH 会话（支持多终端）"""
        data = {"host_id": host_id}
        if terminal_name:
            data["terminal_name"] = terminal_name
        return self._request('POST', '/api/sessions/from-host', data)

    def close_session(self, session_id: str) -> Dict:
        """关闭并删除会话"""
        return self._request('DELETE', f'/api/sessions/{session_id}')

    def run_command(self, session_id: str, command: str, timeout: int = 30,
                    process: bool = False) -> Dict:
        """
        在指定会话执行命令
        - 默认（process=False）：如果会话有交互式 shell，命令注入到 Web 终端，
          输出实时显示在终端中，同时捕获返回；没有 shell 则独立进程执行
        - process=True：强制独立进程执行，不显示在 Web 终端
        """
        data = {"command": command, "timeout": timeout, "process": process}
        return self._request('POST', f'/api/sessions/{session_id}/run', data)

    def get_state(self, session_id: str) -> Dict:
        """获取终端当前状态（前台进程、是否在Python/MySQL/分页器等）"""
        return self._request('GET', f'/api/sessions/{session_id}/state')

    def exec_host(self, host_id: str, command: str, timeout: int = 30,
                  process: bool = False) -> Dict:
        """
        在指定主机执行命令（自动复用已有会话，没有则新建）
        智能识别（按优先级）：
        1. session_id 匹配
        2. host_id 匹配
        3. IP 地址匹配（支持 ip 或 ip:port，默认使用第 1 个终端）
        4. 都不匹配则新建连接
        """
        terminals = self.list_active_terminals()
        # 1. session_id 匹配
        session_match = [t for t in terminals if t['session_id'] == host_id]
        if session_match:
            return self.run_command(session_match[0]['session_id'], command, timeout, process)
        # 2. host_id 匹配
        existing = [t for t in terminals if t['host_id'] == host_id]
        if existing:
            return self.run_command(existing[0]['session_id'], command, timeout, process)
        # 3. IP 匹配（支持 ip 或 ip:port）
        ip_to_match = host_id
        port_to_match = None
        if ':' in host_id:
            parts = host_id.rsplit(':', 1)
            ip_to_match = parts[0]
            try:
                port_to_match = int(parts[1])
            except ValueError:
                pass
        # 在活跃终端中按 IP 匹配
        ip_match = []
        for t in terminals:
            if t.get('host') == ip_to_match:
                if port_to_match is None or t.get('port', 22) == port_to_match:
                    ip_match.append(t)
        if ip_match:
            ip_match.sort(key=lambda x: x.get('created_at', 0))
            return self.run_command(ip_match[0]['session_id'], command, timeout, process)
        # 在保存的主机中按 IP 匹配
        try:
            hosts_data = self.list_hosts()
            for h in hosts_data.get('hosts', []):
                if h.get('host') == ip_to_match:
                    if port_to_match is None or h.get('port', 22) == port_to_match:
                        hid = h['id']
                        existing_by_host = [t for t in terminals if t['host_id'] == hid]
                        if existing_by_host:
                            sid = existing_by_host[0]['session_id']
                        else:
                            sid = self.connect_host(hid)['session_id']
                        return self.run_command(sid, command, timeout, process)
        except Exception:
            pass
        # 4. 都不匹配，新建连接
        result = self.connect_host(host_id)
        return self.run_command(result['session_id'], command, timeout, process)

    # ============ 主机管理 ============

    def list_hosts(self) -> Dict:
        """获取所有保存的主机（含实时连接状态）"""
        return self._request('GET', '/api/hosts')

    def add_host(self, name: str, host: str, port: int = 22, username: str = "root",
                 password: str = "", type: str = "other", group: str = "") -> Dict:
        """新增主机"""
        data = {"name": name, "host": host, "port": port, "username": username,
                "password": password, "type": type, "group": group}
        return self._request('POST', '/api/hosts', data)

    def get_host_id_by_name(self, name: str) -> Optional[str]:
        """根据主机名称获取 host_id"""
        data = self.list_hosts()
        for h in data['hosts']:
            if h.get('name') == name:
                return h['id']
        return None

    # ============ SFTP 文件管理 ============

    def sftp_list(self, session_id: str, path: str = "/") -> Dict:
        """列出远程目录内容"""
        return self._request('POST', f'/api/sftp/{session_id}/list', {"path": path})

    def sftp_download(self, session_id: str, remote_path: str, local_path: str):
        """下载远程文件到本地"""
        url = f"{self.base_url}/api/sftp/{session_id}/download?path={quote(remote_path)}"
        with urlopen(url, timeout=self.timeout) as resp:
            data = resp.read()
        with open(local_path, 'wb') as f:
            f.write(data)

    def sftp_upload(self, session_id: str, remote_path: str, local_path: str):
        """上传本地文件到远程（文本文件）"""
        with open(local_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return self._request('POST', f'/api/sftp/{session_id}/write',
                           {"path": remote_path, "content": content})

    def sftp_read(self, session_id: str, remote_path: str) -> str:
        """读取远程文件内容（文本）"""
        url = f"{self.base_url}/api/sftp/{session_id}/read?path={quote(remote_path)}"
        with urlopen(url, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode('utf-8'))['content']


# ============ CLI 命令行 ============

def main():
    parser = argparse.ArgumentParser(
        description='SSH Web Tool CLI - 通过本地 Server 复用 SSH 连接',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ssh_client.py hosts                          列出所有主机
  python ssh_client.py terminals                      列出活跃终端
  python ssh_client.py connect abc12345               连接主机
  python ssh_client.py run abc12345 "ls -la"         在指定会话执行（输出同步到Web终端）
  python ssh_client.py exec abc12345 "df -h"         在指定主机/会话执行（自动识别ID）
  python ssh_client.py state abc12345                 查看终端当前状态
  python ssh_client.py ls abc12345 /var/log          SFTP 列目录
  python ssh_client.py download abc12345 /etc/hosts ./hosts  下载文件
  python ssh_client.py upload abc12345 ./file.txt /tmp/file.txt  上传文件
        """
    )
    parser.add_argument('--server', default='http://127.0.0.1:8765',
                        help='Server 地址 (默认: http://127.0.0.1:8765)')
    sub = parser.add_subparsers(dest='subcommand', metavar='<command>')

    sub.add_parser('hosts', help='列出所有保存的主机')
    sub.add_parser('sessions', help='列出所有活动会话')
    sub.add_parser('terminals', help='列出活跃终端')

    p = sub.add_parser('connect', help='连接主机，创建新终端')
    p.add_argument('host_id', help='主机ID')
    p.add_argument('--name', help='终端名称（如 终端2）')

    p = sub.add_parser('run', help='在指定会话执行命令（默认注入到Web终端并捕获输出）')
    p.add_argument('session_id', help='会话ID')
    p.add_argument('command', help='要执行的命令')
    p.add_argument('--timeout', type=int, default=30, help='超时秒数')
    p.add_argument('--process', '-p', action='store_true', help='强制独立进程执行（不注入到Web终端）')

    p = sub.add_parser('exec', help='复用指定终端执行命令（默认注入到Web终端并捕获输出）')
    p.add_argument('id', help='会话ID（推荐，从 terminals 命令获取）或 主机ID（兼容，自动选该主机第一个终端）')
    p.add_argument('command', help='要执行的命令')
    p.add_argument('--timeout', type=int, default=30, help='超时秒数')
    p.add_argument('--process', '-p', action='store_true', help='强制独立进程执行（不注入到Web终端）')

    p = sub.add_parser('state', help='查看终端当前状态（前台进程、是否在Python/MySQL等）')
    p.add_argument('session_id', help='会话ID')

    p = sub.add_parser('close', help='关闭会话')
    p.add_argument('session_id', help='会话ID')

    p = sub.add_parser('ls', help='SFTP 列出目录')
    p.add_argument('session_id', help='会话ID')
    p.add_argument('path', nargs='?', default='/', help='远程路径')

    p = sub.add_parser('download', help='SFTP 下载文件')
    p.add_argument('session_id', help='会话ID')
    p.add_argument('remote_path', help='远程文件路径')
    p.add_argument('local_path', help='本地保存路径')

    p = sub.add_parser('upload', help='SFTP 上传文件')
    p.add_argument('session_id', help='会话ID')
    p.add_argument('local_path', help='本地文件路径')
    p.add_argument('remote_path', help='远程保存路径')

    args = parser.parse_args()
    if not args.subcommand:
        parser.print_help()
        return

    client = SSHClient(args.server)

    try:
        if args.subcommand == 'hosts':
            data = client.list_hosts()
            hosts = data['hosts']
            if not hosts:
                print("没有保存的主机")
                return
            print(f"{'ID':<12} {'名称':<16} {'地址':<22} {'类型':<8} {'分组':<10} {'终端':<4} {'状态':<6}")
            print("-" * 82)
            for h in hosts:
                addr = f"{h['host']}:{h['port']}"
                status = "在线" if h.get('is_connected') else "-"
                print(f"{h['id']:<12} {h.get('name',''):<16} {addr:<22} {h.get('type',''):<8} {h.get('group',''):<10} {h.get('terminal_count',0):<4} {status:<6}")

        elif args.subcommand == 'sessions':
            sessions = client.list_sessions()
            if not sessions:
                print("没有活动会话")
                return
            print(f"{'ID':<12} {'主机':<22} {'用户':<10} {'连接':<6} {'终端':<6} {'最后活动':<20}")
            print("-" * 78)
            import time
            for s in sessions:
                addr = f"{s['host']}:{s['port']}"
                last = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s.get('last_active', 0)))
                print(f"{s['session_id']:<12} {addr:<22} {s['username']:<10} {'是' if s['connected'] else '否':<6} {'是' if s.get('has_shell') else '否':<6} {last:<20}")

        elif args.subcommand == 'terminals':
            terminals = client.list_active_terminals()
            if not terminals:
                print("没有活跃终端")
                return
            print(f"{'会话ID':<12} {'主机名':<18} {'地址':<22} {'终端名':<10} {'类型':<8}")
            print("-" * 72)
            for t in terminals:
                addr = f"{t['host']}:{t['port']}"
                print(f"{t['session_id']:<12} {t['host_name']:<18} {addr:<22} {t['terminal_name']:<10} {t.get('host_type',''):<8}")

        elif args.subcommand == 'connect':
            result = client.connect_host(args.host_id, args.name)
            print(f"✓ 已连接: session_id={result['session_id']}, terminal={result.get('terminal_name','')}")

        elif args.subcommand == 'run':
            result = client.run_command(args.session_id, args.command, args.timeout, args.process)
            out = result.get('stdout', '')
            err = result.get('stderr', '')
            mode = result.get('mode', '')
            if out:
                sys.stdout.write(out)
                if not out.endswith('\n'):
                    sys.stdout.write('\n')
                sys.stdout.flush()
            if err:
                sys.stderr.write(err)
                sys.stderr.flush()
            if mode == 'inject':
                rc = result.get('returncode')
                rc_str = str(rc) if rc is not None else 'N/A(非shell)'
                sys.stderr.write(f"[注入模式，退出码={rc_str}，输出已同步显示在Web终端]\n")
                sys.stderr.flush()
            sys.exit(result.get('returncode') or 0)

        elif args.subcommand == 'exec':
            # exec_host 内部会自动识别 session_id 或 host_id
            result = client.exec_host(args.id, args.command, args.timeout, args.process)
            out = result.get('stdout', '')
            err = result.get('stderr', '')
            mode = result.get('mode', '')
            if out:
                sys.stdout.write(out)
                if not out.endswith('\n'):
                    sys.stdout.write('\n')
                sys.stdout.flush()
            if err:
                sys.stderr.write(err)
                sys.stderr.flush()
            if mode == 'inject':
                rc = result.get('returncode')
                rc_str = str(rc) if rc is not None else 'N/A(非shell)'
                sys.stderr.write(f"[注入模式，退出码={rc_str}，输出已同步显示在Web终端]\n")
                sys.stderr.flush()
            sys.exit(result.get('returncode') or 0)

        elif args.subcommand == 'state':
            state = client.get_state(args.session_id)
            print(f"会话: {state.get('session_id', args.session_id)}")
            print(f"连接: {'是' if state.get('connected') else '否'}")
            print(f"前台进程: {state.get('foreground_process', 'unknown')}")
            print(f"在 Python: {'是' if state.get('in_python') else '否'}")
            print(f"在 MySQL: {'是' if state.get('in_mysql') else '否'}")
            print(f"在分页器: {'是' if state.get('in_pager') else '否'}")
            print(f"在 Shell: {'是' if state.get('in_shell') else '否'}")
            print(f"等待输入: {'是' if state.get('has_prompt') else '否'}")
            print(f"最后一行: {state.get('last_line', '')}")

        elif args.subcommand == 'close':
            client.close_session(args.session_id)
            print(f"✓ 会话 {args.session_id} 已关闭")

        elif args.subcommand == 'ls':
            result = client.sftp_list(args.session_id, args.path)
            items = result.get('items', [])
            dirs = [i for i in items if i['type'] == 'dir']
            files = [i for i in items if i['type'] != 'dir']
            for d in dirs:
                print(f"  <DIR>         {d['name']}/")
            for f in files:
                print(f"  {f.get('size',0):>10}  {f['name']}")
            print(f"\n共 {len(items)} 项")

        elif args.subcommand == 'download':
            client.sftp_download(args.session_id, args.remote_path, args.local_path)
            print(f"✓ 已下载: {args.remote_path} -> {args.local_path}")

        elif args.subcommand == 'upload':
            client.sftp_upload(args.session_id, args.remote_path, args.local_path)
            print(f"✓ 已上传: {args.local_path} -> {args.remote_path}")

    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
