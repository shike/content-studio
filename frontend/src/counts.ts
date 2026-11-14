import { api } from './api'

/** 流水线待办统计（工作台卡片数据源）。
2026-11-10 清理：pendingScripts/unwrittenTopics/unwrittenPersona/unwrittenLeadgen/finalScripts
5 个孤儿字段已随后端 /stats/todos 同步删除，契约只剩下面这些。 */
interface TodoStats {
  targetsPersona: number // 今日靶子·待备稿（选题工场 R1.4）
  pendingScriptsPersona: number // 待定稿脚本·人设
  pendingScriptsLeadgen: number // 待定稿脚本·获客
  pendingArticles: number // 内容工场待决策稿（排队写+待发；2026-11-10 系统全干定版）
  openCandidatesPersona: number // 候选账号待定夺·人设线
  openCandidatesLeadgen: number // 候选账号待定夺·获客线
}

async function fetchTodoStats(): Promise<TodoStats> {
  // 服务端真值口径（/api/stats/todos，全库·本租户）——此前前端拉 4 个大列表自行派生，
  // 窗口口径不一致（scripts 被后端截断 200/ articles 只拉 100）且随数据量增长失真
  return api<TodoStats>('/stats/todos')
}

/** 菜单徽章统计（2026-11-07 产线树定版）：徽章落在每个叶子页面自身，
不再做产线合计——树组徽章由 App 按子叶求和。过程性数字（生成中/排队/待拆）不上菜单。 */
interface MenuStats {
  today: number // 今天=待审选题合计
  p_topics: number // 今日靶子=待备稿靶子（2026-11-11 人设号观点怼热点主轴）
  p_content: number // 内容看板=待定稿脚本·人设
  p_bench: number // 对标拆解=拆解待定夺·人设
  p_cand: number // 候选定夺=候选待定夺·人设
  l_scripts: number // 脚本工场=待定稿脚本·获客
  l_cand: number // 纯获客候选=候选待定夺·获客
  l_bench: number // 打法卡库=拆解待定夺·获客
  c_items: number // 内容清单=待改编源文数（2026-11-11 源文改编产线）
  article: number // 内容工场=待拍板选题+待写报题稿（2026-11-09 两页合并后本页全部待人工入口）
  tasks: number // 在途（运行+排队+退避中/慢巡；2026-11-11 用户定版：退避中有自动重试排程，不是失败）
  failed: number // 终态失败计数（需要人看的唯一失败口径；Today 待办卡用）
  tasks_alert: number // 终态失败>0 → 徽章变红（退避中不挂红）
}

export async function fetchMenuStats(): Promise<MenuStats> {
  const [todo, bmP, bmL, jobs, items] = await Promise.all([
    fetchTodoStats(),
    api<{ counts: { todo: number; pending: number } }>('/benchmarks?limit=1&account_type=persona'),
    api<{ counts: { todo: number; pending: number } }>('/benchmarks?limit=1&account_type=leadgen'),
    api<{ counts: { running: number; queued: number; failed: number; summary: { active: number; todo: number; done: number } } }>('/jobs?limit=1'),
    api<{ total: number }>('/articles/content-items?status=pending&limit=1'),
  ])
  return {
    today: todo.targetsPersona,
    p_topics: todo.targetsPersona ?? 0,
    p_content: todo.pendingScriptsPersona,
    p_cand: todo.openCandidatesPersona,
    p_bench: bmP.counts.pending,
    l_scripts: todo.pendingScriptsLeadgen,
    l_cand: todo.openCandidatesLeadgen,
    l_bench: bmL.counts.pending,
    c_items: items.total,
    article: todo.pendingArticles,
    // 三态口径（引擎统一 2026-10-12，后端 counts.summary 已分好）：在途=active（含退避中），
    // 失败=todo（终态）。2026-11-11 用户定版：退避中不挂红——红警只在终态失败时亮
    tasks: jobs.counts.summary.active,
    failed: jobs.counts.summary.todo,
    tasks_alert: jobs.counts.summary.todo > 0 ? 1 : 0,
  }
}

/** 菜单徽章数字：键与三段式菜单一一对应（2026-11-14 定版）——
监控=账号候选+拆解待定夺合计；选题=待备稿靶子+获客选题待审合计；
内容=待定稿脚本两线+待决策稿+待改编源文合计；过程性数字不上菜单。 */
export function menuBadges(s: MenuStats): Partial<Record<string, number>> {
  return {
    today: s.today,
    monitor: s.p_cand + s.l_cand + s.p_bench + s.l_bench,
    topics: s.p_topics,
    content: s.p_content + s.l_scripts + s.article + s.c_items,
    tasks: s.tasks,
    failed: s.failed,
    tasks_alert: s.tasks_alert,
  }
}
