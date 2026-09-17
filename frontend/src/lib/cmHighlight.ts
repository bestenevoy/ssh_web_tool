// CodeMirror 6 关键字高亮扩展：复用终端的 highlight_rules（highlight.ts 纯函数层）。
// 终端层把匹配画成 xterm 装饰；编辑器层把匹配画成 CM Decoration.mark（内联 color）。
// 匹配计算完全复用 compileHighlightRules + findMatches，保证两端行为一致。

import { ViewPlugin, Decoration } from '@codemirror/view'
import { RangeSetBuilder } from '@codemirror/state'
import type { Extension } from '@codemirror/state'
import type { DecorationSet, ViewUpdate } from '@codemirror/view'
import { compileHighlightRules, findMatches } from './highlight'
import type { HighlightRule, CompiledHighlightRule } from './highlight'

/** 对可见范围逐行做关键字匹配并生成 mark 装饰（编译后的规则经闭包注入） */
function buildHighlightDecorations(view: import('@codemirror/view').EditorView, compiled: CompiledHighlightRule[]): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>()
  const doc = view.state.doc
  for (const range of view.visibleRanges) {
    const fromLine = doc.lineAt(range.from).number
    const toLine = doc.lineAt(range.to).number
    for (let n = fromLine; n <= toLine; n++) {
      const line = doc.line(n)
      if (!line.text) continue
      for (const m of findMatches(line.text, compiled)) {
        builder.add(line.from + m.start, line.from + m.end, Decoration.mark({ attributes: { style: `color: ${m.color}` } }))
      }
    }
  }
  return builder.finish()
}

/**
 * 关键字高亮扩展。规则变化时调用方经 Compartment 用新规则重建整个扩展
 * （编译成本极低，重建比 facet 订阅简单直接）。
 */
export function cmHighlightExtension(rules: HighlightRule[]): Extension {
  const compiled = compileHighlightRules(rules)
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet
      constructor(view: import('@codemirror/view').EditorView) {
        this.decorations = buildHighlightDecorations(view, compiled)
      }
      update(u: ViewUpdate) {
        if (u.docChanged || u.viewportChanged) this.decorations = buildHighlightDecorations(u.view, compiled)
      }
    },
    { decorations: (v) => v.decorations }
  )
}
