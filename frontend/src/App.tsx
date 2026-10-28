import { useEffect, useState } from 'react'
import {
  Activity,
  Fingerprint,
  LayoutDashboard,
  Magnet,
  Radar,
  Send,
  Settings,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import Today from './pages/Today'
import Persona from './pages/Persona'
import Leadgen from './pages/Leadgen'
import Intel from './pages/Intel'
import PublishingHub from './pages/PublishingHub'
import Tasks from './pages/Tasks'
import SettingsPage from './pages/Settings'
import { fetchMenuStats, menuBadges } from './counts'
import Login from './pages/Login'
import ForceChangePassword from './pages/ForceChangePassword'

/**
 * 新页面结构（2026-10-28 用户确认的彻底重构）：
 * 一级入口 6 个——今日工作台 / 人设号产线 / 获客号产线 / 情报中心 / 发布台 / 任务（设置沉底）。
 * 旧页面（选题库/脚本工场/数字人/公众号/同行监测/拆解库/话题雷达/我的风格/海报/工作台）
 * 全部作为容器内容并入：产线页按账号类型过滤，情报中心四流合一，海报并入发布台。
 * 旧 SectionKey 保留在类型里供内嵌页面的 onNavigate 使用，经 NAVMAP 路由到新分区。
 */
export type SectionKey =
  | 'today' | 'persona' | 'leadgen' | 'intel' | 'publishing' | 'tasks' | 'settings'
  | 'dashboard' | 'topics' | 'scripts' | 'watch' | 'style' | 'benchmarks'
  | 'digital' | 'articles' | 'posters' | 'radar'

/** 旧分区键 → 新分区（内嵌页面 onNavigate 的兼容路由）。 */
const NAVMAP: Record<string, SectionKey> = {
  dashboard: 'today', topics: 'today', scripts: 'persona', digital: 'persona',
  articles: 'persona', posters: 'publishing', watch: 'intel', style: 'persona',
  benchmarks: 'intel', radar: 'intel',
}

interface SectionDef {
  key: SectionKey
  label: string
  icon: LucideIcon
}

const TODAY: SectionDef = { key: 'today', label: '今日工作台', icon: LayoutDashboard }
const PRODUC: SectionDef[] = [
  { key: 'persona', label: '人设号', icon: Fingerprint },
  { key: 'leadgen', label: '获客号', icon: Magnet },
]
const AUTO: SectionDef[] = [
  { key: 'intel', label: '情报中心', icon: Radar },
  { key: 'publishing', label: '发布台', icon: Send },
]
const SYSTEM: SectionDef[] = [
  { key: 'tasks', label: '任务', icon: Activity },
  { key: 'settings', label: '设置', icon: Settings },
]

const BADGED_KEYS = new Set<string>(['today', 'tasks'])

export default function App() {
  const [active, setActive] = useState<SectionKey>('today')
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

  // 菜单待办计数：与今日工作台卡片同源（counts.ts），60s 静默刷新
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

  const renderItem = (s: SectionDef) => {
    const Icon = s.icon
    const isActive = active === s.key
    const raw = BADGED_KEYS.has(s.key) ? (badges[s.key] ?? 0) : null
    const count = raw ?? 0
    return (
      <button
        key={s.key}
        onClick={() => setActive(s.key)}
        className={`group flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition ${
          isActive
            ? 'navitem-gold bg-[rgba(240,233,213,0.08)] font-semibold text-[#f0e9d5]'
            : 'text-[#9dbba9] hover:bg-[rgba(240,233,213,0.06)] hover:text-[#f0e9d5]'
        }`}
      >
        <Icon
          size={16}
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

  const renderGroupLabel = (label: string) => (
    <div className="px-3 pt-4 pb-1 text-[10px] font-semibold tracking-widest text-[#7fa695]/80 uppercase">
      {label}
    </div>
  )

  const navTo = (k: string) => setActive(NAVMAP[k] ?? 'today')

  // 未登录：只渲染登录页，不显示侧边栏和任何功能
  if (authed === false) {
    return <Login onLogin={() => probe()} />
  }

  // 首登强制改密（R11.1）：一次性密码登录后全屏拦截，改完才放行
  if (authed === true && me?.must_change_password) {
    return <ForceChangePassword username={me.display_name} onDone={() => probe()} />
  }

  return (
    <div className="flex h-full">
      {/* 侧边栏（moxt 墨绿） */}
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
          {renderGroupLabel('每日动线')}
          {renderItem(TODAY)}
          {renderGroupLabel('内容产线')}
          {PRODUC.map(renderItem)}
          {renderGroupLabel('情报 · 自动')}
          {AUTO.map(renderItem)}
        </nav>

        <div className="border-t border-[rgba(240,233,213,0.1)] px-3 py-3">
          {SYSTEM.map(renderItem)}
          {me && <UserBlock me={me} onLogout={() => { setMe(null); setAuthed(false) }} />}
        </div>
      </aside>

      {/* 主区域 */}
      <main className="h-full flex-1 overflow-y-auto">
        <div key={active} className="fade-in mx-auto max-w-7xl px-8 py-8">
          {active === 'today' && <Today onNavigate={navTo} />}
          {active === 'persona' && <Persona onNavigate={navTo} />}
          {active === 'leadgen' && <Leadgen onNavigate={navTo} />}
          {active === 'intel' && <Intel onNavigate={navTo} />}
          {active === 'publishing' && <PublishingHub />}
          {active === 'tasks' && <Tasks />}
          {active === 'settings' && <SettingsPage role={me?.role ?? 'member'} />}
        </div>
      </main>
    </div>
  )
}

const ROLE_LABEL: Record<string, string> = {
  platform_admin: '管理员',
  member: '成员',
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

/** 侧栏用户块：当前用户 + 行内改密 + 退出登录 */
function UserBlock({ me, onLogout }: {
  me: MeInfo
  onLogout: () => void
}) {
  const [pwdOpen, setPwdOpen] = useState(false)
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const logout = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }) } catch { /* 忽略 */ }
    onLogout()
  }

  const changePwd = async () => {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        throw new Error(d.detail || `HTTP ${r.status}`)
      }
      setMsg('密码已修改'); setOldPwd(''); setNewPwd(''); setPwdOpen(false)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-2 rounded-xl border border-[rgba(232,201,127,0.14)] bg-[rgba(240,233,213,0.06)] p-2.5">
      <div className="flex items-center gap-2">
        <div className="grid size-7 shrink-0 place-items-center rounded-full bg-linear-to-br from-[#e8c97f] to-[#c9a25c] text-xs font-semibold text-[#0d3b2d]">
          {(me.display_name || '?').slice(0, 1)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-medium text-[#f0e9d5]">{me.display_name}</div>
          <div className="text-[10px] text-[#9dbba9]/70">{ROLE_LABEL[me.role] ?? me.role}</div>
        </div>
        <button title="退出登录" onClick={logout}
          className="rounded-md px-1.5 py-1 text-[11px] text-[#9dbba9] transition hover:bg-[rgba(240,233,213,0.08)] hover:text-[#f0e9d5]">
          退出
        </button>
      </div>
      {me.tenant_name && <div className="mt-1 truncate text-[10px] text-[#9dbba9]/60">{me.tenant_name}</div>}
      <button className="mt-1 text-[10px] text-[#9dbba9]/80 hover:text-[#f0e9d5]"
        onClick={() => { setPwdOpen((v) => !v); setMsg(''); setErr('') }}>
        {pwdOpen ? '收起' : '修改密码'}
      </button>
      {pwdOpen && (
        <div className="mt-1.5 space-y-1.5">
          <input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)}
            placeholder="原密码" className="w-full rounded-md border border-[rgba(240,233,213,0.16)] bg-[rgba(10,47,36,0.6)] px-2 py-1 text-xs text-[#f0e9d5] outline-none placeholder:text-[#7fa695]/60 focus:border-[#e8c97f]" />
          <input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)}
            placeholder="新密码（至少 8 位）" className="w-full rounded-md border border-[rgba(240,233,213,0.16)] bg-[rgba(10,47,36,0.6)] px-2 py-1 text-xs text-[#f0e9d5] outline-none placeholder:text-[#7fa695]/60 focus:border-[#e8c97f]" />
          <button disabled={busy || !oldPwd || newPwd.length < 8} onClick={changePwd}
            className="w-full rounded-full bg-linear-to-br from-[#e8c97f] to-[#c9a25c] px-2 py-1 text-xs font-semibold text-[#0d3b2d] transition hover:brightness-105 disabled:opacity-40">
            保存新密码
          </button>
        </div>
      )}
      {msg && <div className="mt-1 text-[10px] text-[#8ff7d2]">{msg}</div>}
      {err && <div className="mt-1 text-[10px] text-[#ff9b8f]">{err}</div>}
    </div>
  )
}
