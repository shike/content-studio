import { useEffect, useState } from 'react'
import {
  Activity,
  Clapperboard,
  Crosshair,
  Eye,
  FileText,
  Fingerprint,
  Layers,
  LayoutDashboard,
  Lightbulb,
  ListChecks,
  Magnet,
  Newspaper,
  PenLine,
  Radar,
  Scissors,
  Settings,
  Sparkles,
  TrendingUp,
  UserCheck,
  Users,
  ChevronRight,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import Today from './pages/Today'
import Workshop from './pages/Workshop'
import ContentItems from './pages/ContentItems'
import HotTargets from './pages/HotTargets'
import ArticleStyle from './pages/ArticleStyle'
import Tasks from './pages/Tasks'
import SettingsPage from './pages/Settings'
import Topics from './pages/Topics'
import Scripts from './pages/Scripts'
import TopicInsights from './pages/TopicInsights'
import AccountTrends from './pages/AccountTrends'
import Benchmarks from './pages/Benchmarks'
import DiscoveredAccounts from './pages/DiscoveredAccounts'
import Watch from './pages/Watch'
import { fetchMenuStats, menuBadges } from './counts'
import Login from './pages/Login'
import ForceChangePassword from './pages/ForceChangePassword'
import Mobile from './Mobile'

/**
 * 产线树导航（2026-11-07 用户定版方案三·原型拍板）：每条线的页面直接摆在侧栏，
 * 可展开收起（记住展开态），徽章落在每个页面自身——目的地不再藏在页内 tab 里。
 * 人设号=选题库/内容看板/账号经营（含风格画像，2026-11-07 用户拍板合并）/对标拆解/候选定夺/监控清单；
 * 获客号=获客选题/脚本工场/纯获客候选/获客监控/打法卡库；公众号=报题与长文/风格画像。
 * 旧 SectionKey 经 NAVMAP 兼容路由（内嵌页 onNavigate 不断链）。
 */
export type SectionKey =
  | 'today' | 'tasks' | 'settings'
  | 'p_topics' | 'p_content' | 'p_own' | 'p_bench' | 'p_cand' | 'p_watch'
  | 'l_topics' | 'l_scripts' | 'l_cand' | 'l_watch' | 'l_bench'
  | 'c_items' | 'article' | 'a_style'

/** 旧分区键 → 新叶子（内嵌页 onNavigate 的兼容路由）。
2026-11-10 清理：11 个全仓零调用的旧键已删，只保留 Tasks 联动入口仍在产出的 4 键。 */
const NAVMAP: Record<string, SectionKey> = {
  benchmarks: 'p_bench',
  scripts: 'p_content',
  articles: 'article',
  topics: 'p_topics',
}

interface LeafDef {
  key: SectionKey
  label: string
  icon: LucideIcon
}

interface TreeGroup {
  key: string
  label: string
  icon: LucideIcon
  children: LeafDef[]
}

/** 产线树：三组可展开 + 三个独立项。 */
const TREE: TreeGroup[] = [
  {
    key: 'persona', label: '人设号', icon: Fingerprint,
    children: [
      { key: 'p_topics', label: '今日靶子', icon: Crosshair },
      { key: 'p_content', label: '内容看板', icon: Clapperboard },
      { key: 'p_own', label: '账号经营', icon: TrendingUp },
      { key: 'p_bench', label: '对标拆解', icon: Scissors },
      { key: 'p_cand', label: '候选定夺', icon: UserCheck },
      { key: 'p_watch', label: '监控清单', icon: Radar },
    ],
  },
  {
    key: 'leadgen', label: '获客号', icon: Magnet,
    children: [
      { key: 'l_topics', label: '获客选题', icon: Lightbulb },
      { key: 'l_scripts', label: '脚本工场', icon: FileText },
      { key: 'l_cand', label: '纯获客候选', icon: Users },
      { key: 'l_watch', label: '获客监控', icon: Eye },
      { key: 'l_bench', label: '打法卡库', icon: Layers },
    ],
  },
  {
    key: 'article', label: '公众号', icon: Newspaper,
    children: [
      { key: 'c_items', label: '内容清单', icon: ListChecks },
      { key: 'article', label: '内容工场', icon: PenLine },
      { key: 'a_style', label: '风格画像', icon: Sparkles },
    ],
  },
]

const BADGED_LEAVES = new Set<string>([
  'today', 'p_topics', 'p_content', 'p_bench', 'p_cand',
  'l_topics', 'l_scripts', 'l_cand', 'l_bench',
  'c_items', 'article', 'tasks',
])

const TREE_KEY = 'mx-tree-open'

export default function App() {
  const [active, setActive] = useState<SectionKey>('today')
  const [badges, setBadges] = useState<Partial<Record<string, number>>>({})
  const [authed, setAuthed] = useState<boolean | null>(null)  // null=检查中
  const [me, setMe] = useState<MeInfo | null>(null)
  // 树展开态：默认全展开，localStorage 记住用户收起过的组
  const [open, setOpen] = useState<Record<string, boolean>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(TREE_KEY) || '{}')
      if (saved && typeof saved === 'object') return { persona: true, leadgen: true, article: true, ...saved }
    } catch { /* 损坏则回默认 */ }
    return { persona: true, leadgen: true, article: true }
  })

  const probe = () => {
    fetch('/api/auth/me').then((r) => {
      if (r.status === 401) { setAuthed(false); setMe(null); return }
      return r.json().then((d) => {
        if (d.user_id) {
          setAuthed(true)
          setMe({
            role: d.role, user_id: d.user_id, display_name: d.display_name,
            must_change_password: d.must_change_password,
            tenant_id: d.tenant_id, tenant_name: d.tenant_name,
          })
        } else setAuthed(false)
      })
    }).catch(() => setAuthed(false))
  }
  useEffect(probe, [])

  useEffect(() => {
    document.title = '跃迁内容工作室'
  }, [])

  // 菜单待办计数：徽章落在每个页面自身（counts.ts），60s 静默刷新
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

  const toggleGroup = (g: TreeGroup) => {
    setOpen((m) => {
      const next = { ...m, [g.key]: !m[g.key] }
      try { localStorage.setItem(TREE_KEY, JSON.stringify(next)) } catch { /* 私浏览忽略 */ }
      return next
    })
  }

  const badgeOf = (k: string) => (BADGED_LEAVES.has(k) ? (badges[k] ?? 0) : null)

  const renderItem = (s: LeafDef, opts?: { sub?: boolean; groupOpen?: string }) => {
    const Icon = s.icon
    const isActive = active === s.key
    const raw = badgeOf(s.key)
    const count = raw ?? 0
    return (
      <button
        key={s.key}
        onClick={() => {
          setActive(s.key as SectionKey)
          if (opts?.groupOpen) setOpen((m) => ({ ...m, [opts.groupOpen!]: true }))
        }}
        className={`group flex w-full items-center gap-2 rounded-lg text-left text-sm transition ${
          opts?.sub ? 'py-1.5 pl-5 pr-2.5 text-[13px]' : 'px-3 py-2'
        } ${
          isActive
            ? 'navitem-gold bg-[rgba(240,233,213,0.08)] font-semibold text-[#f0e9d5]'
            : 'text-[#9dbba9] hover:bg-[rgba(240,233,213,0.06)] hover:text-[#f0e9d5]'
        }`}
      >
        <Icon
          size={opts?.sub ? 14 : 16}
          className={isActive ? 'text-[#e8c97f]' : 'text-[#7fa695] group-hover:text-[#f0e9d5]'}
        />
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

  const renderToday = () => renderItem({ key: 'today', label: '今天', icon: LayoutDashboard })

  /** 产线组：组行（聚合徽章）+ 子页列表（可展开收起，localStorage 记住）。 */
  const renderGroup = (g: TreeGroup) => {
    const isOpen = open[g.key] !== false
    const groupBadge = g.children.reduce((n, c) => n + (badgeOf(c.key) ?? 0), 0)
    return (
      <div key={g.key}>
        <button
          onClick={() => toggleGroup(g)}
          aria-expanded={isOpen}
          className={`group flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm transition ${
            isOpen && g.children.some((c) => c.key === active)
              ? 'font-semibold text-[#f0e9d5]'
              : 'text-[#9dbba9] hover:bg-[rgba(240,233,213,0.06)] hover:text-[#f0e9d5]'
          }`}
        >
          <ChevronRight
            size={13}
            className={`shrink-0 text-[#7fa695] transition-transform ${isOpen ? 'rotate-90' : ''}`}
          />
          <g.icon size={15} className="shrink-0 text-[#7fa695] group-hover:text-[#f0e9d5]" />
          <span className="flex-1">{g.label}</span>
          {groupBadge > 0 && (
            <span className="min-w-5 rounded-full bg-[rgba(232,201,127,0.16)] px-1.5 py-px text-center text-[10px] font-semibold tabular-nums text-[#e8c97f]">
              {groupBadge}
            </span>
          )}
        </button>
        {isOpen && (
          <div className="mb-1">
            {g.children.map((c) => renderItem(c, { sub: true, groupOpen: g.key }))}
          </div>
        )}
      </div>
    )
  }

  const renderSystemItem = (s: LeafDef) => renderItem(s)

  /** 跳转：叶子键直达，旧键走 NAVMAP 兼容，未知键落工作台。 */
  const LEAF_KEYS = new Set<string>(['today', 'tasks', 'settings', ...TREE.flatMap((g) => g.children.map((c) => c.key))])
  const navTo = (k: string) =>
    setActive((LEAF_KEYS.has(k) ? (k as SectionKey) : NAVMAP[k] ?? 'today'))

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
      {/* 侧边栏（moxt 墨绿 · 产线树） */}
      <aside className="flex w-60 shrink-0 flex-col bg-[#0a2f24]">
        <div className="flex items-center gap-3 px-5 py-6">
          <img src="/icon.svg" alt="" className="size-9 shrink-0 rounded-xl" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight text-[#f0e9d5]">
              跃迁内容工作室
            </div>
            <div className="text-[10px] text-[#9dbba9]/70">选题 · 内容 · 发布</div>
          </div>
        </div>

        <nav className="nav-scroll flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {renderToday()}
          <div className="pt-3" />
          {TREE.map(renderGroup)}
        </nav>

        <div className="border-t border-[rgba(240,233,213,0.1)] px-3 py-3">
          {renderSystemItem({ key: 'tasks', label: '任务', icon: Activity })}
          {renderSystemItem({ key: 'settings', label: '设置', icon: Settings })}
        </div>
      </aside>

      {/* 主区域 */}
      <main className="h-full flex-1 overflow-y-auto">
        <div key={active} className="fade-in mx-auto max-w-7xl px-8 py-8">
          {active === 'today' && <Today onNavigate={navTo} />}
          {active === 'p_topics' && <div><HotTargets onNavigate={navTo} /><div className="mt-6"><TopicInsights /></div></div>}
          {active === 'l_topics' && <Topics accountType="leadgen" />}
          {active === 'p_content' && <Scripts onNavigate={navTo} accountType="persona" />}
          {active === 'l_scripts' && <Scripts onNavigate={navTo} accountType="leadgen" />}
          {active === 'p_own' && <AccountTrends onNavigate={navTo} />}
          {active === 'p_bench' && <Benchmarks onNavigate={navTo} accountType="persona" />}
          {active === 'l_bench' && <Benchmarks onNavigate={navTo} accountType="leadgen" />}
          {active === 'p_cand' && <DiscoveredAccounts accountType="persona" />}
          {active === 'l_cand' && <DiscoveredAccounts accountType="leadgen" />}
          {active === 'p_watch' && <Watch onNavigate={navTo} accountType="persona" />}
          {active === 'l_watch' && <Watch onNavigate={navTo} accountType="leadgen" />}
          {active === 'c_items' && <ContentItems onNavigate={navTo} />}
          {active === 'article' && <Workshop />}
          {active === 'a_style' && <ArticleStyle />}
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
  tenant_id: number
  tenant_name: string
}
