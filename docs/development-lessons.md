# 开发踩坑与修复记录（Lessons Learned）

> 本文件记录 ssh_web_tool 开发过程中**真实遇到并修复**的所有问题，含根因、修复与预防措施。
> 目的：避免重蹈覆辙。新增问题时按类别追加，保持条目短小、可直接照做。

---

## 一、前端（React + xterm.js）

### 1. 重连后跳回第一个 Tab
- **现象**：在第二个 Tab 点「重连」，连接完成后跳回第一个 Tab
- **根因**：`closeTerminal` 是 `useCallback`，闭包里捕获的 `activeId` 是**调用时刻的旧值**（React setState 异步批量，紧跟 `createTerminal` 之后的调用读到的仍是旧会话 id），关闭旧标签时走了 `setActiveId(remaining[0])` 把激活抢走
- **修复**：`closeTerminal` 增加 `keepActive` 参数；重连流程 `closeTerminal(session_id, false, true)` 不切换 activeId
- **预防**：凡是「先 setState 又立刻在同函数读该状态」的模式都要小心；用 ref 保存最新值（项目里 `terminalsRef` 就是为此）

### 2. 首次连接/重连后主机 banner（MOTD）不显示或显示不全
- **现象**：连接后看不到 "Welcome to Ubuntu"、系统信息等登录横幅
- **根因（两个叠加）**：
  1. `resyncTerminal` 里 `term.reset()` + 发送 `Ctrl+L`，会清掉刚显示出的 banner（且 onopen 里 0/50/200ms 重复执行，历史加载快时 banner 写完又被 reset）
  2. 历史加载用 `offset=0`（从日志文件**末尾**倒数读 3000 字符），banner 在文件开头，日志稍长即丢
- **修复**：`resyncTerminal` 加 `clean_screen` 参数——首次打开传 `false`（不 reset 不 Ctrl+L）；onopen 去掉 `term.reset()`；历史加载改 `offset=999999` 从开头读全量
- **预防**：改终端时序逻辑时注意「异步历史加载 vs 同步清屏」的竞态

### 3. 方向键残留 `[D`/`[C` 污染命令历史
- **现象**：终端里按方向键修改命令后，历史/搜索里出现 `[D`、`[C` 垃圾，执行后终端显示字面字符
- **根因**：`handleTerminalInput` 只过滤 `\x1b` 单字符（charCode<32），但方向键序列 `\x1b[D` 里的 `[`、`D` 是**可打印字符**，被追加进输入行缓冲区，回车时整条写入历史库
- **修复**：
  - 前端：输入处理改为**精确行编辑**——跟踪 `input_cursor`；ESC 序列整段解析（方向键/Home/End/Delete 只移动光标，绝不写入缓冲）；退格/Delete 按光标位置删除；可打印字符插入光标处
  - 后端：`record_command` 前清洗 ANSI/控制字符；启动时清理既有脏数据；JSON 迁移同样清洗
- **预防**：处理终端输入时，**ESC 序列必须整段判断**（`data.startsWith('\x1b')`），不能按字符过滤

### 4. 选中终端自动复制 vs Ctrl+C 复制
- **现象**：原实现「选中即自动复制」，但 Ctrl+C 在有选区时应该复制且**不打断终端**
- **修复**：移除 `onSelectionChange` 自动复制，只保留 `attachCustomKeyEventHandler` 里的 Ctrl+C（有选区→复制并 return false 阻止发送）
- **预防**：xterm 自定义按键 handler 是**单槽**，复制/粘贴/快捷键必须放同一个 handler 内

### 5. Alt+R 会输入 r 到终端
- **现象**：终端聚焦时按 Alt+R，会输入字母 r
- **根因**：xterm 默认把 Alt+R 当作普通键发送
- **修复**：`attachCustomKeyEventHandler` 拦截 `e.altKey && e.key === 'r'`，return false 阻止发送并打开搜索弹窗
- **预防**：所有终端级快捷键都要显式 return false 阻断，不能只做副作用

