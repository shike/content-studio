import { api } from './api'
import type { Article, Script, Topic } from './api'

/** 流水线待办统计（工作台卡片数据源）。 */
export interface TodoStats {
  draftTopics: number // 待审选题（两线合计）
  draftTopicsPersona: number // 待审选题·人设库
  draftTopicsLeadgen: number // 待审选题·获客库
  pendingScripts: number // 待定稿脚本（两线合计）
  pendingScriptsPersona: number
  pendingScriptsLeadgen: number
  unwrittenTopics: number // 待写脚本（已定审选题）
  unwrittenPersona: number
  unwrittenLeadgen: number
  pendingArticles: number // 待写长文
  finalScripts: number // 已定稿脚本（发布台可生成物料包）
}

export async function fetchTodoStats(): Promise<TodoStats> {
  // 服务端真值口径（/api/stats/todos，全库·本租户）——此前前端拉 4 个大列表自行派生，
  // 窗口口径不一致（scripts 被后端截断 200/ articles 只拉 100）且随数据量增长失真
  return api<TodoStats>('/stats/todos')
}

/** 菜单徽章统计：每个可积压页面的"待处理数"，App 层 60s 刷新。 */
export interface MenuStats extends TodoStats {
  publishing: number // 已定稿可生成物料包
  benchmarks: number // 待拆解视频（待定夺 + 已批准待拆）
  benchmarksPending: number // 其中待定夺
  benchmarksTodo: number // 其中已批准待拆
  watch: number // 监控中账号数
  style: number // 待拆解风格视频
  failed: number // 终态失败（安静可见，慢巡自愈中）
  tasks: number // 运行中 + 排队 + 待处置任务（待处置非空 = 系统带病运行，必须可见）
}

export async function fetchMenuStats(): Promise<MenuStats> {
  const [todo, bm, acc, styleOv, jobs] = await Promise.all([
    fetchTodoStats(),
    api<{ counts: { todo: number; pending: number } }>('/benchmarks?limit=1'),
    api<{ items: { enabled: boolean }[] }>('/watch/accounts'),
    api<{ stats: { pending: number } }>('/style/overview'),
    api<{ counts: { running: number; queued: number; parked?: number; failed: number } }>('/jobs?limit=1'),
  ])
  return {
    ...todo,
    publishing: todo.finalScripts,
    benchmarksPending: bm.counts.pending,
    benchmarksTodo: bm.counts.todo,
    benchmarks: bm.counts.pending + bm.counts.todo,
    watch: acc.items.filter((a) => a.enabled).length,
    style: styleOv.stats.pending,
    // 在途 + 失败（失败是异常不是工作流：可见但不建流程，瞬时类由每日慢巡自愈）
    tasks: jobs.counts.running + jobs.counts.queued + (jobs.counts.failed || 0),
    failed: jobs.counts.failed || 0,
  }
}

/** 菜单徽章数字（2026-09-28 用户定版口径 + 2026-11-02 三线拆分）：
 *  人设号=人设待审选题+待定稿脚本 · 获客号=获客待审选题+待定稿脚本 · 公众号=待写长文 ·
 *  拆解库=待定夺+待拆 · 任务=在途（待处置红警）。过程性数字（生成中/排队）不上菜单。 */
export function menuBadges(s: MenuStats): Partial<Record<string, number>> {
  return {
    today: s.draftTopics,                           // 今日工作台徽章 = 待审选题（每日动线口径）
    persona: s.draftTopicsPersona + s.pendingScriptsPersona,   // 人设号：两 tab 活账合计
    leadgen: s.draftTopicsLeadgen + s.pendingScriptsLeadgen,   // 获客号：两 tab 活账合计
    article: s.pendingArticles,                     // 公众号：待写长文
    benchmarks: s.benchmarksPending + s.benchmarksTodo, // 待定夺 + 待拆
    style: s.style,                                 // 风格视频待定夺
    // 任务徽章（2026-10-07 三次调整）：恒显在途数（运行中+排队+挂起）——待处置优先会把它
    // 顶掉、漏掉 parked 会少算风控窗等待的活任务；待处置>0 由 tasks_alert 让徽章变红警示
    tasks: s.tasks,
    tasks_alert: s.failed > 0 ? 1 : 0,
  }
}
