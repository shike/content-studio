import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  BookOpen,
  Check,
  Inbox,
  Link2,
  Package,
  Plus,
  Radar,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, PageHeader, relTime } from '../components'

/** 页内工具条（回显带 + 主操作 ≤2 的轻量变体；评审队列用共享 QueueBar，不适用本页）。 */
function Toolbar({ left, actions }: { left: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-2.5">
      <div className="min-w-0 flex-1">{left}</div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
    </div>
  )
}

/**
 * 素材库（PRD R1）：唯一进料口。三 tab 按用途分层（2026-11-17 用户定版）：
 * 推荐选题=全部素材的价值视图（待定夺/可用/已忽略/全部）；
 * 监控清单=抖音采集源与候选账号；知识库=ima 知识库线（目录/挖掘）。
 * 层次法则：tab=实体（素材/源/知识线），chips=生命周期，徽章=待办。
 */

interface MaterialRow {
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

interface SourceRow {
  id: number
  kind: string
  platform: string
  name: string
  handle: string
  url: string
  status: string
  config: Record<string, any>
  last_sync_at: string | null
  note: string
  enabled?: boolean
  account_type?: string
  follower_count?: number
  signature?: string
  dismissed?: boolean
  video_total?: number
  video_recent_7d?: number
}

const TYPE_LABEL: Record<string, string> = {
  douyin_video: '抖音视频', douyin_image: '抖音图文', wechat_article: '公众号文章',
  web_page: '网页', kb_doc: '知识库', manual_note: '手动记录', test_record: '实测',
  image: '图片', audio: '音频', video: '本地视频',
}

const ORIGIN_KIND_LABEL: Record<string, string> = {
  peer_account: '同行账号', radar_topic: '话题雷达', kb_folder: '知识库目录',
  hotlist: '热点源', manual: '手动',
}

function KindBadge({ m }: { m: MaterialRow }) {
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

function MaterialDetail({ m }: { m: MaterialRow }) {
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
function Chips({ items, value, onPick }: {
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

/* ─────────────────────────── Tab ① 推荐选题 ─────────────────────────── */

type TopicChip = 'inbox' | 'ready' | 'ignored' | 'all'

const TOPIC_CHIPS: { v: TopicChip; label: string }[] = [
  { v: 'inbox', label: '待定夺' }, { v: 'ready', label: '可用' },
  { v: 'ignored', label: '已忽略' }, { v: 'all', label: '全部' },
]

/** 推荐选题：全部素材大一统——待定夺收件箱 / 可用（按热度排）/ 已忽略翻案 / 全部检索。 */
function TopicsTab() {
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
          <EmptyState icon={Package} title="没有匹配的素材" desc="换个筛选条件，或去「监控清单」扫一轮" />
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

/* ─────────────────────────── Tab ② 监控清单 ─────────────────────────── */

type WatchChip = 'cand' | 'active' | 'disabled'

/** 监控清单：同行账号/话题雷达 + 候选账号定夺——源的完整生命周期一处管。 */
function WatchTab() {
  const [kind, setKind] = useState<'peer_account' | 'radar_topic'>('peer_account')
  const [chip, setChip] = useState<WatchChip>('active')
  const [rows, setRows] = useState<SourceRow[]>([])
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({ name: '', url: '', ch_id: '', account_type: 'persona' })
  const [resolveUrl, setResolveUrl] = useState('')
  const [kw, setKw] = useState('')
  const [dir, setDir] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api<{ items: SourceRow[] }>(`/sources?kind=${kind}`)
      .then((d) => { setRows(d.items || []); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [kind])
  useEffect(() => { load() }, [load])

  // 客户端分态：候选=status=candidate 且未忽略；监控中=enabled；其余=已停用
  const cands = rows.filter((r) => r.status === 'candidate' && !r.dismissed)
  const active = rows.filter((r) => r.enabled && r.status !== 'candidate')
  const disabled = rows.filter((r) => !r.enabled && r.status !== 'candidate')
  const dismissedN = rows.filter((r) => r.status === 'candidate' && r.dismissed).length
  const shown = chip === 'cand' ? cands : chip === 'active' ? active : disabled

  const runJob = async (submit: () => Promise<{ job_id: number }>, label: string, reload = false) => {
    setError('')
    try {
      const r = await submit()
      setMsg(`${label}任务 #${r.job_id} 已提交`)
      waitJob(r.job_id, (j) => setMsg(`${label}：${j.progress}% ${j.message || ''}`))
        .then(() => { setMsg(`${label}完成`); if (reload) load() })
        .catch((e) => { setError(`${label}失败：${apiErrText(e)}`); setMsg('') })
    } catch (e) { setError(apiErrText(e)) }
  }

  const add = async () => {
    setError('')
    try {
      if (kind === 'radar_topic') {
        await api('/sources', { method: 'POST', body: JSON.stringify({ kind: 'radar_topic', name: form.name, ext_id: form.ch_id }) })
      } else {
        await api('/sources', { method: 'POST', body: JSON.stringify({ kind: 'peer_account', name: form.name, url: form.url, account_type: form.account_type }) })
      }
      setForm({ name: '', url: '', ch_id: '', account_type: 'persona' })
      setMsg('已添加采集源')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const toggle = async (r: SourceRow) => {
    try {
      await api(`/sources/${r.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !r.enabled }) })
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const extractTopic = async () => {
    setError('')
    try {
      const r = await api<{ items: { ch_id: string; name: string }[] }>('/sources/radar-extract', {
        method: 'POST', body: JSON.stringify({ url: resolveUrl }),
      })
      if (r.items?.length) {
        setForm((f) => ({ ...f, name: r.items[0].name, ch_id: r.items[0].ch_id }))
        setMsg(`已提取话题：#${r.items[0].name}（${r.items[0].ch_id}）`)
      } else setMsg('该视频未提取到话题标签')
    } catch (e) { setError(apiErrText(e)) }
  }

  const resolve = async () => {
    setError('')
    try {
      const r = await api<{ job_id: number }>('/sources/resolve', {
        method: 'POST', body: JSON.stringify({ url: resolveUrl }),
      })
      const info = await waitJob(r.job_id, (j) => setMsg(`识别：${j.progress}% ${j.message || ''}`))
      setForm((f) => ({ ...f, name: (info.result as any)?.name || f.name, url: (info.result as any)?.url || f.url }))
      setMsg(`识别成功：${(info.result as any)?.name}——确认后点「添加」`)
    } catch (e) { setError(`识别失败：${apiErrText(e)}`); setMsg('') }
  }

  const decide = async (r: SourceRow, action: 'activate' | 'dismiss') => {
    setError('')
    try {
      const res = await api<{ duplicate?: boolean }>(`/sources/${r.id}/${action}`, { method: 'POST' })
      setMsg(action === 'activate'
        ? (res.duplicate ? '该账号已在清单（候选已标已处理）' : `已加入采集清单：${r.name}`)
        : '已忽略')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const backfillLabel = kind === 'peer_account' ? '同行存量回填' : '话题存量回填'

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-1.5">
          {[['peer_account', '同行账号'], ['radar_topic', '话题雷达']].map(([v, label]) => (
            <button key={v} onClick={() => setKind(v as 'peer_account' | 'radar_topic')}
              className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${kind === v ? 'bg-[#0a2f24] text-[#f0e9d5]' : 'text-slate-500 hover:bg-slate-100'}`}>
              {label}
            </button>
          ))}
        </div>
        <Chips
          items={[
            ...(kind === 'peer_account' ? [{ v: 'cand', label: `待定夺 ${cands.length}` }] : []),
            { v: 'active', label: `监控中 ${active.length}` },
            { v: 'disabled', label: `已停用 ${disabled.length}` },
          ]}
          value={chip} onPick={(v) => setChip(v as WatchChip)} />
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />

      {chip === 'cand' ? (
        <>
          <Toolbar
            left={<>
              <input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="关键词（如 FDE、企业AI落地）"
                className="w-52 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="方向（可选）"
                className="w-28 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
            </>}
            actions={<>
              <button onClick={() => runJob(() => api('/sources/discover', { method: 'POST', body: JSON.stringify({ keyword: kw, direction: dir }) }), '搜账号')}
                className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                搜账号
              </button>
              <button onClick={() => runJob(() => api('/sources/hl-search', { method: 'POST' }), '同城矩阵')}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                同城矩阵补跑
              </button>
              <button onClick={() => api('/sources', { method: 'DELETE' }).then((r: any) => { setMsg(`已清 ${r.deleted} 条已处理候选`); load() })}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                清已忽略{dismissedN > 0 ? ` ${dismissedN}` : ''}
              </button>
            </>} />
          {!loaded ? null : cands.length === 0 ? (
            <EmptyState icon={Inbox} title="没有待定夺的候选账号" desc="用上面的「搜账号」关键词发现，或每晚同城矩阵自动挖" />
          ) : (
            <div className="space-y-2">
              {cands.map((r) => {
                const at = (r.config?.account_type || 'persona') as string
                const fans = r.config?.follower_count || r.follower_count || 0
                return (
                  <div key={r.id} className="rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${at === 'leadgen' ? 'bg-violet-100 text-violet-700' : 'bg-emerald-100 text-emerald-700'}`}>
                        {at === 'leadgen' ? '获客' : '人设'}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{r.name}</span>
                      {fans > 0 && <span className="shrink-0 text-[11px] text-slate-400">{fans >= 10000 ? `${(fans / 10000).toFixed(1)}万` : fans} 粉</span>}
                      <span className="shrink-0 text-[11px] text-slate-400">
                        {r.config?.keyword === '搜索矩阵' ? '同城矩阵' : `搜「${r.config?.keyword || '—'}」`}
                      </span>
                      <button onClick={() => decide(r, 'activate')}
                        className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                        加入
                      </button>
                      <button onClick={() => decide(r, 'dismiss')}
                        className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-500 transition hover:bg-slate-50">
                        忽略
                      </button>
                    </div>
                    {(r.signature || r.config?.pure_reason) && (
                      <div className="mt-1.5 line-clamp-2 text-[12.5px] text-slate-500">
                        {r.signature}{r.config?.pure_reason ? `｜${r.config.pure_reason}` : ''}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </>
      ) : (
        <>
          <Toolbar
            left={<span className="text-[13px] text-slate-600">
              {kind === 'peer_account'
                ? `启用 ${active.length} / ${active.length + disabled.length} 个同行采集源`
                : `监控 ${active.length} 个话题（每日 6 点随选题工场巡检）`}
            </span>}
            actions={<>
              <button onClick={() => runJob(() => api('/sources/sync-all', { method: 'POST' }), '全量扫描', true)}
                className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                <RefreshCw size={13} className="inline" /> 全量扫描
              </button>
              <button onClick={() => runJob(() => api(kind === 'peer_account' ? '/materials/backfill' : '/sources/backfill-topics',
                { method: 'POST', body: JSON.stringify({ scope: 'peer' }) }), backfillLabel)}
                className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                {backfillLabel}
              </button>
            </>} />
          <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
            <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
              {kind === 'peer_account' ? '添加同行账号（贴主页或视频链接）' : '添加话题（贴视频链接可提取 ch_id）'}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input value={resolveUrl} onChange={(e) => setResolveUrl(e.target.value)} placeholder="粘贴分享链接/口令全文"
                className="w-64 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              {kind === 'peer_account' ? (
                <button onClick={resolve}
                  className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                  <Link2 size={13} className="inline" /> 贴链识别
                </button>
              ) : (
                <button onClick={extractTopic}
                  className="rounded-md border border-slate-200 px-2.5 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                  提取话题
                </button>
              )}
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="名称"
                className="w-36 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              {kind === 'peer_account' ? (
                <>
                  <input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="主页链接"
                    className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                  <select value={form.account_type} onChange={(e) => setForm({ ...form, account_type: e.target.value })}
                    className="rounded-lg border border-[#E8ECF0] px-2 py-1.5 text-[13px]">
                    <option value="persona">人设号</option>
                    <option value="leadgen">获客号</option>
                  </select>
                </>
              ) : (
                <input value={form.ch_id} onChange={(e) => setForm({ ...form, ch_id: e.target.value })} placeholder="ch_id（数字）"
                  className="w-32 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              )}
              <button onClick={add}
                className="rounded-full bg-[#0a2f24] px-3 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90">
                <Plus size={13} className="inline" /> 添加
              </button>
            </div>
          </div>
          {!loaded ? null : shown.length === 0 ? (
            <EmptyState icon={Radar} title={chip === 'active' ? '还没有监控中的源' : '没有已停用的源'}
              desc={chip === 'active' ? (kind === 'peer_account' ? '贴同行主页链接添加；或切到「待定夺」看看候选' : '贴一条带话题的视频链接提取 ch_id 后添加') : '停用的源出现在这里，可随时启用'} />
          ) : (
            <div className="space-y-2">
              {shown.map((r) => (
                <div key={r.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
                  {kind === 'peer_account' && r.account_type && (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${r.account_type === 'leadgen' ? 'bg-violet-50 text-violet-700' : 'bg-emerald-50 text-emerald-700'}`}>
                      {r.account_type === 'leadgen' ? '获客号' : '人设号'}
                    </span>
                  )}
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{r.name}</span>
                  {kind === 'peer_account' && (
                    <span className="shrink-0 text-[11px] text-slate-400">
                      素材 {r.video_total ?? 0} 条（7 天 {r.video_recent_7d ?? 0}）
                    </span>
                  )}
                  <span className="shrink-0 text-[11px] text-slate-400">
                    上次同步 {r.last_sync_at ? relTime(r.last_sync_at) : '—'}
                  </span>
                  <button onClick={() => runJob(() => api(`/sources/${r.id}/sync`, { method: 'POST' }), `扫描 ${r.name}`, true)}
                    className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                    扫描
                  </button>
                  <button onClick={() => toggle(r)}
                    className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                    {r.enabled ? '停用' : '启用'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

/* ─────────────────────────── Tab ③ 知识库 ─────────────────────────── */

interface KbDoc {
  id: number
  title: string
  author: string
  folder: string
  score: number | null
  why_good: string
  adapt_angle: string
  best_quotes: string[]
  excerpt?: string
  body?: string
  used: { kind: string; kind_label: string; id: number; title: string; status: string }[]
  status: string
  stage: string
  created_at: string
}

/**
 * 知识库（2026-11-18 用户定版）：全库内容榜，默认「全部 · 分数降序」——库=收录即认可，
 * 不让"入选"当门禁。三层递进支撑判断：分数排序=质量梯度 → 用处一行（why_good）=初筛 →
 * 展开读原文（懒加载，cap 1.2 万字）后决策送产线。已产出标记（→视频稿/长文/笔记 #id）防重复选材。
 * 目录 chips=用户自己的 ima 分类法，只导航不管理（增删收在底部目录管理卡）。
 */
/** 稿件 kind → 跳转键（onNavigate 走 App 的 NAVMAP：带 tab 的旧键直达页内） */
const KIND_NAV: Record<string, string> = { video_script: 'p_content', note: 'xhs', long_article: 'articles' }

function KbTab({ onNavigate }: { onNavigate?: (k: string) => void }) {
  const [docs, setDocs] = useState<KbDoc[]>([])          // 全量（无搜索词时的数据源）
  const [searched, setSearched] = useState<KbDoc[] | null>(null) // 全文检索结果（null=未在搜索）
  const [folder, setFolder] = useState('')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(100)                // 触底续载
  const [bodies, setBodies] = useState<Record<number, string>>({}) // 已拉取的全文缓存
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [edit, setEdit] = useState<{ id: number; title: string } | null>(null)
  const [prod, setProd] = useState<null | {
    doc: KbDoc
    dir: 'xhs' | 'article'
    xhsTitle: string
    xhsAngle: string
  }>(null)
  const [gen, setGen] = useState<null | { phase: 'busy' | 'done'; label: string; text: string; noteId?: number; dir: 'xhs' }>(null)
  const [fb, setFb] = useState('')
  const [elapsed, setElapsed] = useState(0)

  // busy 期间秒级计时（2026-11-18 用户裁决：实时进度要上页面——长调用期间进度条不跳，计时在走=活着）
  useEffect(() => {
    if (gen?.phase !== 'busy') return
    setElapsed(0)
    const t0 = Date.now()
    const t = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000)
    return () => clearInterval(t)
  }, [gen?.phase, gen?.label === '' ? 0 : 1])

  /** 打开生成框（2026-11-18 用户定版：知识库 → 公众号长文 + 小红书图文两方向；口播入口移至热评线）。 */
  const openProd = (d: KbDoc) => {
    setProd({
      doc: d, dir: 'xhs',
      xhsTitle: (d.title || '').slice(0, 40),
      xhsAngle: [d.adapt_angle, ...((d.best_quotes || []).slice(0, 2))]
        .filter(Boolean).join('；金句：').slice(0, 200),
    })
  }

  const saveTitle = async () => {
    if (!edit) return
    setError('')
    try {
      await api(`/materials/${edit.id}`, { method: 'PUT', body: JSON.stringify({ title: edit.title.trim() }) })
      setEdit(null)
      setMsg('标题已更新')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const load = useCallback(() => {
    api<{ items: KbDoc[] }>('/kb/docs').then((d) => setDocs(d.items || []))
      .catch((e) => setError(apiErrText(e)))
  }, [])
  useEffect(() => { load() }, [load])

  // 全文检索（服务端：标题/作者/正文）——300ms 防抖；清词回落全量
  useEffect(() => {
    const kw = q.trim()
    if (!kw) { setSearched(null); return }
    const t = setTimeout(() => {
      api<{ items: KbDoc[] }>(`/kb/docs?q=${encodeURIComponent(kw)}`)
        .then((d) => setSearched(d.items || []))
        .catch((e) => setError(apiErrText(e)))
    }, 300)
    return () => clearTimeout(t)
  }, [q])

  const folders = useMemo(() => {
    const m = new Map<string, number>()
    for (const d of docs) if (d.folder) m.set(d.folder, (m.get(d.folder) || 0) + 1)
    return [...m.entries()].sort((a, b) => b[1] - a[1])
  }, [docs])
  const base = searched ?? docs
  const shown = base.filter((d) => !folder || d.folder === folder)
  const vis = shown.slice(0, limit)

  /** 展开行时懒加载全文（GET /kb/docs/{id}）；空串=原文未入库，回落摘录。 */
  const openDoc = (d: KbDoc) => {
    const next = expanded === d.id ? null : d.id
    setExpanded(next)
    if (next != null && bodies[next] === undefined) {
      api<KbDoc>(`/kb/docs/${next}`)
        .then((r) => setBodies((m) => ({ ...m, [next]: r.body || '' })))
        .catch(() => setBodies((m) => ({ ...m, [next]: '' })))
    }
  }

  const toScript = async (d: KbDoc, length: 'short' | 'long', angles: string[], useStyle: boolean, hotspot: string = '') => {
    setError('')
    try {
      const r = await api<{ job_id: number; script_id: number }>('/scripts/generate', {
        method: 'POST', body: JSON.stringify({ kb_id: d.id, length, angles, use_style: useStyle, hotspot }),
      })
      setMsg(`视频脚本生成中（任务 #${r.job_id}）——完成后在「人设号 · 视频稿」`)
      waitJob(r.job_id, (j) => setMsg(`视频脚本：${j.progress}% ${j.message || ''}`))
        .then(() => setMsg(`脚本 #${r.script_id} 已生成 → 「人设号 · 视频稿」定稿`))
        .catch((e) => setError(`脚本生成失败：${apiErrText(e)}`))
    } catch (e) { setError(`发起失败：${apiErrText(e)}`) }
  }

  const toXhs = async (d: KbDoc, xhsTitle: string, xhsAngle: string) => {
    setError('')
    try {
      const idea = await api<{ id: number }>('/channels/xhs/ideas', {
        method: 'POST', body: JSON.stringify({ title: xhsTitle, angle: xhsAngle, material_ids: [d.id] }),
      })
      const c = await api<{ job_id: number }>(`/channels/xhs/ideas/${idea.id}/compose`, { method: 'POST' })
      setMsg(`小红书笔记生成中（任务 #${c.job_id}）`)
      waitJob(c.job_id, (j) => setMsg(`小红书笔记：${j.progress}% ${j.message || ''}`))
        .then(() => setMsg('笔记已生成 → 「小红书」tab 改稿发布'))
        .catch((e) => setError(`笔记生成失败：${apiErrText(e)}`))
    } catch (e) { setError(`发起失败：${apiErrText(e)}`) }
  }

  const toArticle = async (d: KbDoc) => {
    setError('')
    try {
      const r = await api<{ job_id: number }>(`/articles/content-items/${d.id}/generate`, { method: 'POST' })
      setMsg(`长文改编中（任务 #${r.job_id}）——保骨架保原话成文约需几分钟`)
      await waitJob(r.job_id, (j) => setMsg(`改编：${j.progress}% ${j.message || ''}`))
      setMsg('长文已生成 → 「公众号 · 清单」排版待发')
      load()
    } catch (e) { setError(`改编失败：${apiErrText(e)}`) }
  }

  return (
    <div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />

      <div className="mb-3 space-y-2">
        <Chips items={[
          { v: '', label: `全部目录 ${docs.length}` },
          ...folders.map(([name, n]) => ({ v: name, label: `${name} ${n}` })),
        ]} value={folder} onPick={(v) => { setFolder(v); setLimit(100) }} />
        <div className="flex flex-wrap items-center gap-2">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜标题 / 作者 / 全文…"
            className="ml-auto w-64 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        </div>
        {searched !== null && (
          <div className="text-[12.5px] text-slate-500">
            全文检索「{q.trim()}」：{searched.length} 篇
            <button onClick={() => { setQ(''); setSearched(null) }}
              className="ml-2 text-emerald-700 hover:underline">清除</button>
          </div>
        )}
      </div>

      {shown.length === 0 ? (
        <EmptyState icon={BookOpen} title={docs.length === 0 ? '知识库还没有入库文档' : '没有匹配的文档'}
          desc={docs.length === 0 ? '每日 04:00 自动挖掘进新文档' : '换个目录或关键词'} />
      ) : (
        <div className="space-y-2">
          {vis.map((d) => {
            const open = expanded === d.id
            return (
              <div key={d.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => openDoc(d)}>
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="shrink-0 text-[17px] font-bold num-serif text-[#0a2f24]">
                    {d.score ?? '—'}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">
                    《{d.title || `文档 #${d.id}`}》
                  </span>
                  {(d.used || []).length > 0 && (
                    <span className="flex shrink-0 items-center gap-1">
                      {d.used.slice(0, 2).map((u) => (
                        <button key={u.id} title={`《${u.title}》${u.status === 'published' ? '（已发布）' : ''}`}
                          onClick={(e) => { e.stopPropagation(); onNavigate?.(KIND_NAV[u.kind] || 'tasks') }}
                          className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 transition hover:bg-amber-100">
                          →{u.kind_label} #{u.id}
                        </button>
                      ))}
                      {d.used.length > 2 && <span className="text-[11px] text-slate-400">+{d.used.length - 2}</span>}
                    </span>
                  )}
                  {d.author && <span className="shrink-0 text-xs text-slate-400">@{d.author}</span>}
                  <span className="shrink-0 text-[11px] text-slate-400">{relTime(d.created_at)}</span>
                </div>
                {d.why_good && (
                  <div className="mt-1 line-clamp-1 text-[12.5px] text-slate-500">用处：{d.why_good}</div>
                )}
                {open && (
                  <div className="mt-3 space-y-2 border-t border-[#E8ECF0] pt-3" onClick={(e) => e.stopPropagation()}>
                    {edit?.id === d.id ? (
                      <div className="flex flex-wrap items-center gap-2">
                        <input value={edit.title} onChange={(e) => setEdit({ ...edit, title: e.target.value })}
                          className="min-w-0 flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                        <button onClick={saveTitle} className="rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">保存</button>
                        <button onClick={() => setEdit(null)} className="rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-500 transition hover:bg-slate-50">取消</button>
                      </div>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        <button onClick={() => setEdit({ id: d.id, title: d.title })}
                          className="rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                          改标题
                        </button>
                        <span className="text-[11px] text-slate-300">｜</span>
                        <button onClick={() => openProd(d)} title="选方向生成内容"
                          className="rounded-md bg-emerald-600 px-2.5 py-1 text-[12px] font-medium text-white transition hover:bg-emerald-700">
                          生成内容
                        </button>
                      </div>
                    )}
                    {(() => {
                      const full = bodies[d.id]
                      if (full === undefined) {
                        return <div className="text-[12px] text-slate-400">读原文中…</div>
                      }
                      if (!full) {
                        return d.excerpt ? (
                          <div>
                            <div className="text-[11px] tracking-wide text-slate-400">原文摘录（全文未入库）</div>
                            <div className="mt-1 text-[13px] leading-relaxed text-slate-700">{d.excerpt}…</div>
                          </div>
                        ) : <div className="text-[12px] text-slate-400">原文未入库</div>
                      }
                      return (
                        <div>
                          <div className="text-[11px] tracking-wide text-slate-400">原文（{full.length} 字）</div>
                          <div className="mt-1 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg bg-[#f7f8fa] px-3 py-2.5 text-[13px] leading-relaxed text-slate-700">{full}</div>
                        </div>
                      )
                    })()}
                    {d.adapt_angle && (
                      <div>
                        <div className="text-[11px] tracking-wide text-slate-400">改编角度（AI 评估）</div>
                        <div className="mt-1 text-[13px] leading-relaxed text-slate-500">{d.adapt_angle}</div>
                      </div>
                    )}
                    {(d.best_quotes || []).length > 0 && (
                      <div>
                        <div className="text-[11px] tracking-wide text-slate-400">金句原文</div>
                        <ul className="mt-1.5 space-y-1">
                          {d.best_quotes.slice(0, 3).map((t, ix) => (
                            <li key={ix} className="text-[13px] text-slate-600">「{t}」</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {(d.used || []).length > 0 && (
                      <div>
                        <div className="text-[11px] tracking-wide text-slate-400">已产出（点击跳转）</div>
                        <ul className="mt-1 space-y-0.5">
                          {d.used.map((u) => (
                            <li key={u.id}>
                              <button onClick={() => onNavigate?.(KIND_NAV[u.kind] || 'tasks')} title={u.title}
                                className="text-[12.5px] text-slate-600 transition hover:text-emerald-700 hover:underline">
                                → {u.kind_label} #{u.id}《{u.title}》{u.status === 'published' ? '（已发布）' : ''}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-x-4 text-[12px] text-slate-400">
                      {d.folder && <span>目录：{d.folder}</span>}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {shown.length > limit && (
            <div className="pt-2 text-center">
              <button onClick={() => setLimit(limit + 100)}
                className="rounded-full border border-slate-200 px-4 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                显示更多（已显 {vis.length} / {shown.length} 篇）
              </button>
            </div>
          )}
        </div>
      )}

      {/* 生成内容对话框（2026-11-18 用户裁决：单按钮入口，先选内容类型与选项再确认；
          portal 到 body——fade-in 动画带 transform 会劫持 fixed 定位，留在组件树里弹窗必错位） */}
      {prod && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#0a2f24]/40 px-4"
          onClick={() => setProd(null)}>
          <div className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl"
            onClick={(e) => e.stopPropagation()}>
            <div className="text-[15px] font-bold text-slate-900">生成内容</div>
            <div className="mt-1 line-clamp-1 text-[12.5px] text-slate-500">《{prod.doc.title || `文档 #${prod.doc.id}`}》</div>
            {(prod.doc.used || []).length > 0 && (
              <div className="mt-3">
                <div className="text-[12px] font-medium tracking-wide text-slate-400">已生成（点击跳转）</div>
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {prod.doc.used.map((u) => (
                    <button key={u.id} onClick={() => { setProd(null); onNavigate?.(KIND_NAV[u.kind] || 'tasks') }}
                      title={u.title}
                      className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[12px] font-medium text-amber-700 transition hover:bg-amber-100">
                      →{u.kind_label} #{u.id}{u.status === 'published' ? '（已发布）' : ''}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-3 text-[12px] font-medium tracking-wide text-slate-400">内容类型（知识库 → 公众号长文 + 小红书图文两出口，2026-11-18 定版）</div>
            <div className="mt-1.5 space-y-2">
              {([
                { v: 'xhs', title: '小红书 · 图文笔记', desc: '生产资料包（因果链/操作/边界/数字账）喂入 → 收藏体清单化 + 标题点击力评分选主' },
                { v: 'article', title: '公众号 · 改编长文', desc: '立即改编：资料包保深度成文 → 原创度闸 → 双审校 → 排版，完成后进「公众号 · 清单」' },
              ] as const).map((o) => (
                <button key={o.v} onClick={() => setProd({ ...prod, dir: o.v })}
                  className={`block w-full rounded-xl border px-3.5 py-2.5 text-left transition ${prod.dir === o.v ? 'border-emerald-400 bg-emerald-50' : 'border-[#E8ECF0] hover:border-slate-300'}`}>
                  <div className="text-[13.5px] font-semibold text-slate-900">{o.title}</div>
                  <div className="mt-0.5 text-[12px] text-slate-500">{o.desc}</div>
                </button>
              ))}
            </div>

            {prod.dir === 'xhs' && (
              <div className="mt-4 space-y-3">
                <div>
                  <div className="text-[12px] font-medium tracking-wide text-slate-400">选题标题</div>
                  <input value={prod.xhsTitle} onChange={(e) => setProd({ ...prod, xhsTitle: e.target.value })}
                    className="mt-1.5 w-full rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                </div>
                <div>
                  <div className="text-[12px] font-medium tracking-wide text-slate-400">切角 / 要求（给生成器的指令）</div>
                  <textarea value={prod.xhsAngle} onChange={(e) => setProd({ ...prod, xhsAngle: e.target.value })} rows={3}
                    className="mt-1.5 w-full resize-none rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] leading-relaxed outline-none focus:border-emerald-400" />
                </div>
              </div>
            )}

            {prod.dir === 'article' && (
              <div className="mt-4 rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] leading-relaxed text-slate-500">
                点「确认生成」立即开始改编（约几分钟）：按文档的改编角度保骨架、保原话，过原创度闸与双审校后排版，
                完成进「公众号 · 清单」排版待发。
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setProd(null)}
                className="rounded-full border border-slate-200 px-4 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">取消</button>
              <button onClick={() => {
                const { doc, dir, xhsTitle, xhsAngle } = prod
                if (dir === 'xhs' && !xhsTitle.trim()) { setError('小红书选题标题不能为空'); return }
                setProd(null)
                if (dir === 'xhs') {
                  // 实时工作台（2026-11-18 用户裁决：生成在框里跑、结果在框里看、不行在框里改）
                  setGen({ phase: 'busy', label: '笔记生成中（0%）', text: '', dir: 'xhs' })
                  void (async () => {
                    try {
                      const idea = await api<{ id: number }>('/channels/xhs/ideas', {
                        method: 'POST', body: JSON.stringify({ title: xhsTitle, angle: xhsAngle, material_ids: [doc.id] }),
                      })
                      const c = await api<{ job_id: number; content_id: number }>(`/channels/xhs/ideas/${idea.id}/compose`, { method: 'POST' })
                      await waitJob(c.job_id, (j) => setGen((g) => (g ? { ...g, label: `笔记生成中（${j.progress}%）${j.message || ''}` } : g)))
                      const n = await api<{ title: string; body: string }>(`/channels/xhs/notes/${c.content_id}`)
                      setGen({ phase: 'done', label: '', text: n.body || '', noteId: c.content_id, dir: 'xhs' })
                    } catch (e) { setGen(null); setError(`笔记生成失败：${apiErrText(e)}`) }
                  })()
                } else {
                  void toArticle(doc)
                }
              }}
                className="rounded-full bg-emerald-600 px-4 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">确认生成</button>
            </div>
          </div>
        </div>,
        document.body)}

      {/* 实时生成工作台（2026-11-18 用户裁决：生成在框里跑、结果在框里看、不行在框里说改） */}
      {gen && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#0a2f24]/40 px-4">
          <div className="w-full max-w-xl rounded-2xl bg-white p-5 shadow-xl">
            <div className="text-[15px] font-bold text-slate-900">生成内容 · 小红书笔记</div>
            {gen.phase === 'busy' ? (
              <div className="mt-5 space-y-2">
                <div className="flex items-center gap-2 text-[13px] text-slate-600">
                  <RefreshCw size={14} className="inline animate-spin text-emerald-600" /> {gen.label || '生成中…'}
                </div>
                <div className="num-serif text-[20px] font-bold text-[#0a2f24]">
                  {Math.floor(elapsed / 60)} 分 {elapsed % 60} 秒
                </div>
                <div className="text-[11.5px] leading-relaxed text-slate-400">
                  计时在走就是活着。写作/打磨是单次大模型长调用，几分钟不跳进度是正常的；挂热点时多一道入题检查，可能多一倍时长。
                </div>
              </div>
            ) : (
              <div className="mt-3">
                <div className="max-h-80 overflow-y-auto whitespace-pre-wrap rounded-lg bg-[#f7f8fa] px-3.5 py-3 text-[13px] leading-relaxed text-slate-700">{gen.text}</div>
                <div className="mt-3">
                  <div className="text-[12px] font-medium tracking-wide text-slate-400">不满意？说哪里不行，我按你的话改：</div>
                  <textarea value={fb} onChange={(e) => setFb(e.target.value)} rows={2}
                    placeholder="例：开头再狠一点，直接质问；故事砍一半；结尾别引导"
                    className="mt-1.5 w-full resize-none rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] leading-relaxed outline-none focus:border-emerald-400" />
                  <button onClick={() => {
                    if (!gen || !fb.trim()) return
                    const f = fb.trim()
                    const { noteId } = gen
                    setGen({ ...gen, phase: 'busy', label: '按反馈修改中…' })
                    void (async () => {
                      try {
                        const r = await api<{ job_id: number }>(`/channels/xhs/notes/${noteId}/revise`, { method: 'POST', body: JSON.stringify({ feedback: f }) })
                        await waitJob(r.job_id, (j) => setGen((g) => (g ? { ...g, label: `修改中（${j.progress}%）` } : g)))
                        const d = await api<{ body: string }>(`/channels/xhs/notes/${noteId}`)
                        setGen({ phase: 'done', label: '', text: d.body || '', noteId, dir: 'xhs' })
                        setFb('')
                      } catch (e) { setGen({ phase: 'done', label: '', text: gen.text, noteId, dir: 'xhs' }); setError(`修改失败：${apiErrText(e)}`) }
                    })()
                  }}
                    disabled={!fb.trim()}
                    className="mt-2 rounded-full bg-emerald-600 px-4 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">
                    按反馈修改
                  </button>
                </div>
                <div className="mt-4 flex justify-end">
                  <button onClick={() => {
                    setMsg('笔记已生成 → 「小红书」tab 改稿发布')
                    setGen(null); setFb('')
                  }}
                    className="rounded-full border border-slate-200 px-4 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
                    完成，去定稿
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>,
        document.body)}
    </div>
  )
}

type Tab = 'topics' | 'watch' | 'kb'

/** 旧 tab 键（收件箱/全部素材/采集源/账号候选）→ 新三 tab 兼容映射。 */
function normalizeTab(tabId?: string): Tab {
  if (tabId === 'sources' || tabId === 'cand') return 'watch'
  if (tabId === 'kb') return 'kb'
  return 'topics' // inbox / all / 未知 → 推荐选题
}

export default function Materials({ onNavigate, tabId, onTabChange }: {
  onNavigate?: (k: string) => void; tabId?: string; onTabChange?: (t: string) => void
} = {}) {
  const [tab, setTab] = useState<Tab>(normalizeTab(tabId))

  const pick = (t: Tab) => { setTab(t); onTabChange?.(t) }
  const tabs: { id: Tab; label: string }[] = [
    { id: 'topics', label: '推荐选题' },
    { id: 'watch', label: '监控清单' },
    { id: 'kb', label: '知识库' },
  ]

  return (
    <div>
      <PageHeader icon={Package} title="素材库"
        desc="唯一进料口：同行监控、话题雷达、知识库与一手素材都落在这里——推荐选题按热度供四条内容线取料，发布口径不变（人工）。" />
      <div className="mb-5 flex items-center gap-1.5 border-b border-[#E8ECF0] pb-2.5">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => pick(t.id)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${tab === t.id ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'topics' && <TopicsTab />}
      {tab === 'watch' && <WatchTab />}
      {tab === 'kb' && <KbTab onNavigate={onNavigate} />}
    </div>
  )
}
