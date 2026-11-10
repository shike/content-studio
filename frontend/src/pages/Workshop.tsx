import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  BookOpen, ChevronDown, ChevronRight, ClipboardCheck, Eye, Inbox, Newspaper,
  PenLine, RefreshCw, Save, X,
} from 'lucide-react'
import { api, apiErrText, Article, KBTopic, waitJob } from '../api'
import { copyRichHtml } from '../clipboard'
import {
  Busy, ChipRow, ConfirmDeleteButton, EmptyState, ErrorLine, PageHeader, Pager, relTime, sumCounts,
} from '../components'
import { usePageGuard } from '../hooks'

const AUDIENCES = ['全部', '老板', '转行者']
const T_STATUS_META: Record<string, { label: string; cls: string }> = {
  new: { label: '待选', cls: 'bg-emerald-100 text-emerald-800' },
  selected: { label: '已选用', cls: 'bg-sky-100 text-sky-800' },
  written: { label: '已成文', cls: 'bg-slate-100 text-slate-600' },
  dismissed: { label: '不做', cls: 'bg-slate-100 text-slate-400' },
}
const A_STATUS_LABEL: Record<string, string> = {
  pending: '待写',
  generating: '生成中',
  failed: '生成失败',
  draft: '待改稿',
  edited: '已改稿',
  rendered: '已渲染',
  published: '已发布',
}
const A_STATUS_BADGE: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-800',
  generating: 'bg-sky-100 text-sky-700',
  failed: 'bg-red-100 text-red-700',
  draft: 'bg-amber-100 text-amber-800',
  edited: 'bg-sky-100 text-sky-700',
  rendered: 'bg-emerald-100 text-emerald-700',
  published: 'bg-slate-800 text-slate-100',
}
// 八个风格 = 同一深度长文底座的八种拆解视角（2026-09-27 重定义：全部零故事专业书面语）
const ART_STYLES: { key: string; label: string; hint: string }[] = [
  { key: 'auto', label: '自动', hint: '默认：读报题素材自动挑最合适的分析透镜（一般不用改）' },
  { key: 'deep_dive', label: '深度研究', hint: '机制拆解——把一个现象/概念的本质讲透，给读者解释框架' },
  { key: 'practice', label: '落地实践', hint: '实施路径——适用条件→步骤与验收物→失败条件→验收指标' },
  { key: 'inquiry', label: '问题深挖', hint: '追因——直觉答案反证→底层变量拆解→可验证结论' },
  { key: 'anatomy', label: '对象解剖', hint: '案例拆解——对象界定→逐层分析→可迁移规律' },
  { key: 'comparison', label: '方案对比', hint: '选型决策——维度定义→数据展开→选型结论矩阵' },
]

