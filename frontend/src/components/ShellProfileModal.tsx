import { useEffect, useRef } from 'react'
import { SHELL_PROFILES } from '../lib/shellProfiles'

interface Props {
  onClose: () => void
}

/** 终端特性档案：全部 shell / 终端环境的特性与注意事项（App 顶栏「终端特性」入口） */
export function ShellProfileModal({ onClose }: Props) {
  const doneBtnRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    doneBtnRef.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const profiles = Object.values(SHELL_PROFILES)

  return (
    <div className="settings-overlay">
      <div className="settings-page shell-profile-page">
        <div className="settings-page-header">
          <span className="settings-page-title">📋 终端特性档案</span>
          <button
            ref={doneBtnRef}
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            title="返回主界面（Esc）"
          >
            ← 返回
          </button>
        </div>
        <div className="settings-page-content">
          <div className="sprof-intro">
            每种 shell / 终端环境都有自己的特点。以下档案标注了清屏、退出、常用特性与
            注意事项——尤其「清屏只上移不清空」是本工具的统一语义：clear / cls / Ctrl+L
            之后历史仍保留，可向上滚动回看。悬停标签可查看当前会话对应条目。
          </div>
          <div className="sprof-grid">
            {profiles.map((p) => (
              <div className="sprof-card" key={p.key}>
                <div className="sprof-card-head">
                  <span className="sprof-badge" style={{ color: p.badgeColor, background: p.badgeBg }}>{p.badge}</span>
                  <span className="sprof-name">{p.label}</span>
                </div>
                <div className="sprof-summary">{p.summary}</div>
                <div className="sprof-row"><b>清屏</b>{p.clearCmd}</div>
                <div className="sprof-row"><b>退出</b>{p.exitCmd}</div>
                {p.features.length > 0 && (
                  <div className="sprof-block">
                    <div className="sprof-block-title">特性</div>
                    {p.features.map((f) => (
                      <div className="sprof-item" key={f.label}><b>{f.label}</b>{f.desc}</div>
                    ))}
                  </div>
                )}
                {p.cautions.length > 0 && (
                  <div className="sprof-block sprof-caution">
                    <div className="sprof-block-title">注意事项（防误操作）</div>
                    {p.cautions.map((c) => (
                      <div className="sprof-item" key={c.label}><b>{c.label}</b>{c.desc}</div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
