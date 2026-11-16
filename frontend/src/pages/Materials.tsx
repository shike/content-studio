import { useCallback, useEffect, useState } from 'react'
import {
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
 * 素材库（重构 P2，PRD R1）：唯一进料口——收件箱定夺 / 全部素材检索 / 采集源管理 / 账号候选定夺。
 * 四类采集（同行账号/话题雷达/知识库/热点）统一落到这里；内容线只从这里取料。
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

/** 收件箱：新采集素材定夺（A=收下并拆解 / X=忽略） */
function InboxTab({ onLoaded }: { onLoaded?: (n: number) => void }) {
  const [rows, setRows] = useState<MaterialRow[]>([])
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = useCallback(() => {
    api<{ items: MaterialRow[]; total: number }>('/materials?status=inbox&origin_type=douyin_video&limit=100')
      .then((d) => { setRows(d.items || []); onLoaded?.(d.total); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [onLoaded])
  useEffect(() => { load() }, [load])

  const decide = async (m: MaterialRow, action: 'approve' | 'ignore') => {
    setError('')
    try {
      if (action === 'approve') {
        const r = await api<{ job_id: number }>(`/materials/${m.id}/status`, {
          method: 'POST', body: JSON.stringify({ status: 'ready' }),
        })
        setMsg(`已收下并排队拆解（任务 #${r.job_id}）`)
      } else {
        await api(`/materials/${m.id}/status`, { method: 'POST', body: JSON.stringify({ status: 'ignored' }) })
        setMsg('已忽略')
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

  return (
    <div>
      <Toolbar
        left={<span className="text-[13px] text-slate-600">待定夺 {rows.length} 条（过拆解闸自动批准的不会出现在这里）</span>}
        actions={<>
          <button onClick={() => batch('approve')} disabled={!rows.length}
            className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">
            全部收下
          </button>
          <button onClick={() => batch('ignore')} disabled={!rows.length}
            className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50 disabled:opacity-40">
            全部忽略
          </button>
        </>} />
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />
      {!loaded ? null : rows.length === 0 ? (
        <EmptyState icon={Inbox} title="收件箱是空的"
          desc="同行扫描/话题雷达的新视频会先进这里；过质量闸（评论≥20或赞≥300）的自动入库拆解" />
      ) : (
        <div className="space-y-2">
          {rows.map((m) => {
            const open = expanded === m.id
            return (
              <div key={m.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => setExpanded(open ? null : m.id)}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700">新</span>
                  <KindBadge m={m} />
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">
                    {m.title || `素材 #${m.id}`}
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">@{m.author || '?'}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">
                    评 {m.stats?.comment ?? '—'} · 赞 {m.stats?.digg ?? '—'} · {relTime(m.created_at || '')}
                  </span>
                  <button onClick={(e) => { e.stopPropagation(); decide(m, 'approve') }} title="收下并排队拆解"
                    className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                    <Check size={13} className="inline" /> 收下
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); decide(m, 'ignore') }} title="忽略"
                    className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-500 transition hover:bg-slate-50">
                    <X size={13} className="inline" /> 忽略
                  </button>
                </div>
                {open && <MaterialDetail m={m} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 全部素材：检索 + 行内展开 + 删除/强制拆 */
function AllTab() {
  const [rows, setRows] = useState<MaterialRow[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [author, setAuthor] = useState('')
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: '50' })
    if (status) params.set('status', status)
    if (q.trim()) params.set('q', q.trim())
    if (author.trim()) params.set('author', author.trim())
    api<{ items: MaterialRow[]; total: number }>(`/materials?${params}`)
      .then((d) => { setRows(d.items || []); setTotal(d.total); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [status, q, author])
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
        setMsg(action === 'ready' ? '已收下' : '已忽略')
      }
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const chip = (v: string, label: string) => (
    <button key={v || 'all'} onClick={() => setStatus(v)}
      className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${status === v ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
      {label}
    </button>
  )

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜标题 / 作者 / 转写…"
          className="w-64 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="按作者筛"
          className="w-36 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <div className="flex items-center gap-1">
          {chip('', '全部')}{chip('ready', '可用')}{chip('inbox', '收件箱')}{chip('ignored', '已忽略')}
        </div>
        <span className="text-[12px] text-slate-400">共 {total} 条</span>
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />
      {!loaded ? null : rows.length === 0 ? (
        <EmptyState icon={Package} title="没有匹配的素材" desc="换个筛选条件，或先去采集源页扫一轮" />
      ) : (
        <div className="space-y-2">
          {rows.map((m) => {
            const open = expanded === m.id
            const stLabel = m.status === 'ready' ? '可用' : m.status === 'inbox' ? '收件箱' : m.status === 'ignored' ? '已忽略' : '归档'
            return (
              <div key={m.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => setExpanded(open ? null : m.id)}>
                <div className="flex flex-wrap items-center gap-2">
                  <KindBadge m={m} />
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${m.status === 'ready' ? 'bg-emerald-50 text-emerald-700' : m.status === 'inbox' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-500'}`}>
                    {stLabel}{m.status === 'ready' && !m.analyzed ? '·待拆' : ''}{m.analyzed ? '·已拆' : ''}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">
                    {m.title || `素材 #${m.id}`}
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">@{m.author || '?'}</span>
                  <span className="shrink-0 text-[11px] text-slate-400">热度 {m.quality_score ?? '—'} · {relTime(m.created_at || '')}</span>
                  {m.status !== 'ready' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'ready') }}
                      className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                      收下
                    </button>
                  )}
                  {m.origin_type === 'douyin_video' && (
                    <button onClick={(e) => { e.stopPropagation(); act(m, 'enrich') }} title="强制（重）拆解"
                      className="shrink-0 rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                      <RefreshCw size={13} className="inline" /> 拆解
                    </button>
                  )}
                  <button onClick={(e) => { e.stopPropagation(); act(m, 'delete') }} title="删除素材"
                    className="shrink-0 rounded-md border border-red-100 px-2 py-1 text-[12px] text-red-500 transition hover:bg-red-50">
                    <Trash2 size={13} />
                  </button>
                </div>
                {open && <MaterialDetail m={m} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 采集源：同行账号 + 话题雷达（添加/启停/单扫/全扫/回填/贴链识别/扫描历史入口） */
function SourcesTab({ onNavigate }: { onNavigate?: (k: string) => void }) {
  const [rows, setRows] = useState<SourceRow[]>([])
  const [kind, setKind] = useState<'peer_account' | 'radar_topic'>('peer_account')
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState({ name: '', url: '', ch_id: '', account_type: 'persona' })
  const [resolveUrl, setResolveUrl] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api<{ items: SourceRow[] }>(`/sources?kind=${kind}`)
      .then((d) => { setRows(d.items || []); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [kind])
  useEffect(() => { load() }, [load])

  const runJob = async (submit: () => Promise<{ job_id: number }>, label: string) => {
    setError('')
    try {
      const r = await submit()
      setMsg(`${label}任务 #${r.job_id} 已提交`)
      waitJob(r.job_id, (j) => setMsg(`${label}：${j.progress}% ${j.message || ''}`))
        .then(() => { setMsg(`${label}完成`); load() })
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

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <button onClick={() => setKind('peer_account')}
          className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${kind === 'peer_account' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
          同行账号
        </button>
        <button onClick={() => setKind('radar_topic')}
          className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${kind === 'radar_topic' ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
          话题雷达
        </button>
      </div>
      {kind === 'peer_account' ? (
        <Toolbar
          left={<span className="text-[13px] text-slate-600">启用 {rows.filter((r) => r.enabled).length} / {rows.length} 个采集源</span>}
          actions={<>
            <button onClick={() => runJob(() => api('/sources/sync-all', { method: 'POST' }), '全量扫描')}
              className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
              <RefreshCw size={13} className="inline" /> 全量扫描
            </button>
            <button onClick={() => runJob(() => api('/materials/backfill', { method: 'POST', body: JSON.stringify({ scope: 'peer' }) }), '存量回填')}
              className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
              存量回填
            </button>
            <button onClick={() => onNavigate?.('tasks')}
              className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
              扫描历史
            </button>
          </>} />
      ) : (
        <Toolbar
          left={<span className="text-[13px] text-slate-600">监控 {rows.length} 个话题（每日 6 点随选题工场巡检）</span>}
          actions={<button onClick={() => runJob(() => api('/sources/backfill-topics', { method: 'POST' }), '话题回填')}
            className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
            存量回填
          </button>} />
      )}
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />

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
        <div className="mt-2 text-[11.5px] text-slate-400">
          本地视频/音频走「贴链拆解」旁的上传口：
          <label className="ml-1 cursor-pointer text-sky-600 hover:underline">
            <Upload size={12} className="inline" /> 上传拆解
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
          ；贴单条视频链接在线拆解：
          <button onClick={() => runJob(() => api('/materials/analyze-url', { method: 'POST', body: JSON.stringify({ url: resolveUrl }) }), '在线拆解')}
            className="ml-1 text-sky-600 hover:underline">拆这条</button>
        </div>
      </div>

      {!loaded ? null : rows.length === 0 ? (
        <EmptyState icon={Radar} title={kind === 'peer_account' ? '还没有同行采集源' : '还没有监控话题'}
          desc={kind === 'peer_account' ? '贴同行主页链接添加；也可以用「账号候选」页的搜账号发现' : '贴一条带话题的视频链接提取 ch_id 后添加'} />
      ) : (
        <div className="space-y-2">
          {rows.map((r) => (
            <div key={r.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${r.enabled ? 'bg-emerald-50 text-emerald-700' : r.status === 'disabled' ? 'bg-slate-100 text-slate-500' : 'bg-amber-50 text-amber-700'}`}>
                {r.enabled ? '监控中' : r.status === 'disabled' ? '已停用' : '候选'}
              </span>
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
              <button onClick={() => runJob(() => api(`/sources/${r.id}/sync`, { method: 'POST' }), `扫描 ${r.name}`)}
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
    </div>
  )
}

/** 账号候选：发现产出待定夺（批准加入 / 忽略）+ 搜账号 + 同城矩阵补跑 */
function CandidatesTab() {
  const [rows, setRows] = useState<SourceRow[]>([])
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [kw, setKw] = useState('')
  const [dir, setDir] = useState('')
  const [line, setLine] = useState<'all' | 'persona' | 'leadgen'>('all')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api<{ items: SourceRow[] }>('/sources?status=candidate')
      .then((d) => { setRows(d.items || []); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [])
  useEffect(() => { load() }, [load])

  const filtered = rows.filter((r) => {
    if (r.dismissed) return false
    if (line === 'all') return true
    return (r.config?.account_type || 'persona') === line
  })
  const dismissedN = rows.filter((r) => r.dismissed).length

  const act = async (r: SourceRow, action: 'activate' | 'dismiss') => {
    setError('')
    try {
      const res = await api<{ duplicate?: boolean }>(`/sources/${r.id}/${action}`, { method: 'POST' })
      setMsg(action === 'activate'
        ? (res.duplicate ? '该账号已在清单（候选已标已处理）' : `已加入采集清单：${r.name}`)
        : '已忽略')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  return (
    <div>
      <Toolbar
        left={<span className="text-[13px] text-slate-600">待定夺 {filtered.length} 条（已忽略 {dismissedN} 条不显示）</span>}
        actions={<>
          <button onClick={() => api('/sources', { method: 'DELETE' }).then((r: any) => { setMsg(`已清 ${r.deleted} 条已处理候选`); load() })}
            className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
            清已忽略
          </button>
          <button onClick={() => runJobMsg(setMsg, setError, () => api('/sources/hl-search', { method: 'POST' }), '同城矩阵')}
            className="rounded-full border border-slate-200 px-3 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">
            同城矩阵补跑
          </button>
        </>} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={kw} onChange={(e) => setKw(e.target.value)} placeholder="关键词（如 FDE、企业AI落地）"
          className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="方向（可选）"
          className="w-32 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
        <button onClick={() => runJobMsg(setMsg, setError, () => api('/sources/discover', { method: 'POST', body: JSON.stringify({ keyword: kw, direction: dir }) }), '搜账号')}
          className="rounded-full bg-emerald-600 px-3 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
          搜账号
        </button>
        <div className="ml-auto flex items-center gap-1">
          {(['all', 'persona', 'leadgen'] as const).map((ln) => (
            <button key={ln} onClick={() => setLine(ln)}
              className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${line === ln ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
              {ln === 'all' ? '全部' : ln === 'persona' ? '人设' : '获客'}
            </button>
          ))}
        </div>
      </div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />
      {!loaded ? null : filtered.length === 0 ? (
        <EmptyState icon={Inbox} title="没有待定夺的候选账号" desc="用上面的「搜账号」关键词发现，或每晚同城矩阵自动挖" />
      ) : (
        <div className="space-y-2">
          {filtered.map((r) => {
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
                  <button onClick={() => act(r, 'activate')}
                    className="shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 transition hover:bg-emerald-100">
                    加入
                  </button>
                  <button onClick={() => act(r, 'dismiss')}
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
    </div>
  )
}

async function runJobMsg(setMsg: (s: string) => void, setError: (s: string) => void,
  submit: () => Promise<{ job_id: number }>, label: string) {
  try {
    const r = await submit()
    setMsg(`${label}任务 #${r.job_id} 已提交`)
  } catch (e) { setError(apiErrText(e)) }
}

type Tab = 'inbox' | 'all' | 'sources' | 'cand'

export default function Materials({ onNavigate, tabId }: { onNavigate?: (k: string) => void; tabId?: string } = {}) {
  const [tab, setTab] = useState<Tab>((tabId as Tab) || 'inbox')
  const [inboxN, setInboxN] = useState(0)

  const tabs: { id: Tab; label: string; badge?: number }[] = [
    { id: 'inbox', label: '收件箱', badge: inboxN },
    { id: 'all', label: '全部素材' },
    { id: 'sources', label: '采集源' },
    { id: 'cand', label: '账号候选' },
  ]

  return (
    <div>
      <PageHeader icon={Package} title="素材库"
        desc="唯一进料口：同行监控、话题雷达、知识库、站外热点与一手素材都落在这里；人设/获客/公众号/小红书四条内容线只从这里取料。" />
      <div className="mb-5 flex items-center gap-1.5 border-b border-[#E8ECF0] pb-2.5">
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${tab === t.id ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'}`}>
            {t.label}{t.badge ? ` ${t.badge}` : ''}
          </button>
        ))}
      </div>
      {tab === 'inbox' && <InboxTab onLoaded={setInboxN} />}
      {tab === 'all' && <AllTab />}
      {tab === 'sources' && <SourcesTab onNavigate={onNavigate} />}
      {tab === 'cand' && <CandidatesTab />}
    </div>
  )
}
