# ============================================================
# SSH Web Tool 一键打包脚本
#
# 产物（dist/ 目录）：
#   SSHWebTool.exe          - 独立可执行程序（后端 + 前端 UI + 依赖，双击即用）
#   wheel/ssh_web_tool-*.whl - 劫持包（pip install 后可用 patch_all() 劫持
#                             当前进程所有 paramiko / asyncssh 连接）
#   example_embed.py        - 嵌入式使用示例（含 patch_all 用法）
#   README.md / requirements.txt
#
# 用法：先构建前端（npm run build 或本脚本自动构建），再运行：
#   powershell -ExecutionPolicy Bypass -File build.ps1
# ============================================================

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Dist = Join-Path $Root 'dist'

Write-Host ''
Write-Host '============================================'
Write-Host ' SSH Web Tool 打包'
Write-Host '============================================'

# ---- [0] 构建前端（产出 static/index.html 单文件） ----
Write-Host ''
Write-Host '=== [0/3] 构建前端 ==='
if (Test-Path (Join-Path $Root 'frontend\package.json')) {
    Push-Location (Join-Path $Root 'frontend')
    if (-not (Test-Path 'node_modules')) {
        Write-Host '安装前端依赖（首次）...'
        npm install
    }
    npm run build
    Pop-Location
} else {
    Write-Host '未找到 frontend 目录，跳过前端构建（使用已有 static/index.html）'
}

# ---- [1] 确保 PyInstaller 可用 ----
Write-Host ''
Write-Host '=== [1/3] 检查打包工具 ==='
python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '安装 PyInstaller...'
    pip install pyinstaller
}

# ---- [2] 打包独立 EXE ----
Write-Host ''
Write-Host '=== [2/3] 打包独立 EXE（SSHWebTool.exe，约需 1-3 分钟）==='
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name SSHWebTool `
    --add-data "static;static" --add-data "config.example.json;." `
    --hidden-import asyncssh `
    --hidden-import paramiko `
    --collect-all winpty `
    --exclude-module playwright `
    main.py

# ---- [3] 打包劫持包 + 整理产物 ----
Write-Host ''
Write-Host '=== [3/3] 打包劫持包并整理产物 ==='
New-Item -ItemType Directory -Force -Path (Join-Path $Dist 'wheel') | Out-Null
python -m pip wheel . -w (Join-Path $Dist 'wheel') --no-deps

Copy-Item (Join-Path $Root 'example_embed.py') (Join-Path $Dist 'example_embed.py') -Force
Copy-Item (Join-Path $Root 'README.md') (Join-Path $Dist 'README.md') -Force
Copy-Item (Join-Path $Root 'requirements.txt') (Join-Path $Dist 'requirements.txt') -Force

Write-Host ''
Write-Host '============================================'
Write-Host ' 打包完成！产物在 dist/ 目录：'
Write-Host '   SSHWebTool.exe          - 双击运行（打开网页 http://localhost:PORT）'
Write-Host '   wheel/ssh_web_tool-*.whl - 劫持包，pip install 后 patch_all() 劫持全部 SSH 连接'
Write-Host '   example_embed.py        - 嵌入式使用示例'
Write-Host '============================================'
