import type { EventLogItem } from '../types'

interface Props {
  events: EventLogItem[]
  onClear: () => void
}

export function EventLog({ events, onClear }: Props) {
  return (
    <div>
      <div className="event-log-header">
        <span className="count">共 {events.length} 条事件</span>
        <button className="clear-btn" onClick={onClear}>清空</button>
      </div>
      {events.length === 0 ? (
        <div style={{ textAlign: 'center', color: '#555', padding: '30px 10px', fontSize: 11 }}>
          暂无事件<br />CLI/SDK/前端操作会实时显示在这里
        </div>
      ) : (
        events.slice(0, 100).map((ev, i) => (
          <div key={i} className={`event-item ${ev.event_type}`}>
            <span className="event-time">{ev.time_str}</span>
            <span className={`event-source ${ev.source}`}>{ev.source.toUpperCase()}</span>
            <span className="event-detail">{ev.detail}</span>
          </div>
        ))
      )}
      {events.length > 100 && (
        <div style={{ textAlign: 'center', color: '#555', padding: 8, fontSize: 10 }}>
          仅显示最近100条，共{events.length}条
        </div>
      )}
    </div>
  )
}
