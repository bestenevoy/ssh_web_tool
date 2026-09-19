# GLM-5.3 开发会话记录（需求与问题总结）

本文档汇总由 GLM-5.3 模型驱动的开发会话中，用户提出的全部需求/问题，以及实现过程中遇到并解决的技术问题。作为后续开发的参考与避坑清单。

## 一、用户提出的需求与问题

### 1. 界面字号缩放（ui_font_scale）
- **需求**：界面 UI 字号支持整体倍率缩放，与终端字号独立。
- **实现**：
  - `index.css` 全部 UI 尺寸接入 `--ui-fs-scale` 倍率变量（`calc(12px * var(--ui-fs-scale))` 形式），内联 fontSize 同步接入；
  - `useSettings` / `api.ts` / 后端 `config.py` 新增 `ui_font_scale` 设置项并持久化；
  - 设置页「字体与主题」增加界面字号选择。

### 2. 上下左右分屏 + 右侧会话列表
- **需求**：评估并实现上下/左右分屏；会话列表放在右侧并按主机 ip 聚合分组；顶部 tab 栏保留并存。
- **实现**：
  - 分屏为固定均分 2 窗格（不做拖拽分割树）：`splitMode: 'none' | 'h' | 'v'` + `panes: [session_id|null, session_id|null]`；
  - `TerminalView` 改 grid 布局，`.pane-0/.pane-1` 控制窗格定位；未上屏会话保持挂载仅 `display:none`（保住 xterm 容器）；
  - `activeId` 始终等于焦点窗格的会话，点击窗格切换焦点；空窗格显示占位提示；
  - 自愈 effect 统一处理：清理已关闭会话的窗格、两窗格全空退回单屏、新建/重连会话自动进入焦点窗格；
  - 布局/窗口尺寸变化后对可见窗格逐一 refit + sendResize（新增 `useTerminals.refitTerminals`）；
  - 新组件 `SessionPanel`：按 ip 聚合分组（组头 = 主机名/ip + 会话数徽标，组内 = 会话名 + 状态点），支持拖拽调宽、一键折叠；
  - 分屏中的 tab 加 ◫ 标记；窄窗口（<800px）禁止进入分屏。

### 3. 右侧面板日志挪到设置页
- **需求**：右侧工具面板的「日志」tab（活动日志 + API 文档）迁入设置页。
- **实现**：右侧面板只留「快速指令」「SFTP」；`SettingsModal` 新增「日志与 API」分区；事件采集仍在 App 层 `useEvents()` 持续进行（设置页关闭时也不丢事件），通过 props 传入展示。
- **后续演进（2026-09，以下述第三节为准）**：会话列表并入工具面板 tab；设置页 API 文档删除；事件采集从 App 层 props 改为 `useEvents.ts` 模块级外部 store。

### 4. 重连失败会生成本地终端（bug → 需求已演进）
- **现象**（原始 bug）：SSH 重连失败后，标签变成一个 cmd/PowerShell 本地终端。
- **第一版修复（v0.1.61，3adc567）**：区分「用户 exit 正常退出 → 切本机」与「传输层断开 → 不切本机，发 `ssh_disconnected` 后关 WebSocket，前端走断开态由用户手动重连」。
- **当前设计（后续需求变更，以 6f6fc84 为准）**：传输层断开也**自动切回本机终端**，不留断开态——「断开即回本地，保证终端始终可用」；顶部「断开」按钮（`POST /sessions/{id}/disconnect`）走同一 `_auto_switch_to_local` 路径；从本地 shell 重连时**复用同一会话**，终端历史与日志完整保留（`ssh_connected` 消息经 WebSocket 推送，前端不重建终端实例）。
- `ssh_disconnected` 现存用途仅剩「页面重开时检测到会话已断开」一处（`ws.py` 恢复路径）。曾经的死代码 `set/take_disconnect_notice`（无调用者）与 `ws.py` 输出循环里的旧「不切本机」注释已于 2026-09-19 清理，双端协议常量注释同步为现行语义。
- 两版共同的实现细节：双端消息类型同步（`ws_protocol.py` + `types/ws.ts`，含 `SERVER_MSG_TYPES` / `MSG_REQUIRED_FIELDS`）；删除了失去调用者的包装方法 `auto_switch_to_local` / `schedule_auto_switch_local`（内部 `_auto_switch_to_local` 保留）。
- **遗留待办**：后端会话已消失时（服务重启 / 他端关闭标签 DELETE / 本机 shell exit 后 remove_session / 24h 空闲清理），前端「重连」调 `POST /sessions/{id}/reconnect` 得 404「会话不存在」，`App.tsx` 只在终端写失败文案，**无"按新建会话重建"兜底**——需实现：404 时复用当前 tab 走私钥/凭据重建会话（历史尽量从日志回放）。

