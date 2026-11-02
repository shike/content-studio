import { useCallback, useEffect, useMemo, useState } from 'react'
import { ClipboardCheck, Eye, FileText, Inbox, Newspaper, Save, Trash2 } from 'lucide-react'
import { api, apiErrText, Article, waitJob } from '../api'
import type { SectionKey } from '../App'
import { copyRichHtml } from '../clipboard'
import { Busy, ChipRow, EmptyState, ErrorLine, PageHeader, relTime, Pager } from '../components'

const STATUS_LABEL: Record<string, string> = {
  pending: '待写',
  generating: '生成中',
  failed: '生成失败',
  draft: '待改稿',
  edited: '已改稿',
  rendered: '已渲染',
  published: '已发布',
}
const STATUS_BADGE: Record<string, string> = {
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
  // 即梦海报提示词：按配图位生成完整提示词，复制去即梦/豆包出图
  const [pp, setPp] = useState<Record<number, { prompt: string; mode: string }>>({})
  const [ppBusy, setPpBusy] = useState<number | null>(null)
  const genPosterPrompt = async (idx: number) => {
    setPpBusy(idx)
    try {
      const r = await api<{ prompt: string; mode: string }>(`/articles/${articleId}/poster-prompt`, {
        method: 'POST',
        body: JSON.stringify({ slot_index: idx }),
      })
      setPp((m) => ({ ...m, [idx]: r }))
    } catch (e) {
      setPp((m) => ({ ...m, [idx]: { prompt: String(e), mode: 'error' } }))
    } finally {
      setPpBusy(null)
    }
  }
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
              onClick={(e) => { e.stopPropagation(); genPosterPrompt(i) }}
              disabled={ppBusy === i}
              className="btn-ghost btn-xs text-violet-600"
              title="生成即梦(Seedream)海报提示词：复制去即梦/豆包出图"
            >
              {ppBusy === i ? '提示词…' : '海报提示词'}
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); genImage(ph.kw, ph.caption) }}
              disabled={busyKw !== ''}
              className="btn-accent btn-xs"
            >
              {busyKw === ph.kw ? '生成中…（约 20 秒）' : 'AI 生图'}
            </button>
          </div>
          {pp[i] && (
            <div className="mt-1.5 rounded-lg border border-violet-200 bg-violet-50/60 p-2">
              <div className="flex items-center gap-2">
                <span className="badge shrink-0 bg-violet-100 text-violet-700">即梦提示词{pp[i].mode === 'llm' ? '' : '（模板）'}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(pp[i].prompt) }}
                  className="btn-ghost btn-xs ml-auto text-violet-600"
                >
                  复制 → 去即梦出图
                </button>
              </div>
              <pre className="mt-1.5 max-h-44 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed text-slate-600">{pp[i].prompt}</pre>
            </div>
          )}
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

/**
 * 公众号 · 深度长文线（2026-11-02 三线定版）：
 * 报题即建草稿（无选题阶段）→ 深度长文（唯一档位）→ 改稿渲染 → 一键复制（发布人工）。
 * 独立热点报题任务每晚 0 点自动跑，产出待写草稿；独立风格画像从已发长文提取。
 */
