// CodeMirror 6 的 .zs 脚本装饰：块首行 gutter ▶️ 播放按钮 + 错误行标红。
// 解析/校验逻辑在 zsScript.ts（纯函数），本文件只负责把它们画出来并接线播放。

import { ViewPlugin, Decoration, gutter, GutterMarker } from '@codemirror/view'
import { RangeSetBuilder } from '@codemirror/state'
import type { Extension } from '@codemirror/state'
import type { DecorationSet, ViewUpdate, EditorView } from '@codemirror/view'
import { parseZs, validateZs } from './zsScript'
import type { ZsBlock } from './zsScript'
import type { QuickCommand } from '../types'

export interface ZsConfig {
  quickCommands: QuickCommand[]
  /** 点击块首 ▶️：播放该块（FileEditor 提供最新选择与取消句柄） */
  onPlay: (block: ZsBlock) => void
}

class PlayMarker extends GutterMarker {
  private block: ZsBlock
  private onPlay: (block: ZsBlock) => void

  constructor(block: ZsBlock, onPlay: (block: ZsBlock) => void) {
    super()
    this.block = block
    this.onPlay = onPlay
  }
  override toDOM(): HTMLElement {
    const el = document.createElement('button')
    el.className = 'cm-zs-play'
    el.textContent = '▶'
    el.title = `播放该块${this.block.name ? `「${this.block.name}」` : ''}（广播到选中会话）`
    // 阻止 CM 把 mousedown 当成光标操作/选区
    el.addEventListener('mousedown', (e) => e.preventDefault())
    el.addEventListener('click', (e) => {
      e.stopPropagation()
      this.onPlay(this.block)
    })
    return el
  }
}

/** .zs 装饰插件类工厂：cfg 经闭包注入。每次文档变化重新解析（.zs 文件通常很小） */
function createZsPlugin(cfg: ZsConfig) {
  return class {
    decorations: DecorationSet
    /** 块首行（0 起始）→ 块，gutter lineMarker 查表用 */
    blockStarts: Map<number, ZsBlock> = new Map()

    constructor(view: EditorView) {
      this.decorations = this.build(view)
    }

    update(u: ViewUpdate) {
      if (u.docChanged || u.viewportChanged) this.decorations = this.build(u.view)
    }

    markerFor(lineNumber: number): PlayMarker | null {
      const block = this.blockStarts.get(lineNumber - 1) // CM 行号 1 起始
      return block ? new PlayMarker(block, cfg.onPlay) : null
    }

    build(view: EditorView): DecorationSet {
      const doc = view.state.doc
      const content = doc.toString()
      const file = parseZs(content)
      this.blockStarts = new Map(file.blocks.map((b) => [b.startLine, b]))

      const builder = new RangeSetBuilder<Decoration>()
      // 错误行标红（validateZs 按 0 起始行号升序返回，满足 RangeSetBuilder 的有序要求）
      const errors = validateZs(content, cfg.quickCommands)
      for (const err of errors) {
        const line = doc.line(err.line + 1)
        builder.add(line.from, line.to, Decoration.mark({ class: 'cm-zs-err', attributes: { title: err.message } }))
      }
      return builder.finish()
    }
  }
}

/** .zs 扩展集合：错误标红插件 + 块首 gutter ▶️ */
export function zsExtensions(cfg: ZsConfig): Extension[] {
  const Plugin = createZsPlugin(cfg)
  const plugin = ViewPlugin.fromClass(Plugin, { decorations: (v) => v.decorations })
  const zsGutter = gutter({
    class: 'cm-zs-gutter',
    lineMarker(view: EditorView, line) {
      const p = view.plugin(plugin)
      if (!p) return null
      return p.markerFor(view.state.doc.lineAt(line.from).number)
    },
  })
  return [plugin, zsGutter]
}
