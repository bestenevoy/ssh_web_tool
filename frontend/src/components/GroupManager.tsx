import { useState } from 'react'

interface Props {
  open: boolean
  groups: string[]
  onClose: () => void
  onAdd: (name: string) => void
  onRename: (oldName: string, newName: string) => void
  onDelete: (name: string) => void
}

export function GroupManager({ open, groups, onClose, onAdd, onRename, onDelete }: Props) {
  const [newGroupName, setNewGroupName] = useState('')
  const [editingGroup, setEditingGroup] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')

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

  return (
    <div className="modal-overlay show" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>分组管理</h3>

        {/* 添加新分组 */}
        <div className="group-add-row">
          <input
            type="text"
            placeholder="输入新分组名称..."
            value={newGroupName}
            onChange={(e) => setNewGroupName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            className="group-add-input"
          />
          <button className="btn btn-primary btn-sm" onClick={handleAdd}>添加</button>
        </div>

        {/* 分组列表 */}
        <div className="group-list">
          {groups.length === 0 && (
            <div className="group-empty">暂无分组，添加一个吧</div>
          )}
          {groups.map((g) => (
            <div key={g} className="group-item">
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
                  <span className="group-name">📁 {g}</span>
                  <div className="group-actions">
                    <button className="action-btn" onClick={() => startEdit(g)} title="重命名">✎</button>
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

        <div className="modal-actions">
          <button className="btn btn-secondary" onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  )
}
