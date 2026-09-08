# 测试说明

## 分层结构（可扩展）

```
tests/
├── conftest.py           # 公共隔离 + fixtures（数据目录隔离、TestClient、FakeSessionManager、e2e 开关）
├── unit/                 # 纯单元测试：无 IO、无网络、不依赖外部
│   ├── test_storage.py       # data.json CRUD / 原子写（回归 Bug4）/ 排序 / 复制
│   ├── test_history_db.py    # 历史库：记录/搜索/忽略/去重
│   └── test_echo_parser.py   # 命令回显解析（历史记录正确性核心）
├── api/                  # API 测试：TestClient + 内存会话，不发起真实 SSH
│   ├── test_hosts_api.py     # 主机/分组/快捷指令 API
│   ├── test_preop_api.py     # 预操作上传：二进制无损（回归 Bug1）/ 路径穿越
│   └── test_sessions_api.py  # 注入并发锁（回归 Bug2）/ 广播有界队列（回归 Bug3）
├── e2e/                  # 端到端：真实 SSH（默认跳过，--e2e 启用）
│   └── test_ssh_e2e.py       # 连接/命令/交互注入/二进制上传端到端
├── run_tests.ps1         # 一键运行
└── README.md
```

## 怎么跑

```powershell
# 单元 + API（默认，最快，无需任何外部依赖）
python -m pytest tests/unit tests/api -v

# 全部（含真实 SSH 端到端）
$env:WSTOOL_E2E_HOST="1.2.3.4"; $env:WSTOOL_E2E_PASSWORD="xxx"
python -m pytest tests -v --e2e

# 一键脚本
.\tests\run_tests.ps1
.\tests\run_tests.ps1 -All
```

## 设计原则

1. **隔离优先**：conftest 在 import main 之前把 storage.data_file 指到临时目录；
   history_db 用 `fake_history_db` fixture 指到 tmp_path。**测试永远不会碰真实
   ~/.ai4one/wstool 下的 data.json / history.db / 日志**。
2. **分层标记**：`e2e` 标记默认 skip，只有显式 `--e2e` 才运行，避免误连远端。
3. **网络全 mock**：API 层用 `fake_sessions` fixture 替换 `main.session_manager`，
   新会话用内存 FakeSession 实现，不发真实 SSH。
4. **回归测试随 Bug 走**：每个修过的 Bug 都必须在本框架内留下对应测试
   （见各文件注释中的"回归 BugN"），防止复发。

## 怎么加新测试

- 纯逻辑 → `tests/unit/test_xxx.py`
- 需要调 HTTP API → `tests/api/`，用 `client` fixture
- 需要真实 SSH → `tests/e2e/`，加 `@pytest.mark.e2e`，用 `ssh_target` fixture
- 新公共 fixture → `tests/conftest.py`

## 依赖

- pytest + pytest-asyncio（`pip install pytest pytest-asyncio`，run_tests.ps1 会自动装）
- fastapi.testclient（需要 httpx，项目运行依赖已含）

## 约定（强制）

> **修复任何 Bug 的同时必须添加对应的回归测试**（写进
> docs/development-lessons.md 的开发流程章节），否则视为修复不完整。
