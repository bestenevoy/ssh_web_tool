/**
 * 隐形字符防御（纯函数，可单测）
 *
 * 背景：从网页/聊天工具/文档复制的命令与文件名常混入 Unicode 隐形字符——
 * 不间断空格（NBSP）、零宽空格、BOM 等。它们显示上与正常文本无异，但 shell
 * 把 NBSP 当作单词的一部分，于是出现「看着一样的命令效果不同」「创建的文件名
 * 里混入不该有的字符」。本模块提供两类处理：
 * - normalizeInvisible：把隐形/异形空白字符清理为正常形式（执行与入库前调用）
 * - scanHiddenChars：检出问题字符并给出人类可读清单（弹窗提示用，只报不改）
 */

// 零宽与不可见格式字符码点：直接删除
const ZERO_WIDTH_CPS = new Set([0x200b, 0x200c, 0x200d, 0x2060, 0x00ad, 0xfeff])
// 各类非 ASCII 空白码点（NBSP/全角空格等，\u2000-\u200a 一族另行按区间判）：
// 替换为普通空格。注意不含 \r \n \t（合法控制字符，由其他路径处理）
const ODD_SPACE_CPS = new Set([0x00a0, 0x1680, 0x202f, 0x205f, 0x3000])
// 弯引号/破折号/间隔号等形近标点：仅提示不替换
const LOOKALIKE_CPS = new Set([0x2018, 0x2019, 0x201c, 0x201d, 0x2013, 0x2014, 0x00b7])

const CP_NAMES: Record<number, string> = {
  0x00a0: '不间断空格(U+00A0)',
  0x1680: '欧根空格(U+1680)',
  0x202f: '窄不间断空格(U+202F)',
  0x205f: '中等数学空格(U+205F)',
  0x3000: '全角空格(U+3000)',
  0x200b: '零宽空格(U+200B)',
  0x200c: '零宽不连字(U+200C)',
  0x200d: '零宽连接符(U+200D)',
  0x2060: '单词连接符(U+2060)',
  0x00ad: '软连字符(U+00AD)',
  0xfeff: 'BOM/零宽签(U+FEFF)',
}

function hex(cp: number): string {
  return `U+${cp.toString(16).toUpperCase().padStart(4, '0')}`
}

/**
 * 清理隐形字符：零宽类删除、非 ASCII 空白替换为普通空格。
 * 幂等：对正常文本是恒等变换，可安全用在任何发送/入库路径上。
 */
export function normalizeInvisible(text: string): string {
  return text.replace(/[\u200b\u200c\u200d\u2060\u00ad\ufeff]/g, '').replace(/[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]/g, ' ')
}

/** text 是否会被 normalizeInvisible 改变（混有隐形/异形空白字符） */
export function hasInvisible(text: string): boolean {
  return normalizeInvisible(text) !== text
}

/**
 * 检出问题字符，返回人类可读清单（如「零宽空格(U+200B)×2」），无问题返回空数组。
 * includeLookalikes=true 时附带全角/形近标点统计（仅提示，normalize 不处理它们）。
 */
export function scanHiddenChars(text: string, includeLookalikes = false): string[] {
  const counts = new Map<string, number>()
  let lookalike = 0
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0
    let isHidden = ZERO_WIDTH_CPS.has(cp) || ODD_SPACE_CPS.has(cp)
    if (!isHidden && cp >= 0x2000 && cp <= 0x200a) isHidden = true // en-space 一族
    if (isHidden) {
      const name = CP_NAMES[cp] ?? `排版空格(${hex(cp)})`
      counts.set(name, (counts.get(name) ?? 0) + 1)
    } else if (includeLookalikes && ((cp >= 0xff01 && cp <= 0xff5e) || LOOKALIKE_CPS.has(cp))) {
      // 全角 ASCII 变体（！＂…～）与弯引号/破折号：显示近似半角但 shell 语义不同，
      // 不自动替换（中文文件名/引号内文本可能合法），仅提示确认
      lookalike++
    }
  }
  const out = [...counts.entries()].map(([name, n]) => `${name}×${n}`)
  if (includeLookalikes && lookalike > 0) {
    out.push(`全角/形近标点×${lookalike}（未自动替换，请确认是否有意输入）`)
  }
  return out
}
