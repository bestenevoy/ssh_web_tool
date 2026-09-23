/**
 * captureTypedLine 单测：从 mock 终端缓冲捕获"用户实际看到的输入行"
 * （Tab 补全命令历史识别主路径——不依赖 readline 重绘字节序与 PS1 形态）
 */
import { describe, expect, it } from 'vitest'
import { captureTypedLine, type BufferSource, type LineAnchor } from './lineEditor'

/** 造一个只有活动缓冲的假终端：lines[r] 为第 r 物理行文本，cursorY 为光标行 */
function fakeTerm(lines: string[], cursorY: number): BufferSource {
  return {
    buffer: {
      active: {
        cursorY,
        getLine(y: number) {
          if (y < 0 || y >= lines.length) return undefined
          return { translateToString: (_trim?: boolean) => lines[y] }
        },
      },
    },
  }
}

// 提示符 "root@host:~$ " 共 13 列；输入行位于第 3 物理行
const A: LineAnchor = { row: 3, col: 13 }

describe('captureTypedLine', () => {
  it('同行捕获：从锚点列截取到行尾（含补全后全文）', () => {
    const t = fakeTerm(['', '', '', 'root@host:~$ cd /var/log/nginx'], 3)
    expect(captureTypedLine(t, A)).toBe('cd /var/log/nginx')
  })

  it('行宽折行：续行按物理行顺序拼接（PTY 层无真实换行）', () => {
    const t = fakeTerm(['', '', '', 'root@host:~$ docker compose ps --format "{{.N', 'ame}} {{.Status}}"', ''], 4)
    expect(captureTypedLine(t, A)).toBe('docker compose ps --format "{{.Name}} {{.Status}}"')
  })

  it('光标在锚点之前（行被推挤/滚屏）：放弃捕获返回空串', () => {
    const t = fakeTerm(['x', 'root@host:~$ ls'], 1)
    expect(captureTypedLine(t, { row: 3, col: 13 })).toBe('')
  })

  it('距离超限（>20 行，锚点早已失效）：不捕获', () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line${i}`)
    const t = fakeTerm(lines, 29)
    expect(captureTypedLine(t, A)).toBe('')
  })

  it('光标行超出可得缓冲（getLine undefined）：截到可得行为止', () => {
    const t = fakeTerm(['', '', '', 'root@host:~$ ls -la'], 5)
    expect(captureTypedLine(t, A)).toBe('ls -la')
  })
})
