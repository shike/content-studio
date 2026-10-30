import { useEffect, useState } from 'react'
import { Radar, RefreshCw } from 'lucide-react'
import { api } from '../api'
import { EmptyState, relTime } from '../components'

interface HitVideo {
  id: string
  desc: string
  author: string
  digg: number
  followers?: number
  ratio?: number
}

interface TopicRow {
  id: number
  name: string
  enabled: boolean
  hits: HitVideo[]
  updated_at?: string
}

/** 话题洞察（2026-10-30 新建）：已监测抖音话题与最近飙升视频，热点雷达每日 06:00 自动检查。 */
export default function TopicInsights() {
  const [items, setItems] = useState<TopicRow[]>([])
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [checking, setChecking] = useState<number | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = () => {
    api<{ items: TopicRow[] }>('/radar/topics')
      .then((d) => setItems(d.items || []))
      .catch((e) => setError(String(e)))
  }

  const checkNow = (id: number) =>
    (async () => {
      setChecking(id)
      setError('')
      try {
        await api(`/radar/topics/${id}/check`, { method: 'POST' })
        load()
      } catch (e) {
        setError(String(e))
      } finally {
        setChecking(null)
      }
    })()

  useEffect(() => {
    api<{ items: TopicRow[] }>('/radar/topics')
      .then((d) => { setItems(d.items || []); setLoaded(true) })
      .catch((e) => { setError(String(e)); setLoaded(true) })
  }, [])

  const enabled = items.filter((t) => t.enabled)

  return (
    <div>
      <div className="mb-2.5 text-xs text-slate-500">
        每日 06:00「热点雷达」自动检查这些抖音话题下的新视频增速，飙升的爆款视频会自动走质量闸拆解、候选进选题库。新增监测话题：贴该话题下的任意视频链接走「发现的账号」或「拆解资料库」，标签会自动入库。
      </div>
      {error && <div className="mb-2 text-sm text-red-600">{error}</div>}
      {!loaded ? (
        <div className="rounded-2xl border border-[#e8ecf0] bg-white p-8 text-center text-sm text-slate-400">加载中…</div>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-[#e8ecf0] bg-white p-8">
          <EmptyState
            icon={Radar}
            title="还没有监测的话题"
            desc="话题由系统从拆解样本的标签自动沉淀，也可在拆解库贴话题下视频链接补充。"
          />
        </div>
      ) : (
        <div className="space-y-3">
          <div className="text-xs text-slate-400">
            监测 {items.length} 个话题（启用 {enabled.length}）
          </div>
          {items.map((t) => (
            <div key={t.id} className="rounded-2xl border border-[#e8ecf0] bg-white p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`badge shrink-0 ${t.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-500'}`}>
                  {t.enabled ? '监测中' : '已停用'}
                </span>
                <span className="text-sm font-semibold text-zinc-900">#{t.name}</span>
                <span className="ml-auto text-[11px] text-slate-400">
                  {t.updated_at ? `最近检查 ${relTime(t.updated_at)}` : ''}
                </span>
                <button
                  onClick={() => checkNow(t.id)}
                  disabled={checking !== null}
                  className="btn-ghost btn-xs text-sky-600 disabled:opacity-40"
                  title="立即拉取该话题下最新视频"
                >
                  {checking === t.id ? <RefreshCw size={12} className="animate-spin" /> : <RefreshCw size={12} />}
                  {checking === t.id ? '拉取中…' : '立即刷新'}
                </button>
              </div>
              {(t.hits || []).length > 0 ? (
                <div className="mt-3 space-y-1.5">
                  {(t.hits || []).slice(0, expanded === t.id ? 20 : 5).map((h) => (
                    <div key={h.id} className="flex items-center gap-2 text-[13px]">
                      <span className="num-serif shrink-0 font-semibold tabular-nums text-orange-600">
                        {Number(h.digg || 0).toLocaleString()} 赞
                      </span>
                      <span className="min-w-0 flex-1 truncate text-zinc-700">{h.desc || h.id}</span>
                      <span className="shrink-0 text-[11px] text-slate-400">@{h.author || '未知'}</span>
                      <a
                        href={`https://www.douyin.com/video/${h.id}`}
                        target="_blank"
                        rel="noreferrer"
                        className="shrink-0 text-[11px] text-sky-600 hover:underline"
                      >
                        看视频 →
                      </a>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="mt-2.5 text-xs text-slate-400">
                  暂无命中视频——话题下新增视频增速未达飙升线，持续观察中。
                </div>
              )}
              {(t.hits || []).length > 5 && (
                <button onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                  className="mt-2 text-xs text-sky-600 hover:underline">
                  {expanded === t.id ? '收起' : `展开全部 ${(t.hits || []).length} 条`}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
