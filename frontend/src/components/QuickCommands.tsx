import type { QuickCommand } from '../types'

interface Props {
  commands: QuickCommand[]
  onExecute: (cmd: QuickCommand) => void  // 点击 item：立即执行
  onEditExecute: (cmd: QuickCommand) => void  // 点击播放按钮：编辑后执行（输入到终端，可编辑）
  onEdit: (cmd: QuickCommand) => void  // 点击编辑按钮：打开编辑弹窗
  onDelete: (id: string) => void
  onOpenAddModal: () => void
  disabled: boolean
}

export function QuickCommands({ commands, onExecute, onEditExecute, onEdit, onDelete, onOpenAddModal, disabled }: Props) {
  return (
    <div>
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

      {commands.length === 0 && (
        <div style={{ textAlign: 'center', color: '#5a6a8a', padding: '20px 10px', fontSize: 11 }}>
          暂无快捷指令，点击右上角"添加"创建
        </div>
      )}

      {commands.map((qc) => (
        <div
          key={qc.id}
          className="qc-item"
          style={{ opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}
          onClick={() => !disabled && onExecute(qc)}
        >
          <div className="qc-item-header">
            <div className="qc-name" title={qc.name}>{qc.name}</div>
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
          <div className="qc-cmd" title={qc.command}>{qc.command}</div>
        </div>
      ))}

      {disabled && commands.length > 0 && (
        <div style={{ textAlign: 'center', color: '#666', padding: '10px', fontSize: 11 }}>
          请先连接 SSH 终端
        </div>
      )}
    </div>
  )
}
