import { api } from './api'
import type { Article, Script, Topic } from './api'

/** 流水线待办统计（工作台卡片数据源）。 */
export interface TodoStats {
  draftTopics: number // 待审选题
  pendingScripts: number // 待定稿脚本
  unwrittenTopics: number // 待写脚本（已定审选题）
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
  tasks: number // 运行中 + 排队 + 待处置任务（待处置非空 = 系统带病运行，必须可见）
  disposal: number // 待处置条数（失败中台：按指纹聚类的无人认领失败）
  digital: number // 数字人视频生成中
}

export async function fetchMenuStats(): Promise<MenuStats> {
  const [todo, bm, acc, styleOv, jobs, avres, failures] = await Promise.all([
    fetchTodoStats(),
    api<{ counts: { todo: number; pending: number } }>('/benchmarks?limit=1'),
    api<{ items: { enabled: boolean }[] }>('/watch/accounts'),
    api<{ stats: { pending: number } }>('/style/overview'),
    api<{ counts: { running: number; queued: number; parked?: number } }>('/jobs?limit=1'),
    api<{ items: { status: string }[]; counts?: Record<string, number> }>('/avatar/videos'),
    api<{ disposal: number }>('/jobs/failures'),
  ])
  const av = avres
  return {
    ...todo,
    publishing: todo.finalScripts,
    benchmarksPending: bm.counts.pending,
    benchmarksTodo: bm.counts.todo,
    benchmarks: bm.counts.pending + bm.counts.todo,
    watch: acc.items.filter((a) => a.enabled).length,
    style: styleOv.stats.pending,
    disposal: failures.disposal || 0,
    // 在途任务 = 运行中 + 排队 + 挂起（额度/风控等待自动重试，也是活任务）+ 待处置
    tasks: jobs.counts.running + jobs.counts.queued + (jobs.counts.parked || 0) + (failures.disposal || 0),
    digital: av.counts?.generating ?? av.items.filter((x) => x.status === 'generating').length,
  }
}

/** 菜单徽章数字（2026-09-28 用户定版，替代 Q1 收紧口径）：显示「可工作/待拍板」的活账——
 *  选题库=待审 · 脚本工场=待定稿 · 公众号=待写长文 · 拆解库=待定夺+待拆 · 任务=待处置。
 *  过程性数字（生成中/排队）仍不上菜单，需要时去任务页看。 */
export function menuBadges(s: MenuStats): Partial<Record<string, number>> {
  return {
    topics: s.draftTopics,                          // 待审选题
    scripts: s.pendingScripts,                      // 待定稿脚本
    articles: s.pendingArticles,                    // 待写长文
    benchmarks: s.benchmarksPending + s.benchmarksTodo, // 待定夺 + 待拆
    style: s.style,                                 // 风格视频待定夺
    // 任务徽章（2026-10-07 三次调整）：恒显在途数（运行中+排队+挂起）——待处置优先会把它
    // 顶掉、漏掉 parked 会少算风控窗等待的活任务；待处置>0 由 tasks_alert 让徽章变红警示
    tasks: s.tasks - s.disposal,
    tasks_alert: s.disposal > 0 ? 1 : 0,
  }
}
