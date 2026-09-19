/**
 * Shell / 终端特性档案
 *
 * 每种 shell 或终端环境都有一份结构化档案：名称、徽标、清屏/退出命令、
 * 特性与注意事项。用途：
 *  - tab / 会话列表悬停与 ⓘ 浮层标注当前会话的特性（防止误操作）
 *  - 「终端特性」弹窗展示全部档案（App 顶栏入口）
 *  - 后端开启会话记录时，把对应档案要点写入日志文件头（记录在案）
 *
 * 注意事项刻意写"防误操作"语义：清屏是否真清空、退出方式、临时会话性质、
 * 破坏性命令提醒等——每一条都对应一个真实容易踩的坑。
 */
import type { TerminalInstance } from './terminalInstance'

export interface ShellFeature {
  label: string
  desc: string
}

export interface ShellProfile {
  /** 档案 key（powershell / cmd / pwsh / ssh-remote / ssh-link / python / mysql / pager / other） */
  key: string
  /** 完整名称（弹窗/浮层/日志用） */
  label: string
  /** tab 徽标短文本（PS / CMD / pwsh / sh / py / sql / pg / ··） */
  badge: string
  badgeColor: string
  badgeBg: string
  /** 一句话说明 */
  summary: string
  /** 清屏命令（本工具语义：只上移视口、历史保留可滚动回看） */
  clearCmd: string
  /** 退出命令 */
  exitCmd: string
  /** 常用特性 */
  features: ShellFeature[]
  /** 注意事项 / 防误操作 */
  cautions: ShellFeature[]
}

const PS_COLOR = '#4a90d9'
const CMD_COLOR = '#5c6bc0'
const PWSH_COLOR = '#8e24aa'
const SH_COLOR = '#4caf50'
const SHLINK_COLOR = '#ff9800'
const PY_COLOR = '#ff9800'
const SQL_COLOR = '#2196f3'
const PG_COLOR = '#9c27b0'
const OTHER_COLOR = '#999'

/** 本地/远端共用的"清屏只上移"说明（本工具全局语义） */
const CLEAR_HINT = '本工具中 clear/cls/Ctrl+L 只是把窗口上移，历史保留，可向上滚动回看（不会真清空）'