### 6. Ctrl+V 粘贴格式问题
- **现象**：终端里 Ctrl+V 粘贴带格式文本（或粘贴无效果，只能用 Ctrl+Shift+V）
- **修复**：`attachCustomKeyEventHandler` 拦截 Ctrl+V，`navigator.clipboard.readText()` 读纯文本后 `term.paste(text)`
- **预防**：xterm 没有原生 Ctrl+V 粘贴，必须自己实现

### 7. 终端重连后名称消失
- **现象**：重连后新标签名变成默认名
- **修复**：重连流程把旧实例的 `terminal_name` 传给 `createTerminal(host, inst.terminal_name)`
- **预防**：重连/复制等「衍生终端」操作要显式传递名称

### 8. 终端字体放大后底部被遮住
- **现象**：字号调大后若光标在最后一行，底部显示不全
- **处理**：容器 fit 时机要在字体加载完成后（`document.fonts.ready` 后重新 fit），已加入 registerContainer
- **预防**：字号/字体变化后必须重新 `fitTerminal` + 发送 resize

---

## 二、后端（FastAPI + asyncssh）

### 9. 终端输入失败触发自动重连，杀死正在运行的 vi/vim
- **现象**：用 vi 编辑文件时提示「终端输入失败，正在尝试自动重连」，vi 会话被中断
- **根因**：WS `handle_message` 的 input 写入失败（如 process 未就绪）会调 `session.reconnect()`，重连会 `process.close()`——**正在运行的全屏程序被直接杀死**
- **修复**：input 失败**不再自动重连**——先等 shell 就绪（最多 3 秒）重试，仍失败只提示错误；monitor 检测到 shell 状态异常也不自动重启（改提示用户手动重连）
- **预防**：**任何「自动重连/重启」逻辑都不能 close 当前 process**，否则会中断用户正在跑的程序；与用户「不自动重连」的需求一致

### 10. 存储阵列/慢速 SSH：长输入后 channel 挂死
- **现象**：输入一长串后终端「死掉」，Ctrl+C、退格全部无效，最后报 `channel not open for sending`
- **根因**：xterm `onData` **逐字符**发送，每个字符一次 WS 消息 + 一次 `stdin.write`；存储阵列等慢速 SSH 服务被大量小数据包淹没，服务端关闭 channel（channel 已不在 open 状态，之后任何输入都失败）
- **修复**：前端**输入合并发送**——按 50ms 窗口把连续输入积累后**一次** WS 发送；粘贴/长输入从几十次 write 降为 1 次
- **预防**：终端输入不能逐字符通信，必须有合并窗口；合并窗口不宜过大（交互程序需即时性），50ms 是平衡点

### 11. asyncssh 参数顺序坑
- `create_process(term_size=(cols, rows))`、`change_terminal_size(cols, rows)` —— **实际是 (cols, rows)，与文档写的 (rows, cols) 相反**，传反会导致 `$COLUMNS` 错误
- **预防**：见 `troubleshooting-terminal-size.md`，改动前先查

### 12. inject_command 调用未定义方法
- **现象**：`inject_command` 里调用 `self._record_command(command)`，但该类从未定义该方法 → 运行 AttributeError
- **修复**：改用 `history_db.record_command`（async，try/except 包裹，记录失败不影响命令执行）
- **预防**：新代码调用私有方法前确认其存在；历史记录类逻辑统一走 `history_db`

---

## 三、数据与存储（SQLite / data.json）

### 13. 测试误清历史库（严重教训）
- **现象**：调试清洗逻辑时用 `os.replace(db, db.bak)` 备份后跑 `init_db`，**init_db 会新建空库**，随后又删除了备份文件 → 历史命令全丢
- **恢复**：幸好 `data.json` 里保留着完整 `command_history`（29 条），用 `migrate_from_json` 全量恢复
- **预防**：
  - **测试涉及用户真实数据前必须先备份，且备份保留到确认无误后再删**
  - 对用户数据做破坏性操作（删除/清空）前先确认有恢复路径
  - `init_db`/`migrate` 这类「幂等但可能重建」的函数，测试时用临时目录

