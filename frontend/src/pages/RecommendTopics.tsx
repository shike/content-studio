import { useCallback, useEffect, useState } from 'react'
import { Check, Inbox, Link2, Package, RefreshCw, Trash2, Upload, X } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, relTime } from '../components'
import { Chips, KindBadge, MaterialDetail, Toolbar, type MaterialRow } from './MaterialsShared'

/**
 * 推荐选题（2026-11-18 用户定版：从素材库移到「今天」页 tab）：
 * 全部素材的价值视图——待定夺收件箱 / 可用（按热度排）/ 已忽略翻案 / 全部检索；
 * 顶部热评雷达（车道二：监控作者高赞评论=口播选题源，一键出稿）。
 * 素材库只剩「源与库的维护」（监控清单/知识库），每日取料在这一页。
 */

type TopicChip = 'inbox' | 'ready' | 'ignored' | 'all'

const TOPIC_CHIPS: { v: TopicChip; label: string }[] = [
  { v: 'inbox', label: '待定夺' }, { v: 'ready', label: '可用' },
  { v: 'ignored', label: '已忽略' }, { v: 'all', label: '全部' },
]

export default function RecommendTopics() {
  const [chip, setChip] = useState<TopicChip>('ready')
  const [rows, setRows] = useState<MaterialRow[]>([])
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [q, setQ] = useState('')
  const [author, setAuthor] = useState('')
  const [pasteUrl, setPasteUrl] = useState('')
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  // 热评雷达（2026-11-18 车道二：受众用赞投票的共鸣点=口播选题源）
  interface HotComment { material_id: number; idx: number; comment: string; likes: number; author?: string; source: string }
  const [hc, setHc] = useState<HotComment[]>([])
  const [hcBar, setHcBar] = useState(50)
  const [hcOpen, setHcOpen] = useState(true)
  const [hcGen, setHcGen] = useState<Record<string, { phase: 'busy' | 'done'; label: string; text: string; scriptId?: number }>>({})

  useEffect(() => {
    api<{ items: HotComment[]; bar: number }>('/kb/hot_comments')
      .then((d) => { setHc(d.items || []); setHcBar(d.bar || 50) })
      .catch(() => {})
  }, [])

  const hcFire = (h: HotComment) => {
    const key = `${h.material_id}:${h.idx}`
    setHcGen((m) => ({ ...m, [key]: { phase: 'busy', label: '弹药匹配+生成中…', text: '' } }))
    api<{ job_id: number }>('/scripts/hotcomment', {
      method: 'POST', body: JSON.stringify({ material_id: h.material_id, comment_idx: h.idx }),
    })
      .then((r) => waitJob(r.job_id, (j) => setHcGen((m) => ({
        ...m, [key]: { ...(m[key] || { phase: 'busy', label: '', text: '' }), label: `生成中（${j.progress}%）${j.message || ''}` },
      }))))
      .then(async (j) => {
        const sid = (j as { result?: { script_id?: number } })?.result?.script_id
        let text = ''
        if (sid) {
          const d = await api<{ versions: { body: string }[] }>(`/scripts/${sid}`)
          text = d.versions?.[d.versions.length - 1]?.body || ''
        }
        setHcGen((m) => ({ ...m, [key]: { phase: 'done', label: '', text, scriptId: sid } }))
        setMsg('口播稿已生成 → 「人设号 · 视频稿」可打磨定稿')
      })
      .catch((e) => setHcGen((m) => ({ ...m, [key]: { phase: 'done', label: '', text: `生成失败：${apiErrText(e)}` } })))
  }

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: '100' })
    if (chip !== 'all') params.set('status', chip)
    if (q.trim()) params.set('q', q.trim())
    if (author.trim()) params.set('author', author.trim())
    api<{ items: MaterialRow[]; total: number; counts: Record<string, number> }>(`/materials?${params}`)
      .then((d) => {
        const items = d.items || []
        // 可用视图按热度排（推荐语义：拆过的按分，没拆的靠后）
        if (chip === 'ready') {
          items.sort((a, b) => (b.quality_score ?? -1) - (a.quality_score ?? -1))
        }
        setRows(items); setTotal(d.total); setCounts(d.counts || {}); setLoaded(true)
      })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [chip, q, author])
  useEffect(() => { load() }, [load])

  const act = async (m: MaterialRow, action: 'ready' | 'ignored' | 'enrich' | 'delete') => {
    setError('')
    try {
      if (action === 'delete') {
        await api(`/materials/${m.id}`, { method: 'DELETE' })
        setMsg(`已删除 #${m.id}`)
      } else if (action === 'enrich') {
        const r = await api<{ job_id: number }>(`/materials/${m.id}/enrich`, { method: 'POST' })
        setMsg(`拆解任务 #${r.job_id} 已排队`)
      } else {
        await api(`/materials/${m.id}/status`, { method: 'POST', body: JSON.stringify({ status: action }) })
        setMsg(action === 'ready' ? '已收下' + (m.origin_type === 'douyin_video' ? '，拆解已排队' : '') : '已忽略')
      }
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const batch = async (action: 'approve' | 'ignore') => {
    try {
      const r = await api<{ resolved: number }>('/materials/inbox-resolve', {
        method: 'POST', body: JSON.stringify({ action }),
      })
      setMsg(action === 'approve' ? `批量收下 ${r.resolved} 条，已排队拆解` : `批量忽略 ${r.resolved} 条`)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const analyzeUrl = async () => {
    setError('')
    try {
      const r = await api<{ job_id: number }>('/materials/analyze-url', {
        method: 'POST', body: JSON.stringify({ url: pasteUrl }),
      })
      setPasteUrl('')
      setMsg(`在线拆解任务 #${r.job_id} 已排队`)
      waitJob(r.job_id, (j) => setMsg(`在线拆解：${j.progress}% ${j.message || ''}`)).then(() => load()).catch(() => load())
    } catch (e) { setError(`贴链拆解失败：${apiErrText(e)}`) }
  }

  const stLabel = (m: MaterialRow) =>
    m.status === 'ready' ? '可用' : m.status === 'inbox' ? '待定夺' : m.status === 'ignored' ? '已忽略' : '归档'

  return (
    <div>
      <Toolbar
        left={<>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜标题 / 作者 / 转写…"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="按作者筛"
            className="w-32 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        </>}
        actions={<>
          <input value={pasteUrl} onChange={(e) => setPasteUrl(e.target.value)} placeholder="贴抖音链接/口令，在线拆解"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <button onClick={analyzeUrl} disabled={!pasteUrl.trim()}
            className="rounded-full bg-[#0a2f24] px-3 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
            <Link2 size={13} className="inline" /> 拆这条
          </button>
          <label className="cursor-pointer rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
            <Upload size={13} className="inline" /> 上传拆解
            <input type="file" accept="video/*,audio/*" className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0]
                if (!f) return
                const fd = new FormData()
                fd.append('file', f)
                setError('')
                try {
                  const r = await api<{ job_id: number }>('/materials/upload', { method: 'POST', body: fd })
                  setMsg(`上传完成，拆解任务 #${r.job_id} 已排队`)
                } catch (err) { setError(apiErrText(err)) }
              }} />
          </label>
        </>} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Chips items={TOPIC_CHIPS.map((c) => ({
          v: c.v, label: c.label + (counts[c.v] != null && c.v !== 'ready' ? ` ${counts[c.v]}` : ''),
        }))} value={chip} onPick={(v) => setChip(v as TopicChip)} />
        <span className="text-[12px] text-slate-400">共 {total} 条{chip === 'ready' ? '（按热度排）' : ''}</span>
      </div>
      {/* 热评雷达（2026-11-18 车道二：别人视频的高赞评论=受众用赞投票的共鸣点，口播选题源） */}
      {hc.length > 0 && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/40 px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[13px] font-semibold text-slate-900">
              热评雷达 <span className="text-[11.5px] font-normal text-slate-500">监控号的高赞评论=受众用赞投票的共鸣点（赞≥{hcBar}）——口播选题源，生成走「弹药匹配+形态读稿闸」</span>
            </div>
            <button onClick={() => setHcOpen(!hcOpen)} className="shrink-0 text-[12px] text-slate-500 hover:text-slate-700">
              {hcOpen ? '收起' : `展开 ${hc.length} 条`}
            </button>
          </div>
          {hcOpen && (
            <div className="mt-2 space-y-1.5">
              {hc.slice(0, 10).map((h) => {
                const key = `${h.material_id}:${h.idx}`
                const g = hcGen[key]
                return (
                  <div key={key} className="rounded-lg border border-[#EDE7D8] bg-white px-3 py-2">
                    <div className="flex items-baseline gap-2">
                      <span className="num-serif shrink-0 text-[13px] font-bold text-amber-700">{h.likes}赞</span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">「{h.comment}」</span>
                      <button onClick={() => hcFire(h)} disabled={g?.phase === 'busy'}
                        className="shrink-0 rounded-full bg-emerald-600 px-2.5 py-0.5 text-[11.5px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">
                        生成口播稿
                      </button>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-slate-400">来源：{h.author || '监控号'}《{h.source}》</div>
                    {g && (
                      <div className="mt-1.5 border-t border-[#EDE7D8] pt-1.5">
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
              {hc.length > 10 && <div className="text-[11.5px] text-slate-400">雷达共 {hc.length} 条，这里只展示赞数前 10</div>}
            </div>
          )}
        </div>
      )}
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />
      {!loaded ? null : rows.length === 0 ? (
        chip === 'inbox' ? (
          <EmptyState icon={Inbox} title="没有待定夺的素材"
            desc="同行扫描/话题雷达的新视频先落这里；过质量闸（评论≥20或赞≥300）的自动入「可用」，不过闸的自动忽略——只有拿不准的才等你定夺" />
        ) : (
          <EmptyState icon={Package} title="没有匹配的素材" desc="换个筛选条件，或去「素材库 · 监控清单」扫一轮" />
        )
      ) : (
        <div className="space-y-2">
          {rows.map((m) => {
            const open = expanded === m.id
            return (
              <div key={m.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => setExpanded(open ? null : m.id)}>
                <div className="flex flex-wrap items-center gap-2">
                  {chip === 'inbox' && <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">新</span>}
                  <KindBadge m={m} />
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${m.status === 'ready' ? 'bg-emerald-50 text-emerald-700' : m.status === 'inbox' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>
                    {stLabel(m)}{m.status === 'ready' && !m.analyzed ? '·待拆' : ''}{m.analyzed ? '·已拆' : ''}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">
                    {m.title || `素材 #${m.id}`}
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">@{m.author || '?'}</span>
                  {chip === 'inbox' ? (
                    <span className="shrink-0 text-[11px] text-slate-400">
                      评 {m.stats?.comment ?? '—'} · 赞 {m.stats?.digg ?? '—'} · {relTime(m.created_at || '')}
                    </span>
                  ) : (
                    <span className="shrink-0 text-[11px] text-slate-400">热度 {m.quality_score ?? '—'} · {relTime(m.created_at || '')}</span>
                  )}
                  {chip === 'inbox' && (
                    <>
                      <button onClick={(e) => { e.stopPropagation(); act(m, 'ready') }} title="收下并排队拆解"
                        className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                        <Check size={13} className="inline" /> 收下
                      </button>
                      <button onClick={(e) => { e.stopPropagation(); act(m, 'ignored') }} title="忽略"
                        className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-500 transition hover:bg-slate-50">
                        <X size={13} className="inline" /> 忽略
                      </button>
                    </>
                  )}
                  {chip !== 'inbox' && m.status !== 'ready' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'ready') }}
                      className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                      收下
                    </button>
                  )}
                  {chip !== 'inbox' && m.origin_type === 'douyin_video' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'enrich') }} title="强制（重）拆解"
                      className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                      <RefreshCw size={13} className="inline" /> 拆解
                    </button>
                  )}
                  {chip !== 'inbox' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'delete') }} title="删除素材"
                      className="shrink-0 rounded-md border border-red-100 px-2 py-1 text-[12px] text-red-500 transition hover:bg-red-50">
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
                {open && <MaterialDetail m={m} />}
              </div>
            )
          })}
          {chip === 'inbox' && rows.length > 0 && (
            <div className="flex items-center justify-end gap-2 pt-1">
              <button onClick={() => batch('approve')}
                className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                全部收下
              </button>
              <button onClick={() => batch('ignore')}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                全部忽略
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
