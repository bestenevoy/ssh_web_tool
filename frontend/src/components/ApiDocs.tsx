const endpoints = [
  { method: 'POST', path: '/api/sessions', desc: '创建SSH会话（手动输入连接信息）' },
  { method: 'POST', path: '/api/sessions/from-host', desc: '从保存的主机创建SSH会话' },
  { method: 'GET', path: '/api/sessions', desc: '列出所有活动会话' },
  { method: 'GET', path: '/api/sessions/active', desc: '获取所有活跃终端（用于页面恢复）' },
  { method: 'DELETE', path: '/api/sessions/{id}', desc: '关闭并删除会话' },
  { method: 'POST', path: '/api/sessions/{id}/run', desc: '在会话中执行命令（AI调用）' },
  { method: 'GET', path: '/api/hosts', desc: '获取所有保存的主机（含连接状态）' },
  { method: 'POST', path: '/api/hosts', desc: '新增主机' },
  { method: 'PUT', path: '/api/hosts/{id}', desc: '更新主机' },
  { method: 'DELETE', path: '/api/hosts/{id}', desc: '删除主机' },
  { method: 'GET', path: '/api/quick-commands', desc: '获取快速指令列表' },
  { method: 'POST', path: '/api/quick-commands', desc: '新增快速指令' },
  { method: 'DELETE', path: '/api/quick-commands/{id}', desc: '删除快速指令' },
  { method: 'POST', path: '/api/sftp/{id}/list', desc: 'SFTP列出目录' },
  { method: 'GET', path: '/api/sftp/{id}/download', desc: 'SFTP下载文件' },
  { method: 'POST', path: '/api/sftp/{id}/write', desc: 'SFTP上传/写入文件' },
  { method: 'POST', path: '/api/sftp/{id}/delete', desc: 'SFTP删除文件' },
  { method: 'WS', path: '/ws/ssh/{id}', desc: 'WebSocket交互式终端' },
  { method: 'WS', path: '/ws/events', desc: 'WebSocket事件订阅（观察操作过程）' },
]

export function ApiDocs() {
  return (
    <div>
      <div style={{ fontSize: 11, color: '#6b7a99', marginBottom: 10 }}>
        AI/Python可通过以下HTTP API调用SSH
      </div>
      {endpoints.map((ep, i) => {
        const cls = ep.method === 'GET' ? 'get' : ep.method === 'POST' ? 'post' : ep.method === 'DELETE' ? 'delete' : ''
        return (
          <div key={i} className="api-endpoint">
            <span className={`method ${cls}`}>{ep.method}</span>
            <span className="path">{ep.path}</span>
            <div className="desc">{ep.desc}</div>
          </div>
        )
      })}
    </div>
  )
}
