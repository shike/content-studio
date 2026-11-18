/** 素材库与推荐选题共用的行/片组件（2026-11-18 拆出：推荐选题移到「今天」页后两页共用一套渲染）。 */

/** 页内工具条（回显带 + 主操作 ≤2 的轻量变体；评审队列用共享 QueueBar，不适用本页）。 */
export function Toolbar({ left, actions }: { left: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-2.5">
      <div className="min-w-0 flex-1">{left}</div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
    </div>
  )
}

export interface MaterialRow {
  id: number
  origin_type: string
  origin_url: string
  url?: string
  author: string
  title: string
  transcript: string
  stats: Record<string, number>
  quality: Record<string, any>
  status: string
  note: string
  comment_kind?: string
  comment_leadgen_share?: number
  quality_score?: number
  analysis?: Record<string, any>
  top_comments?: { t: string; d: number }[]
  analyzed?: boolean
  created_at: string
  published_at: string | null
  tags: string[]
}

export const TYPE_LABEL: Record<string, string> = {
  douyin_video: '抖音视频', douyin_image: '抖音图文', wechat_article: '公众号文章',
  web_page: '网页', kb_doc: '知识库', manual_note: '手动记录', test_record: '实测',
  image: '图片', audio: '音频', video: '本地视频',
}

export function KindBadge({ m }: { m: MaterialRow }) {
  const kind = m.comment_kind || ''
  if (kind === 'leadgen_ore') {
    const pct = Math.round((m.comment_leadgen_share ?? 0) * 100)
    return <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700">获客矿 {pct}%</span>
  }
  if (kind === 'persona_ore') {
    return <span className="shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">人设矿</span>
  }
  return (
    <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">
      {TYPE_LABEL[m.origin_type] || m.origin_type}
    </span>
  )
}

export function MaterialDetail({ m }: { m: MaterialRow }) {
  const top = (m.top_comments || []).slice(0, 10)
  return (
    <div className="mt-3 space-y-2.5 border-t border-[#E8ECF0] pt-3">
      {m.note && <div className="text-[12.5px] text-slate-500">{m.note}</div>}
      {top.length > 0 && (
        <div>
          <div className="text-[11px] tracking-wide text-slate-400">高赞评论原文（观点的矿脉）</div>
          <ul className="mt-1.5 space-y-1">
            {top.map((c, ix) => (
              <li key={ix} className="text-[13px] text-slate-600">
                <span className="mr-1.5 text-slate-400">({c.d}赞)</span>{c.t}
              </li>
            ))}
          </ul>
        </div>
      )}
      {m.analysis?.summary && (
        <div>
          <div className="text-[11px] tracking-wide text-slate-400">拆解要点</div>
          <div className="mt-1 text-[13px] text-slate-600">{String(m.analysis.summary).slice(0, 200)}</div>
        </div>
      )}
      {m.transcript && (
        <div>
          <div className="text-[11px] tracking-wide text-slate-400">转写</div>
          <div className="mt-1 line-clamp-4 text-[12.5px] text-slate-500">{m.transcript.slice(0, 400)}…</div>
        </div>
      )}
      {m.url && (
        <a href={m.url} target="_blank" rel="noreferrer" className="inline-block text-[12px] text-sky-600 hover:underline">
          打开原视频 ↗
        </a>
      )}
    </div>
  )
}

/** 状态圆片（页内 chips：tab=实体，chips=生命周期，徽章=待办）。onRemove=条目级 ✕（目录移除用）。 */
export function Chips({ items, value, onPick }: {
  items: { v: string; label: string; onRemove?: () => void }[]
  value: string; onPick: (v: string) => void
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {items.map((c) => (
        <span key={c.v}
          className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12.5px] font-medium transition ${value === c.v ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
          <button onClick={() => onPick(c.v)}>{c.label}</button>
          {c.onRemove && (
            <button title="移除该目录" onClick={(e) => { e.stopPropagation(); c.onRemove?.() }}
              className="text-slate-400 transition hover:text-red-500">✕</button>
          )}
        </span>
      ))}
    </div>
  )
}
