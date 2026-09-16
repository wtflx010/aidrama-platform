/**
 * MarkdownRenderer —— 对话内容 Markdown 渲染（对齐 Trae Work 展示格式，2026-08-11）
 *
 * - markdown-it 渲染为 HTML（linkify 自动识别链接、breaks 单换行转 <br>）
 * - html: false 不渲染原始 HTML，防注入
 * - 样式由 index.css 中的 .md-body 纯 CSS 统一定义
 *   （Tailwind 无法扫描 dangerouslySetInnerHTML 内的类名）
 * - highlight 参数：会话历史搜索命中时，将关键词文本用 <mark> 标亮
 */
import { useMemo } from 'react'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

export default function MarkdownRenderer({
  content,
  highlight,
}: {
  content: string
  highlight?: string
}) {
  const html = useMemo(() => {
    let h = md.render(content ?? '')
    const kw = highlight?.trim()
    if (kw) {
      // 关键词可能含正则特殊字符 → 转义；匹配后包 <mark> 高亮（大小写不敏感）
      const esc = kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      h = h.replace(new RegExp(esc, 'gi'), (m) => `<mark class="bg-amber-200/90 text-inherit rounded-[3px] px-0.5">${m}</mark>`)
    }
    return h
  }, [content, highlight])
  return <div className="md-body" dangerouslySetInnerHTML={{ __html: html }} />
}
