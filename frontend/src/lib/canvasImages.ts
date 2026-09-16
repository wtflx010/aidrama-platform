/** M0 原型用的纯本地 SVG 占位图(data URI),不依赖网络与后端 */

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

export interface PlaceholderOpts {
  w: number
  h: number
  hue: number
  label: string
  sub?: string
  dark?: boolean
}

export function svgPlaceholder({ w, h, hue, label, sub, dark }: PlaceholderOpts): string {
  const c1 = `hsl(${hue}, 70%, ${dark ? '16%' : '38%'})`
  const c2 = `hsl(${(hue + 40) % 360}, 74%, ${dark ? '28%' : '56%'})`
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">` +
    `<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">` +
    `<stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/>` +
    `</linearGradient></defs>` +
    `<rect width="${w}" height="${h}" rx="6" fill="url(#g)"/>` +
    `<text x="${w / 2}" y="${h / 2 - 4}" font-family="system-ui,sans-serif" font-size="${Math.round(h * 0.14)}" font-weight="600" fill="rgba(255,255,255,0.96)" text-anchor="middle" dominant-baseline="middle">${esc(label)}</text>` +
    (sub
      ? `<text x="${w / 2}" y="${h / 2 + Math.round(h * 0.17)}" font-family="system-ui,sans-serif" font-size="${Math.round(h * 0.08)}" fill="rgba(255,255,255,0.72)" text-anchor="middle">${esc(sub)}</text>`
      : '') +
    `</svg>`
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

export function videoPlaceholder(opts: { w: number; h: number; hue: number; label: string; duration: string }): string {
  return svgPlaceholder({
    ...opts,
    sub: `▶ ${opts.duration}`,
    dark: true,
  })
}

export function hueFrom(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  return h
}

export function versionImageUrl(nodeId: string, idx: number, label: string): string {
  return svgPlaceholder({
    w: 640,
    h: 360,
    hue: (hueFrom(nodeId) + idx * 34) % 360,
    label,
    sub: idx === 0 ? 'v1 初始' : `v${idx + 1} 变体`,
  })
}
