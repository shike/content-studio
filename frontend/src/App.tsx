import { useEffect, useState } from 'react'
import {
  Activity,
  FileText, Flame,
  Fingerprint,
  LayoutDashboard,
  Lightbulb,
  PenLine,
  Radar,
  Scissors,
  Send,
  Settings,
  ShieldCheck,
  UserRound,
  Wand2,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import Articles from './pages/Articles'
import Benchmarks from './pages/Benchmarks'
import RadarPage from './pages/Radar'
import Dashboard from './pages/Dashboard'
import Publishing from './pages/Publishing'
import PosterPrompts from './pages/PosterPrompts'
import Scripts from './pages/Scripts'
import SettingsPage from './pages/Settings'
import Topics from './pages/Topics'
import Watch from './pages/Watch'
import Style from './pages/Style'
import Digital from './pages/Digital'
import Tasks from './pages/Tasks'
import Admin from './pages/Admin'
import { fetchMenuStats, menuBadges } from './counts'
import Login from './pages/Login'
import ForceChangePassword from './pages/ForceChangePassword'

export type SectionKey =
  | 'dashboard' | 'topics' | 'scripts' | 'watch' | 'style' | 'benchmarks'
  | 'digital' | 'articles' | 'publishing' | 'posters' | 'tasks' | 'settings'
  | 'radar' | 'admin'

interface SectionDef {
  key: SectionKey
  label: string
  icon: LucideIcon
}

/** 菜单三组两段：总览 → 流水线（生产顺序）→ 情报（喂选题的输入端）；设置独立沉底。 */
const OVERVIEW: SectionDef = { key: 'dashboard', label: '工作台', icon: LayoutDashboard }
const PIPELINE: SectionDef[] = [
  { key: 'topics', label: '选题库', icon: Lightbulb },
  { key: 'scripts', label: '脚本工场', icon: PenLine },
  { key: 'digital', label: '口播数字人', icon: UserRound },
  { key: 'articles', label: '公众号', icon: FileText },
  { key: 'publishing', label: '发布台', icon: Send },
  { key: 'posters', label: '海报提示词', icon: Wand2 },
]
const INTEL: SectionDef[] = [
  { key: 'watch', label: '同行监测', icon: Radar },
  { key: 'style', label: '我的风格', icon: Fingerprint },
  { key: 'benchmarks', label: '拆解库', icon: Scissors },
  { key: 'radar', label: '话题雷达', icon: Flame },
]
const SYSTEM: SectionDef[] = [
  { key: 'tasks', label: '任务', icon: Activity },
  { key: 'settings', label: '设置', icon: Settings },
]

/** 挂数字徽章的页面：流水线 + 情报 + 任务；0 值弱显。工作台/设置除外。 */
const BADGED_KEYS = new Set<string>(
  [...PIPELINE, ...INTEL, { key: 'tasks' }].map((s) => s.key))

export default function App() {
  const [active, setActive] = useState<SectionKey>('dashboard')
  const [badges, setBadges] = useState<Partial<Record<string, number>>>({})
  const [authed, setAuthed] = useState<boolean | null>(null)  // null=检查中
  const [me, setMe] = useState<{ role: string; user_id: number; display_name: string; must_change_password?: boolean } | null>(null)

  const probe = () => {
    // 启动/登录后探测：未登录则显示登录页；登录态下取角色决定管理入口
    fetch('/api/auth/me').then((r) => {
      if (r.status === 401) { setAuthed(false); setMe(null); return }
      return r.json().then((d) => {
        if (d.user_id) {
          setAuthed(true)
          setMe({ role: d.role, user_id: d.user_id, display_name: d.display_name, must_change_password: d.must_change_password })
        } else setAuthed(false)
      })
    }).catch(() => setAuthed(false))
  }
  useEffect(probe, [])

  useEffect(() => {
    document.title = '跃迁内容工作室'
  }, [])

  // 菜单待办计数：与工作台卡片同源（counts.ts），60s 静默刷新
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
              count > 0 ? 'bg-[rgba(232,201,127,0.16)] text-[#e8c97f]' : 'text-[#7fa695]/70'
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

  // 未登录：只渲染登录页，不显示侧边栏和任何功能
  if (authed === false) {
    return <Login onLogin={() => probe()} />
  }

  // 首登强制改密（R11.2）：一次性密码登录后全屏拦截，改完才放行
  if (authed === true && me?.must_change_password) {
    return <ForceChangePassword username={me.display_name} onDone={() => probe()} />
  }

  const isAdmin = !!me && (me.role === 'platform_admin' || me.role === 'tenant_admin')
  const ADMIN_ITEM: SectionDef = { key: 'admin', label: '管理后台', icon: ShieldCheck }

  return (
    <div className="flex h-full">
      {/* 侧边栏（D2 墨绿） */}
      <aside className="flex w-60 shrink-0 flex-col bg-[#0a2f24]">
        <div className="flex items-center gap-3 px-5 py-6">
          <img src="/icon.svg" alt="" className="size-9 shrink-0 rounded-xl" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight text-[#f0e9d5]">
              跃迁内容工作室
            </div>
            <div className="text-[10px] text-[#9dbba9]/70">内容生产流水线</div>
          </div>
        </div>

        <nav className="nav-scroll flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {renderItem(OVERVIEW)}
          {renderGroupLabel('流水线')}
          {PIPELINE.map(renderItem)}
          {renderGroupLabel('情报')}
          {INTEL.map(renderItem)}
        </nav>

        <div className="border-t border-[rgba(240,233,213,0.1)] px-3 py-3">
          {isAdmin && renderItem(ADMIN_ITEM)}
          {me?.role === 'platform_admin' && renderItem(SYSTEM[1])}
          {SYSTEM.slice(0, 1).map(renderItem)}
          {me && <UserBlock me={me} onLogout={() => { setMe(null); setAuthed(false) }} />}
        </div>
      </aside>

      {/* 主区域 */}
      <main className="h-full flex-1 overflow-y-auto">
        <div key={active} className="fade-in mx-auto max-w-7xl px-8 py-8">
          {active === 'dashboard' && <Dashboard onNavigate={setActive} />}
          {active === 'topics' && <Topics />}
          {active === 'scripts' && <Scripts onNavigate={setActive} />}
          {active === 'watch' && <Watch onNavigate={setActive} />}
          {active === 'style' && <Style onNavigate={setActive} />}
          {active === 'articles' && <Articles />}
          {active === 'benchmarks' && <Benchmarks onNavigate={setActive} />}
          {active === 'radar' && <RadarPage />}
          {active === 'digital' && <Digital role={me?.role ?? 'member'} />}
          {active === 'publishing' && <Publishing />}
          {active === 'posters' && <PosterPrompts />}
          {active === 'tasks' && <Tasks />}
          {active === 'settings' && <SettingsPage role={me?.role ?? 'member'} />}
          {active === 'admin' && isAdmin && (
            <Admin key={me?.user_id ?? 'admin'} role={me!.role} selfId={me!.user_id} onNavigate={setActive} />
          )}
        </div>
      </main>
    </div>
  )
}

const ROLE_LABEL: Record<string, string> = {
  platform_admin: '平台管理员',
  tenant_admin: '租户管理员',
  member: '成员',
}

/** 侧栏用户块：当前用户 + 角色徽章 + 行内改密 + 退出登录（全角色可见） */
function UserBlock({ me, onLogout }: {
  me: { role: string; user_id: number; display_name: string }
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
