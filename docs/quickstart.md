# 快速开始

## 环境要求

- Python 3.10+（开发用 3.13）
- Node.js 18+（仅前端开发需要，运行时不需要）
- asyncssh >= 2.20.0（2.17.0 有 SFTP bug）
- 推荐使用 UV 管理 Python 虚拟环境

## 安装依赖（UV 方式，推荐）

```bash
cd ssh-web-tool
uv sync
```

## 安装依赖（pip 方式）

```bash
cd ssh-web-tool
pip install -e .
```

## 启动服务

```bash
# UV 方式
uv run python main.py

# 或直接运行
python main.py
```

浏览器打开 **http://127.0.0.1:8765**

## 配置文件（config.json）

服务监听端口等启动参数通过 `config.json` 配置（首次启动自动生成，统一放在用户目录 `~/.ai4one/wstool/`，`config.json` / `data.json` / `logs/` 均在该目录）：

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 8765,
    "auto_find_free_port": true
  },
  "open_browser": true
}
```

| 字段 | 说明 |
|------|------|
| `server.host` | 服务监听地址 |
| `server.port` | 服务监听端口 |
| `server.auto_find_free_port` | `true`（默认）：端口被占用时自动向上寻找空闲端口并切换；`false`：被占用时直接报错退出 |
| `open_browser` | 启动后是否自动打开浏览器 |

- 修改配置后**重启程序生效**；启动时会打印实际使用的端口。
- 前端页面、API 均使用相对路径，端口变化后浏览器地址自动跟随，无需改前端。
- `config.json` 为本地配置（不入库），模板见 `config.example.json`；程序首次运行会自动把旧位置（EXE 目录/项目根）已有的配置与数据迁移到 `~/.ai4one/wstool/`，仅复制不覆盖。

## 添加主机

点击右上角「+ 新建主机」，填写：

- 别名（显示名称，如"阿里云生产"）
- 主机 IP、端口
- 用户名、密码
- 类型（prod/test/dev/web/db/other）
- 分组（如"生产环境"，可自定义）
- 设备类型（普通主机 / 存储阵列）

点击主机即可建立 SSH 连接。

## 作为 Python 包使用

```bash
# clone 后安装到当前 Python 环境
pip install -e .

# 然后在任何 Python 脚本中导入使用
from ssh_client import SSHClient
client = SSHClient("http://127.0.0.1:8765")
```

## 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Alt+R` | 打开全局命令历史搜索弹窗（跨终端跨主机，按频次排序） |
| `↑/↓` | 在搜索弹窗中选择历史命令 |
| `Enter` | 选中的命令输入到终端（可编辑后再执行） |
| `Ctrl+R` | 终端内反向搜索（bash 原生） |

> 注意：Alt+空格在 Windows 上被系统拦截（打开窗口菜单），所以改用 Alt+R。
