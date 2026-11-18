import { api } from './api'

/** 工作台（今天页）卡片数据源。菜单徽章功能已下线（2026-11-17 用户裁决：菜单不挂数字），
本文件只服务今天页的待办/候选真实数字。 */
interface TodoStats {
  targetsPersona: number // 今日选题·待备稿（选题工场 R1.4）
  pendingScriptsPersona: number // 待定稿脚本·人设
  pendingScriptsLeadgen: number // 待定稿脚本·获客
  pendingArticles: number // 内容工场待决策稿（排队写+待发）
  openCandidatesPersona: number // 候选账号待定夺·人设线
  openCandidatesLeadgen: number // 候选账号待定夺·获客线
  materialsInbox: number // 素材收件箱待定夺
}

interface CardStats {
  tasks: number // 在途（运行+排队+退避中）
  failed: number // 终态失败计数（今天页红字用）
  p_cand: number // 候选待定夺·人设
  l_cand: number // 候选待定夺·获客
}

export async function fetchCardStats(): Promise<CardStats> {
  const [todo, jobs] = await Promise.all([
    api<TodoStats>('/stats/todos'),
    api<{ counts: { summary: { active: number; todo: number; done: number } } }>('/jobs?limit=1'),
  ])
  return {
    // 三态口径（引擎统一 2026-10-12，后端 counts.summary 已分好）：在途=active（含退避中），
    // 失败=todo（终态）。退避中不挂红——红字只在终态失败时亮
    tasks: jobs.counts.summary.active,
    failed: jobs.counts.summary.todo,
    p_cand: todo.openCandidatesPersona,
    l_cand: todo.openCandidatesLeadgen,
  }
}
