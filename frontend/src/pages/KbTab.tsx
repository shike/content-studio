import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { BookOpen, Loader2, Pickaxe, RefreshCw } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { ConfirmDeleteButton, EmptyState, ErrorLine, relTime } from '../components'
import { Chips } from './MaterialsShared'

/**
 * 知识库（2026-11-18 用户定版，两处分工）：
 * - mode="full"（素材库 · 知识库）=全库内容榜；
 * - mode="today"（今天 · 知识库）=今日可写推荐：只出**未产出**的文档，分数降序——
 *   产出过的沉到全库，避免首页推荐全是已写过的东西。
 */

/* ─────────────────────────── Tab ③ 知识库 ─────────────────────────── */

interface KbDoc {
  id: number
  title: string
  author: string
  folder: string
  tags: string[]
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
 * chips=体裁轴（2026-11-18 用户定版：标签=「这是什么货」7 词表，主1+次1；资讯/软文类不打标
 * 直接沉底——目录降为行内小字，不再做导航轴）。
 */
/** 稿件 kind → 跳转键（onNavigate 走 App 的 NAVMAP：带 tab 的旧键直达页内） */
const KIND_NAV: Record<string, string> = { video_script: 'p_content', note: 'xhs', long_article: 'articles' }

/** 体裁词表（2026-11-18 用户定版 7 个，生产有用的货；打标=kb_genre_tag.py，主在前） */
const KB_GENRES = ['方法论', '客户案例', '观点判断', '操作教程', '工具资源', '学习路线', '职业发展']

/** 今天·知识库只放未产出的前 100 篇（2026-11-18 用户定版），素材库·知识库仍全库 */
const TODAY_LIMIT = 100

/** 改编链状态徽章（2026-11-18：公众号「清单」并入知识库——状态回到行上）。 */
const STAGE_META: Record<string, { label: string; cls: string }> = {
  pending: { label: '待改编', cls: 'bg-amber-50 text-amber-700' },
  running: { label: '改编中', cls: 'bg-sky-50 text-sky-700' },
  done: { label: '已改编', cls: 'bg-emerald-50 text-emerald-700' },
  published: { label: '已改编·已发', cls: 'bg-emerald-100 text-emerald-800' },
  low: { label: '低分未入', cls: 'bg-slate-100 text-slate-400' },
  dismissed: { label: '不做', cls: 'bg-slate-100 text-slate-400' },
}

/** 选题会现场（2026-11-21 阶段 2）：article_pitch 出的 5 个可单独成篇的现场 */
interface ArticlePitch {
  scene: string
  thesis: string
  title: string
  evidence: string[]
}

/** 笔记角度三元组（2026-11-21 阶段 3）：note_pitch 出的 5 个角度 */
interface NoteAngle {
  audience: string
  pain: string
  keyword: string
  title: string
  hook: string
}

export default function KbTab({ onNavigate, mode = 'full' }: {
  onNavigate?: (k: string) => void; mode?: 'full' | 'today'
}) {
  const [docs, setDocs] = useState<KbDoc[]>([])          // 全量（无搜索词时的数据源）
  const [searched, setSearched] = useState<KbDoc[] | null>(null) // 全文检索结果（null=未在搜索）
  const [genre, setGenre] = useState('')
  const [q, setQ] = useState('')
  const [limit, setLimit] = useState(100)                // 触底续载
  const [bodies, setBodies] = useState<Record<number, string>>({}) // 已拉取的全文缓存
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  // 挖掘/改编日账（2026-11-18 从「公众号·清单」迁入）
  interface LedgerRow { id: number; phase: string; produced: string; reason: string; anomaly?: boolean }
  const [ledger, setLedger] = useState<LedgerRow[]>([])
  const [mining, setMining] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [edit, setEdit] = useState<{ id: number; title: string } | null>(null)
  const [prod, setProd] = useState<null | {
    doc: KbDoc
    dir: 'xhs' | 'article'
    xhsTitle: string
    xhsAngle: string
    meeting: null | { loading: boolean; err: string; items: (ArticlePitch | NoteAngle)[] }
    pickedIdx: number | null
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
      meeting: null, pickedIdx: null,
    })
  }

  /** 开选题会（2026-11-21 阶段 2/3）：现场（长文）或角度三元组（笔记），人选定后喂对应证据。 */
  const loadMeeting = (docId: number, kind: 'article' | 'note', restart = false) => {
    setProd((p) => (p ? { ...p, meeting: { loading: true, err: '', items: restart ? [] : (p.meeting?.items || []) }, pickedIdx: null } : p))
    void (async () => {
      try {
        const ep = kind === 'article' ? 'pitches' : 'note-angles'
        const r = await api<{ job_id: number }>(`/articles/content-items/${docId}/${ep}`, { method: 'POST' })
        const j = await waitJob(r.job_id, (jj) => setProd((p) => (p && p.meeting?.loading
          ? { ...p, meeting: { ...p.meeting, err: `${jj.progress}% ${jj.message || ''}` } } : p)))
        const res = (j as { result?: { pitches?: ArticlePitch[]; angles?: NoteAngle[] } })?.result
        const items = (kind === 'article' ? res?.pitches : res?.angles) || []
        setProd((p) => (p ? { ...p, meeting: { loading: false, err: items.length ? '' : '没出可写的条目', items } } : p))
      } catch (e) {
        setProd((p) => (p ? { ...p, meeting: { loading: false, err: apiErrText(e), items: [] } } : p))
      }
    })()
  }

  /** 选定笔记角度：三元组填进选题标题/切角（仍可手改）。 */
  const pickAngle = (a: NoteAngle, idx: number) => {
    setProd((p) => (p ? {
      ...p, pickedIdx: idx,
      xhsTitle: a.title,
      xhsAngle: `写给：${a.audience}；痛点：${a.pain}；搜索词：${a.keyword}（标题与正文首行必须含它）${a.hook ? `；可用硬事实：${a.hook}` : ''}`,
    } : p))
  }

  const mine = async () => {
    setMining(true); setError('')
    try {
      await api('/articles/mine', { method: 'POST' })
      setTimeout(() => { setMining(false); load() }, 1500)
    } catch (e) { setError(apiErrText(e)); setMining(false) }
  }

  const setStage = async (d: KbDoc, status: 'pending' | 'dismissed') => {
    setError('')
    try {
      await api(`/articles/content-items/${d.id}/status`, {
        method: 'POST', body: JSON.stringify({ status }),
      })
      setMsg(status === 'dismissed' ? '已标不做（文档保留）' : '已恢复待改编')
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const removeDoc = async (d: KbDoc) => {
    setError('')
    try {
      await api(`/materials/${d.id}`, { method: 'DELETE' })
      setMsg(`已删除《${(d.title || '').slice(0, 20)}》`)
      setExpanded(null)
      load()
    } catch (e) { setError(apiErrText(e)) }
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
    if (mode !== 'today') {
      api<{ items: any[] }>('/articles/ledger?limit=10')
        .then((d) => setLedger(d.items || [])).catch(() => {})
    }
  }, [mode])
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

  // today 模式=只留未产出的文档（今天可写什么）；素材库 full 模式=全库
  const statsSrc = useMemo(
    () => (mode === 'today' ? docs.filter((d) => (d.used || []).length === 0) : docs),
    [docs, mode])
  // 体裁 chips 计数（客户端聚合：docs 已全量在内存）
  const genreCounts = useMemo(() => {
    const m = new Map<string, number>()
    for (const d of statsSrc) for (const t of (d.tags || [])) m.set(t, (m.get(t) || 0) + 1)
    return m
  }, [statsSrc])
  const base = (searched ?? docs).filter((d) => mode !== 'today' || (d.used || []).length === 0)
  // today 模式（2026-11-18 用户定版）：只放未产出的前 100 篇，按评分降序（无分垫底）
  const sorted = mode !== 'today' ? base
    : [...base].sort((a, b) => (b.score ?? -1) - (a.score ?? -1)).slice(0, TODAY_LIMIT)
  const shown = sorted.filter((d) => !genre || (d.tags || []).includes(genre))
  const vis = mode === 'today' ? shown : shown.slice(0, limit)

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

  const toArticle = async (d: KbDoc, pitch?: ArticlePitch) => {
    setError('')
    try {
      const r = await api<{ job_id: number }>(`/articles/content-items/${d.id}/generate`, {
        method: 'POST',
        body: JSON.stringify(pitch ? { pitch } : {}),
      })
      setMsg(pitch
        ? `按现场《${(pitch.title || '').slice(0, 20)}》改编中（任务 #${r.job_id}）——只喂证据包成文约需几分钟`
        : `长文改编中（任务 #${r.job_id}）——保骨架保原话成文约需几分钟`)
      await waitJob(r.job_id, (j) => setMsg(`改编：${j.progress}% ${j.message || ''}`))
      setMsg('长文已生成 → 「公众号 · 清单」排版待发')
      load()
    } catch (e) { setError(`改编失败：${apiErrText(e)}`) }
  }

  const phaseLine = (phase: string, name: string) => {
    const r = ledger.find((x) => x.phase === phase)
    if (!r) return { text: `${name}：还没跑过`, anomaly: false }
    if (r.anomaly) return { text: `${name}：成功但无产出且无原因——异常，去任务页查`, anomaly: true }
    return { text: `${name}：${r.produced || r.reason || '空过'}`, anomaly: false }
  }
  const mineLine = phaseLine('mine', '昨次挖掘')
  const adaptLine = phaseLine('adapt', '昨次改编')

  return (
    <div>
      {msg && <div className="mb-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800">{msg}</div>}
      <ErrorLine text={error} />
      {mode !== 'today' && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-[13px] text-slate-500">
          <span className={mineLine.anomaly ? 'font-medium text-red-600' : ''}>{mineLine.text}</span>
          <span className="text-slate-300">·</span>
          <span className={adaptLine.anomaly ? 'font-medium text-red-600' : ''}>{adaptLine.text}</span>
          <button className="btn-ghost btn-sm ml-auto flex items-center gap-1" disabled={mining} onClick={mine}>
            {mining ? <Loader2 size={13} className="animate-spin" /> : <Pickaxe size={13} />} 手动补挖
          </button>
        </div>
      )}

      {mode !== 'today' && <div className="mb-3 space-y-2">
        <Chips items={[
          { v: '', label: `全部 ${statsSrc.length}` },
          ...KB_GENRES.map((g) => ({ v: g, label: `${g} ${genreCounts.get(g) || 0}` })),
        ]} value={genre} onPick={(v) => { setGenre(v); setLimit(100) }} />
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
      </div>}

      {shown.length === 0 ? (
        <EmptyState icon={BookOpen}
          title={mode === 'today'
            ? (statsSrc.length === 0 ? '今日推荐已清空——库里的文档都产出过了' : '没有匹配的文档')
            : (docs.length === 0 ? '知识库还没有入库文档' : '没有匹配的文档')}
          desc={mode === 'today'
            ? '全库与已产出记录在「素材库 · 知识库」；新文档每日 04:00 自动挖掘入库'
            : (docs.length === 0 ? '每日 04:00 自动挖掘进新文档' : '换个体裁或关键词')} />
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
                  {(() => {
                    const m = STAGE_META[d.stage || '']
                    return m ? <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${m.cls}`}>{m.label}</span> : null
                  })()}
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
                        <span className="text-[11px] text-slate-300">｜</span>
                        {(d.stage === 'pending' || d.stage === 'dismissed') && (
                          <button onClick={() => setStage(d, d.stage === 'pending' ? 'dismissed' : 'pending')}
                            className="rounded-md border border-slate-200 px-2.5 py-1 text-[12px] text-slate-600 transition hover:bg-slate-50">
                            {d.stage === 'pending' ? '不做（移出改编队列）' : '恢复待改编'}
                          </button>
                        )}
                        <ConfirmDeleteButton onConfirm={() => removeDoc(d)} title="删除这篇文档（素材删除）" />
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
                      {(d.tags || []).length > 0 && <span>体裁：{d.tags.join(' · ')}</span>}
                      {d.folder && <span>目录：{d.folder}</span>}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {mode !== 'today' && shown.length > limit && (
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
                { v: 'xhs', title: '小红书 · 图文笔记', desc: '先开角度会：人群+痛点+搜索词三元组，你选定一个 → 对着三元组成文 + 轻质量体检' },
                { v: 'article', title: '公众号 · 改编长文', desc: '先开选题会：源文抽 5 个现场，你选定一个 → 只喂证据包成文 → 原创度闸 → 双审校 → 排版' },
              ] as const).map((o) => (
                <button key={o.v} onClick={() => {
                  if (o.v === 'article' && !prod.meeting) {
                    setProd({ ...prod, dir: 'article' })
                    loadMeeting(prod.doc.id, 'article')  // 默认先开选题会（2026-11-21 阶段 2 定版）
                    return
                  }
                  if (o.v === 'xhs' && !prod.meeting) {
                    setProd({ ...prod, dir: 'xhs' })
                    loadMeeting(prod.doc.id, 'note')  // 默认先开角度会（2026-11-21 阶段 3 定版）
                    return
                  }
                  setProd({ ...prod, dir: o.v })
                }}
                  className={`block w-full rounded-xl border px-3.5 py-2.5 text-left transition ${prod.dir === o.v ? 'border-emerald-400 bg-emerald-50' : 'border-[#E8ECF0] hover:border-slate-300'}`}>
                  <div className="text-[13.5px] font-semibold text-slate-900">{o.title}</div>
                  <div className="mt-0.5 text-[12px] text-slate-500">{o.desc}</div>
                </button>
              ))}
            </div>

            {prod.dir === 'xhs' && (
              <div className="mt-4 space-y-3">
                {(() => {
                  const ps = prod.meeting
                  if (ps === null) {
                    return (
                      <div className="rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] leading-relaxed text-slate-500">
                        两步生成：先开角度会——从源文抽 5 个角度（人群+痛点+搜索词），你选定一个，生成对着三元组写。
                        <button onClick={() => loadMeeting(prod.doc.id, 'note')}
                          className="mt-2 block rounded-full bg-emerald-600 px-3.5 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                          开角度会
                        </button>
                      </div>
                    )
                  }
                  if (ps.loading) {
                    return (
                      <div className="flex items-center gap-2 rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] text-slate-500">
                        <RefreshCw size={13} className="inline animate-spin text-emerald-600" />
                        角度会进行中：{ps.err || '拉源文全文 → 抽角度…'}
                      </div>
                    )
                  }
                  if (ps.items.length > 0) {
                    return (
                      <>
                        <div className="text-[12px] font-medium tracking-wide text-slate-400">
                          选一个角度（三元组会填进下面的标题与切角，仍可手改）
                        </div>
                        <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
                          {(ps.items as NoteAngle[]).map((a, i) => (
                            <button key={i} onClick={() => pickAngle(a, i)}
                              className={`block w-full rounded-xl border px-3.5 py-2.5 text-left transition ${prod.pickedIdx === i ? 'border-emerald-400 bg-emerald-50' : 'border-[#E8ECF0] hover:border-slate-300'}`}>
                              <div className="text-[13.5px] font-semibold text-slate-900">{a.title}</div>
                              <div className="mt-1 text-[12px] leading-relaxed text-slate-600">写给 {a.audience}：{a.pain}</div>
                              <div className="mt-0.5 text-[11.5px] text-slate-400">搜索词：{a.keyword}{a.hook ? `｜硬事实：${a.hook.slice(0, 30)}` : ''}</div>
                            </button>
                          ))}
                        </div>
                        <div className="text-[11.5px] text-slate-400">
                          都不合意？<button onClick={() => loadMeeting(prod.doc.id, 'note', true)}
                            className="underline decoration-slate-300 underline-offset-2 hover:text-slate-600">重开一次</button>
                          ，或<button onClick={() => setProd({ ...prod, meeting: null, pickedIdx: null })}
                            className="underline decoration-slate-300 underline-offset-2 hover:text-slate-600">跳过，手动填</button>
                        </div>
                      </>
                    )
                  }
                  return (
                    <div className="rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] leading-relaxed text-slate-500">
                      角度会没开成：{ps.err || '没出合格角度'}
                      <div className="mt-2 flex gap-2">
                        <button onClick={() => loadMeeting(prod.doc.id, 'note', true)}
                          className="rounded-full border border-slate-200 px-3.5 py-1.5 text-[12px] text-slate-600 transition hover:bg-slate-50">重开一次</button>
                        <button onClick={() => setProd({ ...prod, meeting: null, pickedIdx: null })}
                          className="rounded-full border border-slate-200 px-3.5 py-1.5 text-[12px] text-slate-600 transition hover:bg-slate-50">跳过，手动填</button>
                      </div>
                    </div>
                  )
                })()}
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
              <div className="mt-4 space-y-3">
                {(() => {
                  const ps = prod.meeting
                  if (ps === null) {
                    return (
                      <div className="rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] leading-relaxed text-slate-500">
                        两步改编：先开选题会——从源文抽 5 个可单独成篇的现场（时间地点人动作数字），
                        你选定一个，写稿只喂那个现场的证据，不喂全书。
                        <button onClick={() => loadMeeting(prod.doc.id, 'article')}
                          className="mt-2 block rounded-full bg-emerald-600 px-3.5 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700">
                          开选题会
                        </button>
                      </div>
                    )
                  }
                  if (ps.loading) {
                    return (
                      <div className="flex items-center gap-2 rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] text-slate-500">
                        <RefreshCw size={13} className="inline animate-spin text-emerald-600" />
                        选题会进行中：{ps.err || '拉源文全文 → 抽现场…'}
                      </div>
                    )
                  }
                  if (ps.items.length > 0) {
                    return (
                      <>
                        <div className="text-[12px] font-medium tracking-wide text-slate-400">
                          选一个现场（写稿只喂它的证据包，标题优先用它）
                        </div>
                        <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                          {(ps.items as ArticlePitch[]).map((p, i) => (
                            <button key={i} onClick={() => setProd({ ...prod, pickedIdx: i })}
                              className={`block w-full rounded-xl border px-3.5 py-2.5 text-left transition ${prod.pickedIdx === i ? 'border-emerald-400 bg-emerald-50' : 'border-[#E8ECF0] hover:border-slate-300'}`}>
                              <div className="text-[13.5px] font-semibold text-slate-900">{p.title || `现场 ${i + 1}`}</div>
                              <div className="mt-1 line-clamp-2 text-[12px] leading-relaxed text-slate-500">{p.scene}</div>
                              <div className="mt-1 line-clamp-1 text-[12px] leading-relaxed text-slate-700">判断：{p.thesis}</div>
                              <div className="mt-1 text-[11px] text-slate-400">证据 {p.evidence.length} 条源文原文</div>
                            </button>
                          ))}
                        </div>
                        <div className="flex items-center justify-between">
                          <button onClick={() => loadMeeting(prod.doc.id, 'article', true)}
                            className="text-[12px] text-slate-500 underline decoration-slate-300 underline-offset-2 hover:text-slate-700">
                            重新开一次（换一批现场）
                          </button>
                        </div>
                        <div className="text-[11.5px] text-slate-400">
                          都不合意？<button onClick={() => setProd({ ...prod, pickedIdx: null })}
                            className="underline decoration-slate-300 underline-offset-2 hover:text-slate-600">跳过选题，按文档角度整篇改编</button>
                        </div>
                      </>
                    )
                  }
                  return (
                    <div className="rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] leading-relaxed text-slate-500">
                      选题会没开成：{ps.err || '没出可写的现场'}
                      <div className="mt-2 flex gap-2">
                        <button onClick={() => loadMeeting(prod.doc.id, 'article', true)}
                          className="rounded-full border border-slate-200 px-3.5 py-1.5 text-[12px] text-slate-600 transition hover:bg-slate-50">重开一次</button>
                        <span className="text-[11.5px] leading-6 text-slate-400">或直接走下面的整篇改编</span>
                      </div>
                    </div>
                  )
                })()}
                {prod.meeting !== null && prod.meeting.loading === false && (
                  <div className="rounded-xl bg-[#f7f8fa] px-3.5 py-3 text-[12.5px] leading-relaxed text-slate-500">
                    {prod.pickedIdx === null
                      ? '未选现场：按文档的改编角度保骨架、保原话成文，过原创度闸与双审校后排版。'
                      : `已选「${prod.meeting.items[prod.pickedIdx]?.title || `现场 ${prod.pickedIdx + 1}`}」：写稿只喂这个现场的证据包，判断从现场长出来。`}
                  </div>
                )}
              </div>
            )}

            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setProd(null)}
                className="rounded-full border border-slate-200 px-4 py-1.5 text-[12.5px] text-slate-600 transition hover:bg-slate-50">取消</button>
              <button onClick={() => {
                const { doc, dir, xhsTitle, xhsAngle, meeting, pickedIdx } = prod
                if (dir === 'xhs' && !xhsTitle.trim()) { setError('小红书选题标题不能为空'); return }
                if (meeting?.loading) { setError('选题会还在跑，等它出完或直接取消'); return }
                const pitch = dir === 'article' && meeting && pickedIdx !== null
                  ? (meeting.items[pickedIdx] as ArticlePitch) : undefined
                const angle = dir === 'xhs' && meeting && pickedIdx !== null
                  ? (meeting.items[pickedIdx] as NoteAngle) : undefined
                setProd(null)
                if (dir === 'xhs') {
                  // 实时工作台（2026-11-18 用户裁决：生成在框里跑、结果在框里看、不行在框里改）
                  setGen({ phase: 'busy', label: '笔记生成中（0%）', text: '', dir: 'xhs' })
                  void (async () => {
                    try {
                      const idea = await api<{ id: number }>('/channels/xhs/ideas', {
                        method: 'POST', body: JSON.stringify({
                          title: xhsTitle, angle: xhsAngle, material_ids: [doc.id],
                          audience: angle?.audience || '', pain: angle?.pain || '',
                          keyword: angle?.keyword || '',
                        }),
                      })
                      const c = await api<{ job_id: number; content_id: number }>(`/channels/xhs/ideas/${idea.id}/compose`, { method: 'POST' })
                      await waitJob(c.job_id, (j) => setGen((g) => (g ? { ...g, label: `笔记生成中（${j.progress}%）${j.message || ''}` } : g)))
                      const n = await api<{ title: string; body: string }>(`/channels/xhs/notes/${c.content_id}`)
                      setGen({ phase: 'done', label: '', text: n.body || '', noteId: c.content_id, dir: 'xhs' })
                    } catch (e) { setGen(null); setError(`笔记生成失败：${apiErrText(e)}`) }
                  })()
                } else {
                  void toArticle(doc, pitch)
                }
              }}
                disabled={prod.meeting?.loading === true}
                className="rounded-full bg-emerald-600 px-4 py-1.5 text-[12.5px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">确认生成</button>
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
