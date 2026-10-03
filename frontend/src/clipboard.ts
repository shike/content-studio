/** 剪贴板富文本复制：公众号编辑器只认 text/html——writeText 贴过去是 HTML 源码。
 *
 * 用 ClipboardItem 写 text/html + text/plain 双格式：富文本目标（公众号编辑器）取 HTML，
 * 纯文本目标取纯文本兜底。老浏览器/权限不足时回落到隐藏容器 + 选区 + execCommand（同样带 text/html）。
 */

/** 把根相对地址补成绝对：公众号编辑器跨域抓图，拿不到相对路径（图片会裂）。 */
export function absolutizeMediaUrls(html: string, origin = location.origin): string {
  return html.replace(/(src|href)="\/([^"]*)"/g,
    (_m, attr: string, path: string) => `${attr}="${origin}/${path}"`)
}

function htmlToPlain(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html")
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim()
}

export async function copyRichHtml(html: string, origin = location.origin): Promise<void> {
  const abs = absolutizeMediaUrls(html, origin)
  const plain = htmlToPlain(abs)
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/html": new Blob([abs], { type: "text/html" }),
          "text/plain": new Blob([plain], { type: "text/plain" }),
        }),
      ])
      return
    } catch {
      /* 权限/浏览器差异：走 execCommand 回落 */
    }
  }
  const box = document.createElement("div")
  box.contentEditable = "true"
  box.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0"
  box.innerHTML = abs
  document.body.appendChild(box)
  const range = document.createRange()
  range.selectNodeContents(box)
  const sel = window.getSelection()
  sel?.removeAllRanges()
  sel?.addRange(range)
  const ok = typeof document.execCommand === "function" && document.execCommand("copy")
  sel?.removeAllRanges()
  box.remove()
  if (!ok) throw new Error("浏览器拒绝写入剪贴板：请改用 Chrome/Edge，或手动选中预览内容复制")
}
