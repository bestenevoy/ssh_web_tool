# HTTP REST API

API 文档（Swagger UI）：**http://127.0.0.1:8765/docs**

路由按域拆分在 `ssh_web_tool/api/routers/`，除注明外前缀均为 `/api`。

## 会话管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建 SSH 会话（手动输入连接信息） |
| POST | `/api/sessions/raw` | 从原始连接信息建会话（本机终端拦截 ssh 命令的会话重连入口，无关联主机） |
| POST | `/api/sessions/from-host` | 从保存的主机创建 SSH 会话 |
| POST | `/api/local/session` | 创建本机终端（WinPTY cmd/powershell/pwsh，协议与 SSH 终端一致） |
| GET | `/api/sessions` | 列出所有活动会话 |
| GET | `/api/sessions/active` | 获取所有活跃终端（用于页面恢复） |
| DELETE | `/api/sessions/{id}` | 关闭并删除会话 |
| POST | `/api/sessions/{id}/disconnect` | 手动断开 SSH 并自动切回本机终端（顶部「断开」，与意外断开同路径） |
| POST | `/api/sessions/{id}/reconnect` | 从本机 shell 重连 SSH（复用同一会话，终端历史/日志不丢；结果经 WS `ssh_connected` 推送） |
| POST | `/api/sessions/{id}/run` | 在会话中执行命令（AI/CLI 调用） |
| GET | `/api/sessions/{id}/state` | 获取终端状态（shell 类型检测） |
| POST | `/api/sessions/states` | 批量获取多终端状态（前端轮询合并为单请求） |
| GET | `/api/sessions/{id}/cwd` | SSH 会话当前目录（cd 跟踪，基准 home 于连接后由 SFTP getcwd 种入；None 时回退缓存 home→现场 getcwd=远端 home） |
| GET | `/api/sessions/{id}/logs` | 获取终端历史日志（分页，含未 flush 缓冲） |
| GET | `/api/logs/{session_id}` | 按日志文件读取完整历史（会话删除后仍可读，重连回放用） |
| GET | `/api/sessions/{id}/record` | 查询会话日志记录开关 |
| POST | `/api/sessions/{id}/record` | 开启/暂停日志记录（默认不记录；开启可指定目录） |

## 终端持久化

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/terminals/saved` | 获取已保存的终端列表 |
| POST | `/api/terminals/{id}/restore` | 恢复已保存的终端（多 tab/多页面有抢占保护） |

## 主机 / 分组 / 类型

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/hosts` | 获取所有保存的主机（含连接状态、terminal_count） |
| POST | `/api/hosts` | 新增主机 |
| PUT | `/api/hosts/{id}` | 更新主机 |
| DELETE | `/api/hosts/{id}` | 删除主机 |
| POST | `/api/hosts/reorder` | 主机排序 |
| POST | `/api/hosts/{id}/duplicate` | 复制主机 |
| GET | `/api/host-types` | 主机类型列表 |
| POST | `/api/host-types` | 新增主机类型 |
| DELETE | `/api/host-types/{key}` | 删除主机类型 |
| GET | `/api/groups` | 获取分组列表 |
| POST | `/api/groups` | 新增分组 |
| PUT | `/api/groups/{name}` | 重命名分组 |
| DELETE | `/api/groups/{name}` | 删除分组 |
| POST | `/api/groups/reorder` | 分组排序 |
| POST | `/api/groups/{name}/duplicate` | 复制分组（含主机） |
| POST | `/api/hosts/{id}/auto-login` | 存储阵列自动登录（Playwright） |
| POST | `/api/hosts/{id}/close-browser` | 关闭存储阵列浏览器 |
| GET | `/api/storage/browsers` | 存储阵列浏览器列表 |

## 快捷指令

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/quick-commands` | 获取快捷指令列表 |
| POST | `/api/quick-commands` | 新增快捷指令 |
| PUT | `/api/quick-commands/reorder` | 快捷指令排序 |
| PUT | `/api/quick-commands/{id}` | 更新快捷指令 |
| DELETE | `/api/quick-commands/{id}` | 删除快捷指令 |

## 全局命令历史（SQLite）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/history/record` | 记录命令执行（自动调用） |
| GET | `/api/history/search` | 搜索历史命令（按频次排序，可含忽略项） |
| GET | `/api/history/recent` | 获取最近使用的命令 |
| GET | `/api/history/ignored` | 被忽略的命令列表 |
| POST | `/api/history/ignore` / `/unignore` | 忽略 / 取消忽略命令 |
| POST | `/api/history/clear` | 清空历史 |
| GET | `/api/search?q=xxx` | 统一搜索（快捷指令 + 历史命令） |

