# 前端开发指南

## 项目位置

```
ssh-web-tool/frontend/
```

## 开发模式（热更新，推荐）

```bash
# 终端1：启动后端
cd ssh-web-tool
python main.py

# 终端2：启动前端开发服务器
cd ssh-web-tool/frontend
npm install   # 首次需要
npm run dev
```

浏览器打开 **http://localhost:5173**

- 修改 React/TS 代码保存后页面自动刷新
- `/api` 和 `/ws` 请求自动代理到后端 8765
- 不需要编译
- 注意：pywebview 桌面壳只在进程启动时加载页面，后端改动要**完全重启**才生效

## 生产构建（单文件）

```bash
cd ssh-web-tool/frontend
npm run build
```

输出到 `../static/index.html`（单个文件，git 跟踪）。构建配置了
`vite-plugin-singlefile`，所有 JS/CSS 内联到 HTML 中。

**改前端源码后必须重建 `static/index.html` 并提交**，否则后端提供的仍是旧版。

## 验证流程（提交前）

```bash
cd frontend
npx tsc --noEmit    # 类型检查
npx vitest run      # 单元测试（lib 下 *.test.ts）
npm run build       # 重建 static/index.html
```

## 前端技术栈

- React 19 + TypeScript（项目约定尽量不用 any）
- Vite 构建工具 + vitest 单测
- xterm.js + @xterm/addon-fit 终端组件（渲染器仅内置 DomRenderer；
  webgl addon 处于 beta，未引入——大量输出时渲染是已知上限）
- CodeMirror 6 文件编辑器（zs 脚本语法高亮/折叠，见 lib/cmZs、folds）
- 无 UI 组件库，纯 CSS 样式（CSS 变量主题，暗色/亮色；
  `--accent` 主色与 `--danger` 警示色分离；xterm 颜色需与 CSS 手动同步）
- UI 控件风格约定：拒绝原生外观（ghost 按钮/自绘下拉/聚焦光环）；
  弹窗只能通过关闭按钮（「完成」「✕」）关闭，禁止点遮罩关闭（防误触丢表单）

## 关键设计点

1. **状态点单一判定源**：tab、会话列表的连接状态统一走
   `lib/terminalDisconnect.sessionConnState`（`disconnected || ssh_exited` 都算
   断开——SSH 断开会自动切回本机 shell，终端可用但连接态是断开）。配色共用：
   绿=已连接、红（--danger）=断开、橙+闪烁=连接中。
2. **命名口径**：会话列表条目 = 「链接类型 · ip:port」（默认 ssh；本机 =
   `local · 名称`），禁止把 `user:pass` 明文画上界面（含 tooltip）。
3. **提示信息只走 toast**：顶栏不承载任何提示信息，统一走终端区上方多条堆叠
   toast（逐条 3.2s 消失）。
4. **终端 resize**：容器挂 ResizeObserver 重 fit（pywebview 最大化/还原不一定
   派发 window.resize，RO 是唯一可靠信号）。注意防渲染风暴：observe 必触发
   一次，回调用 ref 读最新实例、元素未变不重建观察器、尺寸基线不变不 fit；
   行内 `ref={(el)=>...}` 链路挂资源必须保证回调身份稳定。
5. **活动日志不进 React 渲染路径**：`lib/useEvents.ts` 是模块级外部 store
   （useSyncExternalStore 订阅），App 状态变更不重放事件列表；WS 懒建连、
   断开 3s 重连、上限 500 条。
6. **大量输出喂入**：`lib/outputFeeder.ts` 有界（2MB）分块喂 xterm，
   历史回放不冻结主线程。
7. **未上屏会话不卸载**：分屏/隐藏会话保持挂载仅 `display:none`，
   卸载重建会导致 xterm 实例与 DOM 脱节。

## 前端目录结构

```
frontend/src/
├── components/
│   ├── HostList.tsx           # 主机列表（分组、状态、拖拽排序、计数徽标）
│   ├── HostModal.tsx          # 主机新增/编辑弹窗
│   ├── TerminalWindow.tsx     # 终端标签栏（tab + 状态点）
│   ├── TerminalView.tsx       # 终端视图（xterm 实例上屏、分屏窗格）
│   ├── SessionPanel.tsx       # 工具面板：会话列表 tab（按主机聚合）
│   ├── QuickCommands.tsx      # 快捷指令面板
│   ├── QuickCommandModal.tsx  # 快捷指令添加/编辑弹窗
│   ├── HistorySearchModal.tsx # 历史命令搜索弹窗（Alt+R）
│   ├── SearchBar.tsx          # 终端区筛选栏
│   ├── SftpPanel.tsx          # SFTP 文件管理
│   ├── SftpWorkbench.tsx      # SFTP 工作台（本地脚本推送远端执行）
│   ├── TransferList.tsx       # SFTP 传输任务列表（进度/取消/打开目录）
│   ├── FileEditor.tsx         # CodeMirror 文件编辑器
│   ├── FileOpenModal.tsx      # 打开文件弹窗（目录浏览）
│   ├── DirPickerModal.tsx     # 日志保存目录选择弹窗
│   ├── SessionSelectModal.tsx # 会话选择弹窗
│   ├── PasswordModal.tsx      # 密码输入弹窗
│   ├── GroupManager.tsx       # 分组/类型管理
│   ├── ContextMenu.tsx        # 右键菜单
│   ├── EventLog.tsx           # 活动日志（设置页内嵌）
│   └── SettingsModal.tsx      # 设置页（主题/字体/日志/快捷键/活动日志）
├── lib/
│   ├── api.ts                 # HTTP API 调用封装
│   ├── useTerminals.ts        # 终端状态管理 hook（WebSocket/xterm/fit/RO）
│   ├── useEvents.ts           # 活动日志：模块级 store + useEventList()
│   ├── useSettings.ts         # 设置管理 hook（主题/字体/字号）
│   ├── terminalDisconnect.ts  # 断开处理 + sessionConnState 单一判定源
│   ├── terminalInstance.ts    # TerminalInstance 类型（含 resizeObserver 等）
│   ├── terminalCopy.ts        # 终端复制选区处理
│   ├── outputFeeder.ts        # 有界分块喂 xterm（历史回放）
│   ├── blockBar.ts            # 命令块色块栏（侧边导航条）
│   ├── commandBlocks.ts       # 命令块切分
│   ├── promptPatterns.ts      # 提示符识别（与后端 echo_parser 对应）
│   ├── highlight.ts / cmHighlight.ts / highlightDecorations.ts  # 高亮
│   ├── cmZs.ts / zsScript.ts  # zs 脚本语言（CodeMirror 语法/执行）
│   ├── folds.ts / lineEditor.ts / reflow.ts  # 编辑器折叠/行编辑/重排
│   ├── sessionOrder.ts        # 会话列表排序
│   └── shellProfiles.ts       # 终端/Shell 特性档案（徽标、说明）
├── types/                     # TypeScript 类型定义（ws.ts 与后端协议同步）
├── App.tsx                    # 主应用
├── main.tsx                   # 入口
└── index.css                  # 全局样式（CSS 变量主题）
```

> 注：外部会话面板（跨进程观测）为独立原生 JS（`static/external-panel.js`），
> 由 `index.html` 直接引用，不经过 React 构建。
