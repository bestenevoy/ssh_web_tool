import { useState, useRef, useEffect, useCallback } from 'react'

interface Props {
  open: boolean
  groups: string[]
  onClose: () => void
  onAdd: (name: string) => void
  onRename: (oldName: string, newName: string) => void
  onDelete: (name: string) => void
  onDuplicate: (name: string) => void
  onReorder: (names: string[]) => void
}

export function GroupManager({ open, groups, onClose, onAdd, onRename, onDelete, onDuplicate, onReorder }: Props) {
  const [newGroupName, setNewGroupName] = useState('')
  const [editingGroup, setEditingGroup] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  // 拖拽排序
  const [dragIndex, setDragIndex] = useState<number | null>(null)
  const [overIndex, setOverIndex] = useState<number | null>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // ESC 关闭弹窗
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (editingGroup) {
          setEditingGroup(null)
          setEditValue('')
        } else {
          onClose()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [open, editingGroup, onClose])

  if (!open) return null

  const handleAdd = () => {
    if (newGroupName.trim()) {
      onAdd(newGroupName.trim())
      setNewGroupName('')
    }
  }

  const startEdit = (name: string) => {
    setEditingGroup(name)
    setEditValue(name)
  }

  const saveEdit = () => {
    if (editingGroup && editValue.trim() && editValue.trim() !== editingGroup) {
      onRename(editingGroup, editValue.trim())
    }
    setEditingGroup(null)
    setEditValue('')
  }

  // ---- 拖拽排序 ----
  const moveGroup = useCallback((from: number, to: number) => {
    if (from === to) return
    const next = [...groups]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    onReorder(next)
  }, [groups, onReorder])

  const handleDragStart = (idx: number) => {
    setDragIndex(idx)
  }

  const handleDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault()
    setOverIndex(idx)
  }

  const handleDrop = (idx: number) => {
    if (dragIndex !== null && dragIndex !== idx) {
      moveGroup(dragIndex, idx)
    }
    setDragIndex(null)
    setOverIndex(null)
  }

  const handleDragEnd = () => {
    setDragIndex(null)
    setOverIndex(null)
  }

  return (
    <div className="modal-overlay show" onMouseDown={(e) => e.stopPropagation()}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title-row">
          <h3>分组管理</h3>
          <button className="modal-close-btn" onClick={onClose} title="关闭 (ESC)">✕</button>
        </div>

        {/* 添加新分组 */}
        <div className="group-add-row">
          <input
            type="text"
            placeholder="输入新分组名称..."
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleAdd()
              if (e.key === 'Escape') setNewGroupName('')
            }}
            className="group-add-input"
          />
          <button className="btn btn-primary btn-sm" onClick={handleAdd}>添加</button>
        </div>

        {/* 分组列表 */}
        <div className="group-list" ref={listRef}>
          {groups.length === 0 && (
            <div className="group-empty">暂无分组，添加一个吧</div>
          )}
          {groups.map((g, idx) => (
            <div
              key={g}
              className={`group-item${dragIndex === idx ? ' dragging' : ''}${overIndex === idx && dragIndex !== null && dragIndex !== idx ? ' drag-over' : ''}`}
              draggable={editingGroup !== g}
              onDragStart={() => handleDragStart(idx)}
              onDragOver={(e) => handleDragOver(e, idx)}
              onDrop={() => handleDrop(idx)}
              onDragEnd={handleDragEnd}
            >
              {editingGroup === g ? (
                <>
                  <input
                    type="text"
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveEdit()
                      if (e.key === 'Escape') setEditingGroup(null)
                    }}
                    className="group-edit-input"
                    autoFocus
                  />
                  <button className="action-btn" onClick={saveEdit} title="保存">✓</button>
                  <button className="action-btn" onClick={() => setEditingGroup(null)} title="取消">✕</button>
                </>
              ) : (
                <>
                  <span className="group-drag-handle" title="拖拽排序">⠿</span>
                  <span className="group-name">📁 {g}</span>
                  <div className="group-actions">
                    <button className="action-btn" onClick={() => startEdit(g)} title="重命名">✎</button>
                    <button className="action-btn" onClick={() => onDuplicate(g)} title="复制分组（含组内主机）">📄</button>
                    <button
                      className="action-btn"
                      onClick={() => {
                        if (confirm(`确定删除分组"${g}"吗？组内主机将移到"未分组"。`)) {
                          onDelete(g)
                        }
                      }}
                      title="删除"
                    >🗑</button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>
        <div style={{ fontSize: 10, color: '#5a6a8a', marginTop: 6 }}>
          拖动 ⠿ 可调整分组顺序 · 复制分组会一并复制组内主机
        </div>

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>关闭 (ESC)</button>
        </div>
      </div>
    </div>
  )
}
