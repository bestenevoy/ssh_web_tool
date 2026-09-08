# 测试运行脚本
# 用法：
#   .\tests\run_tests.ps1           # 单元 + API（快速，无需真实 SSH）
#   .\tests\run_tests.ps1 -All      # 全部（含 e2e，需配置测试机）
#   .\tests\run_tests.ps1 -File tests\unit\test_storage.py  # 指定文件
param(
    [switch]$All,
    [string]$File
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (python -c "import pytest" 2>$null)) {
    Write-Host "[tests] 未检测到 pytest，正在安装..." -ForegroundColor Yellow
    python -m pip install pytest pytest-asyncio
}

$args = @()
if ($All) { $args += "--e2e" }
if ($File) { $args += $File } else { $args += @("tests/unit", "tests/api") }

Write-Host "[tests] python -m pytest $($args -join ' ')" -ForegroundColor Cyan
python -m pytest @args -v