export default function Articles({ onNavigate: _onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [articles, setArticles] = useState<Article[]>([])
  const [quickTitle, setQuickTitle] = useState('')
  const [quickNote, setQuickNote] = useState('')
  const [draftBusy, setDraftBusy] = useState('')
  const [hotBusy, setHotBusy] = useState('')
  const [styleNote, setStyleNote] = useState('')
  const [styleBusy, setStyleBusy] = useState('')
  const [writeStyle, setWriteStyle] = useState<Record<number, string>>({})
  // 整篇配图提示词包（一次 LLM 抽取全部配图位的即梦提示词）
  const [pack, setPack] = useState<{ items: { slot: number; caption: string; kw: string; title: string; layout: string; prompt: string }[]; mode: string } | null>(null)
  const [packBusy, setPackBusy] = useState(false)
  const [booted, setBooted] = useState(false)
  const [filter, setFilter] = useState('')
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [mdDraft, setMdDraft] = useState('')
  const [titleDraft, setTitleDraft] = useState('')
  const [copyState, setCopyState] = useState<{ id: number; kind: 'busy' | 'ok' | 'err'; msg?: string } | null>(null)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  // 渲染是毫秒级同步操作，进度意义不大；关键是结果回显落在点击处（展开行内）
  const [renderState, setRenderState] = useState<{ id: number; kind: 'busy' | 'ok' | 'err'; msg?: string } | null>(null)
  // 一页纸知识图解：LLM 抽取 → 排版 PNG，文末自动追加；回显同 renderState 落在点击处
  const [kgState, setKgState] = useState<{ id: number; kind: 'busy' | 'ok' | 'err'; msg?: string; file?: string } | null>(null)

  const LIMIT = 20
  const reload = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) })
    if (filter) params.set('status', filter)
    const [a, st] = await Promise.all([
      api<{ items: Article[]; total: number; counts?: Record<string, number> }>(`/articles?${params}`),
      api<{ note: string }>('/articles/style-summary'),
    ])
    setArticles(a.items)
    setTotal(a.total)
    if (a.counts) setCounts(a.counts)
    setStyleNote(st.note || '')
    return a.items
  }, [offset, filter])

  useEffect(() => {
    reload()
      .then(() => setBooted(true))
      .catch((e) => { setError(String(e)); setBooted(true) })
  }, [reload])

  const guard = async (fn: () => Promise<void>) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBusy('')
      setDraftBusy('')
    }
  }

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
      setFilter('')
      setOffset(0)
      await reload()
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
      } finally {
        setHotBusy('')
      }
    })

  const extractStyle = () =>
    guard(async () => {
      setStyleBusy('风格提取中…')
      try {
        const { job_id } = await api<{ job_id: number }>('/articles/extract-style', { method: 'POST' })
        const job = await waitJob(job_id, (j) => setStyleBusy(`风格提取：${j.progress}% ${j.message}`))
        setStyleNote(String(job.result?.note ?? ''))
      } finally {
        setStyleBusy('')
      }
    })

  const writeDraft = (art: Article) =>
    guard(async () => {
      const style = writeStyle[art.id] || 'auto'
      setBusy(`长文生成：${art.title}`)
      const { job_id } = await api<{ job_id: number }>(`/articles/${art.id}/write`, {
        method: 'POST',
        body: JSON.stringify({ style }),
      })
      await waitJob(job_id, (j) => setBusy(`长文生成：${j.progress}% ${j.message}`))
      setBusy('')
      setExpandedId(art.id)
      const a = await api<Article>(`/articles/${art.id}`)
      setArticles((list) => list.map((x) => (x.id === a.id ? a : x)))
      setMdDraft(a.md)
      setTitleDraft(a.title || '')
    })

  const deleteArticle = (id: number) =>
    guard(async () => {
      await api(`/articles/${id}`, { method: 'DELETE' })
      setConfirmDel(null)
      setArticles((list) => list.filter((x) => x.id !== id))
      if (expandedId === id) setExpandedId(null)
    })

  const save = (article: Article) =>
    guard(async () => {
      setBusy('保存中…')
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
      await reload() // 刷「已渲染」等 chips 计数（服务端口径）
    } catch (e) {
      setRenderState({ id: article.id, kind: 'err', msg: apiErrText(e) })
    }
  }

  const loadPosterPack = (article: Article) =>
    guard(async () => {
      setPackBusy(true)
      try {
        // method 必须显式 POST：api() 默认 GET，GET 打到 POST-only 路由会落 404 兜底（"unknown api route"）
        const r = await api<{ items: { slot: number; caption: string; kw: string; title: string; layout: string; prompt: string }[]; mode: string }>(
          `/articles/${article.id}/poster-pack`, { method: 'POST' })
        setPack({ items: r.items, mode: r.mode })
      } finally {
        setPackBusy(false)
      }
    })

  const genKg = (article: Article) =>
    guard(async () => {
      setKgState({ id: article.id, kind: 'busy' })
      try {
        const r = await api<{ file: string; title: string }>(`/articles/${article.id}/knowledge-graphic`, { method: 'POST' })
        setKgState({ id: article.id, kind: 'ok', file: r.file, msg: r.title })
        await reload()
      } catch (e) {
        setKgState({ id: article.id, kind: 'err', msg: apiErrText(e) })
      }
    })

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

  if (!booted) return null

  const statusCount = (k: string) => (k ? counts[k] || 0 : Object.values(counts).reduce((a, b) => a + b, 0))
  const pendingCount = statusCount('pending') + statusCount('generating')

  return (
    <div>
      <PageHeader
        icon={Newspaper}
        title="公众号"
        desc="报题即草稿 → 深度长文（唯一档位）→ 改稿渲染 → 一键复制（发布人工）"
        actions={<span className="text-xs text-slate-400">共 {total} 篇 · 待写 {pendingCount}</span>}
      />

      {/* 报题卡：报题即建草稿（无选题阶段） */}
      <div className="card border-sky-200/70 bg-gradient-to-br from-sky-50/80 to-white p-4">
        <div className="flex flex-col gap-2">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              value={quickTitle}
              onChange={(e) => setQuickTitle(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && createDraft()}
              placeholder="报题一句话：这篇深度长文写什么（回车入库）"
              className="input flex-1 border-sky-200"
            />
            <button onClick={createDraft} disabled={!quickTitle.trim() || !!draftBusy}
              className="btn-accent justify-center disabled:opacity-40 sm:w-28">
              建草稿
            </button>
            <button onClick={runHotspots} disabled={!!hotBusy}
              className="btn-ghost justify-center border-violet-300 text-violet-700 disabled:opacity-40 sm:w-44"
              title="独立热点报题任务：站外热点 × 画像 → 3 篇待写草稿（每晚 0 点也自动跑）">
              <Newspaper size={14} /> {hotBusy ? '报题中…' : '提炼今日热点'}
            </button>
          </div>
          <input
            value={quickNote}
            onChange={(e) => setQuickNote(e.target.value)}
            placeholder="备注/参考链接（选填，写长文时作为素材）"
            className="input border-sky-100 text-[13px]"
          />
        </div>
        {draftBusy && <div className="mt-2"><Busy text={draftBusy} /></div>}
        {hotBusy && <div className="mt-2"><Busy text={hotBusy} /></div>}
      </div>

      {/* 公众号独立风格画像（从已发长文提取，长文生成自动套用） */}
      <div className="card mt-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold text-slate-800">公众号风格画像</div>
          <span className="text-[11px] text-slate-400">从已发长文提取的表达习惯，生成长文时自动注入</span>
          <button onClick={extractStyle} disabled={!!styleBusy} className="btn-ghost btn-xs ml-auto disabled:opacity-40">
            {styleBusy ? styleBusy : styleNote ? '重新提取' : '从已发长文提取'}
          </button>
        </div>
        {styleNote ? (
          <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-[13px] leading-relaxed text-slate-600">{styleNote}</div>
        ) : (
          !styleBusy && <div className="mt-2 text-xs text-slate-400">还没有画像——写出第一篇后点「从已发长文提取」。</div>
        )}
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      <div className="mt-4">
        <ChipRow
          active={filter}
          onChange={(k) => { setFilter(k); setOffset(0) }}
          chips={[
            { key: '', label: '全部', count: statusCount('') },
            { key: 'pending', label: '待写', count: statusCount('pending') },
            { key: 'failed', label: '生成失败', count: statusCount('failed') },
            { key: 'draft', label: '待改稿', count: statusCount('draft') },
            { key: 'edited', label: '已改稿', count: statusCount('edited') },
            { key: 'rendered', label: '已渲染', count: statusCount('rendered') },
          ]}
        />
      </div>

      <div className="card mt-4 overflow-hidden p-0">
        {articles.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Inbox}
              title="草稿箱是空的"
              desc="报题一句话建草稿，或点「提炼今日热点」——每晚 0 点也会自动报题。待写草稿在行上「写长文」。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {articles.map((art) => {
              const exp = expandedId === art.id
              const isPending = art.status === 'pending'
              return (
                <div key={art.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => {
                      setExpandedId(exp ? null : art.id)
                      if (!exp) {
                        setMdDraft(art.md)
                        setTitleDraft(art.title || '')
                      }
                    }}
                    className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    <span className="w-14 shrink-0 text-xs tabular-nums text-slate-400">#{art.id}</span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-900">
                        {art.title || `草稿 #${art.id}`}
                      </div>
                      <div className="mt-0.5 truncate text-xs text-slate-400">
                        {isPending ? (art.note || '待写') : art.status === 'generating' ? '生成中' : `${art.md.length} 字`}
                      </div>
                    </div>
                    <span className={`badge shrink-0 ${STATUS_BADGE[art.status] || STATUS_BADGE.draft}`}>
                      {STATUS_LABEL[art.status] || art.status}
                    </span>
                    <span className="hidden w-16 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {relTime(art.created_at)}
                    </span>
                    {isPending ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); writeDraft(art) }}
                        disabled={!!busy}
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
                    {confirmDel === art.id ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); deleteArticle(art.id) }}
                        className="btn-danger btn-xs shrink-0"
                      >
                        确认删除
                      </button>
                    ) : (
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          setConfirmDel(art.id)
                          setTimeout(() => setConfirmDel((c) => (c === art.id ? null : c)), 3000)
                        }}
                        title="删除草稿"
                        className="btn-ghost btn-xs shrink-0 text-slate-300 hover:text-red-500"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>

                  {exp && (
                    <div className="space-y-3 border-t border-sky-100 px-5 py-4">
                      {isPending ? (
                        <div className="space-y-3">
                          <div className="rounded-lg bg-slate-50 p-3 text-[13px] leading-relaxed text-slate-600">
                            {art.note || '（无备注）'}
                          </div>
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
                              disabled={!!busy}
                              className="btn-accent btn-xs ml-auto disabled:opacity-40"
                            >
                              {busy ? '生成中…' : '写长文（深度版，约 2~3 分钟）'}
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
                              <ImageHints md={art.md} articleId={art.id} />
                              {pack && pack.items.length > 0 && (
                                <div className="space-y-2 rounded-lg border border-violet-200 bg-violet-50/50 p-3">
                                  <div className="flex items-center gap-2">
                                    <span className="text-xs font-semibold text-violet-700">即梦海报提示词包 · {pack.items.length} 条</span>
                                    <span className="text-[10px] text-slate-400">{pack.mode === 'llm' ? 'LLM 精修' : pack.mode}</span>
                                    <button
                                      onClick={() => navigator.clipboard.writeText(pack.items.map((it, i) => `【${i + 1}】${it.title}\n${it.prompt}`).join('\n\n'))}
                                      className="btn-ghost btn-xs ml-auto text-violet-600"
                                    >
                                      一键复制全部
                                    </button>
                                  </div>
                                  {pack.items.map((it, i) => (
                                    <div key={i} className="rounded-lg border border-violet-100 bg-white p-2.5">
                                      <div className="flex items-center gap-2">
                                        <span className="badge shrink-0 bg-violet-100 text-violet-700">{i + 1}</span>
                                        <span className="truncate text-xs font-medium text-slate-700">{it.title || it.caption || `配图位 ${it.slot + 1}`}</span>
                                        <span className="ml-auto shrink-0 text-[10px] text-slate-400">{it.layout}</span>
                                        <button
                                          onClick={() => navigator.clipboard.writeText(it.prompt)}
                                          className="btn-ghost btn-xs text-violet-600"
                                        >
                                          复制
                                        </button>
                                      </div>
                                      <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap text-[12px] leading-relaxed text-slate-600">{it.prompt}</pre>
                                    </div>
                                  ))}
                                </div>
                              )}
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
                            <button
                              onClick={(e) => { e.stopPropagation(); loadPosterPack(art) }}
                              disabled={packBusy}
                              className="btn-ghost btn-xs text-violet-600"
                              title="一次生成全部配图位的即梦海报提示词，复制去即梦/豆包批量出图"
                            >
                              {packBusy ? '生成中…' : '全套配图提示词'}
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); genKg(art) }}
                              disabled={kgState?.id === art.id && kgState.kind === 'busy'}
                              className="btn-ghost btn-xs text-sky-600"
                              title="LLM 提炼全文 → 自动排版一页纸知识图解 PNG，追加到文末"
                            >
                              {kgState?.id === art.id && kgState.kind === 'busy' ? '图解生成中…（约 1 分钟）' : '生成知识图解'}
                            </button>
                            {kgState?.id === art.id && kgState.kind === 'ok' && (
                              <span className="flex items-center gap-1.5 text-xs text-emerald-600">
                                <img src={`/api/articles/images/${kgState.file}`} alt="" className="h-8 rounded border border-slate-200" />
                                已生成「{kgState.msg}」· 文末已追加，重新渲染即可内嵌
                              </span>
                            )}
                            {kgState?.id === art.id && kgState.kind === 'err' && (
                              <span className="text-xs text-red-500">图解失败：{kgState.msg}</span>
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
    </div>
  )
}
