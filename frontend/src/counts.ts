import { api } from './api'

/** 流水线待办统计（工作台卡片数据源）。服务端真值口径 /api/stats/todos。
2026-11-16 P2 素材域：候选/收件箱口径改读 sources+materials。 */
interface TodoStats {
  targetsPersona: number // 今日靶子·待备稿（选题工场 R1.4）
  pendingScriptsPersona: number // 待定稿脚本·人设
  pendingScriptsLeadgen: number // 待定稿脚本·获客
  pendingArticles: number // 内容工场待决策稿（排队写+待发）
  openCandidatesPersona: number // 候选账号待定夺·人设线
  openCandidatesLeadgen: number // 候选账号待定夺·获客线
  materialsInbox: number // 素材收件箱待定夺（P2 素材域）
}

async function fetchTodoStats(): Promise<TodoStats> {
  return api<TodoStats>('/stats/todos')
}

/** 菜单徽章统计：徽章落在每页自身；过程性数字（生成中/排队/待拆）不上菜单。 */
interface MenuStats {
  today: number // 今天=待审选题合计
  p_topics: number // 今日靶子=待备稿靶子
  p_content: number // 内容看板=待定稿脚本·人设
  p_bench: number // 素材收件箱待定夺（P2：原拆解待定夺口径并入素材库）
  p_cand: number // 候选定夺=候选待定夺·人设
  l_scripts: number // 脚本工场=待定稿脚本·获客
  l_cand: number // 纯获客候选=候选待定夺·获客
  l_bench: number // 0（获客拆解待定夺已并入素材收件箱——占位键保留防旧引用）
  c_items: number // 内容清单=待改编源文数
  article: number // 内容工场=待拍板稿
  tasks: number // 在途（运行+排队+退避中）
  failed: number // 终态失败计数（Today 待办卡用）
  tasks_alert: number // 终态失败>0 → 徽章变红
  oreLeadgen: number // 获客矿素材数（获客选题行数，分型路由）
}

export async function fetchMenuStats(): Promise<MenuStats> {
  const [todo, mats, jobs, items, oreL] = await Promise.all([
    fetchTodoStats(),
    api<{ counts: { inbox: number; ore_leadgen: number } }>('/materials?limit=1&status=all'),
    api<{ counts: { running: number; queued: number; failed: number; summary: { active: number; todo: number; done: number } } }>('/jobs?limit=1'),
    api<{ total: number }>('/articles/content-items?status=pending&limit=1'),
    api<{ counts: { all: number } }>('/materials?ore=leadgen&status=all&limit=1'),
  ])
  return {
    today: todo.targetsPersona,
    p_topics: todo.targetsPersona ?? 0,
    p_content: todo.pendingScriptsPersona,
    p_cand: todo.openCandidatesPersona,
    p_bench: mats.counts?.inbox ?? 0,
    l_scripts: todo.pendingScriptsLeadgen,
    l_cand: todo.openCandidatesLeadgen,
    l_bench: 0,
    c_items: items.total,
    article: todo.pendingArticles,
    // 三态口径（引擎统一 2026-10-12，后端 counts.summary 已分好）：在途=active（含退避中），
    // 失败=todo（终态）。退避中不挂红——红警只在终态失败时亮
    tasks: jobs.counts.summary.active,
    failed: jobs.counts.summary.todo,
    tasks_alert: jobs.counts.summary.todo > 0 ? 1 : 0,
    oreLeadgen: oreL.counts?.all ?? 0,
  }
}

/** 菜单徽章数字：素材库=收件箱待定夺+候选待定夺合计；其余同侧重版口径。 */
export function menuBadges(s: MenuStats): Partial<Record<string, number>> {
  return {
    today: s.today,
    persona: s.p_topics + s.p_content,
    leadgen: s.oreLeadgen + s.l_scripts,
    article: s.article + s.c_items,
    materials: s.p_bench + s.p_cand + s.l_cand,
    tasks: s.tasks,
    tasks_alert: s.tasks_alert,
  }
}