export const SHELL_PROFILES: Record<string, ShellProfile> = {
  powershell: {
    key: 'powershell',
    label: 'PowerShell',
    badge: 'PS',
    badgeColor: PS_COLOR,
    badgeBg: 'rgba(74,144,217,0.15)',
    summary: '本机 PowerShell 5.1（Windows 自带）',
    clearCmd: `clear / cls / Ctrl+L —— ${CLEAR_HINT}`,
    exitCmd: 'exit',
    features: [
      { label: '历史命令', desc: '↑/↓ 召回上一条；Get-History 查看本次会话全部历史' },
      { label: '自动补全', desc: 'Tab 补全命令/路径（PSReadLine）；Ctrl+R 反向搜索历史' },
      { label: '大小写不敏感', desc: 'Get-ChildItem 与 get-childitem 等价' },
      { label: '常用别名', desc: 'ls=Get-ChildItem、cd=Set-Location、cat=Get-Content' },
      { label: '管道与变量', desc: '$var 存值；| 连接命令；; 分隔多条语句' },
    ],
    cautions: [
      { label: '清屏不清空', desc: CLEAR_HINT },
      { label: '退出方式', desc: 'exit 才会退出会话；直接点标签 ✕ 会结束会话' },
      { label: '粘贴即执行', desc: '粘贴多行内容会逐行立即执行，粘贴前先确认内容' },
      { label: '临时会话', desc: '若本会话由 ssh user@host 建立则属临时会话：主机列表单击不会切换过来，关闭后需重新输入 ssh 命令' },
    ],
  },
  pwsh: {
    key: 'pwsh',
    label: 'pwsh（PowerShell 7+）',
    badge: 'pwsh',
    badgeColor: PWSH_COLOR,
    badgeBg: 'rgba(142,36,170,0.15)',
    summary: '本机 PowerShell 7+（独立安装版）',
    clearCmd: `clear / cls / Ctrl+L —— ${CLEAR_HINT}`,
    exitCmd: 'exit / Ctrl+D',
    features: [
      { label: '历史命令', desc: '↑/↓ 召回；Get-History 查看；PSReadLine 预测提示' },
      { label: '自动补全', desc: 'Tab 补全命令/路径；Ctrl+R 反向搜索历史' },
      { label: '大小写不敏感', desc: '与 Windows PowerShell 一致' },
      { label: '跨平台', desc: '语法与 PowerShell 5 基本一致，额外支持部分新命令' },
    ],
    cautions: [
      { label: '清屏不清空', desc: CLEAR_HINT },
      { label: '退出方式', desc: 'exit 或 Ctrl+D 退出会话；误点标签 ✕ 会结束会话' },
      { label: '粘贴即执行', desc: '粘贴多行会立即逐行执行，粘贴前先确认' },
      { label: '临时会话', desc: '若由 ssh user@host 建立则属临时会话：主机列表单击不会切换过来' },
    ],
  },
  cmd: {
    key: 'cmd',
    label: 'CMD（命令提示符）',
    badge: 'CMD',
    badgeColor: CMD_COLOR,
    badgeBg: 'rgba(92,107,192,0.15)',
    summary: '本机 CMD（命令提示符）',
    clearCmd: `cls —— ${CLEAR_HINT}`,
    exitCmd: 'exit',
    features: [
      { label: '历史命令', desc: '↑/↓ 召回（doskey）；doskey /history 查看全部' },
      { label: '路径补全', desc: '默认 Tab 只补当前目录下的名称；无 PSReadLine 智能补全' },
      { label: '大小写不敏感', desc: 'dir、cd、copy 等命令与路径均不区分大小写' },
      { label: '变量', desc: '%var% 引用环境变量；set 查看/设置' },
    ],
    cautions: [
      { label: '清屏不清空', desc: CLEAR_HINT },
      { label: '退出方式', desc: 'exit 退出会话；误点标签 ✕ 会结束会话' },
      { label: '与 PowerShell 不同', desc: '没有 Get-ChildItem 等 cmdlet；路径分隔符用 \\；命令行为与 PS 有差异' },
      { label: '粘贴即执行', desc: '粘贴多行会逐行执行，粘贴前先确认' },
    ],
  },
  'ssh-remote': {
    key: 'ssh-remote',
    label: 'SSH 远程会话',
    badge: 'sh',
    badgeColor: SH_COLOR,
    badgeBg: 'rgba(76,175,80,0.15)',
    summary: '已保存主机的远程 shell，远端类型依服务器而定（常见 bash/sh/zsh）',
    clearCmd: `clear / Ctrl+L —— ${CLEAR_HINT}`,
    exitCmd: 'exit / Ctrl+D',
    features: [
      { label: '远端特性以服务器为准', desc: 'bash 常用 ↑/↓ 历史、Tab 补全、Ctrl+R 搜索；zsh 支持更丰富补全' },
      { label: '常用命令', desc: 'ls、cd、cat、grep、vim、top 等，路径/权限以远端账户为准' },
      { label: '远端环境', desc: '环境变量、PATH、别名按服务器配置生效' },
    ],
    cautions: [
      { label: '破坏性操作不可撤销', desc: '命令在远端服务器执行：rm、dd、mkfs、覆盖写文件等务必确认再回车' },
      { label: '清屏不清空', desc: CLEAR_HINT },
      { label: '断开自动切回本机', desc: '会话断开后自动切回本机终端，可点"重连"回到原主机' },
      { label: '密码不回显', desc: 'SSH 密码在连接时输入，不会出现在终端内容里' },
    ],
  },
  'ssh-link': {
    key: 'ssh-link',
    label: 'SSH 链接（临时）',
    badge: 'sh',
    badgeColor: SHLINK_COLOR,
    badgeBg: 'rgba(255,152,0,0.15)',
    summary: '由本地终端 ssh user:pass@ip:port 建立的临时会话',
    clearCmd: `clear / Ctrl+L —— ${CLEAR_HINT}`,
    exitCmd: 'exit / Ctrl+D',
    features: [
      { label: '连接方式', desc: '在本地终端输入 ssh 用户名:密码@主机:端口 回车后建立' },
      { label: '远端特性以服务器为准', desc: 'bash/sh/zsh 常见：↑/↓ 历史、Tab 补全、Ctrl+R 搜索' },
    ],
    cautions: [
      { label: '临时会话', desc: '不归属任何已保存主机：主机列表单击不会切换到此会话，只能在会话列表/标签上操作' },
      { label: '关闭即失联', desc: '关闭后需重新输入 ssh 命令重建；不会自动重连' },
      { label: '连接串含密码', desc: '若开启会话记录，明文连接信息会写入日志文件，注意保管日志' },
      { label: '破坏性操作不可撤销', desc: '远端命令在服务器上执行，rm 等操作谨慎确认' },
    ],
  },
  python: {
    key: 'python',
    label: 'Python REPL',
    badge: 'py',
    badgeColor: PY_COLOR,
    badgeBg: 'rgba(255,152,0,0.15)',
    summary: '当前处于 Python 交互解释器（>>> 提示符）',
    clearCmd: 'Ctrl+L（解释器清屏）',
    exitCmd: 'exit() 或 Ctrl+Z 回车',
    features: [
      { label: '交互执行', desc: '输入的是 Python 语句，直接回车立即求值' },
      { label: '内置帮助', desc: 'help()、dir()、type() 可查看对象信息' },
    ],
    cautions: [
      { label: '不是 shell', desc: 'shell 命令（ls/cd 等）会报错；需先用 exit() 回到 shell 再执行' },
    ],
  },
  mysql: {
    key: 'mysql',
    label: 'MySQL 客户端',
    badge: 'sql',
    badgeColor: SQL_COLOR,
    badgeBg: 'rgba(33,150,243,0.15)',
    summary: '当前处于 MySQL 命令行客户端（mysql> 提示符）',
    clearCmd: 'system clear / Ctrl+L',
    exitCmd: 'exit 或 \\q',
    features: [
      { label: 'SQL 语句', desc: '以 ; 结尾回车执行；\\G 结尾纵向显示结果' },
      { label: '客户端命令', desc: 'show databases; use db; show tables; desc table;' },
    ],
    cautions: [
      { label: '不是 shell', desc: 'shell 命令需用 system 前缀（如 system ls），或 \\! 执行' },
      { label: '破坏性 SQL', desc: 'DROP / DELETE / UPDATE 无 WHERE 会清库，执行前确认' },
    ],
  },
  pager: {
    key: 'pager',
    label: '分页器（less/more）',
    badge: 'pg',
    badgeColor: PG_COLOR,
    badgeBg: 'rgba(156,39,176,0.15)',
    summary: '当前处于 less/more 分页浏览',
    clearCmd: '—',
    exitCmd: 'q',
    features: [
      { label: '翻页', desc: '空格/PageDown 下翻，b/PageUp 上翻' },
      { label: '搜索', desc: '/ 关键词 向下搜索，n/N 跳转下一个/上一个' },
    ],
    cautions: [
      { label: 'q 退出', desc: '按 q 返回 shell；方向键/翻页操作不会退出分页器' },
    ],
  },
  other: {
    key: 'other',
    label: '终端环境',
    badge: '··',
    badgeColor: OTHER_COLOR,
    badgeBg: 'rgba(153,153,153,0.15)',
    summary: '当前环境类型未知，或处于其他交互程序',
    clearCmd: '—',
    exitCmd: '视程序而定（常见 Ctrl+C / q / exit）',
    features: [],
    cautions: [
      { label: '环境未知', desc: '请先确认当前处于什么程序，再输入命令，避免误操作' },
    ],
  },
}

