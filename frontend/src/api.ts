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
    throw new Error(detail)
  }
  return res.json()
}

/** 把 api 抛出的 "HTTP 400 {detail}" 还原成可读人话（行内错误提示用）。 */
export function apiErrText(e: unknown): string {
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

export interface Topic {
  id: number
  title: string
  angle: string
  audience: 'boss' | 'fde' | 'both'
  source_type: string
  source_ref?: string
  score: number | null
  status: string
  evidence: Record<string, any>
  research_report?: string
  scripts?: { id: number; status: string }[]
  articles?: { id: number; status: string }[]
  created_at?: string
  updated_at?: string
}

interface ScriptVersion {
  label: string
  hook: string
  body: string
  notes: string
}

export interface Script {
  id: number
  topic_id: number | null
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

export const SOURCE_LABELS: Record<string, string> = {
  idea: 'idea深研',
  benchmark: '同行拆解',
  manual: '手动',
  trending: '热点切角',
  self_story: '一线交付',
}

export const STATUS_LABELS: Record<string, string> = {
  draft: '待审',
  approved: '已定审',
  rejected: '已否决',
  produced: '已产出',
}

export const AUDIENCE_LABELS: Record<string, string> = {
  boss: '企业主',
  fde: '准FDE',
  both: '双受众',
}
