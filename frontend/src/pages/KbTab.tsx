import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { BookOpen, RefreshCw } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { EmptyState, ErrorLine, relTime } from '../components'
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
        {mode === 'today' && (
          <div className="text-[12px] text-slate-400">
            推荐口径：只取未产出的文档，按评分降序前 {TODAY_LIMIT} 篇（未产出共 {statsSrc.length} 篇）——已写成内容的在「素材库 · 知识库」回看
          </div>
        )}
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
      </div>

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
