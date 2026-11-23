import { useCallback, useEffect, useState } from 'react'
import { Kanban } from 'lucide-react'
import { api, apiErrText } from '../api'
import { ErrorLine, relTime } from '../components'

/**
 * 数据 · 看板（遗留收尾批 M8 2026-11-22）：六列内容流程视图——
 * 选题待定夺 → 成稿中 → 待终审 → 待发布 → 已发布 → 已复盘。
 * 只读视图：卡片点击直达处理页；真实状态流转在各线页面，不做拖拽。
 */

interface Card { id: number; title: string; kind?: string; channel?: string; days: number | null }
interface Board { columns: Record<string, { n: number; items: Card[] }> }

const COLS: { key: string; label: string; nav: string; cls: string }[] = [
  { key: 'ideas', label: '选题待定夺', nav: 'persona', cls: 'border-slate-200' },
  { key: 'drafting', label: '成稿中', nav: 'persona', cls: 'border-sky-200' },
  { key: 'review', label: '待终审', nav: 'persona', cls: 'border-amber-200' },
  { key: 'ready', label: '待发布', nav: 'schedule', cls: 'border-emerald-200' },
  { key: 'published', label: '已发布·未回流', nav: 'analytics', cls: 'border-orange-200' },
  { key: 'reviewed', label: '已复盘', nav: 'analytics', cls: 'border-violet-200' },
]
const KIND_LABEL: Record<string, string> = {
  video_script: '口播', long_article: '长文', note: '笔记', visual_deck: '图文卡', moments: '朋友圈',
}
const CH_LABEL: Record<string, string> = { persona: '人设', leadgen: '获客', wechat: '公众号', xhs: '小红书' }

export default function PipelineTab({ onNavigate }: { onNavigate?: (k: string) => void } = {}) {
  const [board, setBoard] = useState<Board | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api<Board>('/analytics/pipeline')
      .then(setBoard)
      .catch((e) => setError(apiErrText(e)))
  }, [])
  useEffect(load, [load])

  return (
    <div>
      <ErrorLine text={error} />
      <div className="mb-3 flex items-center gap-2 text-[12.5px] text-slate-500">
        <Kanban size={14} className="text-emerald-600" />
        六列=一条内容的一生；卡片点击直达处理页（状态在各线页面流转，看板只读）。
      </div>
      {!board ? <div className="py-6 text-center text-sm text-slate-300">看板加载中…</div> : (
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-6">
          {COLS.map((col) => {
            const c = board.columns[col.key] || { n: 0, items: [] }
            return (
              <div key={col.key} className={`rounded-xl border ${col.cls} bg-white px-2.5 py-2`}>
                <div className="mb-2 flex items-center gap-1.5">
                  <span className="text-[12px] font-semibold text-slate-700">{col.label}</span>
                  <span className="num-serif ml-auto text-[12px] text-slate-400">{c.n}</span>
                </div>
                <div className="space-y-1.5">
                  {c.items.map((it) => (
                    <button key={`${col.key}-${it.id}`} onClick={() => onNavigate?.(col.nav)}
                      className="w-full rounded-lg bg-slate-50 px-2 py-1.5 text-left transition hover:bg-slate-100">
                      <div className="truncate text-[12px] font-medium text-slate-800">
                        {it.title || `#${it.id}`}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5 text-[10.5px] text-slate-400">
                        {it.kind && <span>{KIND_LABEL[it.kind] || it.kind}</span>}
                        {it.channel && <span>{CH_LABEL[it.channel] || it.channel}</span>}
                        {it.days !== null && it.days > 0 && <span className={it.days >= 3 ? 'text-amber-600' : ''}>停 {it.days} 天</span>}
                      </div>
                    </button>
                  ))}
                  {c.n === 0 && <div className="py-1 text-[11px] text-slate-300">空</div>}
                  {c.n > c.items.length && (
                    <button onClick={() => onNavigate?.(col.nav)}
                      className="w-full text-[11px] text-slate-400 hover:text-slate-700">
                      还有 {c.n - c.items.length} 条 →
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {board && (board.columns.published?.n ?? 0) === 0 && (
        <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px] text-amber-800">
          已发布=0——发完记得回登记（数据 · 补录带）；已发布/已复盘/排期/轨迹靠登记的数据喂养。
        </div>
      )}
      {board && (
        <div className="mt-3 text-[11px] text-slate-300">
          卡片时间：{relTime(new Date().toISOString())} 拉取 · 停留≥3 天标黄（该催自己了）
        </div>
      )}
    </div>
  )
}
