import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, Newspaper, RefreshCw } from 'lucide-react'
import { api, apiErrText, Article } from '../api'
import { copyRichHtml } from '../clipboard'
import { ConfirmDeleteButton, EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import { usePageGuard } from '../hooks'

/** 内容工场 · 决策页（2026-11-10 用户定版「系统做全部工作，用户只做最终决策」）：
 *  系统每日自动圈题→写稿→质量自评→排版，成品落在本页；用户只看一眼，
 *  能发就复制去公众号后台，不想要就「不用」。选题池/报题/热点等创作入口全部收进系统内部。 */

const A_BADGE: Record<string, { label: string; cls: string }> = {
  pending: { label: '排队写', cls: 'bg-amber-100 text-amber-800' },
  generating: { label: '写稿中', cls: 'bg-sky-100 text-sky-700' },
  failed: { label: '写稿失败', cls: 'bg-red-100 text-red-700' },
  draft: { label: '待发', cls: 'bg-emerald-100 text-emerald-700' },
  edited: { label: '待发', cls: 'bg-emerald-100 text-emerald-700' },
  rendered: { label: '待发 · 已排版', cls: 'bg-emerald-100 text-emerald-700' },
  published: { label: '已发布', cls: 'bg-slate-800 text-slate-100' },
}

const charsOf = (a: Article) => (a.md || '').replace(/\s/g, '').length
const qualityOf = (a: Article) => {
  const m = (a.note || '').match(/质量自评 (\d+\.?\d*)/)
  return m ? m[1] : ''
}

export default function Workshop() {
  const [items, setItems] = useState<Article[]>([])
  const [booted, setBooted] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [detail, setDetail] = useState<Record<number, Article>>({})
  const [justDone, setJustDone] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const { guard, busy, error, setError } = usePageGuard()

  const load = useCallback(async () => {
    try {
      const d = await api<{ items: Article[] }>('/articles?limit=100')
      setItems(d.items || [])
      setError('')
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBooted(true)
    }
  }, [setError])

  useEffect(() => { load() }, [load])
  // 有写稿中的行：60s 静默自动刷新（系统在干活，界面跟着走）
  useEffect(() => {
    if (!items.some((a) => a.status === 'generating' || a.status === 'pending')) return
    const t = setInterval(() => { load() }, 60_000)
    return () => clearInterval(t)
  }, [items, load])

  const openRow = async (a: Article) => {
    if (expanded === a.id) { setExpanded(null); return }
    setExpanded(a.id)
    if (!detail[a.id]) {
      try {
        const d = await api<Article>(`/articles/${a.id}`)
        setDetail((m) => ({ ...m, [a.id]: d }))
      } catch { /* 预览失败回落列表数据 */ }
    }
  }

  const copyOne = async (a: Article) => {
    await guard(async () => {
      const r = await api<{ html: string }>(`/articles/${a.id}/wechat-copy`)
      await copyRichHtml(r.html)
      setJustDone(`已复制《${(a.title || '').slice(0, 24)}》——去公众号后台粘贴即可`)
      await load()
    })
  }
  const removeOne = async (a: Article) => {
    await guard(async () => {
      await api(`/articles/${a.id}`, { method: 'DELETE' })
      setJustDone(`已移除《${(a.title || '').slice(0, 24)}》`)
      await load()
    })
  }
  const refresh = async () => {
    setRefreshing(true)
    try { await load() } finally { setRefreshing(false) }
  }

  const today = new Date().toISOString().slice(0, 10)
  const pendingN = items.filter((a) => ['draft', 'edited', 'rendered'].includes(a.status)).length
  const writingN = items.filter((a) => ['pending', 'generating'].includes(a.status)).length
  const todayN = items.filter((a) => (a.created_at || '').slice(0, 10) === today).length

  if (!booted) return <div className="text-sm text-slate-400">载入内容工场…</div>

  return (
    <div>
      <PageHeader icon={Newspaper} title="公众号内容工场"
        desc="稿子系统全自动写好、自评质量、排版完毕（每天凌晨自动产出）；你只做最终决策：点开看一眼，能发就复制去公众号后台发表。" />
      {error && <div className="mt-4"><ErrorLine text={error} /></div>}
      {justDone && (
        <div className="mt-3 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          ✓ {justDone}
          <button className="ml-auto text-xs text-emerald-700 underline" onClick={() => setJustDone('')}>收起</button>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 text-[13px]">
        <span className="rounded-full bg-emerald-100 px-3 py-1 font-medium text-emerald-800">待发 {pendingN}</span>
        {writingN > 0 && <span className="rounded-full bg-sky-100 px-3 py-1 font-medium text-sky-700">写稿中 {writingN}</span>}
        <span className="rounded-full bg-slate-100 px-3 py-1 text-slate-600">今日产出 {todayN}</span>
        <span className="ml-auto hidden text-xs text-slate-400 md:block">系统每天 4 点自动补产 · 自评低于 7 分的稿子自动重写一次</span>
        <button className="btn-ghost btn-sm flex items-center gap-1" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />刷新
        </button>
      </div>

      {items.length === 0 ? (
        <div className="mt-8"><EmptyState icon={Newspaper} title="队列空着"
          desc="系统每天凌晨自动圈题写稿，写完自动排版落到这里。不用做任何事，明天早上来看就行。" /></div>
      ) : (
        <div className="mt-4 space-y-2">
          {items.map((a) => {
            const badge = A_BADGE[a.status] || { label: a.status, cls: 'bg-slate-100 text-slate-600' }
            const q = qualityOf(a)
            const full = detail[a.id]
            const isOpen = expanded === a.id
            const writing = a.status === 'generating' || a.status === 'pending'
            return (
              <div key={a.id}
                className={`rounded-2xl border bg-white transition ${isOpen ? 'border-emerald-300 shadow-sm' : 'border-[#E8ECF0]'}`}>
                <div className="flex cursor-pointer items-center gap-3 px-4 py-3" onClick={() => openRow(a)}>
                  {isOpen ? <ChevronDown size={15} className="shrink-0 text-slate-400" />
                    : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                  <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badge.cls}`}>
                    {writing && <Loader2 size={11} className="animate-spin" />}{badge.label}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-slate-800">{a.title || '(无题)'}</span>
                  <span className="hidden shrink-0 text-xs text-slate-400 sm:block">
                    {charsOf(a)} 字{q && <> · 自评 <span className="font-medium text-slate-600">{q}</span></>} · {relTime(a.created_at)}
                  </span>
                </div>
                {isOpen && (
                  <div className="border-t border-[#E8ECF0] px-4 py-4">
                    <div className="grid gap-5 lg:grid-cols-[1fr_260px]">
                      <div className="min-w-0">
                        {a.status === 'failed' ? (
                          <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">
                            这篇写稿失败了（系统会按退避节奏自动重试；确定不要可点下方「不用」移除）。
                          </div>
                        ) : full?.html ? (
                          <div className="max-h-[560px] overflow-y-auto rounded-xl border border-[#E8ECF0] bg-white px-5 py-4 text-[14px] leading-[1.9]"
                            dangerouslySetInnerHTML={{ __html: full.html }} />
                        ) : (
                          <div className="max-h-[560px] overflow-y-auto whitespace-pre-wrap rounded-xl border border-[#E8ECF0] px-5 py-4 text-[14px] leading-[1.9] text-slate-700">
                            {(full?.md || a.md) || '（正文生成中…）'}
                          </div>
                        )}
                      </div>
                      <div className="space-y-3 text-[13px] text-slate-600">
                        {(full?.note || a.note) && (
                          <div className="rounded-xl bg-slate-50 px-3 py-2.5 leading-relaxed">
                            {((full?.note || a.note) as string).split('｜').map((seg, i) => (
                              <div key={i} className={i === 0 ? 'font-medium text-slate-700' : ''}>{seg}</div>
                            ))}
                          </div>
                        )}
                        {!!full?.kb_sources?.length && (
                          <div className="text-xs text-slate-400">依据：{full.kb_sources.map((k) => k.title).slice(0, 3).join('、')}</div>
                        )}
                        <div className="flex flex-wrap gap-2 pt-1">
                          {['rendered', 'draft', 'edited'].includes(a.status) && (
                            <button className="btn-accent btn-sm" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); copyOne(a) }}>
                              复制公众号格式
                            </button>
                          )}
                          <ConfirmDeleteButton onConfirm={() => removeOne(a)} confirmText="不用" />
                        </div>
                        <div className="text-[11px] leading-relaxed text-slate-400">
                          复制后到公众号后台「新的创作 → 写新文章」粘贴，检查无误点发表。发布永远由你亲手完成。
                        </div>
                      </div>
                    </div>
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
