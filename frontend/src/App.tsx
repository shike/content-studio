import { useEffect, useState } from 'react'
import {
  Activity,
  Fingerprint,
  LayoutDashboard,
  Magnet,
  Newspaper,
  Radar,
  Settings,
} from 'lucide-react'
import Today from './pages/Today'
import Tasks from './pages/Tasks'
import SettingsPage from './pages/Settings'
import { PersonaStage, LeadgenStage, ArticleStage, MonitorStage } from './pages/Stages'
import { fetchMenuStats, menuBadges } from './counts'
import Login from './pages/Login'
import ForceChangePassword from './pages/ForceChangePassword'
import Mobile from './Mobile'

/**
 * 侧重版导航（2026-11-14 用户定版：菜单按业务线组织，主线靠前）：
 * 顶层 7 项=今天/人设号（主线）/获客/公众号/监控/任务/设置，目的地由页内 tab 承载
 * （用户批准破「目的地不藏页内 tab」旧规）。
 * 旧产线树 16 叶 + 三段式 3 键 + 更早 4 键经 NAVMAP 全量兼容路由（内嵌页 onNavigate 不断链）。
 */
export type SectionKey = 'today' | 'persona' | 'leadgen' | 'article' | 'monitor' | 'tasks' | 'settings'

interface NavTarget {
  key: SectionKey
  tab?: string
}

/** 旧键 → 新页+页内 tab（全量兼容：产线树 16 叶 + 更早 4 键）。 */
const NAVMAP: Record<string, NavTarget> = {
  p_topics: { key: 'persona', tab: 'topics' },
  l_topics: { key: 'leadgen', tab: 'topics' },
  p_content: { key: 'persona', tab: 'scripts' },
  l_scripts: { key: 'leadgen', tab: 'scripts' },
  p_own: { key: 'persona', tab: 'own' },
  p_bench: { key: 'monitor', tab: 'bench:persona' },
  l_bench: { key: 'monitor', tab: 'bench:leadgen' },
  p_cand: { key: 'monitor', tab: 'cand:persona' },
  l_cand: { key: 'monitor', tab: 'cand:leadgen' },
  p_watch: { key: 'monitor', tab: 'watch:persona' },
  l_watch: { key: 'monitor', tab: 'watch:leadgen' },
  c_items: { key: 'article', tab: 'items' },
  article: { key: 'article', tab: 'workshop' },
  a_style: { key: 'article', tab: 'style' },
  benchmarks: { key: 'monitor', tab: 'bench:persona' },
  scripts: { key: 'persona', tab: 'scripts' },
  articles: { key: 'article', tab: 'workshop' },
  topics: { key: 'persona', tab: 'topics' },
}

const BADGED = new Set<string>(['today', 'persona', 'leadgen', 'article', 'monitor', 'tasks'])

const DEFAULT_TAB: Partial<Record<SectionKey, string>> = {
  persona: 'topics',
  leadgen: 'topics',
  article: 'workshop',
  monitor: 'watch:persona',
}

