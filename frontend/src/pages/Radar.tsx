import { useCallback, useEffect, useState } from 'react'
import { Flame, Plus, Power, RefreshCw, Search, Trash2 } from 'lucide-react'
import { api, waitJob } from '../api'
import { Busy, ErrorLine, PageHeader, relTime } from '../components'

interface RadarTopic {
  id: number
  ch_id: string
  name: string
  enabled: boolean
  notify: boolean
  new_since_last: number
  recent: { id: string; desc: string; new?: boolean }[]
  hits: { id: string; desc: string; author: string; digg: number; followers: number; ratio: number }[]
  last_checked_at: string | null
}

export default function Radar() {
  const [items, setItems] = useState<RadarTopic[]>([])
  const [form, setForm] = useState({ name: '', ch_id: '' })
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set())
  const [linkUrl, setLinkUrl] = useState('')
  const [extractBusy, setExtractBusy] = useState('')
  const [extracted, setExtracted] = useState<{ ch_id: string; name: string }[]>([])

  const [mineInfo, setMineInfo] = useState('')

  const mine = () =>
    guard(async () => {
      const r = await api<{ job_id: number }>('/radar/mine', { method: 'POST' })
      const job = await waitJob(r.job_id, (j) => setExtractBusy(`从拆解库挖话题：${j.progress}% ${j.message}`))
      const hs = (job.result?.hashtags as { ch_id: string; name: string; from?: string }[]) ?? []
      setExtracted((e) => {
        const known = new Set(e.map((t) => t.ch_id))
        return [...e, ...hs.filter((h) => !known.has(h.ch_id)).map((h) => ({ ch_id: h.ch_id, name: h.name }))]
      })
      // 空结果必须说清楚，不能静默（此前修复后返回空数组时界面毫无反应，像"功能没实现"）
      setMineInfo(hs.length > 0
        ? `挖到 ${hs.length} 个话题（去重后新增 ${hs.length}）`
        : '拆解库里没挖到新话题：可能已全部添加过，或最近批准的视频不带 # 标签')
      setExtractBusy('')
    })

  const extract = () =>
    guard(async () => {
      const r = await api<{ job_id: number }>('/radar/extract-hashtags', {
        method: 'POST',
        body: JSON.stringify({ url: linkUrl.trim() }),
      })
      const job = await waitJob(r.job_id, (j) => setExtractBusy(`${j.progress}% ${j.message}`))
      setExtracted((job.result?.hashtags as { ch_id: string; name: string }[]) ?? [])
      setExtractBusy('')
    })

  const addKnown = (ch_id: string, name: string) =>
    guard(async () => {
      await api('/radar', { method: 'POST', body: JSON.stringify({ ch_id, name }) })
      setExtracted((e) => e.filter((t) => t.ch_id !== ch_id))
      await reload()
    })

  const addToBenchmarks = (vid: string) =>
    guard(async () => {
      await api('/analyze/video', {
        method: 'POST',
        body: JSON.stringify({ url: `https://www.douyin.com/video/${vid}` }),
      })
      setAddedIds((s) => new Set(s).add(vid))
    })

  const reload = useCallback(async () => {
    const r = await api<{ items: RadarTopic[] }>('/radar')
    setItems(r.items)
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
  }, [reload])

  const guard = async (fn: () => Promise<void>) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy('')
    }
  }

  const add = () =>
    guard(async () => {
      if (!form.name.trim() || !form.ch_id.trim()) return
      await api('/radar', { method: 'POST', body: JSON.stringify(form) })
      setForm({ name: '', ch_id: '' })
      await reload()
    })

  const remove = (id: number) =>
    guard(async () => {
      await api(`/radar/${id}`, { method: 'DELETE' })
      await reload()
    })

  const toggle = (id: number) =>
    guard(async () => {
      await api(`/radar/${id}/toggle`, { method: 'POST' })
      await reload()
    })

  const checkNow = (t: RadarTopic) =>
    guard(async () => {
      setBusy(`巡检 #${t.name}…`)
      const r = await api<{ new: number }>(`/radar/${t.id}/check`, { method: 'POST' })
      setBusy(`#${t.name}：新增 ${r.new} 条视频`)
      setTimeout(() => setBusy(''), 2500)
      await reload()
    })

  const checkAll = () =>
    guard(async () => {
      const r = await api<{ job_id: number }>('/radar/check-all', { method: 'POST' })
      const job = await waitJob(r.job_id, (j) => setBusy(`话题雷达：${j.progress}% ${j.message}`))
      setBusy(`巡检完成：${(job.result?.notes ?? []).join('；') || '无变化'}`)
      setTimeout(() => setBusy(''), 4000)
      await reload()
    })

  return (
    <div>
      <PageHeader
        icon={Flame}
        title="话题雷达"
        desc="监控泛 AI 类抖音话题：每日巡检话题下新视频，新增提速即推送（macOS 通知）"
        actions={
          <button onClick={checkAll} disabled={busy !== ''} className="btn-accent disabled:opacity-50">
            <RefreshCw size={14} /> 立即巡检全部
          </button>
        }
      />

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      <div className="card mt-3 border-sky-200 bg-sky-50/40 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={linkUrl}
            onChange={(e) => setLinkUrl(e.target.value)}
            placeholder="贴一条带该话题的抖音视频链接/口令"
            className="input min-w-72 flex-1"
          />
          <button
            onClick={mine}
            disabled={extractBusy !== ''}
            className="btn-ghost btn-xs"
            title="探测拆解库视频携带的话题，供点选监控"
          >
            从拆解库挖话题
          </button>
          <button
            onClick={extract}
            disabled={!linkUrl.trim() || extractBusy !== ''}
            className="btn-accent btn-xs disabled:opacity-40"
          >
            <Search size={13} /> {extractBusy || '提取话题'}
          </button>
        </div>
        {mineInfo && (
          <div className="mt-2 rounded-lg bg-slate-50 px-3 py-1.5 text-xs text-slate-500">{mineInfo}</div>
        )}
        {extracted.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-slate-400">点击添加监控：</span>
            {extracted.map((t) => (
              <button
                key={t.ch_id}
                onClick={(e) => { e.stopPropagation(); addKnown(t.ch_id, t.name) }}
                className="rounded-full border border-sky-300 bg-white px-2.5 py-1 text-xs text-sky-700 hover:bg-sky-50"
              >
                #{t.name} +
              </button>
            ))}
          </div>
        )}
        <details className="text-[11px] text-slate-400">
          <summary className="cursor-pointer">或手动输入话题名 + ch_id（高级）</summary>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="话题名，如：企业数字化转型"
              className="input max-w-40"
            />
            <input
              value={form.ch_id}
              onChange={(e) => setForm({ ...form, ch_id: e.target.value })}
              placeholder="话题 ch_id（纯数字）"
              className="input max-w-64"
            />
            <button
              onClick={add}
              disabled={!form.name.trim() || !form.ch_id.trim()}
              className="btn-accent btn-xs disabled:opacity-40"
            >
              <Plus size={13} /> 添加监控
            </button>
          </div>
        </details>
        <div className="mt-2 text-[11px] text-slate-400">
          热度代理 = 话题下新增视频速度（话题接口无互动数字）；单次巡检新增 ≥5 条判为飙升并推送。
          每日凌晨随定时扫描自动巡检一次。
        </div>
      </div>

      <div className="card mt-4 overflow-hidden p-0">
        {items.length === 0 ? (
          <div className="p-6 text-sm text-slate-400">还没有监控话题，在上面添加或等自动预置。</div>
        ) : (
          <div className="divide-y divide-slate-100">
            {items.map((t) => {
              const exp = expanded === t.id
              return (
                <div key={t.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => setExpanded(exp ? null : t.id)}
                    className="flex cursor-pointer items-center gap-3 px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    <span className={`badge shrink-0 ${t.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                      {t.enabled ? '监控中' : '已停用'}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-zinc-900">#{t.name}</div>
                      <div className="mt-0.5 truncate text-xs text-slate-400">
                        上次巡检新增 {t.new_since_last} 条
                        {t.recent.length > 0 && ` · 最新：${t.recent[0]?.desc ?? ''}`}
                        {t.new_since_last === 0 && t.recent.length > 0 && '（话题下暂无新视频，属正常）'}
                      </div>
                    </div>
                    <span className="hidden w-20 shrink-0 text-right text-[11px] text-slate-400 md:inline">
                      {t.last_checked_at ? relTime(t.last_checked_at) : '未巡检'}
                    </span>
                    <code className="hidden shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-400 md:inline">
                      {t.ch_id}
                    </code>
                  </div>
                  <div className="flex items-center gap-1 border-t border-slate-50 px-4 py-1.5">
                    <button onClick={() => checkNow(t)} className="btn-ghost btn-xs text-slate-500">
                      <RefreshCw size={12} /> 巡检
                    </button>
                    <button onClick={() => toggle(t.id)} className="btn-ghost btn-xs text-slate-500">
                      <Power size={12} /> {t.enabled ? '停用' : '启用'}
                    </button>
                    <button
                      onClick={() => remove(t.id)}
                      className="btn-ghost btn-xs ml-auto text-slate-300 hover:text-red-500"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                  {exp && (t.hits?.length ?? 0) > 0 && (
                    <div className="border-t border-sky-100 bg-orange-50/40 px-5 py-3">
                      <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-orange-700">
                        <Flame size={12} /> 低粉高赞清单（小账号爆款信号，赞粉比降序）
                      </div>
                      <ul className="space-y-1.5">
                        {t.hits!.map((h) => (
                          <li key={h.id} className="flex flex-wrap items-center gap-2 text-[13px]">
                            <a
                              href={`https://www.douyin.com/video/${h.id}`}
                              target="_blank"
                              rel="noreferrer"
                              className="truncate text-sky-600 hover:underline"
                            >
                              {h.desc || h.id}
                            </a>
                            <span className="shrink-0 text-[11px] text-slate-400">@{h.author}</span>
                            <span className="shrink-0 text-[11px] text-slate-500">
                              粉 <b className="tabular-nums">{h.followers >= 10000 ? `${(h.followers / 10000).toFixed(1)}w` : h.followers}</b>
                              {' '}· 赞 <b className="tabular-nums text-orange-600">{h.digg.toLocaleString()}</b>
                              {' '}· 赞粉比 <b className="tabular-nums text-orange-600">{h.ratio}</b>
                            </span>
                            {addedIds.has(h.id) ? (
                              <span className="badge shrink-0 bg-emerald-100 text-emerald-700">已入拆解库</span>
                            ) : (
                              <button
                                onClick={(e) => { e.stopPropagation(); addToBenchmarks(h.id) }}
                                className="btn-accent btn-xs shrink-0"
                              >
                                加入拆解库
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {exp && t.recent.length > 0 && (
                    <div className="border-t border-sky-100 px-5 py-3">
                      <div className="mb-1.5 text-xs font-semibold text-slate-500">话题下最新视频（每次巡检刷新）</div>
                      <ul className="space-y-1">
                        {t.recent.map((v) => (
                          <li key={v.id} className="flex items-center gap-2 truncate text-[13px] text-slate-600">
                            · {v.new && <span className="rounded bg-amber-100 px-1 text-[10px] text-amber-700">新</span>}
                            <a
                              href={`https://www.douyin.com/video/${v.id}`}
                              target="_blank"
                              rel="noreferrer"
                              className="truncate text-sky-600 hover:underline"
                            >
                              {v.desc || v.id}
                            </a>
                            {addedIds.has(v.id) ? (
                              <span className="badge shrink-0 bg-emerald-100 text-emerald-700">已入拆解库</span>
                            ) : (
                              <button
                                onClick={(e) => { e.stopPropagation(); addToBenchmarks(v.id) }}
                                className="btn-ghost btn-xs shrink-0 text-slate-500"
                              >
                                加入拆解库
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
