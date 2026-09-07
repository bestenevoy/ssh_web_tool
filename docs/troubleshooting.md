# 已知问题和注意事项

## asyncssh 版本

- 必须使用 **asyncssh >= 2.20.0**
- 2.17.0 有多个 bug：SFTPAttrs 无法设置 size、SFTPClientFile.write() 不接受 bytes、conn.run() 返回值缺少字段
- 2.24.0 已验证全部功能正常

## asyncssh 参数顺序坑（重要！）

- `create_process(term_size=(cols, rows))` —— 参数顺序是 (cols, rows)
- `change_terminal_size(cols, rows)` —— **参数顺序是 (cols, rows)，和文档写的 (rows, cols) 相反！**
- 详见 [终端尺寸排错专题](troubleshooting-terminal-size.md)

## Windows 环境

- pip 命令可能不可用，用 `python -m pip` 代替
- 中文显示问题：确保 data.json 用 UTF-8 编码，PowerShell 输出可能乱码但不影响功能

## 终端关闭行为

- 点击终端标签的 ✕ 时，会弹出确认框：
  - **确定**：同时关闭后端 SSH 连接
  - **取消**：仅关闭前端标签，后端连接保持，页面重开时自动恢复
- 设计目的：避免误操作断开正在运行的任务（如编译、部署）

## 连接超时

- 后端会话 24 小时无活动自动清理
- 可在 `sessions.py` 中修改 `SESSION_TIMEOUT` 常量

## 密码安全

- 主机密码明文存储在 `data.json` 中，仅适合本地使用
- 前端默认不展示密码（全局配置可切换明文展示），前端仅作展示不保存密码
- 生产环境建议改用密钥认证或加密存储

## 前端单文件构建

- `vite-plugin-singlefile` 将所有 JS/CSS 内联到 HTML
- 构建产物约 560KB（xterm.js 占大部分），gzip 后约 141KB
- 如果需要拆分为多个文件，移除 `viteSingleFile()` 插件即可

## CLI 执行命令的输出同步

- CLI 执行命令时使用「注入+捕获」模式：命令注入到已有终端，输出同步显示在 Web 终端
- CLI 也能拿到输出和返回码
- 使用独立的输出监听器捕获新输出，不受历史输出和缓冲区清理影响
- 支持多提示符检测（shell/python/mysql/sqlite/redis/node 等），不会干扰交互程序
- 等待时间约 0.28 秒（多提示符检测 + 空闲超时兜底）

## SSH 连接保活与自动重连

- 连接时设置 `keepalive_interval=30`，每 30 秒发送保活包，防止空闲超时断开
- `keepalive_count_max=3`，3 次无响应则认为连接断开
- 自动重连机制：连接意外断开后自动重连并恢复 shell，最多重连 5 次
- WebSocket 连接时自动检测连接状态，如果断开自动重连
- 输入时如果检测到 shell 已死，自动重连并重发输入
- 后台每 15 秒检测一次连接状态，发现断开自动重连
- 重连成功/失败都会在终端中显示提示

## 前端自动同步活跃终端

- 前端每 5 秒同步一次后端活跃终端列表
- CLI/SDK/Python 包创建的新终端会自动显示在 Web 页面的终端标签中
- 已连接的终端不会重复连接，只连接新创建的终端
- 主机列表每 5 秒自动刷新，终端计数及时更新
- 终端计数使用 `is_alive()` 检测真实连接状态，而非 `is_connected` 标志位