export default function App() {
  const [active, setActive] = useState<SectionKey>('today')
  const [stageTab, setStageTab] = useState<Partial<Record<SectionKey, string>>>({})
  const [badges, setBadges] = useState<Partial<Record<string, number>>>({})
  const [authed, setAuthed] = useState<boolean | null>(null)  // null=检查中
  const [me, setMe] = useState<MeInfo | null>(null)

  const probe = () => {
    fetch('/api/auth/me').then((r) => {
      if (r.status === 401) { setAuthed(false); setMe(null); return }
      return r.json().then((d) => {
        if (d.user_id) {
          setAuthed(true)
          setMe({
            role: d.role, user_id: d.user_id, display_name: d.display_name,
            must_change_password: d.must_change_password,
          })
        } else setAuthed(false)
      })
    }).catch(() => setAuthed(false))
  }
  useEffect(probe, [])

  useEffect(() => {
    document.title = '跃迁内容工作室'
  }, [])

  // 菜单待办计数：徽章落在阶段页自身（counts.ts 求和），60s 静默刷新
  useEffect(() => {
    let alive = true
    const load = () =>
      fetchMenuStats()
        .then((s) => alive && setBadges(menuBadges(s)))
        .catch(() => {})
    load()
    const timer = setInterval(load, 60_000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  /** 跳转：新键直达，旧键走 NAVMAP（页+页内 tab），未知键落工作台。 */
  const navTo = (k: string) => {
    const t = NAVMAP[k]
    if (t) {
      setActive(t.key)
      if (t.tab) setStageTab((m) => ({ ...m, [t.key]: t.tab! }))
      return
    }
    setActive(['today', 'persona', 'leadgen', 'article', 'monitor', 'tasks', 'settings'].includes(k)
      ? (k as SectionKey) : 'today')
  }

  const badgeOf = (k: string) => (BADGED.has(k) ? (badges[k] ?? 0) : null)

  const renderItem = (s: { key: SectionKey; label: string; icon: typeof Radar }) => {
    const Icon = s.icon
    const isActive = active === s.key
    const raw = badgeOf(s.key)
    const count = raw ?? 0
    return (
      <button
        key={s.key}
        onClick={() => setActive(s.key)}
        className={`group flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
          isActive
            ? 'navitem-gold bg-[rgba(240,233,213,0.08)] font-semibold text-[#f0e9d5]'
            : 'text-[#9dbba9] hover:bg-[rgba(240,233,213,0.06)] hover:text-[#f0e9d5]'
        }`}
      >
        <Icon size={16} className={isActive ? 'text-[#e8c97f]' : 'text-[#7fa695] group-hover:text-[#f0e9d5]'} />
        <span className="flex-1">{s.label}</span>
        {raw !== null && (
          <span
            className={`min-w-5 rounded-full px-1.5 py-px text-center text-[10px] font-semibold tabular-nums ${
              count > 0
                ? s.key === 'tasks' && (badges.tasks_alert ?? 0) > 0
                  ? 'bg-red-500/25 text-red-300'  // 任务徽章红色 = 有待处置失败（带病运行）
                  : 'bg-[rgba(232,201,127,0.16)] text-[#e8c97f]'
                : 'text-[#7fa695]/70'
            }`}
          >
            {count}
          </span>
        )}
      </button>
    )
  }

  // 未登录：只渲染登录页，不显示侧边栏和任何功能
  if (authed === false) {
    return <Login onLogin={() => probe()} />
  }

  // 首登强制改密（R11.1）：一次性密码登录后全屏拦截，改完才放行
  if (authed === true && me?.must_change_password) {
    return <ForceChangePassword username={me.display_name} onDone={() => probe()} />
  }

  // 手机轻量页（/?view=m，2026-11-05 ⑤）：只做拍板动作，不渲染桌面侧栏
  if (new URLSearchParams(window.location.search).get('view') === 'm') {
    return <div className="min-h-full bg-[#f7f8fa]"><Mobile onExit={() => {
      const u = new URL(window.location.href)
      u.searchParams.delete('view')
      window.location.href = u.toString()
    }} /></div>
  }

  return (
    <div className="flex h-full">
      {/* 侧边栏（moxt 墨绿 · 三段流水线） */}
      <aside className="flex w-60 shrink-0 flex-col bg-[#0a2f24]">
        <div className="flex items-center gap-3 px-5 py-6">
          <img src="/icon.svg" alt="" className="size-9 shrink-0 rounded-xl" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight text-[#f0e9d5]">
              跃迁内容工作室
            </div>
            <div className="text-[10px] text-[#9dbba9]/70">人设号 · 获客 · 公众号</div>
          </div>
        </div>

        <nav className="nav-scroll flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {renderItem({ key: 'today', label: '今天', icon: LayoutDashboard })}
          <div className="pt-3" />
          {renderItem({ key: 'persona', label: '人设号', icon: Fingerprint })}
          {renderItem({ key: 'leadgen', label: '获客', icon: Magnet })}
          {renderItem({ key: 'article', label: '公众号', icon: Newspaper })}
          <div className="pt-3" />
          {renderItem({ key: 'monitor', label: '监控', icon: Radar })}
        </nav>

        <div className="border-t border-[rgba(240,233,213,0.1)] px-3 py-3">
          {renderItem({ key: 'tasks', label: '任务', icon: Activity })}
          {renderItem({ key: 'settings', label: '设置', icon: Settings })}
        </div>
      </aside>

      {/* 主区域 */}
      <main className="h-full flex-1 overflow-y-auto">
        <div key={active} className="fade-in mx-auto max-w-7xl px-8 py-8">
          {active === 'today' && <Today onNavigate={navTo} />}
          {active === 'persona' && (
            <PersonaStage tab={stageTab.persona ?? DEFAULT_TAB.persona!}
              onTab={(t) => setStageTab((m) => ({ ...m, persona: t }))} onNavigate={navTo} />
          )}
          {active === 'leadgen' && (
            <LeadgenStage tab={stageTab.leadgen ?? DEFAULT_TAB.leadgen!}
              onTab={(t) => setStageTab((m) => ({ ...m, leadgen: t }))} onNavigate={navTo} />
          )}
          {active === 'article' && (
            <ArticleStage tab={stageTab.article ?? DEFAULT_TAB.article!}
              onTab={(t) => setStageTab((m) => ({ ...m, article: t }))} onNavigate={navTo} />
          )}
          {active === 'monitor' && (
            <MonitorStage tab={stageTab.monitor ?? DEFAULT_TAB.monitor!}
              onTab={(t) => setStageTab((m) => ({ ...m, monitor: t }))} onNavigate={navTo} />
          )}
          {active === 'tasks' && <Tasks onNavigate={navTo} />}
          {active === 'settings' && <SettingsPage displayName={me?.display_name ?? ''} role={me?.role ?? 'member'}
            onLogout={() => { setMe(null); setAuthed(false) }} />}
        </div>
      </main>
    </div>
  )
}


/** 会话口径：/auth/me 返回的用户上下文（单管理员版） */
interface MeInfo {
  role: string
  user_id: number
  display_name: string
  must_change_password?: boolean
}
