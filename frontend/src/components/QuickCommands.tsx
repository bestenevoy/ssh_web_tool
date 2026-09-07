import type { QuickCommand } from '../types'

interface Props {
  commands: QuickCommand[]
  onExecute: (cmd: QuickCommand) => void  // 直接执行（direct 类型）
  onEditExecute: (cmd: QuickCommand) => void  // 输入到终端（可编辑后手动执行）
  onEdit: (cmd: QuickCommand) => void  // 点击编辑按钮：打开编辑弹窗
  onDelete: (id: string) => void
  onOpenAddModal: () => void
  disabled: boolean
}

const PRE_OP_ICONS: Record<string, string> = {
  upload: '📤',
  chmod: '🔧',
  env: '🌱',
}

export function QuickCommands({ commands, onExecute, onEditExecute, onEdit, onDelete, onOpenAddModal, disabled }: Props) {
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
              className="qc-item"
              style={{ opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}
              onClick={() => {
                if (disabled) return
                if (isParam) onEditExecute(qc)  // 带参数指令：输入到终端，用户自行编辑 {args} 后执行
                else onExecute(qc)
              }}
              title={isParam ? '带参数指令：点击输入到终端（含 {args} 占位），编辑后回车执行' : '点击立即执行'}
            >
              <div className="qc-item-header">
                <div className="qc-name" title={qc.name}>
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
              <div className="qc-cmd" title={qc.command}>{qc.command}</div>
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
