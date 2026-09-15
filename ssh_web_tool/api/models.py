"""API 请求模型（从 main.py 拆分，按域集中在单文件便于统一审视）"""

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    host: str
    port: int = 22
    username: str
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


class CreateRawSessionRequest(BaseModel):
    """原始连接信息创建会话（本地终端拦截 SSH 后的"重连"入口，无已保存主机）"""

    host: str
    port: int = 22
    username: str
    password: str | None = None
    terminal_name: str | None = ""


class CreateSessionFromHostRequest(BaseModel):
    host_id: str
    terminal_name: str | None = ""  # 可选：指定终端名称，不填则自动生成


class RunCommandRequest(BaseModel):
    command: str
    timeout: int = 30
    process: bool = False  # True=强制独立进程执行（不注入到Web终端），False=默认自动（有shell就注入+捕获）
    capture_exit_code: bool = True  # False=跳过 echo $? 获取退出码（节省1-2秒），退出码返回 None


class HostRequest(BaseModel):
    name: str | None = ""
    host: str
    port: int = 22
    username: str = "root"
    password: str | None = ""
    private_key: str | None = ""
    passphrase: str | None = ""
    type: str | None = "other"
    group: str | None = ""
    # 设备类型：linux（普通主机）/ storage（存储阵列）
    device_type: str | None = "linux"
    # 存储阵列管理页面配置
    mgmt_port: int | None = 8088
    mgmt_username: str | None = ""
    mgmt_password: str | None = ""
    # Playwright 自动登录选择器配置
    pw_username_selector: str | None = ""
    pw_password_selector: str | None = ""
    pw_login_btn_selector: str | None = ""
    pw_old_password_selector: str | None = ""
    pw_new_password_selector: str | None = ""
    pw_confirm_password_selector: str | None = ""
    pw_confirm_btn_selector: str | None = ""
    pw_success_selector: str | None = ""
    pw_headless: bool | None = False


class GroupRequest(BaseModel):
    name: str


class RenameGroupRequest(BaseModel):
    new_name: str


class ReorderGroupsRequest(BaseModel):
    names: list[str]


class ReorderHostsRequest(BaseModel):
    ids: list[str]


class QuickCommandRequest(BaseModel):
    name: str
    command: str
    description: str = ""
    # 指令类型：direct 直接执行 / param 带参数（输入后不执行，命令含 {args} 供编辑）
    type: str = "direct"
    # 预操作（执行命令前依次执行）：[{"type": "upload", "remote": "/path"}, {"type": "chmod", "mode": "+x", "path": "/path"}, {"type": "env", "key": "VAR", "value": "x"}]
    pre_ops: list = []


class ReorderQuickCommandsRequest(BaseModel):
    ids: list[str]


class HostTypeRequest(BaseModel):
    key: str
    label: str
    color: str = "#999999"


class SftpListRequest(BaseModel):
    path: str = "/"


class SftpWriteRequest(BaseModel):
    path: str
    content: str


class SftpDeleteRequest(BaseModel):
    path: str


class FallbackShellRequest(BaseModel):
    """设置 SSH 断开后切换的本机 shell"""

    shell: str


class RecordCommandRequest(BaseModel):
    command: str


class IgnoreCommandRequest(BaseModel):
    command: str


class PreopUploadRequest(BaseModel):
    session_id: str
    source: str  # 源文件：本机绝对路径，或 scripts 目录下的文件名
    source_type: str = "path"  # path=本机绝对路径 / script=scripts 目录文件
    remote: str  # 远端目标路径
