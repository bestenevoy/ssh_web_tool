"""API 请求模型（从 main.py 拆分，按域集中在单文件便于统一审视）"""

from pydantic import BaseModel, Field


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
    password: str | None = None  # 可选：密码覆盖（重连弹窗输入），优先于已保存密码


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
    # 可选短标识（唯一，大小写不敏感），用于 .zs 脚本 @ 调用
    key: str = ""


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


class ConnectTimeoutRequest(BaseModel):
    """设置 SSH 连接超时（秒）"""

    seconds: int = Field(ge=1, le=300)


class SessionRecordRequest(BaseModel):
    """设置当前会话日志记录开关（默认不记录；开启时可指定保存目录）"""

    enabled: bool
    log_dir: str | None = None  # 开启记录时的保存目录；None 用默认目录


class HighlightRuleItem(BaseModel):
    """关键字高亮规则（keyword 即正则源，前端按 RegExp 编译；keyword 为规则身份键）"""

    keyword: str
    name: str
    color: str = "#FF6B6B"
    enabled: bool = True
    is_case_sensitive: bool = False


class UiSettingsRequest(BaseModel):
    """部分更新前端 UI 设置（只传需要修改的键；逐键校验，非法键返回 400）"""

    theme: str | None = None
    font_family: str | None = None
    font_size: int | None = None
    block_bar: bool | None = None
    block_auto_fold: bool | None = None
    block_max_lines: int | None = None
    block_split_mode: str | None = None
    custom_prompt_patterns: list[str] | None = None
    highlight_rules: list[HighlightRuleItem] | None = None
    log_record_dir: str | None = None  # 终端日志默认保存目录（空 = 程序默认 logs 目录）
    log_record_no_ask: bool | None = None  # 开启记录时不再询问保存目录


class RecordCommandRequest(BaseModel):
    command: str


class IgnoreCommandRequest(BaseModel):
    command: str


class PreopUploadRequest(BaseModel):
    session_id: str
    source: str  # 源文件：本机绝对路径，或 scripts 目录下的文件名
    source_type: str = "path"  # path=本机绝对路径 / script=scripts 目录文件
    remote: str  # 远端目标路径


class FileWriteRequest(BaseModel):
    """编辑器保存文件（encoding 与读取探测到的编码一致）"""

    path: str
    content: str
    encoding: str = "utf-8"
