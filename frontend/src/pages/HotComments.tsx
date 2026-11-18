import { useEffect, useState } from 'react'
import { MessageSquare, RefreshCw } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState } from '../components'

/**
 * 热门评论（2026-11-18 用户定版：今天页三 tab 之一，独立于知识库）：
 * 只出监控清单作者的高赞评论（赞 ≥ hot_comment_bar，默认 50）——受众用赞投票的共鸣点，
 * 点「生成口播稿」走弹药匹配 + 形态读稿闸，成稿进「人设号 · 视频稿」。
 * 口径收窄后供给靠评论补采任务持续补齐（素材库 · 监控清单的监控号）。
 */

interface HotComment {
  material_id: number
  idx: number
  comment: string
  likes: number
  author?: string
  source: string
}

interface GenState { phase: 'busy' | 'done'; label: string; text: string; scriptId?: number }

export default function HotComments() {
  const [items, setItems] = useState<HotComment[]>([])
  const [bar, setBar] = useState(50)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [showAll, setShowAll] = useState(false)
  const [gen, setGen] = useState<Record<string, GenState>>({})

  useEffect(() => {
    api<{ items: HotComment[]; bar: number }>('/kb/hot_comments')
      .then((d) => { setItems(d.items || []); setBar(d.bar || 50); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [])

  const fire = (h: HotComment) => {
    const key = `${h.material_id}:${h.idx}`
    setMsg('')
    setGen((m) => ({ ...m, [key]: { phase: 'busy', label: '弹药匹配+生成中…', text: '' } }))
    api<{ job_id: number }>('/scripts/hotcomment', {
      method: 'POST', body: JSON.stringify({ material_id: h.material_id, comment_idx: h.idx }),
    })
      .then((r) => waitJob(r.job_id, (j) => setGen((m) => ({
        ...m, [key]: { ...(m[key] || { phase: 'busy', label: '', text: '' }), label: `生成中（${j.progress}%）${j.message || ''}` },
      }))))
      .then(async (j) => {
        const sid = (j as { result?: { script_id?: number } })?.result?.script_id
        let text = ''
        if (sid) {
          const d = await api<{ versions: { body: string }[] }>(`/scripts/${sid}`)
          text = d.versions?.[d.versions.length - 1]?.body || ''
        }
        setGen((m) => ({ ...m, [key]: { phase: 'done', label: '', text, scriptId: sid } }))
        setMsg('口播稿已生成 → 「人设号 · 视频稿」可打磨定稿')
      })
      .catch((e) => setGen((m) => ({ ...m, [key]: { phase: 'done', label: '', text: `生成失败：${apiErrText(e)}` } })))
  }

  const shown = showAll ? items : items.slice(0, 10)

  return (
    <div>
      <div className="mb-3 text-[12px] text-slate-400">
        只出监控清单作者的高赞评论：赞 ≥ {bar}，共 {items.length} 条——生成走「弹药匹配 + 形态读稿闸」，
        成稿进「人设号 · 视频稿」。评论存档由补采任务持续补齐，名单在「素材库 · 监控清单」维护。
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      {error && <div className="mb-3 text-sm text-red-600">{error}</div>}
      {!loaded ? null : items.length === 0 ? (
        <EmptyState icon={MessageSquare} title="还没有达标的热评"
          desc={`监控作者的评论存档里暂无赞 ≥ ${bar} 的评论。评论补采在后面逐条重抓，新热评会自动出现在这里；若监控名单太薄，去「素材库 · 监控清单」加号。`} />
      ) : (
        <div className="space-y-2">
          {shown.map((h) => {
            const key = `${h.material_id}:${h.idx}`
            const g = gen[key]
            return (
              <div key={key} className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="num-serif shrink-0 text-[15px] font-bold text-amber-700">{h.likes}赞</span>
                  <span className="min-w-0 flex-1 text-[13.5px] leading-relaxed text-slate-800">「{h.comment}」</span>
                  <button onClick={() => fire(h)} disabled={g?.phase === 'busy'}
                    className="shrink-0 rounded-full bg-emerald-600 px-3 py-1 text-[12px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">
                    生成口播稿
                  </button>
                </div>
                <div className="mt-1 truncate text-[11.5px] text-slate-400">来源：{h.author || '监控号'}《{h.source}》</div>
                {g && (
                  <div className="mt-2 border-t border-[#E8ECF0] pt-2">
                    {g.phase === 'busy' ? (
                      <div className="flex items-center gap-2 text-[12px] text-slate-500">
                        <RefreshCw size={12} className="inline animate-spin text-emerald-600" /> {g.label}
                      </div>
                    ) : (
                      <div>
                        <div className="whitespace-pre-wrap rounded bg-[#f7f8fa] px-2.5 py-2 text-[12.5px] leading-relaxed text-slate-700">{g.text}</div>
                        <div className="mt-1 text-[11px] text-slate-400">已进「人设号 · 视频稿」——打磨/改标题/定稿在那边</div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          {items.length > 10 && (
            <div className="pt-1 text-center">
              <button onClick={() => setShowAll(!showAll)}
                className="rounded-full border border-slate-200 px-4 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                {showAll ? '收起' : `显示全部 ${items.length} 条（已显 10）`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
