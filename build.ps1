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
#
# 说明：
#   - exe 固定用项目 .venv（Python 3.13）打包，避免系统 Python 环境污染
#     （曾用系统 Python 3.14 打包，混入 invoke/yaml 等无关模块，产物膨胀 3+MB）
#   - --exclude-module winpty.tests：--collect-all winpty 会把 winpty.tests
#     （pytest 测试模块）连带 pytest/setuptools/pluggy 整条链收集进 EXE，
#     排除后可减小数 MB 体积
# ============================================================

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Dist = Join-Path $Root 'dist'
$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host '未找到 .venv\Scripts\python.exe，回退使用系统 python（建议先执行 uv sync）'
    $Py = 'python'
}

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

# ---- [1] 确保 PyInstaller 可用（.venv） ----
Write-Host ''
Write-Host '=== [1/3] 检查打包工具 ==='
& $Py -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '安装 PyInstaller（写入 .venv）...'
    uv pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'uv 安装失败，请先执行 uv sync 安装开发依赖后重试' -ForegroundColor Red
        exit 1
    }
}

# ---- [1.5] 收集 VC++ 运行时 DLL ----
# winpty.dll 依赖 vcruntime140.dll 等，干净 Windows 可能没装 VC++ Redistributable。
# vcruntime 与 Python 版本无关：优先从 .venv 基座 Python 目录收集，System32 兜底。
Write-Host ''
Write-Host '=== [1.5/3] 收集 VC++ 运行时 DLL ==='
$vcrtDir = Join-Path $Root 'vcrt'
New-Item -ItemType Directory -Force -Path $vcrtDir | Out-Null
$venvCfg = Join-Path $Root '.venv\pyvenv.cfg'
$pyHome = ''
if (Test-Path $venvCfg) {
    $pyHome = ((Get-Content $venvCfg | Select-String '^home\s*=') -replace '^home\s*=\s*', '').Trim()
}
$searchDirs = @()
if ($pyHome) { $searchDirs += $pyHome }
$searchDirs += "$env:WINDIR\System32"
$dlls = @()
foreach ($d in $searchDirs) {
    if (Test-Path $d) {
        $dlls += Get-ChildItem -Path $d -Filter 'vcruntime*.dll' -ErrorAction SilentlyContinue
        $dlls += Get-ChildItem -Path $d -Filter 'msvcp*.dll' -ErrorAction SilentlyContinue
    }
}
$copied = @()
foreach ($dll in $dlls) {
    if ($copied -notcontains $dll.FullName) {
        Copy-Item $dll.FullName $vcrtDir -Force
        $copied += $dll.FullName
        Write-Host "  Found: $($dll.Name)"
    }
}
if (-not $copied) {
    Write-Host '  No VC++ runtime DLLs found (may already be bundled by PyInstaller)'
}

# ---- [2] 打包独立 EXE ----
Write-Host ''
Write-Host '=== [2/3] 打包独立 EXE（SSHWebTool.exe，约需 1-3 分钟）==='
& $Py -m PyInstaller --noconfirm --clean --onefile --noconsole --name SSHWebTool `
    --add-data "static;static" --add-data "config.example.json;." `
    --add-data "vcrt;." `
    --hidden-import asyncssh `
    --hidden-import paramiko `
    --collect-all winpty `
    --exclude-module playwright `
    --exclude-module winpty.tests `
    main.py
if ($LASTEXITCODE -ne 0) {
    Write-Host 'PyInstaller 打包失败' -ForegroundColor Red
    exit 1
}

# ---- [3] 打包劫持包 + 整理产物 ----
Write-Host ''
Write-Host '=== [3/3] 打包劫持包并整理产物 ==='
New-Item -ItemType Directory -Force -Path (Join-Path $Dist 'wheel') | Out-Null
# 注意：uv/pip 会向 stderr 输出进度，PowerShell 5.1 在 ErrorActionPreference=Stop 下
# 对 2>&1 重定向会误判为 NativeCommandError 中断脚本，因此用 2>$null 丢弃 stderr
# wheel 用系统 python 构建（源包构建与 exe 体积无关；uv build 需联网下载构建依赖易卡住）
python -m pip wheel . -w (Join-Path $Dist 'wheel') --no-deps 2>$null

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
