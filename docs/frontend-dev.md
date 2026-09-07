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

## 生产构建（单文件）

```bash
cd ssh-web-tool/frontend
npm run build
```

输出到 `../static/index.html`（单个文件，约 560KB，gzip 后约 141KB）。

构建配置了 `vite-plugin-singlefile`，所有 JS/CSS 内联到 HTML 中。构建后访问 **http://127.0.0.1:8765** 即可看到最新版本。

## 前端技术栈

- React 19 + TypeScript
- Vite 构建工具
- xterm.js + @xterm/addon-fit 终端组件
- 无 UI 组件库，纯 CSS 样式（暗色/亮色主题）

## 前端目录结构

```
frontend/src/
├── components/              # React 组件
│   ├── HostList.tsx         # 主机列表（分组、状态、操作）
│   ├── TerminalTabs.tsx     # 终端标签栏
│   ├── TerminalView.tsx     # 终端视图（xterm 实例管理）
│   ├── QuickCommands.tsx    # 快捷指令面板
│   ├── QuickCommandModal.tsx# 快捷指令添加/编辑弹窗
│   ├── HistorySearchModal.tsx# 历史命令搜索弹窗（Alt+R）
│   ├── SftpPanel.tsx        # SFTP 文件管理
│   ├── EventLog.tsx         # 活动日志
│   ├── SettingsBar.tsx      # 顶部设置栏（主题/字体/字号）
│   └── HostModal.tsx        # 主机新增/编辑弹窗
├── lib/
│   ├── api.ts               # HTTP API 调用封装
│   ├── useTerminals.ts      # 终端状态管理 hook（WebSocket/xterm）
│   ├── useEvents.ts         # 事件订阅 hook
│   └── useSettings.ts       # 设置管理 hook（主题/字体/字号）
├── types/index.ts           # TypeScript 类型定义
├── App.tsx                  # 主应用
├── App.css                  # 历史搜索弹窗样式
├── main.tsx                 # 入口
└── index.css                # 全局样式（CSS 变量主题）
```

> 注：外部会话面板（跨进程观测）为独立原生 JS（`static/external-panel.js`），由 `index.html` 直接引用，不经过 React 构建。