### 5. Tab 标签名称简化
- **需求**：tab 名称只显示「序号 + 当前会话 ip」。
- **实现**：显示 `1 · 192.168.1.10`，本地会话显示 `2 · 本地`；ip 解析优先级：拦截 SSH 会话原始凭据（`ssh_conn.host`）→ 已保存主机配置 → 显示名兜底；悬停提示保留完整信息；筛选栏支持按 ip 过滤。类型色点、环境徽章（sh/py/sql）、分屏 ◫ 标记保留。
- **后续演进（2026-09，见第三节）**：tab 类型色点删除，改单一连接状态点（`sessionConnState` 判定）；会话列表条目命名改「链接类型 · ip:port」。

### 6. 历史会话中的需求（摘要）
- **日志记录体验**：默认关闭日志记录；开启前弹目录选择（`DirPickerModal`）；设置页新增「日志记录」分区（默认目录 + 「不再询问」）。
- **日志与终端显示一致**：用 pyte 终端镜像（`terminal_mirror.py`）替代正则清洗路径，保证日志内容与终端渲染完全一致（进度条终态、退格擦除、排除 alt-screen）。
- **历史命令/提示符解析**：`EchoParser` 支持多种 shell 提示符（zsh `%`、oh-my-zsh `➜`、starship `❯` 等）；修复 `root@host:~#` 误匹配导致空回车后输出误记。
- **快捷键**：Ctrl+B 切换主机列表（终端焦点内经 `customKeyEventHandler` 拦截 + 全局兜底）；设置页新增「快捷键」说明分区。
- **主机列表显示**：只显示 ip 和类型，有名称则显示名称和类型；修复无名称时 ip 重复显示两行的问题。

## 二、实现中遇到的技术问题与解决

### 前端 / xterm
1. **xterm 容器不能卸载重建**：分屏切换窗格时若卸载隐藏会话的容器，重挂载会导致 xterm 实例与 DOM 脱节。解决：未上屏会话保持挂载、仅 `display:none`，窗格归属用 class 控制。
2. **fit 时序坑**：`display:none` 容器尺寸为 0，FitAddon 计算错误；历史输出若按默认 80 列写入，后续 fit 不会重新折行，导致 banner/MOTD 错乱。解决：`ensureFit` 重试机制（容器就绪后再 fit + sendResize）；分屏布局变化后 `requestAnimationFrame` 延迟一帧再 refit。
3. **`activeId` 与焦点窗格同步**：新建/重连/自动恢复会话可能不在任何窗格中。解决：单一自愈 effect 收敛（清理死会话 → 全空退单屏 → 新会话进焦点窗格 → 焦点与 activeId 对齐），注意 effect 内分支顺序避免互相打架。
4. **`closeTerminal` 闭包过期**：工厂回调捕获旧状态导致后端 `closed` 消息找不到实例。解决：经 ref 转发最新函数。
5. **色块栏（blockBar）自适应**：fit 后色块需重排——已有 `term.onResize` 监听，分屏零改动即可工作。

### 后端 / 协议
6. **前后端消息类型是硬约束**：`ws_protocol.py` 与 `types/ws.ts` 双端判别联合一一对应，且有协议一致性测试（`tests/protocol`）。新增 `ssh_disconnected` 时必须同步三处：后端常量、`SERVER_MSG_TYPES`、`MSG_REQUIRED_FIELDS`（必填字段表），否则测试失败。
7. **断开语义设计（已演进出第二版）**：最终语义是「任何 SSH 断开（exit 或传输层）都自动切回本机终端，前端不保留断开态；重连复用同一会话」。第一版的 `ssh_disconnected`「通知但不关标签」机制仅在页面重开恢复路径保留。改这类语义时注意：不能复用 `closed` 消息（会关标签）；`switched_to_local` 与 `ssh_connected` 成对负责切出/切回的前端状态更新。

### 工程环境（Windows）
8. **后台 pytest 输出捕获失败**：Windows 下后台运行 `uv run pytest` 输出缓冲不释放，job 日志为空。解决：输出重定向到文件（`> out.txt 2>&1`）再读取；注意同名文件可能被残留进程占用，换新文件名。
9. **并发 pytest 冲突**：多个 pytest 进程并发会互相卡住。解决：清理残留进程、串行运行。
10. **临时文件删除失败**：被占用文件无法进回收站，用 `Remove-Item -Force` 或换名规避。
11. **旧缓冲区覆盖已提交代码（真实事故）**：工作区曾出现一批「未提交改动」，内容是把
    b240535/3fce885 已提交的功能整体退回旧版本——特征：删除的是新代码、写回的注释是旧
    commit 的文案（如「优化：使用 multipart 代替 JSON sftpWrite」），且新旧文件混装导致
    前后端脱节（后端删了 `/sftp/{id}/download-to`，已提交的 `SftpWorkbench` 仍在调用 →
    运行时 404）。疑因编辑器旧缓冲区延迟保存覆盖。防范：改动前 `git status` + `git diff`
    确认基线；看到「成百上千行删除且夹带旧注释」的 diff 一律先停下来核对，不要直接提交。

