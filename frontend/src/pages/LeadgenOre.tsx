import { useEffect, useState } from 'react'
import { Magnet } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, PageHeader, relTime } from '../components'

/**
 * 获客选题 = 获客矿素材流（2026-11-14 用户定规则：求学打卡型评论区=获客需求信号，
 * 路由到获客号——这里就是它的消费面）。每行=一条获客矿视频，展开看求学评论原文
 * （需求清单：什么课/什么资料在被求）。老管线「选题库」候选表已被分型路由替代。
 */
interface OreRow {
  id: number
  title: string
  author: string
  url: string
  stats: Record<string, number>
  comment_leadgen_share: number
  top_comments: { t: string; d: number }[]
  created_at: string
}

export default function LeadgenOre({ onNavigate }: { onNavigate?: (k: string) => void } = {}) {
  const [rows, setRows] = useState<OreRow[]>([])
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [genMsg, setGenMsg] = useState('')

  // 建稿：获客矿（视频+求学需求）直接进获客脚本生成，完成后落「内容 · 视频稿」
  const compose = async (b: OreRow) => {
    setGenMsg(`建稿中：${(b.title || '').slice(0, 24)}…`)
    setError('')
    try {
      const r = await api<{ script_id: number; job_id: number }>('/scripts/generate', {
        method: 'POST',
        body: JSON.stringify({ ore_id: b.id, length: 'short' }),
      })
      await waitJob(r.job_id, (j) => setGenMsg(`建稿：${j.progress}% ${j.message || ''}`))
      setGenMsg('已建稿，落「内容 · 视频稿」待定稿')
      onNavigate?.('l_scripts')
    } catch (e) {
      setError(`建稿失败：${apiErrText(e)}`)
      setGenMsg('')
    }
  }

  useEffect(() => {
    api<{ items: OreRow[] }>('/benchmarks?comment_kind=leadgen_ore&scan_status=all&limit=50')
      .then((d) => {
        setRows(d.items || [])
        setLoaded(true)
      })
      .catch((e) => {
        setError(apiErrText(e))
        setLoaded(true)
      })
  }, [])

  return (
    <div>
      <PageHeader icon={Magnet} title="获客选题"
        desc="获客矿素材流——评论区被求资料/求教程的监控视频（分型路由自动归线）。展开看他们在求什么：每条求学评论都是一个获客需求。" />
      {genMsg && <div className="mb-3 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-[13px] text-violet-800">{genMsg}</div>}
      <ErrorLine text={error} />
      {!loaded ? null : rows.length === 0 ? (
        <EmptyState icon={Magnet} title="暂无获客矿"
          desc="监控号还没出现求学打卡型评论区（阈值 comment_leadgen_route_min 可在设置页调整）" />
      ) : (
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[12px] font-medium text-violet-700">
            获客矿 {rows.length} 条
          </span>
          <span className="text-[11px] text-slate-400">按评论互动实数排序见拆解库；这里按路由时间倒序</span>
        </div>
      )}
      <div className="space-y-2">
        {rows.map((b) => {
          const pct = Math.round((b.comment_leadgen_share ?? 0) * 100)
          const open = expanded === b.id
          return (
            <div key={b.id}
              className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
              onClick={() => setExpanded(open ? null : b.id)}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700"
                  title="评论区求学打卡占比——分型路由判获客矿">
                  获客矿 {pct}%
                </span>
                <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">
                  {b.title || `样本 #${b.id}`}
                </span>
                <span className="shrink-0 text-xs text-slate-400">@{b.author || '?'}</span>
                <span className="shrink-0 text-[11px] text-slate-400">
                  评 {b.stats?.comment ?? '—'} · 赞 {b.stats?.digg ?? '—'} · {relTime(b.created_at || '')}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); compose(b) }}
                  title="从这条获客矿的需求直接生成获客脚本（黄金档）"
                  className="shrink-0 rounded-md border border-violet-200 bg-violet-50 px-2.5 py-1 text-[12px] font-medium text-violet-700 transition hover:bg-violet-100">
                  建稿
                </button>
              </div>
              {open && (
                <div className="mt-3 border-t border-[#E8ECF0] pt-3">
                  <div className="text-[11px] tracking-wide text-slate-400">求学评论原文（=获客需求清单：他们在求什么课/什么资料）</div>
                  <ul className="mt-1.5 space-y-1">
                    {(b.top_comments || []).map((c, ix) => (
                      <li key={ix} className="text-[13px] text-slate-600">
                        <span className="mr-1.5 text-slate-400">({c.d}赞)</span>{c.t}
                      </li>
                    ))}
                  </ul>
                  {(b.top_comments || []).length === 0 && (
                    <div className="text-[12.5px] text-slate-400">（评论原文未采集）</div>
                  )}
                  {b.url && (
                    <a href={b.url} target="_blank" rel="noreferrer"
                      className="mt-2 inline-block text-[12px] text-sky-600 hover:underline"
                      onClick={(e) => e.stopPropagation()}>打开原视频 ↗</a>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
