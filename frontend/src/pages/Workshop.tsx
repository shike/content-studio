import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, Loader2, Newspaper, RefreshCw } from 'lucide-react'
import { api, apiErrText, Article } from '../api'
import { copyRichHtml } from '../clipboard'
import { ConfirmDeleteButton, EmptyState, ErrorLine, PageHeader, relTime } from '../components'
import { usePageGuard } from '../hooks'

/** 内容工场 · 决策页（2026-11-10 定版「系统做全部工作，用户只做最终决策」；
 *  2026-11-11 接源文改编产线：稿件来自内容清单，加发布登记与 48h 回流）：
 *  系统每日自动取项→改编→审校→排版，成品落在本页；用户只做最终决策：
 *  能发就复制去公众号后台发表，回来登记链接；不想要就「不用」。 */

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
  const [pubFor, setPubFor] = useState<number | null>(null)
  const [pubUrl, setPubUrl] = useState('')
  const [rfFor, setRfFor] = useState<number | null>(null)
  const [rf, setRf] = useState({ reads: '', welook: '', follows: '', leads: '' })
  const [filter, setFilter] = useState<'' | 'generating' | 'published' | 'failed'>('')
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
  const registerPublish = async (a: Article) => {
    await guard(async () => {
      await api(`/articles/${a.id}/publish`, {
        method: 'POST', body: JSON.stringify({ url: pubUrl.trim() }),
      })
      setPubFor(null); setPubUrl('')
      setJustDone(`已登记发布《${(a.title || '').slice(0, 24)}》——48h 后这里会提醒你补阅读/进线数据`)
      await load()
    })
  }
  const saveReflow = async (a: Article) => {
    await guard(async () => {
      await api(`/articles/${a.id}/reflow`, {
        method: 'PUT',
        body: JSON.stringify({
          reads: Number(rf.reads) || 0, welook: Number(rf.welook) || 0,
          follows: Number(rf.follows) || 0, leads: Number(rf.leads) || 0,
        }),
      })
      setRfFor(null); setRf({ reads: '', welook: '', follows: '', leads: '' })
      setJustDone('回流数据已记账——周报会按栏目归因')
      await load()
    })
  }
  const refresh = async () => {
    setRefreshing(true)
    try { await load() } finally { setRefreshing(false) }
  }

  const readyN = items.filter((a) => ['draft', 'edited', 'rendered'].includes(a.status)).length
  const writingN = items.filter((a) => ['pending', 'generating'].includes(a.status)).length
  const publishedN = items.filter((a) => a.status === 'published').length
  const failedN = items.filter((a) => a.status === 'failed').length
  // 决策队列：默认只看 待发+写稿中；失败/已发布走筛选，不混进每天的决策流
  const WEIGHT: Record<string, number> = { generating: 0, pending: 0, rendered: 1, edited: 1, draft: 1, failed: 2, published: 3 }
  const visible = items
    .filter((a) => {
      if (filter === '') return ['draft', 'edited', 'rendered', 'pending', 'generating'].includes(a.status)
      return a.status === filter
    })
    .sort((a, b) => (WEIGHT[a.status] ?? 9) - (WEIGHT[b.status] ?? 9) || (b.id ?? 0) - (a.id ?? 0))

  if (!booted) return <div className="text-sm text-slate-400">载入内容工场…</div>

  return (
    <div>
      <PageHeader icon={Newspaper} title="公众号内容工场"
        desc="系统每天从内容清单取源文改编、四审过闸、排版完毕；你只做最终决策：能发就复制去公众号后台发表，回来登记链接。" />
      {error && <div className="mt-4"><ErrorLine text={error} /></div>}
      {justDone && (
        <div className="mt-3 flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          ✓ {justDone}
          <button className="ml-auto text-xs text-emerald-700 underline" onClick={() => setJustDone('')}>收起</button>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 text-[13px]">
        <button className={`rounded-full px-3 py-1 font-medium ${filter === '' ? 'bg-emerald-600 text-white' : 'bg-emerald-50 text-emerald-800 hover:bg-emerald-100'}`}
          onClick={() => setFilter('')}>
          决策队列 <span className="num-serif">{readyN + writingN}</span>
        </button>
        {writingN > 0 && (
          <button className={`rounded-full px-3 py-1 ${filter === 'generating' ? 'bg-sky-600 text-white' : 'bg-sky-50 text-sky-700 hover:bg-sky-100'}`}
            onClick={() => setFilter(filter === 'generating' ? '' : 'generating')}>
            写稿中 <span className="num-serif">{writingN}</span>
          </button>
        )}
        <button className={`rounded-full px-3 py-1 ${filter === 'published' ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}
          onClick={() => setFilter(filter === 'published' ? '' : 'published')}>
          已发布 <span className="num-serif">{publishedN}</span>
        </button>
        {failedN > 0 && (
          <button className={`rounded-full px-3 py-1 ${filter === 'failed' ? 'bg-red-600 text-white' : 'bg-red-50 text-red-700 hover:bg-red-100'}`}
            onClick={() => setFilter(filter === 'failed' ? '' : 'failed')}>
            写稿失败 <span className="num-serif">{failedN}</span>
          </button>
        )}
        <span className="ml-auto hidden text-xs text-slate-400 md:block">每天 4 点挖清单 · 5 点取项改编 · 四审（原创度/事实对源/读者/去AI味）全过才进待发</span>
        <button className="btn-ghost btn-sm flex items-center gap-1" onClick={refresh} disabled={refreshing}>
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />刷新
        </button>
      </div>

      {visible.length === 0 ? (
        <div className="mt-8"><EmptyState icon={Newspaper} title={filter === '' ? '决策队列清空了' : '这一类暂无稿件'}
          desc={filter === '' ? '系统每天凌晨改编一篇进待发，改完自动排版落到这里。不用做任何事，明天早上来看就行。' : '换个筛选看看。'} /></div>
      ) : (
        <div className="mt-4 space-y-2">
          {visible.map((a) => {
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
                          <div className="text-xs text-slate-400">改编自：{full.kb_sources.map((k) => k.title).slice(0, 3).join('、')}</div>
                        )}
                        {a.status === 'published' && (
                          <div className="rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-relaxed">
                            <span className="rounded bg-blue-50 px-1.5 py-0.5 text-blue-800">{a.publish_url || '（未贴链接）'}</span>
                            {a.published_at && <span className="ml-1.5 text-slate-400">{a.published_at} 发布</span>}
                            {a.stats?.filled_at && (
                              <div className="mt-1.5 flex flex-wrap gap-x-3 text-slate-600">
                                <span>阅读 <b className="num-serif text-slate-900">{a.stats.reads ?? 0}</b></span>
                                <span>在看 <b className="num-serif text-slate-900">{a.stats.welook ?? 0}</b></span>
                                <span>涨粉 <b className="num-serif text-slate-900">{a.stats.follows ?? 0}</b></span>
                                <span>进线 <b className="num-serif text-emerald-700">{a.stats.leads ?? 0}</b></span>
                                <span className="text-slate-400">{a.stats.filled_at} 回流</span>
                              </div>
                            )}
                          </div>
                        )}
                        <div className="flex flex-wrap gap-2 pt-1">
                          {['rendered', 'draft', 'edited'].includes(a.status) && (
                            <button className="btn-accent btn-sm" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); copyOne(a) }}>
                              复制公众号格式
                            </button>
                          )}
                          {['rendered', 'draft', 'edited'].includes(a.status) && pubFor !== a.id && (
                            <button className="btn-ghost btn-sm" disabled={!!busy}
                              onClick={(e) => { e.stopPropagation(); setPubFor(a.id); setPubUrl('') }}>
                              已发布 · 登记
                            </button>
                          )}
                          {a.status === 'published' && rfFor !== a.id && (
                            <button className="btn-ghost btn-sm" disabled={!!busy}
                              onClick={(e) => {
                                e.stopPropagation(); setRfFor(a.id)
                                setRf({ reads: String(a.stats?.reads ?? ''), welook: String(a.stats?.welook ?? ''),
                                  follows: String(a.stats?.follows ?? ''), leads: String(a.stats?.leads ?? '') })
                              }}>
                              {a.stats?.filled_at ? '改回流数据' : '48h 补数据'}
                            </button>
                          )}
                          {a.status !== 'published' && <ConfirmDeleteButton onConfirm={() => removeOne(a)} confirmText="不用" />}
                        </div>
                        {pubFor === a.id && (
                          <div className="rounded-xl border border-dashed border-emerald-300 bg-emerald-50/50 px-3 py-2.5">
                            <div className="mb-1.5 text-[11px] leading-relaxed text-slate-500">
                              发布永远由你亲手完成——这里只是贴回链接告诉系统结果（状态转「已发布」，48h 后提醒补数据）。
                            </div>
                            <div className="flex gap-2">
                              <input value={pubUrl} onChange={(e) => setPubUrl(e.target.value)}
                                placeholder="https://mp.weixin.qq.com/s/…"
                                className="min-w-0 flex-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[12.5px] outline-none focus:border-slate-400" />
                              <button className="btn-ghost btn-sm" disabled={!!busy || !pubUrl.trim().startsWith('http')}
                                onClick={(e) => { e.stopPropagation(); registerPublish(a) }}>确认已发布</button>
                              <button className="btn-sm btn-ghost" onClick={(e) => { e.stopPropagation(); setPubFor(null) }}>取消</button>
                            </div>
                          </div>
                        )}
                        {rfFor === a.id && (
                          <div className="rounded-xl border border-dashed border-blue-300 bg-blue-50/50 px-3 py-2.5" onClick={(e) => e.stopPropagation()}>
                            <div className="mb-1.5 text-[11px] text-slate-500">公众号后台 T+2 能看到的数据，填四个数（30 秒）；「进线」= 私信/微信新增咨询数，北极星就靠它。</div>
                            <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
                              {([['reads', '阅读'], ['welook', '在看'], ['follows', '涨粉'], ['leads', '进线']] as const).map(([k, label]) => (
                                <label key={k} className="flex items-center gap-1">
                                  {label}
                                  <input value={rf[k]} onChange={(e) => setRf({ ...rf, [k]: e.target.value })}
                                    inputMode="numeric" className="w-16 rounded-lg border border-slate-200 px-2 py-1 text-right num-serif outline-none focus:border-slate-400" />
                                </label>
                              ))}
                              <button className="btn-ghost btn-sm" disabled={!!busy} onClick={() => saveReflow(a)}>保存</button>
                              <button className="btn-sm btn-ghost" onClick={() => setRfFor(null)}>取消</button>
                            </div>
                          </div>
                        )}
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
