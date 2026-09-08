# 打包发布

## 一键打包（EXE + 劫持包）

双击 `build_exe.bat`（或执行 `powershell -ExecutionPolicy Bypass -File build.ps1`），自动完成：

1. **构建前端**：`npm run build`（产出单文件 `static/index.html`）
2. **打包独立 EXE**：PyInstaller 将后端 + 前端 + 依赖打包为 `dist/SSHWebTool.exe`（`--noconsole` 无黑窗口），双击即用
3. **打包劫持包**：`pip wheel` 产出 `dist/wheel/ssh_web_tool-*.whl`，`pip install` 后调用 `patch_all()` 可劫持当前进程所有 paramiko / asyncssh 连接（含 fabric/scp 等基于 paramiko 的库）

产物结构：

```
dist/
├── SSHWebTool.exe       # 打包生成的 EXE 文件（双击运行，常驻系统托盘）
├── wheel/               # 劫持包（ssh_web_tool-*.whl）
├── release/             # ssh-monkeypatch 独立包发布产物（wheel + sdist）
├── example_embed.py     # 嵌入式使用示例（含 patch_all）
└── README.md / requirements.txt
```

## 系统托盘（EXE 运行模式）

`SSHWebTool.exe` 以 **--noconsole 无窗口**模式打包，双击后服务常驻**右下角系统托盘**（纯 Win32 实现，零第三方依赖）：

| 操作 | 功能 |
|------|------|
| 左键单击 / 双击托盘图标 | 打开 Web 界面（默认浏览器） |
| 右键 → 打开界面 (Web) | 随时找回 Web 页面，防止误关页面 |
| 右键 → 打开配置目录 | 资源管理器打开数据目录 `~/.ai4one/wstool`（config.json / data.json / logs 所在） |
| 右键 → 打开配置文件 | 默认编辑器打开 config.json |
| 右键 → 打开日志窗口 | 新开 cmd 黑窗口实时跟随 `logs/server.log`（请求日志） |
| 右键 → 退出 | 停止服务并退出 |

其他特性：

- **单实例**：重复双击 EXE 不会启动第二个服务，而是打开已运行实例的 Web 页面（自动读取实际端口）
- **请求日志**：服务所有请求/错误日志同时写入 `logs/server.log`（开发模式额外输出到控制台）
- 实际使用的端口记录在数据目录 `~/.ai4one/wstool/.running_port`（运行时文件，不入库）
- 开发模式（`python main.py`）同样启用托盘，方便调试

## GitHub Actions 自动发布

| Workflow | 触发 | 产物 |
|----------|------|------|
| `release.yml` | 推送 `v*` 标签 | 自动构建 EXE 并发布到 GitHub Release |
| `ssh-monkeypatch-release.yml` | 推送 `ssh-monkeypatch-v*` 标签 | 自动构建 wheel + sdist 并发布 pip 包 |

### 发布 ssh-monkeypatch 独立包

```bash
git tag ssh-monkeypatch-v0.2.0
git push origin master --tags
```

Action 自动完成构建 + Release 创建 + 资产上传（使用 `GITHUB_TOKEN`，无需手动登录 GitHub）。发布后：

```bash
pip install https://github.com/bestenevoy/ssh_web_tool/releases/download/ssh-monkeypatch-v0.2.0/ssh_monkeypatch-0.2.0-py3-none-any.whl
```

## 常用命令速查

```bash
# 启动服务
cd ssh-web-tool && uv run python main.py

# 前端开发（热更新）
cd ssh-web-tool/frontend && npm run dev

# 前端构建（单文件）
cd ssh-web-tool/frontend && npm run build

# CLI 执行命令（输出同步到 Web 终端）
uv run ./ssh_client.py exec <host_id或IP> "command"

# CLI 列出主机
uv run ./ssh_client.py hosts

# CLI 列出活跃终端
uv run ./ssh_client.py terminals

# 打包成 EXE
build_exe.bat

# 查看 API 文档
# 浏览器打开 http://127.0.0.1:8765/docs
```
