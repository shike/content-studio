import { useCallback, useEffect, useState } from 'react'
import {
  BookOpen,
  Check,
  FolderPlus,
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

interface KbOverview {
  configured: boolean
  folders: { folder_id: string; name: string; docs: number; latest: string | null }[]
  unknown_folder_docs: number
  docs_total: number
  stage: { pending: number; low: number }
  last_mine: { status: string; message: string; updated_at: string } | null
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

/** 状态圆片（页内 chips：tab=实体，chips=生命周期，徽章=待办）。 */
function Chips({ items, value, onPick }: { items: { v: string; label: string }[]; value: string; onPick: (v: string) => void }) {
  return (
    <div className="flex items-center gap-1">
      {items.map((c) => (
        <button key={c.v} onClick={() => onPick(c.v)}
          className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${value === c.v ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
          {c.label}
        </button>
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

/** 知识库：ima 源这条线的管理台——目录清单增删 + 挖掘控制；文档本体在「推荐选题」可检索。 */
function KbTab() {
  const [ov, setOv] = useState<KbOverview | null>(null)
  const [form, setForm] = useState({ folder_id: '', name: '' })
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api<KbOverview>('/kb/overview').then(setOv)
      .catch((e) => setError(apiErrText(e)))
  }, [])
  useEffect(() => { load() }, [load])

  const addFolder = async () => {
    setError('')
    try {
      await api('/kb/folders', { method: 'POST', body: JSON.stringify(form) })
      setForm({ folder_id: '', name: '' })
      setMsg('已加入挖掘目录')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const removeFolder = async (fid: string, name: string) => {
    setError('')
    try {
      await api(`/kb/folders/${encodeURIComponent(fid)}`, { method: 'DELETE' })
      setMsg(`已移除目录「${name}」——已入库文档不受影响`)
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const mine = async () => {
    setError(''); setBusy(true)
    try {
      const r = await api<{ job_id: number }>('/articles/mine', { method: 'POST' })
      await waitJob(r.job_id, (j) => setMsg(`挖掘：${j.progress}% ${j.message || ''}`))
      setMsg('挖掘完成')
      load()
    } catch (e) { setError(`挖掘失败：${apiErrText(e)}`) } finally { setBusy(false) }
  }

  return (
    <div>
      <Toolbar
        left={ov ? <>
          <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${ov.configured ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}`}>
            {ov.configured ? '凭证已配置' : '凭证未配置（设置页 ima_*）'}
          </span>
          <span className="text-[13px] text-slate-600">
            共入库 <b className="num-serif text-slate-900">{ov.docs_total}</b> 篇
            {ov.stage.pending > 0 && <> · 待改编 {ov.stage.pending}</>}
            {ov.last_mine && <> · 最近挖掘 {relTime(ov.last_mine.updated_at)}：{ov.last_mine.message?.slice(0, 40) || ov.last_mine.status}</>}
          </span>
        </> : <span className="text-[13px] text-slate-400">载入中…</span>}
        actions={<button onClick={mine} disabled={busy || (ov != null && !ov.configured)}
          className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">
          <RefreshCw size={13} className="inline" /> 立即挖掘
        </button>} />
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />

      <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
        <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
          添加挖掘目录（ima 知识库的 folder_id；每日 04:00 自动翻一遍，评分过线入库进「推荐选题」）
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input value={form.folder_id} onChange={(e) => setForm({ ...form, folder_id: e.target.value })}
            placeholder="folder_id（如 folder_7520…）"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="目录名称"
            className="w-44 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <button onClick={addFolder} disabled={!form.folder_id.trim()}
            className="rounded-full bg-[#0a2f24] px-3 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
            <FolderPlus size={13} className="inline" /> 添加
          </button>
        </div>
      </div>

      {!ov ? null : ov.folders.length === 0 ? (
        <EmptyState icon={BookOpen} title="还没有挖掘目录" desc="上面添加第一个目录；没有目录时挖掘器用内置默认 12 目录" />
      ) : (
        <div className="space-y-2">
          {ov.folders.map((f) => (
            <div key={f.folder_id} className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500">知识库目录</span>
              <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{f.name}</span>
              <span className="shrink-0 text-[11px] text-slate-400">
                入库 <b className="num-serif text-slate-800">{f.docs}</b> 篇{f.latest ? ` · 最近 ${relTime(f.latest)}` : ' · 还没挖到过'}
              </span>
              <button onClick={() => removeFolder(f.folder_id, f.name)}
                className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                移除
              </button>
            </div>
          ))}
          {ov.unknown_folder_docs > 0 && (
            <div className="px-1 text-[12px] text-slate-400">
              另有 {ov.unknown_folder_docs} 篇早期入库文档目录标记缺失，仍在「推荐选题」可检索
            </div>
          )}
        </div>
      )}
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
      {tab === 'kb' && <KbTab />}
    </div>
  )
}