/** 占位卡 AI 生图（智谱 CogView，约 0.06 元/张）：按配图主题生成插画，落本地可直接下载上传公众号 */
function ImageHints({ md, articleId }: { md: string; articleId: number }) {
  const cards = useMemo(() => {
    const out: { kind: string; label: string; caption: string; file: string }[] = []
    for (const [kind, label] of [['cover', '头图'], ['flow', '框架图'], ['chart', '数据图表'], ['gen', 'AI 插画']] as const) {
      const re = new RegExp(`!\\[([^\\]]*)\\]\\(${kind}:([^)]*)\\)`, 'g')
      let m: RegExpExecArray | null
      while ((m = re.exec(md)) !== null) {
        out.push({ kind, label, caption: m[1], file: m[2].trim() })
      }
    }
    return out
  }, [md])
  const phs = useMemo(
    () => (md.match(/!\[([^\]]*)\]\(search:([^)]*)\)/g) || []).map((m) => {
      const mm = m.match(/!\[([^\]]*)\]\(search:([^)]*)\)/)!
      return { caption: mm[1], kw: mm[2].trim() }
    }),
    [md],
  )
  const [gen, setGen] = useState<Record<string, { image_url: string; file: string }>>({})
  const [busyKw, setBusyKw] = useState('')
  const genImage = async (kw: string, caption: string) => {
    setBusyKw(kw)
    try {
      const r = await api<{ image_url: string; file: string }>('/articles/generate-image', {
        method: 'POST',
        body: JSON.stringify({ caption, keywords: kw }),
      })
      setGen((g) => ({ ...g, [kw]: r }))
    } finally {
      setBusyKw('')
    }
  }
  if (cards.length === 0 && phs.length === 0) return null
  return (
    <div className="space-y-1.5 text-xs text-slate-500">
      <div className="flex items-center gap-2">
        <span>
          {cards.length > 0 && `自动配图 ${cards.length} 张（已随正文内嵌进公众号 HTML；微信未自动转存时用「下载图片」手动上传）`}
          {phs.length > 0 && (cards.length > 0 ? ' · ' : '') + `手动配图位 ${phs.length} 个（按搜索词插图）`}
        </span>
      </div>
      {cards.map((c) => (
        <div key={c.file} className="rounded-lg border border-slate-200 bg-white/70 p-2">
          <div className="mb-1.5 flex items-center gap-2">
            <span className={`badge shrink-0 ${c.kind === 'cover' ? 'bg-sky-100 text-sky-700' : c.kind === 'flow' ? 'bg-violet-100 text-violet-700' : 'bg-emerald-100 text-emerald-700'}`}>
              {c.label}
            </span>
            <span className="truncate font-medium text-slate-600">{c.caption}</span>
          </div>
          <div className="flex items-center gap-2">
            <img src={`/api/articles/images/${c.file}`} alt={c.caption} className="max-h-36 rounded-lg border border-slate-200" />
            <a
              href={`/api/articles/images/${c.file}`}
              download
              onClick={(e) => e.stopPropagation()}
              className="btn-ghost btn-xs text-sky-600"
            >
              下载图片
            </a>
          </div>
        </div>
      ))}
      {phs.map((ph, i) => (
        <div key={i} className="rounded-lg border border-slate-200 bg-white/70 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-slate-600">{i + 1}. {ph.caption || '配图'}</span>
            <code className="rounded bg-slate-100 px-1 text-[10px]">{ph.kw}</code>
            <button
              onClick={(e) => { e.stopPropagation(); genImage(ph.kw, ph.caption) }}
              disabled={busyKw !== ''}
              className="btn-accent btn-xs"
            >
              {busyKw === ph.kw ? '生成中…（约 20 秒）' : 'AI 生图'}
            </button>
          </div>
          {gen[ph.kw] && (
            <div className="mt-1.5 flex items-center gap-2">
              <img src={gen[ph.kw].image_url} alt={ph.caption} className="h-20 rounded-lg border border-slate-200" />
              <a
                href={gen[ph.kw].image_url}
                download
                onClick={(e) => e.stopPropagation()}
                className="btn-ghost btn-xs text-sky-600"
              >
                下载图片
              </a>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

const LIMIT = 20

/**
 * 公众号 · 内容工场（2026-11-09 链路连贯性改造：报题与长文+知识库选题 两页合并）：
 * 一页走完「选题池拍板 → 自动成文 → 改稿渲染 → 复制发布（发布人工）」，
 * 选用成文后行内展开成稿预览、一键定位到草稿箱——不再跨页找稿。
 */
export default function Workshop() {
  const [booted, setBooted] = useState(false)

  // ── 选题池段 ──
  const [topics, setTopics] = useState<KBTopic[]>([])
  const [tStatus, setTStatus] = useState('new')
  const [audience, setAudience] = useState('全部')
  const [tCollapsed, setTCollapsed] = useState<Record<string, boolean>>({})
  const [tExpanded, setTExpanded] = useState<number | null>(null)
  const [rowBusy, setRowBusy] = useState<Record<number, string>>({})
  const [syncBusy, setSyncBusy] = useState('')
  const [justDone, setJustDone] = useState<{ topicId: number; art: Article } | null>(null)

  // ── 草稿箱段 ──
  const [articles, setArticles] = useState<Article[]>([])
  const [filter, setFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [dExpanded, setDExpanded] = useState<number | null>(null)
  const [mdDraft, setMdDraft] = useState('')
  const [titleDraft, setTitleDraft] = useState('')
  const [writeStyle, setWriteStyle] = useState<Record<number, string>>({})
  const [copyState, setCopyState] = useState<{ id: number; kind: 'busy' | 'ok' | 'err'; msg?: string } | null>(null)
  const [renderState, setRenderState] = useState<{ id: number; kind: 'busy' | 'ok' | 'err'; msg?: string } | null>(null)
  const [dBusy, setDBusy] = useState('')
  const [draftBusy, setDraftBusy] = useState('')
  const [hotBusy, setHotBusy] = useState('')
  const [quickTitle, setQuickTitle] = useState('')
  const [quickNote, setQuickNote] = useState('')
  const [focusId, setFocusId] = useState<number | null>(null) // 「去审稿」定位高亮
  const poolRef = useRef<HTMLElement>(null)
  const draftsRef = useRef<HTMLElement>(null)

  // 页面守卫（hooks.ts）：错误统一 apiErrText 人话，busy（pageBusy）由 hook 持有并 finally 复位。
  // onReset 里顺手清各段局部 busy（rowBusy/syncBusy 等）——2026-10-29 卡死 bug 修复的一部分，任何路径必须复位，不可丢。
  const { guard, busy: pageBusy, setBusy: setPageBusy, error, setError } = usePageGuard(() => {
    setDBusy('')
    setDraftBusy('')
    setRowBusy({})
    setSyncBusy('')
  })

  const loadTopics = useCallback(async (st: string) => {
    const r = await api<{ topics: KBTopic[] }>(`/articles/kb-topics?status=${st}`)
    setTopics(r.topics || [])
  }, [])

  const loadArticles = useCallback(async (f: string, off: number) => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(off) })
    if (f) params.set('status', f)
    const a = await api<{ items: Article[]; total: number; counts?: Record<string, number> }>(`/articles?${params}`)
    setArticles(a.items)
    setTotal(a.total)
    if (a.counts) setCounts(a.counts)
    return a.items
  }, [])

  useEffect(() => {
    Promise.all([loadTopics('new'), loadArticles('', 0)])
      .then(() => setBooted(true))
      .catch((e) => { setError(apiErrText(e)); setBooted(true) })
  }, [loadTopics, loadArticles])

  const scrollTo = (ref: React.RefObject<HTMLElement | null>) =>
    ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  /** 「去审稿」：草稿箱切全部、定位该篇、展开高亮。 */
  const gotoDraft = (aid: number) => {
    setFilter('')
    setOffset(0)
    loadArticles('', 0)
      .then((items) => {
        const a = items.find((x) => x.id === aid)
        if (a) {
          setDExpanded(aid)
          setMdDraft(a.md)
          setTitleDraft(a.title || '')
        }
        setFocusId(aid)
        setTimeout(() => {
          document.getElementById(`art-row-${aid}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }, 120)
        setTimeout(() => setFocusId((c) => (c === aid ? null : c)), 6000)
      })
      .catch((e) => setError(apiErrText(e)))
  }

  // ── 选题池：遍历 / 选用成文 / 不做 ──
  const runTraversal = () =>
    guard(async () => {
      setSyncBusy('知识库遍历：提交任务')
      const { job_id } = await api<{ job_id: number }>('/articles/kb-topics', { method: 'POST' })
      await waitJob(job_id, (j) => setSyncBusy(`知识库遍历：${j.progress}% ${j.message}`))
      setSyncBusy('')
      await loadTopics('new')
      setTStatus('new')
    })

  const selectTopic = (t: KBTopic) =>
    guard(async () => {
      setRowBusy((m) => ({ ...m, [t.id]: '选用成文：提交任务' }))
      const { job_id, article_id } = await api<{ job_id: number; article_id: number }>(
        `/articles/kb-topics/${t.id}/select`, { method: 'POST' })
      await waitJob(job_id, (j) => setRowBusy((m) => ({ ...m, [t.id]: `成文：${j.progress}% ${j.message}` })))
      setRowBusy((m) => ({ ...m, [t.id]: '' }))
      const art = await api<Article>(`/articles/${article_id}`)
      setJustDone({ topicId: t.id, art }) // 页级 callout：选题沉入「已选用」后成稿入口仍在
      await loadTopics(tStatus)
    })

  const dismissTopic = (t: KBTopic) =>
    guard(async () => {
      setRowBusy((m) => ({ ...m, [t.id]: '处理中' }))
      await api(`/articles/kb-topics/${t.id}/dismiss`, { method: 'POST' })
      setRowBusy((m) => ({ ...m, [t.id]: '' }))
      await loadTopics(tStatus)
    })

  // ── 草稿箱：报题 / 热点 / 写文 / 改稿渲染 / 复制 / 删除 ──
  const reloadArticles = () => loadArticles(filter, offset)

  const createDraft = () =>
    guard(async () => {
      if (!quickTitle.trim()) return
      setDraftBusy('入库中…')
      await api('/articles/draft', {
        method: 'POST',
        body: JSON.stringify({ title: quickTitle.trim(), note: quickNote.trim() }),
      })
      setQuickTitle('')
      setQuickNote('')
      setDraftBusy('')
      await loadArticles('', 0)
      setFilter('')
      setOffset(0)
    })

  const runHotspots = () =>
    guard(async () => {
      setHotBusy('热点报题：提交任务')
      try {
        const { job_id } = await api<{ job_id: number }>('/articles/extract-hotspots', { method: 'POST' })
        const job = await waitJob(job_id, (j) => setHotBusy(`热点报题：${j.progress}% ${j.message}`))
        const titles: string[] = job.result?.drafts ?? []
        setHotBusy('')
        if (titles.length === 0) setError('提炼完成但没有产出草稿（看任务详情）')
        await loadArticles(filter, offset)
      } finally {
        setHotBusy('')
      }
    })

  const writeDraft = (art: Article) =>
    guard(async () => {
      const style = writeStyle[art.id] || 'auto'
      setDBusy(`长文生成：${art.title}`)
      const { job_id } = await api<{ job_id: number }>(`/articles/${art.id}/write`, {
        method: 'POST',
        body: JSON.stringify({ style }),
      })
      await waitJob(job_id, (j) => setDBusy(`长文生成：${j.progress}% ${j.message}`))
      setDBusy('')
      setDExpanded(art.id)
      const a = await api<Article>(`/articles/${art.id}`)
      setArticles((list) => list.map((x) => (x.id === a.id ? a : x)))
      setMdDraft(a.md)
      setTitleDraft(a.title || '')
    })

  const deleteArticle = (id: number) =>
    guard(async () => {
      await api(`/articles/${id}`, { method: 'DELETE' })
      setArticles((list) => list.filter((x) => x.id !== id))
      if (dExpanded === id) setDExpanded(null)
    })

  const save = (article: Article) =>
    guard(async () => {
      setPageBusy('保存中…')
      const a = await api<Article>(`/articles/${article.id}`, {
        method: 'PUT',
        body: JSON.stringify({ md: mdDraft, title: titleDraft || article.title }),
      })
      setArticles((list) => list.map((x) => (x.id === a.id ? a : x)))
    })

  const render = async (article: Article) => {
    setRenderState({ id: article.id, kind: 'busy' })
    try {
      // 改稿框即渲染源（与「保存改稿」同口径）：框里是空的就如实报错，
      // 不能悄悄回落到库里旧稿——那样用户看着空框、预览却是旧内容。
      const a = await api<Article>(`/articles/${article.id}/render`, {
        method: 'POST',
        body: JSON.stringify({ md: mdDraft }),
      })
      setArticles((list) => list.map((x) => (x.id === a.id ? a : x)))
      setMdDraft(a.md)
      setRenderState({ id: article.id, kind: 'ok' })
      setTimeout(() => setRenderState((c) => (c?.id === article.id && c.kind === 'ok' ? null : c)), 3000)
      await loadArticles(filter, offset) // 刷「已渲染」等 chips 计数（服务端口径）
    } catch (e) {
      setRenderState({ id: article.id, kind: 'err', msg: apiErrText(e) })
    }
  }

  const copyHtml = async (article: Article) => {
    setCopyState({ id: article.id, kind: 'busy' })
    try {
      if (!article.html) throw new Error('尚未渲染：先点「渲染公众号格式」')
      const r = await api<{ html: string }>(`/articles/${article.id}/wechat-copy`)
      await copyRichHtml(r.html)
      setCopyState({ id: article.id, kind: 'ok' })
      setTimeout(() => setCopyState((c) => (c?.id === article.id && c.kind === 'ok' ? null : c)), 4000)
    } catch (e) {
      setCopyState({ id: article.id, kind: 'err', msg: apiErrText(e) })
    }
  }

  const topicGroups = useMemo(() => {
    const g: Record<string, KBTopic[]> = {}
    for (const t of topics) {
      const key = t.folder_name || '未分组'
      ;(g[key] = g[key] || []).push(t)
    }
    return Object.entries(g).sort((a, b) => b[1].length - a[1].length)
  }, [topics])

  if (!booted) return null

  const pendingCount = sumCounts(counts, 'pending') + sumCounts(counts, 'generating')
  const visTopics = audience === '全部' ? topics : topics.filter((t) => t.audience === audience)

  return (
    <div className="mx-auto max-w-7xl px-4 py-6">
      <PageHeader
        icon={Newspaper}
        title="公众号内容工场"
        desc="知识库真货喂选题 → 这里拍板 → 自动成文初稿 → 改稿渲染 → 复制发布（发布永远人工）"
        actions={(
          <button onClick={runTraversal} disabled={!!syncBusy}
            className="btn-ghost justify-center border-emerald-300 text-emerald-700 disabled:opacity-40">
            <RefreshCw size={14} /> {syncBusy ? '遍历中…' : '开始遍历知识库'}
          </button>
        )}
      />

      {syncBusy && <div className="mt-3"><Busy text={syncBusy} /></div>}
      {pageBusy && <div className="mt-3"><Busy text={pageBusy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}
      {!booted && <div className="mt-3"><Busy text="加载中…" /></div>}

      {booted && (
        <>
          {/* 今天带：本页待人工的三类事，点击跳到对应段 */}
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-400">今天：</span>
            <button onClick={() => scrollTo(poolRef)}
              className="rounded-full border border-emerald-200 bg-emerald-50/60 px-3 py-1 text-xs text-emerald-800 hover:border-emerald-300">
              待拍板选题 <b className="num-serif">{topics.length}</b>
            </button>
            <button onClick={() => scrollTo(draftsRef)}
              className="rounded-full border border-amber-200 bg-amber-50/60 px-3 py-1 text-xs text-amber-800 hover:border-amber-300">
              待写 {pendingCount}
            </button>
            <button onClick={() => scrollTo(draftsRef)}
              className="rounded-full border border-sky-200 bg-sky-50/60 px-3 py-1 text-xs text-sky-800 hover:border-sky-300">
              待改稿 <b className="num-serif">{sumCounts(counts, 'draft') + sumCounts(counts, 'edited')}</b>
            </button>
            <button onClick={() => scrollTo(draftsRef)}
              className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 hover:border-slate-300">
              已渲染待发 <b className="num-serif">{sumCounts(counts, 'rendered')}</b>
            </button>
          </div>

          {/* 选用成文完成的页级 callout：选题已沉入「已选用」，成稿入口常驻不丢 */}
          {justDone && (
            <div className="mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50/70 px-4 py-3">
              <span className="badge shrink-0 bg-emerald-100 text-emerald-700">初稿已成</span>
              <span className="text-sm font-semibold text-slate-800">{justDone.art.title}</span>
              <span className="text-xs text-slate-400">{justDone.art.md.length} 字</span>
              {(justDone.art.title_alts?.length ?? 0) > 0 && (
                <span className="hidden truncate text-xs text-slate-400 lg:inline">
                  备选：{justDone.art.title_alts!.join(' ／ ')}
                </span>
              )}
              <button onClick={() => gotoDraft(justDone.art.id)} className="btn-accent btn-xs ml-auto shrink-0">
                去审稿 ↓
              </button>
            </div>
          )}

          {/* ① 选题池段 */}
          <section ref={poolRef} className="mt-7 scroll-mt-4">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="flex items-center gap-1.5 text-[15px] font-semibold text-slate-800">
                <BookOpen size={15} className="text-emerald-700" /> 选题池
                <span className="text-xs font-normal text-slate-400">知识库遍历提炼 · 拍板后自动成文</span>
              </h2>
              <ChipRow
                chips={[{ key: 'new', label: '待选' }, { key: 'selected', label: '已选用' }, { key: 'dismissed', label: '不做' }, { key: 'written', label: '已成文' }]}
                active={tStatus}
                onChange={(k) => { setTStatus(k); loadTopics(k).catch((e) => setError(apiErrText(e))) }}
              />
              <div className="flex items-center gap-1.5">
                {AUDIENCES.map((f) => (
                  <button key={f} onClick={() => setAudience(f)}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] transition ${
                      audience === f ? 'border-emerald-400 bg-emerald-100 font-medium text-emerald-800' : 'border-slate-200 text-slate-500 hover:border-slate-300'
                    }`}>{f}</button>
                ))}
              </div>
            </div>

            {visTopics.length === 0 ? (
              <div className="card mt-3 p-6">
                <EmptyState
                  icon={BookOpen}
                  title={tStatus === 'new' ? '选题池是空的' : '该状态下没有选题'}
                  desc={tStatus === 'new' ? '点右上「开始遍历知识库」，系统会扫完全部真货文档并提炼选题。' : ''}
                />
              </div>
            ) : (
              <div className="mt-3 space-y-4">
                {topicGroups.map(([folder, items]) => {
                  const vis = audience === '全部' ? items : items.filter((t) => t.audience === audience)
                  if (vis.length === 0) return null
                  const isCollapsed = tCollapsed[folder]
                  return (
                    <div key={folder} className="rounded-xl border border-slate-200 bg-white">
                      <button
                        onClick={() => setTCollapsed((m) => ({ ...m, [folder]: !m[folder] }))}
                        className="flex w-full items-center gap-2 px-4 py-2.5 text-left"
                      >
                        {isCollapsed ? <ChevronRight size={14} className="text-slate-400" /> : <ChevronDown size={14} className="text-slate-400" />}
                        <span className="text-[13px] font-semibold text-slate-700">{folder}</span>
                        <span className="text-[12px] text-slate-400">{vis.length} 个</span>
                      </button>
                      {!isCollapsed && (
                        <div className="divide-y divide-slate-100 border-t border-slate-100">
                          {vis.map((t) => {
                            const meta = T_STATUS_META[t.status] || { label: t.status, cls: 'bg-slate-100 text-slate-500' }
                            const open = tExpanded === t.id
                            return (
                              <div key={t.id} className="px-4 py-2.5 hover:bg-slate-50/60">
                                <div className="flex items-center gap-2">
                                  <button
                                    onClick={() => setTExpanded(open ? null : t.id)}
                                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                                  >
                                    {open ? <ChevronDown size={13} className="shrink-0 text-slate-300" /> : <ChevronRight size={13} className="shrink-0 text-slate-300" />}
                                    <span className="truncate text-[13px] text-slate-800">{t.title}</span>
                                  </button>
                                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${meta.cls}`}>{meta.label}</span>
                                  {t.status === 'new' && (
                                    <span className="flex shrink-0 items-center gap-1">
                                      <button
                                        onClick={(e) => { e.stopPropagation(); selectTopic(t) }}
                                        disabled={!!rowBusy[t.id]}
                                        className="btn-accent btn-xs"
                                        title="按知识库素材自动成文，完成后可就地预览"
                                      >
                                        <PenLine size={12} /> 选用成文
                                      </button>
                                      <button
                                        onClick={(e) => { e.stopPropagation(); dismissTopic(t) }}
                                        disabled={!!rowBusy[t.id]}
                                        className="rounded p-1 text-slate-300 hover:text-red-500"
                                        title="不做"
                                      >
                                        <X size={13} />
                                      </button>
                                    </span>
                                  )}
                                </div>
                                {open && (
                                  <div className="mt-2 space-y-2 rounded-lg bg-slate-50 p-3 text-[12px] leading-relaxed text-slate-600">
                                    <div><span className="font-semibold text-slate-500">判断：</span>{t.judgment}</div>
                                    <div><span className="font-semibold text-slate-500">读者：</span>{t.audience}　<span className="font-semibold text-slate-500">为什么值得写：</span>{t.why}</div>
                                    <div className="flex flex-wrap items-center gap-1.5">
                                      <span className="shrink-0 font-semibold text-slate-500">依据文档：</span>
                                      {(t.docs ?? []).map((d, i) => (
                                        <span key={i} className="rounded-full border border-emerald-200 bg-emerald-50/60 px-2 py-0.5 text-emerald-700">{d.title}</span>
                                      ))}
                                    </div>
                                    {rowBusy[t.id] && <div className="pt-1"><Busy text={rowBusy[t.id]} /></div>}
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </section>

          {/* ② 草稿箱段 */}
          <section ref={draftsRef} className="mt-10 scroll-mt-4">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="flex items-center gap-1.5 text-[15px] font-semibold text-slate-800">
                <Inbox size={15} className="text-sky-700" /> 草稿箱
                <span className="text-xs font-normal text-slate-400">改稿 → 渲染 → 复制到公众号后台（发布人工）</span>
              </h2>
              <ChipRow
                active={filter}
                onChange={(k) => { setFilter(k); setOffset(0); loadArticles(k, 0).catch((e) => setError(apiErrText(e))) }}
                chips={[
                  { key: '', label: '全部', count: sumCounts(counts) },
                  { key: 'pending', label: '待写', count: sumCounts(counts, 'pending') },
                  { key: 'failed', label: '生成失败', count: sumCounts(counts, 'failed') },
                  { key: 'draft', label: '待改稿', count: sumCounts(counts, 'draft') },
                  { key: 'edited', label: '已改稿', count: sumCounts(counts, 'edited') },
                  { key: 'rendered', label: '已渲染', count: sumCounts(counts, 'rendered') },
                ]}
              />
            </div>

            {/* 紧凑报题行（手动报题+热点，次级入口） */}
            <div className="card mt-3 border-sky-200/70 bg-gradient-to-br from-sky-50/80 to-white p-3">
              <div className="flex flex-col gap-2 sm:flex-row">
                <input
                  value={quickTitle}
                  onChange={(e) => setQuickTitle(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && createDraft()}
                  placeholder="手动报题一句话（回车入库，可跳过选题池直接成文）"
                  className="input flex-1 border-sky-200"
                />
                <button onClick={createDraft} disabled={!quickTitle.trim() || !!draftBusy}
                  className="btn-accent justify-center disabled:opacity-40 sm:w-24">
                  建草稿
                </button>
                <button onClick={runHotspots} disabled={!!hotBusy}
                  className="btn-ghost justify-center border-violet-300 text-violet-700 disabled:opacity-40 sm:w-40"
                  title="独立热点报题任务：站外热点 × 画像 → 3 篇待写草稿（每晚 0 点也自动跑）">
                  <Newspaper size={14} /> {hotBusy ? '报题中…' : '提炼今日热点'}
                </button>
              </div>
              <input
                value={quickNote}
                onChange={(e) => setQuickNote(e.target.value)}
                placeholder="备注/参考链接（选填，写长文时作为素材）"
                className="input mt-2 border-sky-100 text-[13px]"
              />
              {draftBusy && <div className="mt-2"><Busy text={draftBusy} /></div>}
              {hotBusy && <div className="mt-2"><Busy text={hotBusy} /></div>}
            </div>

            {dBusy && <div className="mt-3"><Busy text={dBusy} /></div>}

            <div className="card mt-3 overflow-hidden p-0">
              {articles.length === 0 ? (
                <div className="p-6">
                  <EmptyState
                    icon={Inbox}
                    title="草稿箱是空的"
                    desc="上方选题池点「选用成文」自动出稿；或手动报题一句话建草稿；或点「提炼今日热点」。每晚 0 点/3 点各自动跑一轮。"
                  />
                </div>
              ) : (
                <div className="divide-y divide-slate-100">
                  {articles.map((art) => {
                    const exp = dExpanded === art.id
                    const isPending = art.status === 'pending'
                    const isKb = (art.kb_doc_ids?.length ?? 0) > 0 || (art.kb_sources?.length ?? 0) > 0
                    return (
                      <div
                        key={art.id}
                        id={`art-row-${art.id}`}
                        className={`transition-colors ${exp ? 'bg-sky-50/40' : ''} ${focusId === art.id ? 'ring-2 ring-inset ring-emerald-400 bg-emerald-50/50' : ''}`}
                      >
                        <div
                          onClick={() => {
                            setDExpanded(exp ? null : art.id)
                            if (!exp) {
                              setMdDraft(art.md)
                              setTitleDraft(art.title || '')
                            }
                          }}
                          className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                        >
                          <span className="w-14 shrink-0 text-xs tabular-nums text-slate-400">#{art.id}</span>
                          <span className={`shrink-0 rounded-full border px-1.5 py-px text-[10px] ${isKb ? 'border-emerald-200 bg-emerald-50/60 text-emerald-700' : 'border-slate-200 text-slate-400'}`}>
                            {isKb ? '知识库' : '报题'}
                          </span>
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm font-medium text-slate-900">
                              {art.title || `草稿 #${art.id}`}
                            </div>
                            <div className="mt-0.5 truncate text-xs text-slate-400">
                              {isPending ? (art.note || '待写') : art.status === 'generating' ? '生成中' : `${art.md.length} 字`}
                            </div>
                          </div>
                          <span className={`badge shrink-0 ${A_STATUS_BADGE[art.status] || A_STATUS_BADGE.draft}`}>
                            {A_STATUS_LABEL[art.status] || art.status}
                          </span>
                          <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                            {relTime(art.created_at)}
                          </span>
                          {isPending ? (
                            <button
                              onClick={(e) => { e.stopPropagation(); writeDraft(art) }}
                              disabled={!!dBusy}
                              className="btn-accent btn-xs shrink-0 disabled:opacity-40"
                            >
                              写长文
                            </button>
                          ) : (
                            art.html && (
                              <button
                                onClick={(e) => { e.stopPropagation(); copyHtml(art) }}
                                className="btn-accent btn-xs shrink-0"
                              >
                                <ClipboardCheck size={12} />{' '}
                                {copyState?.id === art.id && copyState.kind === 'busy'
                                  ? '复制中…'
                                  : copyState?.id === art.id && copyState.kind === 'ok'
                                    ? '已复制 ✓'
                                    : '复制HTML'}
                              </button>
                            )
                          )}
                          <ConfirmDeleteButton onConfirm={() => deleteArticle(art.id)} title="删除草稿" />
                        </div>

                        {exp && (
                          <div className="space-y-3 border-t border-sky-100 px-5 py-4">
                            {isPending ? (
                              <div className="space-y-3">
                                <div className="rounded-lg bg-slate-50 p-3 text-[13px] leading-relaxed text-slate-600">
                                  {art.note || '（无备注）'}
                                </div>
                                {(art.kb_doc_ids?.length ?? 0) > 0 && (
                                  <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                                    <span className="shrink-0">依据文档：</span>
                                    {art.kb_doc_ids!.map((k, i) => (
                                      <span
                                        key={i}
                                        className="rounded-full border border-emerald-200 bg-emerald-50/60 px-2 py-0.5 text-emerald-700"
                                      >
                                        {k.title || k.media_id}
                                      </span>
                                    ))}
                                  </div>
                                )}
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="shrink-0 text-xs font-semibold text-slate-500">写作透镜</span>
                                  <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-6">
                                    {ART_STYLES.map((st) => (
                                      <button
                                        key={st.key}
                                        onClick={(e) => {
                                          e.stopPropagation()
                                          setWriteStyle((m) => ({ ...m, [art.id]: st.key }))
                                        }}
                                        title={st.hint}
                                        className={`rounded-lg border px-2 py-1 text-[11px] transition ${
                                          (writeStyle[art.id] || 'auto') === st.key
                                            ? 'border-sky-400 bg-sky-50 font-medium text-sky-700'
                                            : 'border-slate-200 text-slate-500 hover:border-slate-300'
                                        }`}
                                      >
                                        {st.label}
                                      </button>
                                    ))}
                                  </div>
                                  <button
                                    onClick={(e) => { e.stopPropagation(); writeDraft(art) }}
                                    disabled={!!dBusy}
                                    className="btn-accent btn-xs ml-auto disabled:opacity-40"
                                  >
                                    {dBusy ? '生成中…' : '写长文（深度版，约 2~3 分钟）'}
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <>
                                {(art.title || (art.title_alts?.length ?? 0) > 0) && (
                                  <div className="space-y-1.5 rounded-lg bg-slate-50 p-3">
                                    <div className="flex items-center gap-2">
                                      <span className="shrink-0 text-xs font-semibold text-slate-500">标题</span>
                                      <input
                                        value={titleDraft}
                                        onChange={(e) => setTitleDraft(e.target.value)}
                                        onClick={(e) => e.stopPropagation()}
                                        placeholder="公众号标题（≤32 字）"
                                        className="input flex-1 py-1 text-[13px]"
                                      />
                                    </div>
                                    {(art.title_alts?.length ?? 0) > 0 && (
                                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                                        <span className="shrink-0">备选：</span>
                                        {art.title_alts!.map((t, i) => (
                                          <button
                                            key={i}
                                            onClick={(e) => {
                                              e.stopPropagation()
                                              navigator.clipboard.writeText(t)
                                              e.currentTarget.textContent = '已复制 ✓'
                                              setTimeout(() => { e.currentTarget.textContent = t }, 1500)
                                            }}
                                            className="rounded-full border border-slate-200 px-2 py-0.5 hover:border-sky-300"
                                          >
                                            {t}
                                          </button>
                                        ))}
                                      </div>
                                    )}
                                    {(art.kb_sources?.length ?? 0) > 0 && (
                                      <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
                                        <span className="shrink-0">知识库素材：</span>
                                        {art.kb_sources!.map((k, i) => (
                                          <span
                                            key={i}
                                            title="成文时从 ima 知识库抽取的素材（事实唯一来源）"
                                            className="rounded-full border border-emerald-200 bg-emerald-50/60 px-2 py-0.5 text-emerald-700"
                                          >
                                            {k.title || k.media_id}
                                          </span>
                                        ))}
                                      </div>
                                    )}
                                    <ImageHints md={art.md} articleId={art.id} />
                                  </div>
                                )}
                                <div className="flex items-center gap-2">
                                  <button
                                    onClick={(e) => { e.stopPropagation(); save(art) }}
                                    className="btn-ghost btn-xs"
                                  >
                                    <Save size={13} /> 保存改稿
                                  </button>
                                  {!art.html && <span className="text-xs text-slate-400">渲染后可一键复制</span>}
                                  <button
                                    onClick={(e) => { e.stopPropagation(); render(art) }}
                                    disabled={renderState?.id === art.id && renderState.kind === 'busy'}
                                    className="btn-ghost btn-xs"
                                  >
                                    <Eye size={13} />{' '}
                                    {renderState?.id === art.id && renderState.kind === 'busy'
                                      ? '渲染中…'
                                      : renderState?.id === art.id && renderState.kind === 'ok'
                                        ? '已渲染 ✓'
                                        : '渲染公众号格式'}
                                  </button>
                                  {renderState?.id === art.id && renderState.kind === 'err' && (
                                    <span className="text-xs text-red-500">渲染失败：{renderState.msg}</span>
                                  )}
                                  {art.html && (
                                    <button onClick={(e) => { e.stopPropagation(); copyHtml(art) }} className="btn-accent btn-xs ml-auto">
                                      <ClipboardCheck size={13} />{' '}
                                      {copyState?.id === art.id && copyState.kind === 'busy'
                                        ? '复制中…'
                                        : copyState?.id === art.id && copyState.kind === 'ok'
                                          ? '已复制 ✓ 去公众号后台粘贴'
                                          : '复制公众号 HTML'}
                                    </button>
                                  )}
                                  {copyState?.id === art.id && copyState.kind === 'err' && (
                                    <span className="text-xs text-red-500">复制失败：{copyState.msg}</span>
                                  )}
                                </div>
                                <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
                                  <textarea
                                    value={mdDraft}
                                    onChange={(e) => setMdDraft(e.target.value)}
                                    className="h-96 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-4 font-mono text-[13px] leading-relaxed outline-none focus:border-slate-400 focus:bg-white"
                                  />
                                  {art.html ? (
                                    <div className="h-96 overflow-auto rounded-xl border border-slate-200 bg-white p-5">
                                      <div dangerouslySetInnerHTML={{ __html: art.html }} />
                                    </div>
                                  ) : (
                                    <div className="grid h-96 place-items-center rounded-xl border border-dashed border-slate-300 text-sm text-slate-400">
                                      点「渲染」生成公众号内联样式 HTML
                                    </div>
                                  )}
                                </div>
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                  <Pager total={total} limit={LIMIT} offset={offset} onPage={setOffset} />
                </div>
              )}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
