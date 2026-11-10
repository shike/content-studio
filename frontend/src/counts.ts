import { api } from './api'
import type { Article, Script, Topic } from './api'

/** 流水线待办统计（工作台卡片数据源）。
2026-11-10 清理：pendingScripts/unwrittenTopics/unwrittenPersona/unwrittenLeadgen/finalScripts
5 个孤儿字段已随后端 /stats/todos 同步删除，契约只剩下面这些。 */
export interface TodoStats {
  draftTopics: number // 待审选题（两线合计）
  draftTopicsPersona: number // 待审选题·人设库
  draftTopicsLeadgen: number // 待审选题·获客库
  pendingScriptsPersona: number // 待定稿脚本·人设
  pendingScriptsLeadgen: number // 待定稿脚本·获客
  pendingArticles: number // 待写长文
  kbTopics: number // 知识库选题待拍板
  openCandidatesPersona: number // 候选账号待定夺·人设线
  openCandidatesLeadgen: number // 候选账号待定夺·获客线
}

export async function fetchTodoStats(): Promise<TodoStats> {
  // 服务端真值口径（/api/stats/todos，全库·本租户）——此前前端拉 4 个大列表自行派生，
  // 窗口口径不一致（scripts 被后端截断 200/ articles 只拉 100）且随数据量增长失真
  return api<TodoStats>('/stats/todos')
}

/** 菜单徽章统计（2026-11-07 产线树定版）：徽章落在每个叶子页面自身，
不再做产线合计——树组徽章由 App 按子叶求和。过程性数字（生成中/排队/待拆）不上菜单。 */
export interface MenuStats {
  today: number // 今天=待审选题合计
  p_topics: number // 人设选题库=待审选题·人设
  p_content: number // 内容看板=待定稿脚本·人设
  p_bench: number // 对标拆解=拆解待定夺·人设
  p_cand: number // 候选定夺=候选待定夺·人设
  l_topics: number // 获客选题=待审选题·获客
  l_scripts: number // 脚本工场=待定稿脚本·获客
  l_cand: number // 纯获客候选=候选待定夺·获客
  l_bench: number // 打法卡库=拆解待定夺·获客
  article: number // 内容工场=待拍板选题+待写报题稿（2026-11-09 两页合并后本页全部待人工入口）
  tasks: number // 在途（运行中+排队+失败）
  failed: number // 终态失败计数（Today 待办卡用）
  tasks_alert: number // 失败>0 → 徽章变红
}

export async function fetchMenuStats(): Promise<MenuStats> {
  const [todo, bmP, bmL, jobs] = await Promise.all([
    fetchTodoStats(),
    api<{ counts: { todo: number; pending: number } }>('/benchmarks?limit=1&account_type=persona'),
    api<{ counts: { todo: number; pending: number } }>('/benchmarks?limit=1&account_type=leadgen'),
    api<{ counts: { running: number; queued: number; failed: number } }>('/jobs?limit=1'),
  ])
  return {
    today: todo.draftTopics,
    p_topics: todo.draftTopicsPersona,
    p_content: todo.pendingScriptsPersona,
    p_cand: todo.openCandidatesPersona,
    p_bench: bmP.counts.pending,
    l_topics: todo.draftTopicsLeadgen,
    l_scripts: todo.pendingScriptsLeadgen,
    l_cand: todo.openCandidatesLeadgen,
    l_bench: bmL.counts.pending,
    article: todo.kbTopics + todo.pendingArticles,
    // 在途 + 失败（失败是异常不是工作流：可见但不建流程，瞬时类由每日慢巡自愈）
    tasks: jobs.counts.running + jobs.counts.queued + (jobs.counts.failed || 0),
    failed: jobs.counts.failed || 0,
    tasks_alert: (jobs.counts.failed || 0) > 0 ? 1 : 0,
  }
}

/** 菜单徽章数字：键与 App 树叶子一一对应，透传即可。 */
export function menuBadges(s: MenuStats): Partial<Record<string, number>> {
  return { ...s }
}
