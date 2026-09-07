# HTTP REST API

API 文档（Swagger UI）：**http://127.0.0.1:8765/docs**

## 会话管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建 SSH 会话（手动输入连接信息） |
| POST | `/api/sessions/from-host` | 从保存的主机创建 SSH 会话 |
| GET | `/api/sessions` | 列出所有活动会话 |
| GET | `/api/sessions/active` | 获取所有活跃终端（用于页面恢复） |
| GET | `/api/sessions/{id}/state` | 获取终端状态（shell 类型检测） |
| GET | `/api/sessions/{id}/logs` | 获取终端历史日志（分页） |
| DELETE | `/api/sessions/{id}` | 关闭并删除会话 |
| POST | `/api/sessions/{id}/run` | 在会话中执行命令（AI 调用） |

## 主机管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/hosts` | 获取所有保存的主机（含连接状态） |
| POST | `/api/hosts` | 新增主机 |
| PUT | `/api/hosts/{id}` | 更新主机 |
| DELETE | `/api/hosts/{id}` | 删除主机 |
| POST | `/api/hosts/reorder` | 主机排序 |
| POST | `/api/hosts/{id}/duplicate` | 复制主机 |
| POST | `/api/hosts/{id}/auto-login` | 存储阵列自动登录 |
| POST | `/api/hosts/{id}/close-browser` | 关闭存储阵列浏览器 |

## 分组管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/groups` | 获取分组列表 |
| POST | `/api/groups` | 新增分组 |
| POST | `/api/groups/reorder` | 分组排序 |
| POST | `/api/groups/{name}/duplicate` | 复制分组（含主机） |

## 快捷指令

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/quick-commands` | 获取快捷指令列表 |
| POST | `/api/quick-commands` | 新增快捷指令 |
| PUT | `/api/quick-commands/{id}` | 更新快捷指令 |
| DELETE | `/api/quick-commands/{id}` | 删除快捷指令 |

## 全局命令历史

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/history/record` | 记录命令执行（自动调用） |
| GET | `/api/history/search` | 搜索历史命令（按频次排序） |
| GET | `/api/history/recent` | 获取最近使用的命令 |

## 统一搜索

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/search?q=xxx` | 同时搜索快捷指令和历史命令 |

## 终端持久化

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/terminals/saved` | 获取已保存的终端列表 |
| POST | `/api/terminals/{id}/restore` | 恢复已保存的终端 |

## SFTP

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sftp/{id}/list` | SFTP 列出目录 |
| GET | `/api/sftp/{id}/read` | 查看文件内容 |
| GET | `/api/sftp/{id}/download` | SFTP 下载文件 |
| POST | `/api/sftp/{id}/write` | SFTP 上传/写入文件 |
| POST | `/api/sftp/{id}/delete` | SFTP 删除文件 |
| POST | `/api/sftp/{id}/upload` | SFTP 上传（multipart） |

## 外部会话（跨进程观测）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/external-sessions` | 外部会话列表（ssh-monkeypatch 推送的会话） |
| GET | `/api/external-sessions/{id}` | 外部会话详情 + 历史事件（回放） |

## WebSocket

| 路径 | 说明 |
|------|------|
| `/ws/ssh/{session_id}` | 交互式终端（输入/输出/调整大小） |
| `/ws/events` | 事件订阅（实时观察所有操作） |
| `/ws/stream` | 测试进程事件入口（ssh-monkeypatch 推送） |
| `/ws/external/{session_id}` | 浏览器订阅外部会话（历史回放 + 实时转发） |

## API 调用示例（curl）

```bash
# 执行命令
curl -X POST http://127.0.0.1:8765/api/sessions/from-host \
  -H "Content-Type: application/json" \
  -d '{"host_id":"117d5a13"}'

# 列出主机
curl http://127.0.0.1:8765/api/hosts
```

## 活动日志（事件总线）

所有关键操作都会发布事件，通过 `/ws/events` WebSocket 广播。前端「活动日志」面板实时显示，可用于观察 CLI/SDK/API 与 Server 的交互过程。

### 事件类型

| 事件类型 | 颜色 | 说明 |
|----------|------|------|
| `session_create` | 绿色 | 创建 SSH 会话 |
| `session_close` | 红色 | 关闭 SSH 会话 |
| `command_run` | 蓝色 | 执行命令 |
| `sftp_list` | 紫色 | SFTP 列目录 |
| `sftp_download` | 橙色 | SFTP 下载 |
| `sftp_upload` | 青色 | SFTP 上传 |
| `host_add` | 浅绿 | 新增主机 |
| `host_update` | 黄色 | 更新主机 |
| `host_delete` | 粉红 | 删除主机 |

### 事件来源

- `WEB` — 前端网页操作
- `API` — HTTP API 调用
- `CLI` — 命令行工具
- `SDK` — Python SDK

### 观察自动化过程

1. 打开网页，切换到「📊 活动日志」面板
2. 在另一个终端运行 CLI 命令，如 `python ssh_client.py exec 117d5a13 "echo test"`
3. 日志面板会实时显示 `session_create`（如果是新会话）和 `command_run` 事件
4. 事件包含时间、来源、详细信息
