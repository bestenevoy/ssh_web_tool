/**
 * SFTP 传输任务列表：轮询后端 transfer_registry，展示上传/下载的
 * 进度条、速度、状态与取消按钮；完成/失败/取消的历史保留在下方。
 *
 * 轮询节奏：挂载期间每 1s 拉一次（面板 tab 激活或工作台打开时才挂载）。
 * 速度由前端按相邻两次采样的字节差计算，后端不存速率状态。
 */
import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { SftpTransfer } from '../lib/api'

const fmtBytes = (n: number): string => {
  if (n < 1024) return `${Math.round(n)} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`
}

interface Props {
  /** 非空时只显示该会话的传输；缺省显示全部会话 */
  sessionId?: string | null
}

export function TransferList({ sessionId }: Props) {
  const [transfers, setTransfers] = useState<SftpTransfer[]>([])
  // 速度采样：id → 上次 { done, t }；speeds 是本轮算出的 B/s
  const sampleRef = useRef<Map<string, { done: number; t: number }>>(new Map())
  const [speeds, setSpeeds] = useState<Record<string, number>>({})

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const r = await api.listTransfers(sessionId ?? null)
        if (!alive) return
        const now = Date.now()
        const next: Record<string, number> = {}
        for (const t of r.transfers) {
          if (t.status === 'running') {
            const prev = sampleRef.current.get(t.id)
            if (prev && now > prev.t && t.done >= prev.done) {
              next[t.id] = ((t.done - prev.done) * 1000) / (now - prev.t)
            }
            sampleRef.current.set(t.id, { done: t.done, t: now })
          } else {
            sampleRef.current.delete(t.id)
          }
        }
        setTransfers(r.transfers)
        setSpeeds(next)
      } catch {
        // 网络抖动/后端重启清空注册表：本轮跳过，下轮再试
      }
    }
    tick()
    const timer = setInterval(tick, 1000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [sessionId])

  const cancel = (id: string) => {
    api.cancelTransfer(id).catch(() => { /* 已结束/不存在由下轮轮询刷新 */ })
  }

  // 打开本机目录并选中下载文件；失败（文件被删/移动）提示后靠下轮轮询刷新状态
  const reveal = (id: string) => {
    api.revealTransfer(id).catch((e: unknown) => alert(`打开目录失败：${e instanceof Error ? e.message : e}`))
  }

  const running = transfers.filter((t) => t.status === 'running')
  const history = transfers.filter((t) => t.status !== 'running')

  const renderRow = (t: SftpTransfer) => {
    const pct = t.total > 0 ? Math.min(100, Math.round((t.done / t.total) * 100)) : null
    const sp = speeds[t.id]
    const statusText =
      t.status === 'done' ? '已完成'
      : t.status === 'canceled' ? '已取消'
      : t.status === 'failed' ? `失败${t.error ? `：${t.error}` : ''}`
      : ''
    // 本机文件路径才可能"打开目录"：download-to 落盘且已成功（浏览器下载 dst 是占位符）
    const revealable = t.direction === 'download' && t.status === 'done' && t.dst !== '浏览器下载'
    return (
      <div className={`tl-row tl-${t.status}`} key={t.id}>
        <span className={`tl-dir tl-${t.direction}`}>{t.direction === 'upload' ? '⬆' : '⬇'}</span>
        <div className="tl-main">
          <div className="tl-name" title={`${t.src} → ${t.dst}`}>{t.filename}</div>
          {t.host && (
            <div className="tl-host">{t.direction === 'upload' ? `上传到 ${t.host}` : `来自 ${t.host}`}</div>
          )}
          {t.status === 'running' && (
            <div className="tl-bar">
              <div className="tl-bar-fill" style={{ width: `${pct ?? 0}%` }} />
            </div>
          )}
          <div className="tl-meta">
            {fmtBytes(t.done)}
            {t.total > 0 ? ` / ${fmtBytes(t.total)}` : ''}
            {t.status === 'running' && pct !== null ? ` · ${pct}%` : ''}
            {t.status === 'running' && sp !== undefined ? ` · ${fmtBytes(sp)}/s` : ''}
            {statusText ? ` · ${statusText}` : ''}
          </div>
        </div>
        {t.status === 'running' && (
          <button className="tl-cancel" onClick={() => cancel(t.id)} title="取消传输（中断并清理半成品）">✕</button>
        )}
        {revealable && (
          <button className="tl-reveal" onClick={() => reveal(t.id)} title="打开本机目录并选中该文件">📂</button>
        )}
      </div>
    )
  }

  if (transfers.length === 0) {
    return <div className="tl-empty">暂无传输记录</div>
  }
  return (
    <div className="tl-wrap">
      {running.length > 0 && <div className="tl-section-title">进行中 ({running.length})</div>}
      {running.map(renderRow)}
      {history.length > 0 && <div className="tl-section-title">历史</div>}
      {history.map(renderRow)}
    </div>
  )
}