### 14. 历史命令脏数据（`[T` 等残留）
- **现象**：SQLite 历史里有 `[T`、`[D` 这类无意义记录
- **根因**：旧版本输入处理把 ESC 序列拆解后残留字面片段（无 `\x1b` 前缀，ANSI 正则清不掉）
- **修复**：`_purge_dirty_commands` 启动时清理：含 ESC 的命令清洗、孤立残留（`^\[\S{0,3}$`）直接删除；`record_command`/`migrate_from_json` 也清洗
- **预防**：源头在问题 3 的前端行编辑；后端清洗是兜底

### 15. data.json 密码明文与展示
- 密码明文存 data.json（本地工具定位）；前端默认不展示，可全局配置切换明文；前端只展示不保存
- 生产环境建议密钥认证

---

## 四、构建与打包（PyInstaller / Windows）

### 16. EXE 运行时被锁（PermissionError）
- **现象**：build.ps1 构建时 `PermissionError`（EXE 正被运行）
- **修复**：构建前先停占用 8765 端口的进程
  ```powershell
  $c = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
  if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force } }
  ```
- **预防**：构建/覆盖前必查端口占用；`build.ps1` 可内置此逻辑

### 17. 运行中的 EXE 无法覆盖（滚动升级）
- **现象**：程序在运行，下载新版本无法直接替换 EXE
- **原理**：Windows 锁住运行中的 EXE 文件，但**允许重命名**
- **方案**：重命名旧 EXE 为 `.old` → 新 EXE 改名为正式名 → 退出旧进程 → 启动新版 → 下次启动清理 `.old`
- **预防**：升级功能按此三步实现，不要尝试直接覆盖

### 18. EXE 无控制台，print 日志不可见
- **现象**：打包为窗口程序后 `print` 无处可看，线上问题难排查
- **处理**：托盘菜单提供「打开 CMD 查看请求日志」入口
- **预防**：关键路径（WS 输入失败、重连、监控）保留 print 且考虑写文件日志

### 19. build.ps1 续行符问题
- `main.py` 未作为参数传入（续行符解析问题）→ 已修复（commit fecdabd）
- **预防**：改 build.ps1 后先验证打包产物包含预期文件

### 20. 打包体积
- `SSHWebTool.exe` 约 21.3MB（PyInstaller onefile）；体积优化考虑 UPX 压缩、排除未用依赖
- 前端单文件 `static/index.html` 约 570KB（gzip 151KB），xterm.js 占大头

---

## 五、CI / 发布（GitHub Actions + Gitee）

### 21. job 级 `if:` 引用 secrets 导致 workflow 整体失效
- **现象**：`if: secrets.GITEE_TOKEN != ''` 写在 job 级 → workflow 直接失败（0 jobs）
- **修复**：改为 job 内步骤检查 token 再跳过（`if: env.GITEE_TOKEN != ''`）
- **预防**：**secrets 只能在 job/steps 内部引用，不能出现在 job 级 if 的条件里**

### 22. Gitee Release 同步的坑
- 发布 Gitee 需要 token 判空（200+null）、`target_commitish` 参数 → 均已修复
- 后因用户不再需要，**已移除 Gitee 发布 job**（代码镜像仍推 gitee）
- **预防**：第三方 API 的判空和必填参数都要处理；需求变化时及时移除不再需要的流程

### 23. GitHub 下载资产间歇超时
- 反代 `github.com:443` 连接超时，curl retry 仍失败 → **非代码问题**，重试/换网络

### 24. pyproject.toml 依赖误入 keywords 破坏 TOML
- `aiosqlite` 依赖误写入 `keywords` 字段导致 TOML 解析失败 → 已修复（commit 89693d2）
- **预防**：手改 TOML 后跑 `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` 验证

