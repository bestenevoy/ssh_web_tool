@echo off
REM ============================================================
REM SSH Web Tool 一键打包（Windows）
REM 产出：dist/SSHWebTool.exe（独立程序）+ dist/wheel/（劫持包）
REM 详见 build.ps1
REM ============================================================
powershell -ExecutionPolicy Bypass -File "%~dp0build.ps1"
echo.
pause
