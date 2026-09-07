# 终端尺寸排错专题（重要！）

## 症状

输入几个字符后，光标突然跳回行首并覆盖原来的内容；换行时甚至覆盖提示符行。粘贴长文本或快捷指令执行却完全正常。

## 根本原因

**asyncssh 的 `change_terminal_size` 参数顺序和文档不一致！**

| API | 文档写的 | 实际行为 |
|-----|---------|---------|
| `create_process(term_size=...)` | `(cols, rows)` | `(cols, rows)` ✓ |
| `change_terminal_size(...)` | `(rows, cols)` | `(cols, rows)` ✗ |

如果按照文档写 `change_terminal_size(rows=17, cols=96)`，实际会被解释成 **17 列、96 行**，导致 `$COLUMNS=17`，输入超过 17 列就回行首覆盖。

## 验证方法

在终端中执行：

```bash
echo $COLUMNS    # 应该是 96 左右，如果是 17 就说明参数顺序反了
stty size         # 输出格式是 "rows cols"，应该是 "17 96"，如果是 "96 17" 就反了
```

## 正确写法（sessions.py）

```python
# 创建 PTY：term_size=(cols, rows)
self.process = await self.conn.create_process(
    term_type="xterm-256color",
    term_size=(cols, rows),   # ✓ 正确
)

# 调整尺寸：change_terminal_size(cols, rows) —— 注意不是 (rows, cols)！
self.process.change_terminal_size(cols, rows)   # ✓ 正确（和文档相反）
```

## 其他可能导致输入错乱的原因

1. **xterm.js 自动换行被禁用**：`term.reset()` 可能重置 DECSET 模式，需要发送 `\x1b[?7h` 重新启用
2. **PTY 创建时用默认尺寸**：WebSocket 连接时先等待前端的 resize 消息，再启动 shell
3. **前端容器 display:none 时 fit 计算错误**：终端激活后重新 fit + resync

## 修复检查清单

- [ ] `create_process(term_size=(cols, rows))` 参数顺序正确
- [ ] `change_terminal_size(cols, rows)` 参数顺序正确（和文档相反！）
- [ ] WebSocket 连接时先等待 resize 消息再启动 shell
- [ ] `term.reset()` 后发送 `\x1b[?7h` 启用自动换行
- [ ] 终端激活后重新 `fitAddon.fit()` + `resyncTerminal`
- [ ] `echo $COLUMNS` 输出正常值（90+）
- [ ] `stty size` 输出 "rows cols"（行数在前，列数在后）