---

## 六、开发流程与工具链教训

### 25. PowerShell 转义/引号坑（高频踩）
- `python -c "..."` 里的 `\"` 会被 PowerShell 提前处理成 `"` 终止字符串 → **复杂脚本一律写成 `.py` 文件再执行**
- `git commit -m "含中文引号或换行的消息"` 会解析出 pathspec 错误 → **用 `-F 消息文件` 提交**
- `2>&1 | Select-Object` 管道有时返回非零退出码（误报失败）→ 以实际输出为准

### 26. Python patch 脚本：assert 中断则不写盘
- **现象**：脚本先做多个 `assert s.count(x)==1` 替换、最后才 `io.open(...,'w')` 写盘——中间某个 assert 失败会抛异常，**之前所有替换都没写入**，但人容易误以为改了
- **预防**：patch 脚本**每步替换后立即写盘**，或先做**只读校验**（所有 anchor 都断言通过）再统一写盘；跑完必须看是否打印最终 OK

### 27. patch 脚本字符串缩进匹配
- **现象**：`useTerminals.ts` 里同构代码不同缩进（createTerminal 内 6 空格、restoreTerminals 内 8 空格、构造对象 8/10 空格），按一种缩进写 anchor 会 assert 失败
- **预防**：写 patch 前用 `Read` 确认**实际缩进**；对多处同构代码用上下文（注释/相邻行）区分，不要 count==2 硬凑

### 28. React 闭包捕获旧状态
- `useCallback` 闭包里的 state 是**创建时**的值，事件回调里读 `terminals`/`activeId` 都是旧值
- 项目统一用 `terminalsRef.current` 存最新引用（回调里读取）
- **预防**：回调/定时器里读状态必须走 ref，不能直接捕获 state

### 29. 状态只有一个权威来源
- 终端断开状态、shell 类型等，前后端各维护一份且定期同步（前端 5s 轮询 `listActiveTerminals`），避免两边不一致
- **预防**：新增状态时先明确唯一权威来源，另一侧只读

---

### 30. 前端记录历史会丢失 Tab 补全/历史翻查后的真实命令
- **现象**：历史里存的是键盘输入的内容（`cd /va`），Tab 补全后的实际命令（`cd /var`）没被记录
- **根因**：前端 `handleTerminalInput` 只累积 onData（键盘输入字节），而 Tab 补全、方向键历史翻查、Ctrl+R 搜索选中的命令，其**补全/替换内容由 shell 写入输出流**（前端拿不到）
- **修复**：后端从终端输出流解析「提示符之后到换行前的文本」= 实际执行的命令（bash/readline 会把最终输入回显在提示符后），统一 `record_command`；前端回车不再记录本地 buffer
  - 关键细节：清 ANSI 时**保留 `\r`**（`\r` 用于判断 Tab 补全的行内覆盖，取最后一次 `\r` 后的内容）；`...` python 续行提示符不提取；与 3 秒内同命令去重（防与前端/CLI 注入重复记录）
  - **注意**：bash 补全目录会回显为 `cd /var/`（带尾斜杠）——这是实际执行的命令，属正常
- **预防**：涉及「用户实际敲了什么」的统计/记录，一律以**终端回显**为准，不要用前端输入缓冲

## 附：一键自检清单（改动前过一遍）

1. 前端输入相关改动：ESC 序列整段处理？合并窗口？闭包是否用了 ref？
2. 会 close process 的逻辑：是否会杀掉用户正在跑的程序？
3. 涉及用户数据：备份了吗？有恢复路径吗？
4. 改 TOML/配置文件：语法验证了吗？
5. build 前：8765 端口进程停了吗？
6. 提交：消息用 `-F` 文件（避免引号坑）？两端都 push？
7. 发布：tag 两端都推？Release 资产验证了？
