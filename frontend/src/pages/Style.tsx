import { useCallback, useEffect, useState } from 'react'
import {
  CircleAlert, Fingerprint, Play, RefreshCw, Sparkles,
} from 'lucide-react'
import { api, waitJob } from '../api'
import { Busy, EmptyState, ErrorLine, relTime, Pager, safeHref } from '../components'

import type { SectionKey } from '../App'

interface Profile {
  version: number
  digest: string
  traits: string[]
  exemplars: string[]
  based_on: number
  created_at?: string
}

interface SelfVideoRow {
  id: number
  title: string
  url: string
  analyzed: boolean
  analyzed_at?: string | null
  scan_status?: string
  style_analysis: Record<string, any> | null
}

interface Overview {
  account: { id: number; name: string; enabled: boolean } | null
  profile: Profile | null
  profile_history: Profile[]
  videos: SelfVideoRow[]
  stats: { total: number; analyzed: number; pending: number }
}

/** 风格画像区（2026-11-07 并入账号经营页，本组件不再自带页头）：
从自己作品提炼说话方式，脚本生成时自动注入。 */
export default function StyleSection({ onNavigate }: { onNavigate?: (k: SectionKey) => void }) {
  const [data, setData] = useState<Overview | null>(null)
  const [videos, setVideos] = useState<SelfVideoRow[]>([])
  const [vtotal, setVtotal] = useState(0)
  const [voffset, setVoffset] = useState(0)
  const [vstatus, setVstatus] = useState('')
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const VLIMIT = 20
  const [busy, setBusy] = useState('')
  const [done, setDone] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(VLIMIT), offset: String(voffset) })
    if (vstatus) params.set('status', vstatus)
    const [ov, vl] = await Promise.all([
      api<Overview>('/style/overview'),
      api<{ items: SelfVideoRow[]; total: number }>(`/style/videos?${params}`),
    ])
    setData(ov)
    setVideos(vl.items)
    setVtotal(vl.total)
  }, [voffset, vstatus])

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

  const scan = () =>
    guard(async () => {
      setDone('')
      const r = await api<{ job_id: number }>('/style/scan', { method: 'POST' })
      const job = await waitJob(r.job_id, (j) => setBusy(`扫描我的账号：${j.progress}% ${j.message}`))
      setBusy('')
      setDone(`扫描完成：新增 ${job.result?.queued ?? 0} 条，已自动排队风格拆解`)
      await reload()
    })

  // 忽略出口只留给「无法判断」的废视频；正常视频已全自动拆解（2026-11-07 定版）
  const ignoreVideo = (id: number) =>
    (async () => {
      try {
        await api(`/style/videos/${id}/resolve`, {
          method: 'POST',
          body: JSON.stringify({ action: 'ignore' }),
        })
        await reload()
      } catch (e) {
        setError(String(e))
      }
    })()

  const updateProfile = () =>
    guard(async () => {
      setDone('')
      const r = await api<{ job_id: number }>('/style/profile/update', { method: 'POST' })
      const job = await waitJob(r.job_id, (j) => setBusy(`重算画像：${j.progress}% ${j.message}`))
      setBusy('')
      const v = (job.result?.version as number) ?? ''
      setDone(`画像 v${v} 已生成`)
      await reload()
    })

  if (data && !data.account) {
    return (
      <div className="card mt-4 p-6">
        <EmptyState
          icon={Fingerprint}
          title="还没有标记「我的账号」"
          desc="去「监控清单」添加你的账号链接，并勾选「这是我的账号」。系统会持续扫描你的新视频，提炼说话风格，生成脚本时自动贴合。"
        />
        {onNavigate && (
          <div className="flex justify-center">
            <button onClick={() => onNavigate('p_watch')} className="btn-accent">去监控清单添加 →</button>
          </div>
        )}
        {error && <div className="mt-3"><ErrorLine text={error} /></div>}
      </div>
    )
  }

  const p = data?.profile

  return (
    <div className="mt-4">
      {/* 小节头：标题 + 主操作（合并页内自己的动作留在自己的区块） */}
      <div className="flex flex-wrap items-center gap-2">
        <Fingerprint size={16} className="text-[#0a2f24]" />
        <span className="text-sm font-semibold text-zinc-900">风格画像</span>
        <span className="text-[11px] text-slate-400">基于自己视频提炼说话方式，脚本生成时自动注入</span>
        <div className="ml-auto flex gap-2">
          <button onClick={updateProfile} disabled={Boolean(busy) || !data?.stats.analyzed} className="btn-ghost disabled:opacity-40">
            <RefreshCw size={14} /> 重算画像
          </button>
          <button onClick={scan} disabled={Boolean(busy)} className="btn-accent disabled:opacity-40">
            <Play size={14} /> 扫描我的账号
          </button>
        </div>
      </div>
      {/* 状态行 */}
      <div className="card mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 px-5 py-3 text-[13px] text-slate-600">
        <span>账号 <b className="text-zinc-900">{data?.account?.name || '—'}</b></span>
        <span>已分析 <b className="text-zinc-900">{data?.stats.analyzed ?? 0}</b> / {data?.stats.total ?? 0} 条</span>
        <span>画像 <b className="text-zinc-900">{p ? `v${p.version}（基于 ${p.based_on} 条）` : '未生成'}</b></span>
        <span className="ml-auto text-[11px] text-slate-400">
          脚本生成默认注入画像，可在脚本工场按次关闭
        </span>
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {done && (
        <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3.5 py-2 text-sm text-emerald-800">
          {done}
        </div>
      )}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {!p && (
        <div className="card mt-4 flex items-center gap-2.5 border-amber-200 bg-amber-50/60 px-4 py-3 text-[13px] text-amber-800">
          <CircleAlert size={15} />
          画像还没生成：扫描拆解积累几条视频后自动出首版，也可点右上角「重算画像」手动生成。
        </div>
      )}

      {/* 画像卡 */}
      {p && (
        <div className="card mt-4 border-[#d3d6ec] bg-linear-to-br from-white to-[#f4f5fa] p-5">
          <div className="flex flex-wrap items-center gap-2">
            <Sparkles size={15} className="text-sky-600" />
            <span className="text-sm font-semibold text-zinc-900">风格画像 v{p.version}</span>
            <span className="text-xs text-slate-400">基于最近 {p.based_on} 条视频 · {relTime(p.created_at)}</span>
          </div>
          <p className="mt-3 whitespace-pre-wrap text-[13px] leading-relaxed text-slate-700">{p.digest}</p>
          {p.traits.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {p.traits.map((t) => (
                <span key={t} className="badge bg-sky-100 text-sky-700">{t}</span>
              ))}
            </div>
          )}
          {p.exemplars.length > 0 && (
            <div className="mt-4">
              <div className="mb-1.5 text-xs font-semibold text-slate-500">代表原句（逐字）</div>
              <div className="space-y-1.5">
                {p.exemplars.map((e, i) => (
                  <div key={i} className="rounded-lg border-l-2 border-sky-400 bg-sky-50/70 px-3.5 py-2 text-[13px] text-zinc-800">
                    「{e}」
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div className="mt-4">
        {/* 已分析视频 */}
        <div className="card overflow-hidden p-0">
          <div className="border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-zinc-900">
            我的视频（{vtotal}）
          </div>
          <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-100 px-4 py-2.5">
            {[['', '全部'], ['done', '已拆解'], ['todo', '待拆解']].map(([k, label]) => (
              <button key={k} onClick={() => { setVstatus(k); setVoffset(0) }}
                      className={`rounded-full px-3 py-1 text-xs font-medium transition ${vstatus === k ? 'bg-slate-900 text-sky-400' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}>
                {label}
              </button>
            ))}
          </div>
          {videos.length === 0 ? (
            <div className="p-5 text-[13px] text-slate-400">还没有视频，点右上角「扫描我的账号」开始。</div>
          ) : (
            <>
            <div className="divide-y divide-slate-100">
              {videos.map((v) => {
                const sa = v.style_analysis || {}
                const exp = expandedId === v.id
                return (
                <div key={v.id} className={exp ? 'bg-sky-50/40' : ''}>
                  <div
                    onClick={() => { setExpandedId(exp ? null : v.id); if (!exp) setVoffset(voffset) }}
                    className="cursor-pointer px-4 py-2.5 transition hover:bg-slate-50"
                  >
                    <div className="flex items-center gap-2">
                      {(() => {
                        const invalid = v.analyzed && String(v.style_analysis?.opening_pattern || '').startsWith('无法判断')
                        return (
                        <span className={`badge shrink-0 ${invalid ? 'bg-slate-200 text-slate-500' : v.analyzed ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                          {invalid ? '无可分析内容' : v.analyzed ? '已拆解' : '待拆解'}
                        </span>
                        )
                      })()}
                      <span className="truncate text-[13px] text-zinc-800">{v.title || `视频 #${v.id}`}</span>
                      <span className="ml-auto shrink-0 flex items-center gap-1.5">
                        {v.analyzed && String(v.style_analysis?.opening_pattern || '').startsWith('无法判断') && (
                          <button onClick={(e) => { e.stopPropagation(); ignoreVideo(v.id) }} className="btn-ghost btn-xs text-slate-400">忽略</button>
                        )}
                        <span className="text-[11px] text-slate-400">{relTime(v.analyzed_at || undefined)}</span>
                      </span>
                    </div>
                    {!exp && sa.opening_pattern && (
                      <div className="mt-1 truncate text-[11px] text-slate-400">
                        开场：{sa.opening_pattern}
                      </div>
                    )}
                  </div>
                  {exp && v.analyzed && (
                    <div className="space-y-2.5 border-t border-sky-100 px-4 py-3.5 text-[13px] leading-relaxed">
                      {[
                        ['开场模式', sa.opening_pattern],
                        ['节奏', sa.rhythm],
                        ['语气', sa.tone],
                        ['论证习惯', sa.argument_habit],
                        ['收尾风格', sa.closing_style],
                      ].filter(([, val]) => val).map(([label, val]) => (
                        <div key={label}>
                          <span className="mr-2 text-xs font-semibold text-slate-400">{label}</span>
                          <span className="text-slate-700">{val}</span>
                        </div>
                      ))}
                      {Array.isArray(sa.phrases) && sa.phrases.length > 0 && (
                        <div>
                          <span className="mr-2 text-xs font-semibold text-slate-400">口头禅/高频短语</span>
                          <span className="text-slate-700">{sa.phrases.join('、')}</span>
                        </div>
                      )}
                      {Array.isArray(sa.exemplars) && sa.exemplars.length > 0 && (
                        <div>
                          <div className="mb-1 text-xs font-semibold text-slate-400">代表原句</div>
                          {sa.exemplars.map((e: string, i: number) => (
                            <div key={i} className="rounded-lg border-l-2 border-sky-400 bg-sky-50/70 px-3 py-1.5 text-slate-700">「{e}」</div>
                          ))}
                        </div>
                      )}
                      {v.url && (
                        <a href={safeHref(v.url)} target="_blank" rel="noreferrer" className="text-xs text-sky-600 hover:underline">
                          看原视频 →
                        </a>
                      )}
                    </div>
                  )}
                </div>
                )
              })}
            </div>
            <Pager total={vtotal} limit={VLIMIT} offset={voffset} onPage={setVoffset} />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
