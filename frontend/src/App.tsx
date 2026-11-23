import { useEffect, useState } from 'react'
import {
  Activity,
  BarChart3,
  Feather,
  Fingerprint,
  LayoutDashboard,
  Magnet,
  Newspaper,
  Package,
  Settings,
} from 'lucide-react'
import Today from './pages/Today'
import Xhs from './pages/Xhs'
import Analytics from './pages/Analytics'
import Tasks from './pages/Tasks'
import SettingsPage from './pages/Settings'
import { PersonaStage, LeadgenStage, ArticleStage, MaterialsStage } from './pages/Stages'
import Login from './pages/Login'
import Mobile from './Mobile'

/**
 * 侧重版导航（2026-11-14 用户定版：菜单按业务线组织，主线靠前）：
 * 顶层 9 项=今天/人设号（主线）/获客/公众号/小红书/数据/素材库/任务/设置，目的地由页内 tab 承载
 * （用户批准破「目的地不藏页内 tab」旧规）。2026-11-18 用户定版：今天=今日/热门评论/知识库三平级 tab；
 * 素材库=监控清单/素材池/知识库（源·池·库）。
 * 旧产线树 16 叶 + 三段式 3 键 + 更早 4 键经 NAVMAP 全量兼容路由（内嵌页 onNavigate 不断链）。
 */
type SectionKey = 'today' | 'persona' | 'leadgen' | 'article' | 'xhs' | 'analytics' | 'materials' | 'tasks' | 'settings'

interface NavTarget {
  key: SectionKey
  tab?: string
}

/** 旧键 → 新页+页内 tab（全量兼容：产线树 16 叶 + 更早 4 键）。 */
const NAVMAP: Record<string, NavTarget> = {
  p_topics: { key: 'persona', tab: 'topics' },
  p_content: { key: 'persona', tab: 'scripts' },
  l_scripts: { key: 'leadgen', tab: 'scripts' },
  // 旧「拆解库/素材」三键 → 素材库·素材池（2026-11-18：素材列表回素材库独立 tab）
  p_bench: { key: 'materials', tab: 'pool' },
  l_bench: { key: 'materials', tab: 'pool' },
  benchmarks: { key: 'materials', tab: 'pool' },
  // 旧「监控/候选」键 → 素材库·监控清单（源维护归素材库）
  l_cand: { key: 'materials', tab: 'watch' },
  p_watch: { key: 'materials', tab: 'watch' },
  watch: { key: 'materials', tab: 'watch' },
  cand: { key: 'materials', tab: 'watch' },
  radar: { key: 'materials', tab: 'watch' },
  article: { key: 'article', tab: 'workshop' },
  scripts: { key: 'persona', tab: 'scripts' },
  articles: { key: 'article', tab: 'workshop' },
}

const DEFAULT_TAB: Partial<Record<SectionKey, string>> = {
  today: 'cockpit',
  persona: 'topics',
  leadgen: 'topics',
  article: 'workshop',
  materials: 'watch',
}

export default function App() {
  const [active, setActive] = useState<SectionKey>('today')
  const [stageTab, setStageTab] = useState<Partial<Record<SectionKey, string>>>({})
  const [authed, setAuthed] = useState<boolean | null>(null)  // null=检查中
  const [me, setMe] = useState<MeInfo | null>(null)

  const probe = () => {
    fetch('/api/auth/me').then((r) => {
      if (r.status === 401) { setAuthed(false); setMe(null); return }
      return r.json().then((d) => {
        if (d.user_id) {
          setAuthed(true)
          setMe({
            role: d.role, display_name: d.display_name,
          })
        } else setAuthed(false)
      })
    }).catch(() => setAuthed(false))
  }
  useEffect(probe, [])

  useEffect(() => {
    document.title = '跃迁内容工作室'
  }, [])

  /** 跳转：新键直达，旧键走 NAVMAP（页+页内 tab），未知键落工作台。 */
  const navTo = (k: string) => {
    const t = NAVMAP[k]
    if (t) {
      setActive(t.key)
      if (t.tab) setStageTab((m) => ({ ...m, [t.key]: t.tab! }))
      return
    }
    setActive(['today', 'persona', 'leadgen', 'article', 'xhs', 'analytics', 'materials', 'tasks', 'settings'].includes(k)
      ? (k as SectionKey) : 'today')
  }

  const renderItem = (s: { key: SectionKey; label: string; icon: typeof Package }) => {
    const Icon = s.icon
    const isActive = active === s.key
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
      </button>
    )
  }

  // 未登录：只渲染登录页，不显示侧边栏和任何功能
  if (authed === false) {
    return <Login onLogin={() => probe()} />
  }

  // 首登强制改密（R11.1）：一次性密码登录后全屏拦截，改完才放行

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
          </div>
        </div>

        <nav className="nav-scroll flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {renderItem({ key: 'today', label: '今天', icon: LayoutDashboard })}
          <div className="pt-3" />
          {renderItem({ key: 'persona', label: '人设号', icon: Fingerprint })}
          {renderItem({ key: 'leadgen', label: '获客号', icon: Magnet })}
          {renderItem({ key: 'article', label: '公众号', icon: Newspaper })}
          {renderItem({ key: 'xhs', label: '小红书', icon: Feather })}
          {renderItem({ key: 'analytics', label: '数据', icon: BarChart3 })}
          <div className="pt-3" />
          {renderItem({ key: 'materials', label: '素材库', icon: Package })}
        </nav>

        <div className="border-t border-[rgba(240,233,213,0.1)] px-3 py-3">
          {renderItem({ key: 'tasks', label: '任务', icon: Activity })}
          {renderItem({ key: 'settings', label: '设置', icon: Settings })}
        </div>
      </aside>

      {/* 主区域 */}
      <main className="h-full flex-1 overflow-y-auto">
        <div key={active} className="fade-in mx-auto max-w-7xl px-8 py-8">
          {active === 'today' && (
            <Today onNavigate={navTo} tab={stageTab.today ?? DEFAULT_TAB.today!}
              onTab={(t) => setStageTab((m) => ({ ...m, today: t }))} />
          )}
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
          {active === 'materials' && (
            <MaterialsStage tab={stageTab.materials ?? DEFAULT_TAB.materials!}
              onTab={(t) => setStageTab((m) => ({ ...m, materials: t }))} onNavigate={navTo} />
          )}
          {active === 'xhs' && <Xhs />}
          {active === 'analytics' && <Analytics onNavigate={navTo} />}
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
  display_name: string
}