## 三、UI 收敛与性能设计（2026-09 增补，当前有效口径）

以 4c3769b…62686ca 一系列提交为准，对上文第一/二节的过时描述作废旧改：

1. **面板结构**：会话列表并入右侧工具面板 tab（会话/快速指令/SFTP），面板去标题栏与 ✕，显隐统一走顶栏「📋 面板」（de3c47f）；设置页 API 文档已删除，勿恢复（53f0d6a）；顶栏「📖 终端特性档案」入口已移除，`shellProfiles.ts` 仅面向开发者（4c3769b）。
2. **提示信息统一 toast**：顶栏彻底不放提示信息，全部走终端区上方多条堆叠 toast，逐条 3.2s 消失（f3b6517）。
3. **连接状态点单一判定源**：tab 与会话列表统一用 `sessionConnState`（`lib/terminalDisconnect.ts`）：绿=已连接、红=断开、橙+闪烁=连接中；**`ssh_exited` 也算断开**（断开自动切本机 shell，终端可用但连接已断）。tab 只保留这一个状态点，类型色点已删除（0057268、2a1652a）。
4. **命名与泄露口径**：会话列表条目 = 「链接类型 · ip:port」（默认 ssh；本机 = `local · 名称`），主机行计数为 0 不渲染徽标；列表条目无 hover 提示（内容冗余）；`user:pass` 明文禁止上界面（含 tooltip 打码）。
5. **窗口缩放跟随**：pywebview 最大化/还原不一定派发 window.resize，终端容器挂 ResizeObserver 重 fit 是唯一可靠信号（9259e43）。防 churn 三要点：回调经 ref 读最新实例、观察元素未变不重建观察器、尺寸基线不变不 fit（行内 `ref={(el)=>…}` 每渲染重挂，资源回调身份必须稳定）（dfd46d3）。
6. **活动日志移出渲染路径**：`useEvents.ts` 改为模块级外部 store（`useSyncExternalStore`），WS 懒建连/3s 重连/上限 500 条，设置页内部订阅，不再走 App props 管道（dfd46d3）。
7. **后端事件循环阻塞纪律**：所有路由都是 `async def`，同步阻塞调用一律 `asyncio.to_thread`——Ctrl+C 控制台注入（Win32 SendMessageTimeout，进程级 AttachConsole 用全局锁 `_ctrl_c_console_lock` 串行）、本机 PTY terminate（含 time.sleep）、编辑器文件全量读写（62686ca）。
8. **会话日志两不变量**（`session_log.py`，语义相反勿串台）：首次开启记录前，未开启期间的近期输出存 256KB 有界环并在开启时回放，**banner 必须入日志**；暂停后恢复则**丢弃暂停期输出**（不回放、只清环）。未开启记录时不做 pyte 镜像回放（62686ca 前的设计是无条件恒 feed，已改）。

## 四、验证基线

每次改动的验证流程（AGENTS.md 约定）：

```bash
ruff check --fix . && ruff format .   # lint + 格式化
pyright                               # 后端类型检查（或 pyrefly check）
pytest                                # 后端测试（串行跑）
npx tsc --noEmit && npx vitest run && npm run build   # 前端（frontend/ 下）
```

> 2026-09-19 复核与修复：
> - `tests/unit/test_logfile.py::test_feed_log_flush_threshold` 曾因「会话元信息头」含字母 x
>   使全文计数断言 +1 失败——已改为跳过元信息头后再计数（断言不再依赖头部文案）。
> - 复核时发现真实运行时 bug：`ws.py` 的 read_output 循环发送 `ssh_connected` 但**从未 import
>   `SERVER_SSH_CONNECTED`**（v0.1.62/6f6fc84 引入），NameError 被广泛兜底 `except: pass` 吞掉，
>   导致「本机 shell → 重连回 SSH」后输出转发循环静默退出、前端收不到 ssh_connected——
>   即"重连能进去但卡住/无提示符"的一个真实成因。已补 import 修复。教训：给 read_output
>   这类静默循环加消息时，务必确认名字已导入，最好加一条消息路径冒烟测试。
> - 当前基线：pytest 290 passed / 3 skipped，pyright 0 errors，tsc 0 errors，vitest 131 passed。
