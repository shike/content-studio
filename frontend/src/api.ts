export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** 短暂断连（部署重启/网关抖动）：502/503/504 或网络层失败——调用方应静默重试而不是报红。 */
export function isTransientErr(e: unknown): boolean {
  const st = (e as { status?: number })?.status
  if (st === 502 || st === 503 || st === 504) return true
  const raw = String(e)
  return /HTTP (502|503|504)/.test(raw) || /Failed to fetch|NetworkError|Load failed/i.test(raw)
}

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      detail += ' ' + JSON.stringify(await res.json())
    } catch {
      /* 非 JSON 响应体，忽略 */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json()
}

/** 把 api 抛出的 "HTTP 400 {detail}" 还原成可读人话（行内错误提示用）。 */
export function apiErrText(e: unknown): string {
  if (isTransientErr(e)) return '服务正在重启（部署/重启中），稍后自动重试'
  const raw = String(e)
  const j = raw.match(/\{.*\}$/)
  if (j) {
    try {
      return String(JSON.parse(j[0])?.detail || raw)
    } catch {
      /* 非 JSON 错误体，用原文 */
    }
  }
  return raw
}

interface Job {
  id: number
  type: string
  status: string
  progress: number
  message: string
  error: string | null
  result?: Record<string, any>
}

export async function waitJob(
  jobId: number,
  onStatus?: (j: Job) => void,
  timeoutMs = 60 * 60 * 1000, // 长任务上限；超时后任务仍在后台，去「任务」页跟踪
): Promise<Job> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const job = await api<Job>(`/jobs/${jobId}`)
    onStatus?.(job)
    if (job.status === 'succeeded') return job
    if (job.status === 'failed') throw new Error(job.error || '任务失败')
    if (Date.now() > deadline) throw new Error('等待超时：任务仍在后台执行，请到「任务」页跟踪进度或重试')
    await new Promise((r) => setTimeout(r, 2000))
  }
}

interface ScriptVersion {
  label: string
  hook: string
  body: string
  notes: string
}

export interface Script {
  id: number
  ore_id?: number | null   // 获客矿建稿锚（分型路由消费面，2026-11-14）
  card_id?: number | null       // 选题卡建稿（=idea_id 旧面别名）
  falsify_item_id?: number | null // 打假清单成稿
  kb_id?: number | null         // 知识库建稿
  hot_comment?: { material_id: number; idx: number } | null // 热评口播稿
  title_hint?: string      // 生成来源标题快照
  versions: ScriptVersion[]
  teleprompter_text: string
  storyboard: { shot: string; broll_keywords: string[]; subtitle_hint: string }[]
  title_candidates: { platform: string; title: string }[]
  status: string
  account_type?: string
  created_at?: string
}

export interface Article {
  title?: string
  kb_sources?: { title: string }[] | null
  id: number
  note?: string
  md: string
  html: string
  status: string
  publish_url?: string
  published_at?: string
  stats?: Record<string, any>
  created_at?: string
}

/** 内容清单项（2026-11-11 源文改编产线）：挖掘器评估入清单，改编器每日取项成文。 */
export interface ContentItem {
  id: number
  folder_name: string
  title: string
  source_author: string
  quality_score: number
  why_good: string
  adapt_angle: string
  best_quotes: string[]
  category: string
  priority: number
  status: string
  article_id: number | null
  note: string
  created_at?: string
}

