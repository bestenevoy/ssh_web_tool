# 打包发布

## 一键打包（EXE + 劫持包）

双击 `build_exe.bat`（或执行 `powershell -ExecutionPolicy Bypass -File build.ps1`），自动完成：

1. **构建前端**：`npm run build`（产出单文件 `static/index.html`）
2. **打包独立 EXE**：PyInstaller 将后端 + 前端 + 依赖打包为 `dist/SSHWebTool.exe`，双击即用
3. **打包劫持包**：`pip wheel` 产出 `dist/wheel/ssh_web_tool-*.whl`，`pip install` 后调用 `patch_all()` 可劫持当前进程所有 paramiko / asyncssh 连接（含 fabric/scp 等基于 paramiko 的库）

产物结构：

```
dist/
├── SSHWebTool.exe       # 打包生成的 EXE 文件（双击运行）
├── wheel/               # 劫持包（ssh_web_tool-*.whl）
├── release/             # ssh-monkeypatch 独立包发布产物（wheel + sdist）
├── example_embed.py     # 嵌入式使用示例（含 patch_all）
└── README.md / requirements.txt
```

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
