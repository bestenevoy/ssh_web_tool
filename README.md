# AIxTerm — 网页版 SSH 终端管理工具

类似 Xshell 的 SSH 终端管理工具：本地运行、网页操作、多端复用。本地应用统一管理所有 SSH 连接，浏览器/命令行/AI Agent 共用同一批连接。

## 功能亮点

- **多标签终端**：多主机多标签、主题/字体/字号调节，页面关闭连接不中断
- **连接稳定**：自动保活、断线重连，重开页面自动恢复全部活跃终端
- **主机管理**：分组、类型标签、搜索、复制、拖拽排序；快捷指令（预操作/参数替换）与全局命令历史（Alt+R）
- **多端复用**：网页界面 / HTTP API / SDK / CLI 共用同一批 SSH 连接，所有操作实时可见（活动日志）
- **AI Agent CLI `aixterm`**：由运行中的应用代登录，Agent 无需任何凭据即可 `exec / upload / download / sync / broadcast / monitor / ps`；服务自动发现、目标自动解析、`--json` 结构化输出，一键安装 Skill 到 Claude Code / Codex / OpenCode / WorkBuddy
- **SFTP 文件管理**：浏览、上传（拖拽、拖文件到终端、`rz`/`sz` 触发）、下载、在线查看
- **存储阵列支持**：DeviceManager 管理页自动登录
- **外部连接观测**：外部程序建立的 SSH 连接也可纳入采集，面板实时展示与历史回放
- **桌面客户端**：安装即用，应用内「检查更新」自动增量升级

## 下载安装

1. 打开 [Releases](https://github.com/bestenevoy/ssh_web_tool/releases/latest) 下载 `aixterm-setup-x.y.z-amd64.exe`
2. 双击运行安装向导；检测到旧版本时，可选择「卸载旧版本后安装（推荐）」或「直接覆盖安装」
3. 安装完成后启动应用，浏览器打开 **http://127.0.0.1:8765** 即管理页面

- **便携版**：下载 `aixterm-x.y.z-windows-amd64.tar.gz` 解压即用
- **升级**：应用内设置页「检查更新」，只下载有变化的部分，升级更快

## 命令行快速上手（AI Agent 同样适用）

应用保持运行，然后：

```bash
aixterm status                                  # 自动发现服务
aixterm sessions                                # 复用已登录的会话
aixterm exec web-prod "df -h" --json            # 指定目标执行命令
aixterm install-skill --all                     # 一键安装 Skill 到 4 个 Agent 工具
```

## 配置

端口等参数通过 `config.json` 配置（首次启动自动生成默认配置，路径 `~/.ai4one/sshtool`）。

## 关于本仓库

本仓库仅用于发布安装包与更新（Releases 页面），源代码不在此托管。
