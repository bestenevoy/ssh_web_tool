"""SSH 会话 cd 跟踪：回显命令（EchoParser 提取的最终文本）驱动 + home 种子基准"""

from ssh_web_tool.echo_parser import EchoParser
from ssh_web_tool.sessions import SSHSession


def _sess(home: str | None = None) -> SSHSession:
    s = SSHSession("t1", "10.0.0.1", 22, "root")
    s._home_dir = home
    return s


def _cd(s: SSHSession, line: str) -> None:
    s._track_cd(line)


def test_relative_cd_after_home_seed():
    """home 已种入：第一个相对 cd 可跟踪（用户实测场景 cd workspace/）"""
    s = _sess("/root")
    _cd(s, "cd workspace/")
    assert s.current_dir == "/root/workspace"


def test_relative_cd_before_home_seed_is_unknown():
    """home 未种上时相对 cd 置未知，不猜错路径"""
    s = _sess(None)
    _cd(s, "cd workspace")
    assert s.current_dir is None


def test_absolute_cd_and_chain():
    s = _sess("/root")
    _cd(s, "cd /opt/app")
    assert s.current_dir == "/opt/app"
    _cd(s, "cd sub")
    assert s.current_dir == "/opt/app/sub"
    _cd(s, "cd ..")
    assert s.current_dir == "/opt/app"


def test_home_relative_cd():
    s = _sess("/home/admin")
    _cd(s, "cd ~/data")
    assert s.current_dir == "/home/admin/data"
    _cd(s, "cd ~")
    assert s.current_dir is None  # 回 home：由 cwd 接口回退兜底
    _cd(s, "cd")
    assert s.current_dir is None
    _cd(s, "cd other")
    assert s.current_dir == "/home/admin/other"  # 裸 cd 后相对 cd 基准回到 home


def test_unresolvable_forms_are_unknown():
    s = _sess("/root")
    _cd(s, "cd /opt")
    for line in ("cd a && ls", "cd /tmp; pwd", "cd -", "cd ~other/x", "cd --硬~"):
        _cd(s, line)
        assert s.current_dir is None, line


def test_non_cd_lines_ignored():
    """非 cd 命令（含 cdx 前缀撞车）忽略，维持当前跟踪值"""
    s = _sess("/root")
    _cd(s, "cd /opt")
    _cd(s, "cdx /tmp")
    _cd(s, "ls -la")
    assert s.current_dir == "/opt"


def test_tab_completion_tracked_via_echo():
    """Tab 补全场景端到端：输出流里 bash 用 \r 重写整行，EchoParser 提取补全后的
    最终命令（cd workspace/），喂给 _track_cd 即可正确跟踪（原始按键只见 "cd wor"）"""
    s = _sess("/root")
    parser = EchoParser()
    echo = "root@iZ2v:~# cd wor\rroot@iZ2v:~# cd workspace/\r\n"
    cmds = parser.feed(echo)
    assert cmds == ["cd workspace/"]
    for cmd in cmds:
        s._track_cd(cmd)
    assert s.current_dir == "/root/workspace"
