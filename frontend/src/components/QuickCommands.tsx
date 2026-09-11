import { useState } from 'react'
import type { QuickCommand } from '../types'

interface Props {
  commands: QuickCommand[]
  onExecute: (cmd: QuickCommand) => void  // 直接执行（direct 类型）
  onEditExecute: (cmd: QuickCommand) => void  // 输入到终端（可编辑后手动执行）
  onEdit: (cmd: QuickCommand) => void  // 点击编辑按钮：打开编辑弹窗
  onDelete: (id: string) => void
  onOpenAddModal: () => void
  onReorder: (ids: string[]) => void  // 拖拽排序后保存
  disabled: boolean
}

const PRE_OP_ICONS: Record<string, string> = {
  upload: '📤',
  chmod: '🔧',
  env: '🌱',
}

export function QuickCommands({ commands, onExecute, onEditExecute, onEdit, onDelete, onOpenAddModal, onReorder, disabled }: Props) {
  const [dragId, setDragId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)

  // ---- 拖拽排序（与主机/分组一致）----
  const moveQc = (fromId: string, toId: string) => {
    if (fromId === toId) return
    const list = [...commands]
    const fromIdx = list.findIndex((q) => q.id === fromId)
    const toIdx = list.findIndex((q) => q.id === toId)
    if (fromIdx < 0 || toIdx < 0) return
    const [item] = list.splice(fromIdx, 1)
    list.splice(toIdx, 0, item)
    onReorder(list.map((q) => q.id))
  }

  const handleDragStart = (qc: QuickCommand) => setDragId(qc.id)
  const handleDragOver = (e: React.DragEvent, qc: QuickCommand) => {
    e.preventDefault()
    if (dragId && dragId !== qc.id) setOverId(qc.id)
  }
  const handleDrop = (qc: QuickCommand) => {
    if (dragId) moveQc(dragId, qc.id)
    setDragId(null)
    setOverId(null)
  }
  const handleDragEnd = () => {
    setDragId(null)
    setOverId(null)
  }

  return (
    <div className="qc-container">
      <div className="qc-header">
        <span className="qc-header-title">快捷指令</span>
        <button
          className="qc-add-btn"
          onClick={onOpenAddModal}
          title="添加快捷指令"
        >
          + 添加
        </button>
      </div>

      <div className="qc-list">
        {commands.length === 0 && (
          <div style={{ textAlign: 'center', color: '#5a6a8a', padding: '20px 10px', fontSize: 11 }}>
            暂无快捷指令，点击右上角"添加"创建
          </div>
        )}

        {commands.map((qc) => {
          const isParam = qc.type === 'param'
          return (
            <div
              key={qc.id}
              className={`qc-item${dragId === qc.id ? ' dragging' : ''}${overId === qc.id ? ' over' : ''}`}
              draggable={!disabled}
              onDragStart={() => handleDragStart(qc)}
              onDragOver={(e) => handleDragOver(e, qc)}
              onDrop={() => handleDrop(qc)}
              onDragEnd={handleDragEnd}
              style={{ opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'grab' }}
              title={isParam
                ? '带参数指令：点击输入到终端（含 {args} 占位），编辑后回车执行；按住可拖拽排序'
                : '点击立即执行；按住可拖拽排序'}
              onClick={() => {
                if (disabled) return
                if (isParam) onEditExecute(qc)  // 带参数指令：输入到终端，用户自行编辑 {args} 后执行
                else onExecute(qc)
              }}
            >
              <div className="qc-item-header">
                <div className="qc-name" title={qc.command ? `${qc.name}\n${qc.command}` : qc.name}>
                  <span className={`qc-type-badge ${isParam ? 'param' : 'direct'}`} title={isParam ? '带参数' : '直接执行'}>
                    {isParam ? '⌨️' : '⚡'}
                  </span>
                  {qc.name}
                  {(qc.pre_ops?.length || 0) > 0 && (
                    <span className="qc-preop-badge" title={qc.pre_ops!.map((o) => `预操作: ${o.type}`).join('，')}>
                      {qc.pre_ops!.map((o) => PRE_OP_ICONS[o.type] || '').join('')}
                    </span>
                  )}
                </div>
                <div className="qc-item-actions" onClick={(e) => e.stopPropagation()}>
                  <button
                    className="qc-icon-btn qc-icon-exec"
                    onClick={() => !disabled && onEditExecute(qc)}
                    disabled={disabled}
                    title="编辑后执行（输入到终端，可编辑）"
                  >
                    ▶
                  </button>
                  <button
                    className="qc-icon-btn qc-icon-edit"
                    onClick={() => onEdit(qc)}
                    title="编辑指令"
                  >
                    ✎
                  </button>
                  <button
                    className="qc-icon-btn qc-icon-delete"
                    onClick={() => onDelete(qc.id)}
                    title="删除"
                  >
                    ✕
                  </button>
                </div>
              </div>
              {qc.description && (
                <div className="qc-description" title={qc.description}>{qc.description}</div>
              )}
            </div>
          )
        })}

        {disabled && commands.length > 0 && (
          <div style={{ textAlign: 'center', color: '#666', padding: '10px', fontSize: 11 }}>
            请先连接 SSH 终端
          </div>
        )}
      </div>
    </div>
  )
}
