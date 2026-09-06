@echo off
chcp 65001 >nul
echo ========================================
echo SSH Web Tool - 打包单个 EXE
echo ========================================
echo.

echo [1/4] 构建前端...
cd frontend
call npm run build
if errorlevel 1 (
    echo 前端构建失败！
    pause
    exit /b 1
)
cd ..
echo 前端构建完成。
echo.

echo [2/4] 安装 PyInstaller...
uv add --dev pyinstaller
echo.

echo [3/4] 打包 EXE（单文件模式）...
uv run pyinstaller ^
    --onefile ^
    --name "SSHWebTool" ^
    --add-data "static;static" ^
    --hidden-import "uvicorn.logging" ^
    --hidden-import "uvicorn.loops" ^
    --hidden-import "uvicorn.loops.auto" ^
    --hidden-import "uvicorn.protocols" ^
    --hidden-import "uvicorn.protocols.http" ^
    --hidden-import "uvicorn.protocols.http.auto" ^
    --hidden-import "uvicorn.protocols.websockets" ^
    --hidden-import "uvicorn.protocols.websockets.auto" ^
    --hidden-import "uvicorn.lifespan" ^
    --hidden-import "uvicorn.lifespan.on" ^
    --collect-submodules "asyncssh" ^
    --collect-submodules "playwright" ^
    main.py

if errorlevel 1 (
    echo.
    echo 打包失败！
    pause
    exit /b 1
)
echo.

echo [4/4] 清理临时文件...
if exist build rmdir /s /q build
if exist SSHWebTool.spec del /q SSHWebTool.spec
echo.

echo ========================================
echo 打包完成！
echo EXE 文件: dist\SSHWebTool.exe
echo.
echo 使用方法：
echo   1. 双击 SSHWebTool.exe 启动
echo   2. 自动打开浏览器 http://127.0.0.1:8765
echo   3. 数据文件(data.json)和日志(logs)保存在 EXE 同目录
echo   4. 关闭命令行窗口即停止服务
echo ========================================
echo.
pause
