# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(r"C:\Users\wrz\Doubao\chats\2026-09-05\new-chat\ssh-web-tool\docs\development-lessons.md")
t = p.read_text(encoding="utf-8")

add = """

### 45. Release EXE 仍缺 pywinpty：Actions 环境没装依赖，--collect-all 收集了个寂寞（v0.1.37 修复）
- **现象**：v0.1.36 Release（Actions 构建）启动本地 cmd/PowerShell 仍提示"需要安装 pywinpty"；本机 build.ps1 构建的 EXE 正常。
- **根因（三层）**：
  1. `pywinpty` 只声明在 `requirements.txt`（且带 `; sys_platform == "win32"`），**不在 pyproject.toml dependencies**；
  2. Actions `Install dependencies` 只跑 `pip install -e .`（装 pyproject 依赖），**从不装 requirements.txt** → 构建环境里根本没有 pywinpty 包；
  3. `--collect-all "winpty"` 对不存在的包静默无效，PyInstaller 不会报错 → **EXE 里一个 winpty 文件都没有**（用 `archive_viewer -l` 对比本机/Release EXE 一目了然），运行时 `import winpty` 抛 ImportError → 报"需要安装 pywinpty"。
- **修复**：
  - pyproject.toml dependencies 增加 `"pywinpty>=3.0.5; sys_platform == 'win32'"`（`pip install -e .` 在 Windows runner 自动安装）；
  - release.yml 新增 **Verify winpty bundled** 步骤：构建后用 `python -m PyInstaller.utils.cliutils.archive_viewer -l` 检查 EXE 内是否含 winpty，没有直接 `exit 1` —— 把"打包缺依赖"变成构建失败，防止再静默回归。
- **验证**：`archive_viewer -l` 对比（Release 无 winpty / 本机有 winpty\winpty.dll、_winpty.cp*.pyd、conpty.dll、OpenConsole.exe、winpty-agent.exe）；v0.1.37 Actions 重建后重复该检查 + 运行 `/api/local/session` 实测。
- **教训**：① `--collect-all xxx` 只对"已安装"的包有效，**缺失依赖时静默放行**——打包类回归必须验证"产物内容"而非"命令执行成功"；② requirements.txt 与 pyproject dependencies 必须同步，CI 打包环境用 `pip install -e .` 时只认 pyproject；③ 排查打包问题时，`pyi-archive_viewer -l exe` 直接列产物清单，比反复猜测高效得多。
"""

p.write_text(t + add, encoding="utf-8")
print("docs #45 added")
