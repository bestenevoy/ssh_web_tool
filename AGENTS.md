# AGENTS.md — 开发规范

尽量不是用 Any 类型，dict 类型尽量使用 dataclass 类型化

## 编码规范

1. **import 顺序**: 标准库 → 第三方库 → 本项目（`ssh_web_tool`、`main`、`ssh_client`），由 Ruff isort 自动管理
2. **引号风格**: 双引号（`"`），由 Ruff format 自动保证
3. **行宽**: 120 字符
4. **类型标注**: 渐进式 — 新代码应标注，旧代码不强制；函数签名优先标注
5. **异常处理**: 广泛兜底 `except Exception` 是可接受的（项目中有大量网络 I/O 场景）；不要使用 `contextlib.suppress`
6. **中文注释**: 允许且鼓励中文注释，Ruff 的 RUF001/002/003 规则已忽略中文误报

## 提交前检查流程

```bash
# 1. Ruff lint + 自动修复
ruff check --fix .

# 2. Ruff 格式化
ruff format .

# 3. 类型检查（Pyright 和 Pyrefly 任选其一，也可都跑）
pyright
pyrefly check

# 4. 运行测试
pytest
```

## 安装依赖

```bash
uv sync
```
