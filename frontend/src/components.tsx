import { AlertCircle, Loader2 } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export function PageHeader({
  icon: Icon,
  title,
  desc,
  actions,
}: {
  icon: LucideIcon
  title: string
  desc: string
  actions?: React.ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        <div className="grid size-10 place-items-center rounded-xl bg-slate-900 text-sky-400">
          <Icon size={18} />
        </div>
        <div>
          <h2 className="text-lg font-semibold tracking-tight text-slate-900">{title}</h2>
          <p className="text-xs text-slate-500">{desc}</p>
        </div>
      </div>
      {actions}
    </div>
  )
}

export function Busy({ text }: { text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-800">
      <Loader2 size={14} className="animate-spin" />
      {text}
    </div>
  )
}

export function ErrorLine({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span className="break-all">{text}</span>
    </div>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  desc,
}: {
  icon: LucideIcon
  title: string
  desc: string
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 p-12 text-center">
      <div className="grid size-12 place-items-center rounded-full bg-slate-200/70 text-slate-400">
        <Icon size={22} />
      </div>
      <div className="mt-3 text-sm font-medium text-slate-500">{title}</div>
      <div className="mt-1 max-w-sm text-xs leading-relaxed text-slate-400">{desc}</div>
    </div>
  )
}

/* ---------- 队列 + 紧凑表格范式的共享组件 ---------- */

export function QueueBar({
  label,
  done,
  total,
  onExit,
  exitText = '退出',
}: {
  label: string
  done: number
  total: number
  onExit: () => void
  exitText?: string
}) {
  const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0
  return (
    <div className="mb-4 flex items-center gap-3">
      <span className="text-sm font-semibold whitespace-nowrap text-slate-700">
        {label} {done} / {total}
      </span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-sky-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
      <button onClick={onExit} className="btn-ghost btn-xs whitespace-nowrap">
        {exitText}
      </button>
    </div>
  )
}

export function ChipRow({
  chips,
  active,
  onChange,
  right,
}: {
  chips: { key: string; label: string; count?: number }[]
  active: string
  onChange: (k: string) => void
  right?: React.ReactNode
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {chips.map((c) => (
        <button
          key={c.key}
          onClick={() => onChange(c.key)}
          className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${
            active === c.key
              ? 'bg-slate-900 text-sky-400 shadow-sm'
              : 'bg-slate-100 text-slate-500 hover:bg-slate-200 hover:text-slate-700'
          }`}
        >
          {c.label}
          {typeof c.count === 'number' ? ` ${c.count}` : ''}
        </button>
      ))}
      {right && <div className="ml-auto flex items-center gap-1.5">{right}</div>}
    </div>
  )
}

export function relTime(iso?: string) {
  if (!iso) return ''
  // 后端时间戳是无时区后缀的 UTC（datetime.utcnow），补 Z 再解析，避免按本地时区偏移
  const ms = Date.now() - new Date(/[Zz]$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z').getTime()
  if (Number.isNaN(ms)) return ''
  if (ms < 0) return '刚刚'
  const min = Math.floor(ms / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} 天前`
  return `${Math.floor(d / 30)} 个月前`
}

export function Pager({ total, limit, offset, onPage }: {
  total: number
  limit: number
  offset: number
  onPage: (offset: number) => void
}) {
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  return (
    <div className="flex items-center justify-between border-t border-slate-100 px-4 py-2.5 text-xs text-slate-500">
      <span>共 {total} 条 · 第 {page}/{pages} 页</span>
      <div className="flex gap-2">
        <button disabled={offset <= 0} onClick={() => onPage(Math.max(0, offset - limit))}
                className="btn-ghost btn-xs disabled:opacity-30">上一页</button>
        <button disabled={offset + limit >= total} onClick={() => onPage(offset + limit)}
                className="btn-ghost btn-xs disabled:opacity-30">下一页</button>
      </div>
    </div>
  )
}


/** 动态链接协议守卫：仅放行 http/https，其余（javascript:/data: 等）返回 undefined。 */
export function safeHref(url?: string | null): string | undefined {
  if (!url) return undefined
  const u = url.trim().toLowerCase()
  return u.startsWith('http://') || u.startsWith('https://') ? url.trim() : undefined
}
