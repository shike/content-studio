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
  const [t, s, a, dis] = await Promise.all([
    api<{ items: Topic[] }>('/topics?limit=200'),
    // 500 与脚本工场 allScripts 同上限：started 集合要靠全量脚本算，少拉会把「待写脚本」多算
    api<{ items: Script[] }>('/scripts?limit=500'),
    api<{ items: Article[] }>('/articles?limit=100'),
    api<{ items: { scope: string; asset_id: number }[] }>('/publishing/dismissals'),
  ])
  const draftTopics = t.items.filter((x) => x.status === 'draft').length
  const pendingScripts = s.items.filter((x) => x.status === 'generated').length
  // 与脚本工场「待写选题」同口径：approved 且还没有任何脚本——已有脚本的选题走「重新生成」，不算待写
  const startedTopics = new Set(s.items.filter((x) => x.topic_id != null).map((x) => x.topic_id))
  const unwrittenTopics = t.items.filter((x) => x.status === 'approved' && !startedTopics.has(x.id)).length
  const noArticle = new Set(dis.items.filter((d) => d.scope === 'article').map((d) => d.asset_id))
  const written = new Set(a.items.map((x) => x.script_id))
  const pendingArticles = s.items.filter(
    (x) => x.status === 'final' && !written.has(x.id) && !noArticle.has(x.id),
  ).length
  const finalScripts = s.items.filter((x) => x.status === 'final').length
  return { draftTopics, pendingScripts, unwrittenTopics, pendingArticles, finalScripts }
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
    api<{ counts: { running: number; queued: number } }>('/jobs?limit=1'),
    api<{ items: { status: string }[] }>('/avatar/videos'),
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
    tasks: jobs.counts.running + jobs.counts.queued + (failures.disposal || 0),
    digital: av.items.filter((x) => x.status === 'generating').length,
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
    // 任务徽章（2026-10-07 调整）：有待处置显示待处置（带病运行必须可见），
    // 否则显示在途数（运行中+排队）——转写/生成高峰期「一堆在处理却显示 0」的修正
    tasks: s.disposal > 0 ? s.disposal : s.tasks - s.disposal,
  }
}