## SFTP

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sftp/{id}/list` | 列出目录 |
| GET | `/api/sftp/{id}/read` | 查看文件内容 |
| GET | `/api/sftp/{id}/download` | 下载文件（前端另存） |
| POST | `/api/sftp/{id}/download-to` | 下载到本机指定路径（工作台，注册传输任务） |
| POST | `/api/sftp/{id}/write` | 上传/写入文件 |
| POST | `/api/sftp/{id}/delete` | 删除文件 |
| POST | `/api/sftp/{id}/upload` | 上传（multipart） |
| POST | `/api/preop/upload` | 工作台预上传（本地脚本推到远端执行，注册传输任务） |
| GET | `/api/sftp/transfers` | 传输任务列表（进度轮询；可按 session_id 过滤；本机终端会话无传输） |
| POST | `/api/sftp/transfers/{task_id}/cancel` | 取消传输（分块循环中断并清理半成品） |
| POST | `/api/sftp/transfers/{task_id}/reveal` | 打开传输文件所在目录 |
| GET | `/api/scripts` | 本地脚本列表（scripts/ 目录） |

## 本地文件编辑器

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/files/read` | 读文件：≤20MB 全量可编辑；>20MB 只读按行分页；编码探测 utf-8→gbk→latin-1 |
| POST | `/api/files/write` | 写文件（编辑器保存，保持原编码；>20MB 拒绝） |
| GET | `/api/files/list` | 目录浏览（打开文件弹窗，上限 1000 项） |
| GET | `/api/files/defaults` | 编辑器默认目录（scripts/ 与 logs/） |

注：文件 IO 在工作线程执行（`asyncio.to_thread`），不阻塞事件循环。

## 配置 / 设置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取设置（主题/字体/字号/默认本机终端/连接超时/日志目录等） |
| POST | `/api/config/fallback-shell` | 设置本机终端默认 shell（断开回退共用） |
| POST | `/api/config/connect-timeout` | 设置连接超时 |
| POST | `/api/config/ui-settings` | 更新 UI 设置 |
| POST | `/api/config/open-dir` | 资源管理器打开配置目录 |
| POST | `/api/config/reload` | 重载配置文件 |

## 外部会话（跨进程观测）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/external-sessions` | 外部会话列表（ssh-monkeypatch 推送的会话） |
| GET | `/api/external-sessions/{id}` | 外部会话详情 + 历史事件（回放） |

## WebSocket

| 路径 | 说明 |
|------|------|
| `/ws/ssh/{session_id}` | 交互式终端（input/output/resize；本机与 SSH 会话共用同一协议；本机终端输入 ssh 连接串由服务端拦截直连） |
| `/ws/events` | 事件订阅（实时观察所有操作） |
| `/ws/stream` | 测试进程事件入口（ssh-monkeypatch 推送） |
| `/ws/external/{session_id}` | 浏览器订阅外部会话（历史回放 + 实时转发） |

WS 消息类型为双端契约：`ssh_web_tool/ws_protocol.py` ↔ `frontend/src/types/ws.ts`，有协议一致性测试（`tests/protocol`），新增消息须三处同步（常量、`SERVER_MSG_TYPES`、`MSG_REQUIRED_FIELDS`）。

## API 调用示例（curl）

```bash
# 从保存的主机创建会话
curl -X POST http://127.0.0.1:8765/api/sessions/from-host \
  -H "Content-Type: application/json" \
  -d '{"host_id":"117d5a13"}'

# 在会话中执行命令
curl -X POST http://127.0.0.1:8765/api/sessions/<session_id>/run \
  -H "Content-Type: application/json" \
  -d '{"command":"echo test"}'

# 列出主机
curl http://127.0.0.1:8765/api/hosts
```

## 活动日志（事件总线）

所有关键操作都会发布事件，通过 `/ws/events` WebSocket 广播。前端「活动日志」（设置页）实时显示，可用于观察 CLI/SDK/API 与 Server 的交互过程。

### 事件来源

- `WEB` — 前端网页操作
- `API` — HTTP API 调用
- `CLI` — 命令行工具
- `SDK` — Python SDK

### 观察自动化过程

1. 打开设置页「日志」分区
2. 在另一个终端运行 CLI 命令，如 `python ssh_client.py exec 117d5a13 "echo test"`
3. 日志面板会实时显示 `session_create`（如果是新会话）和 `command_run` 事件
4. 事件包含时间、来源、详细信息