/** 本地 shell 名 → 档案 key（默认 powershell） */
const LOCAL_SHELL_KEYS: Record<string, string> = {
  cmd: 'cmd',
  powershell: 'powershell',
  pwsh: 'pwsh',
}

/**
 * 从终端实例推导档案。
 * 优先级：特殊环境（python/mysql/pager）→ 临时 SSH 链接 → 远端会话 → 本地 shell 名。
 */
export function profileOf(
  t: Pick<TerminalInstance, 'type' | 'shell_type' | 'ssh_conn' | 'local_shell'>,
): ShellProfile {
  if (t.shell_type === 'python') return SHELL_PROFILES.python
  if (t.shell_type === 'mysql') return SHELL_PROFILES.mysql
  if (t.shell_type === 'pager') return SHELL_PROFILES.pager
  if (t.ssh_conn) return SHELL_PROFILES['ssh-link']
  if (t.type !== 'local') return SHELL_PROFILES['ssh-remote']
  const ls = (t.local_shell || '').toLowerCase().split('\\').pop() || ''
  return SHELL_PROFILES[LOCAL_SHELL_KEYS[ls] || 'powershell']
}

/** 档案要点文本（后端日志文件头用；纯文本、不含跳转/富文本） */
export function profileToLogLines(p: ShellProfile): string[] {
  const lines = [
    `Shell 类型: ${p.label}`,
    `说明: ${p.summary}`,
    `清屏: ${p.clearCmd}`,
    `退出: ${p.exitCmd}`,
  ]
  if (p.features.length > 0) {
    lines.push('特性:')
    p.features.forEach((f) => lines.push(`  - ${f.label}: ${f.desc}`))
  }
  if (p.cautions.length > 0) {
    lines.push('注意事项（防误操作）:')
    p.cautions.forEach((c) => lines.push(`  - ${c.label}: ${c.desc}`))
  }
  return lines
}
