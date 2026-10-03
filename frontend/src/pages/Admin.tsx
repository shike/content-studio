import { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft, BrainCircuit, Building2, Database, KeyRound, Plus, Search, ShieldCheck, Terminal, Users,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { SectionKey } from '../App'
import { api } from '../api'
import { BrandForm, type BrandShape } from '../brandForm'
import { Busy, EmptyState, ErrorLine, PageHeader, relTime } from '../components'

interface TenantRow {
  id: number
  name: string
  credits: number
  status: string
  member_count: number
  cost30: number
  active_members30: number
  created_at: string | null
  brand?: { label_line1?: string; label_line2?: string; signature?: string; persona?: string;
            accent?: string; primary?: string; asr_vocab?: string;
            cover_slogan?: string; audience_note?: string } | null
}
interface Txn {
  id: number
  delta: number
  balance_after: number
  reason: string
  kind: string
  created_at: string | null
}
interface UserRow {
  id: number
  tenant_id: number
  tenant_name: string
  username: string
  role: string
  display_name: string
  status: string
  must_change_password: boolean
  memberships: { tenant_id: number; tenant_name: string; role: string }[]
  created_at: string | null
}
interface TenantUsage {
  days: number
  llm: {
    calls: number
    tokens_in: number
    tokens_out: number
    cost_est: number
    by_purpose: { purpose: string; calls: number; tokens_in: number; tokens_out: number; cost_est: number }[]
  }
  jobs: {
    total: number
    by_status: Record<string, number>
    success_rate: number | null
    by_type: [string, number][]
    by_day: { day: string; count: number; failed: number; cost: number }[]
  }
  beans: { beans: number; seconds: number }
  credits: { topup: number; consume: number }
  balance: number
  members: {
    user_id: number
    username: string
    display_name: string
    role: string
    jobs: number
    points: number
    last_active_at: string | null
  }[]
}
interface UserUsage {
  username: string
  tenant_name: string
  days: number
  jobs_total: number
  by_type: Record<string, number>
  points: number
  last_active_at: string | null
  recent: { id: number; type: string; status: string; created_at: string | null }[]
}
interface Overview {
  tenants: { total: number; active: number }
  members: { total: number; active30: number }
  llm: { today: number; d30: number; calls_today: number }
  jobs: { today: number; success_rate: number | null }
  credits_total: number
  trend: { day: string; cost: number; jobs: number; failed: number }[]
  top_tenants: { id: number; name: string; cost: number }[]
  feed: { job_id: number; type: string; status: string; user: string; tenant: string; created_at: string | null }[]
}
interface JobItem {
  id: number
  type: string
  status: string
  progress: number
  message: string
  error: string | null
  created_at: string | null
}

const ROLE_LABEL: Record<string, string> = {
  platform_admin: '平台管理员',
  tenant_admin: '租户管理员',
  member: '成员',
}

const JOB_LABEL: Record<string, string> = {
  idea_research: '选题深研',
  benchmark_analyze: '同行拆解',
  script_generate: '脚本生成',
  script_polish: '脚本打磨',
  script_finalize: '脚本定稿',
  article_generate: '长文生成',
  avatar_video: '数字人视频',
  media_align: '媒体对齐',
  draft_generate: '剪映草稿',
  watch_scan: '同行扫描',
  watch_discover: '账号发现',
  watch_resolve: '链接识别',
  self_scan: '风格扫描',
  self_analyze: '风格拆解',
  self_profile_update: '画像更新',
  radar_extract: '话题提取',
  radar_mine: '话题挖掘',
  topic_radar: '雷达巡检',
}
const jobLabel = (t: string) => JOB_LABEL[t] ?? t

const JOB_STATUS: Record<string, { label: string; cls: string }> = {
  running: { label: '运行中', cls: 'bg-sky-50 text-sky-700' },
  queued: { label: '排队', cls: 'bg-slate-100 text-slate-500' },
  parked: { label: '挂起', cls: 'bg-amber-50 text-amber-700' },
  succeeded: { label: '完成', cls: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '失败', cls: 'bg-red-50 text-red-600' },
  superseded: { label: '承接', cls: 'bg-slate-100 text-slate-400' },
}

const fmtYuan = (n: number) => `¥${(n ?? 0).toFixed(2)}`

/** 回显带：错误 / 成功消息 / 一次性密码（各视图共用同一位置） */
function EchoBand({ err, msg, oneTime }: {
  err: string
  msg: string
  oneTime: { username: string; password: string } | null
}) {
  return (
    <>
      {err && <div className="mb-4"><ErrorLine text={err} /></div>}
      {msg && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
          {msg}
        </div>
      )}
      {oneTime && (
        <div className="mb-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
            <KeyRound size={14} /> 账号 {oneTime.username} 的初始密码（仅显示这一次，请立即复制交付）：
          </div>
          <code className="mt-1.5 inline-block rounded bg-white px-2 py-1 text-base font-bold tracking-wider text-zinc-900">
            {oneTime.password}
          </code>
          <span className="ml-2 text-xs text-amber-700">首次登录将强制修改密码</span>
        </div>
      )}
    </>
  )
}

/** 轻量柱状趋势（SVG 免依赖）：data 逐日 [{day, v}]，failed 用于红色高亮可选 */
function MiniBars({ data, color, height = 64, failedKey }: {
  data: { day: string; v: number; failed?: number }[]
  color: string
  height?: number
  failedKey?: boolean
}) {
  const max = Math.max(1e-9, ...data.map((d) => d.v))
  return (
    <div>
      <div className="flex items-end gap-1" style={{ height }}>
        {data.map((d) => (
          <div key={d.day} className="group relative flex-1"
            title={`${d.day}：${d.v >= 1000 ? d.v.toLocaleString() : d.v}${failedKey && d.failed ? `（失败 ${d.failed}）` : ''}`}>
            <div className="w-full rounded-t transition-all group-hover:opacity-100"
              style={{
                height: `${Math.max(3, (d.v / max) * (height - 6))}px`,
                background: failedKey && d.failed ? '#f87171' : color,
                opacity: d.v > 0 ? 0.85 : 0.2,
              }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-400">
        <span>{data[0]?.day}</span><span>{data[data.length - 1]?.day}</span>
      </div>
    </div>
  )
}

/** 管理后台：总览（平台仪表盘）+ 租户（列表/详情页）+ 成员，六段式骨架。
 *  一次性密码只显示一次（明文不落库），需当场复制交付。 */
export default function Admin({ role, selfId, onNavigate }: {
  role: string
  selfId: number
  onNavigate?: (k: SectionKey) => void
}) {
  const isPlatform = role === 'platform_admin'
  const [tab, setTab] = useState<'overview' | 'tenants' | 'members'>(isPlatform ? 'overview' : 'members')
  const [tenants, setTenants] = useState<TenantRow[]>([])
  const [users, setUsers] = useState<UserRow[]>([])
  const [manageableTenants, setManageableTenants] = useState<{ id: number; name: string }[]>([])
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [oneTime, setOneTime] = useState<{ username: string; password: string } | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  const [expandUser, setExpandUser] = useState<number | null>(null)
  const [showNewTenant, setShowNewTenant] = useState(false)
  const [showNewUser, setShowNewUser] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    setErr('')
    try {
      if (isPlatform) {
        const d = await api<{ tenants: TenantRow[] }>('/admin/tenants')
        setTenants(d.tenants)
      }
      const u = await api<{ users: UserRow[]; manageable_tenants?: { id: number; name: string }[] }>('/admin/users')
      setUsers(u.users)
      setManageableTenants(u.manageable_tenants ?? [])
    } catch (e) {
      setErr(String(e))
    } finally {
      setLoading(false)
    }
  }, [isPlatform])

  useEffect(() => { reload() }, [reload])

  const act = async (fn: () => Promise<void>, okMsg: string) => {
    setErr('')
    setMsg('')
    try {
      await fn()
      setMsg(okMsg)
      await reload()
    } catch (e) {
      setErr(String(e))
    }
  }

  const detailTenant = detailId !== null ? tenants.find((t) => t.id === detailId) : undefined

  // 租户详情（整页视图，点返回回列表）
  if (isPlatform && detailTenant) {
    return (
      <TenantDetailPage
        tenant={detailTenant}
        onBack={() => { setDetailId(null); setMsg(''); setErr('') }}
        onNavigate={onNavigate}
        act={act}
        setErr={setErr}
        setMsg={setMsg}
        err={err}
        msg={msg}
        reload={reload}
        onOneTime={(un, p) => { setOneTime({ username: un, password: p }); setMsg('') }}
      />
    )
  }

  return (
    <div>
      <PageHeader
        icon={ShieldCheck}
        title="管理后台"
        desc={isPlatform ? '平台运营总览 · 租户 · 成员 · 共享积分池（1 积分 = ¥0.01 成本锚点）' : '成员管理'}
        actions={
          <>
            {isPlatform && tab === 'tenants' && (
              <button className="btn-ghost" onClick={() => { setShowNewTenant((v) => !v); setShowNewUser(false) }}>
                <Plus size={14} /> 新建租户
              </button>
            )}
            {tab === 'members' && (
              <button className="btn-accent" onClick={() => { setShowNewUser((v) => !v); setShowNewTenant(false) }}>
                <Plus size={14} /> 新建成员
              </button>
            )}
          </>
        }
      />

      {/* ② 回显带 */}
      {loading && <Busy text="加载中…" />}
      <EchoBand err={err} msg={msg} oneTime={oneTime} />

      {/* ③ tab 分段 */}
      <div className="segment mb-4">
        {isPlatform && (
          <button className={`segment-item ${tab === 'overview' ? 'segment-item-active' : ''}`}
            onClick={() => { setTab('overview'); setExpandUser(null) }}>
            总览
          </button>
        )}
        {isPlatform && (
          <button className={`segment-item ${tab === 'tenants' ? 'segment-item-active' : ''}`}
            onClick={() => { setTab('tenants'); setExpandUser(null) }}>
            <Building2 size={13} className="mr-1 inline" /> 租户（{tenants.length}）
          </button>
        )}
        <button className={`segment-item ${tab === 'members' ? 'segment-item-active' : ''}`}
          onClick={() => { setTab('members'); setExpandUser(null) }}>
          <Users size={13} className="mr-1 inline" /> 成员（{users.length}）
        </button>
      </div>

      {tab === 'overview' && isPlatform && (
        <OverviewTab onNavigate={onNavigate} onOpenTenant={(id) => { setDetailId(id); setTab('tenants'); setMsg(''); setErr('') }} />
      )}

      {tab === 'tenants' && isPlatform && (
        <>
          <NewTenantForm
            open={showNewTenant}
            onClose={() => setShowNewTenant(false)}
            onDone={(u, p) => { setShowNewTenant(false); setOneTime({ username: u, password: p }); setMsg(''); reload() }}
            setErr={setErr}
          />
          <div className="card overflow-hidden">
            <div className="grid grid-cols-[1.2fr_0.9fr_0.9fr_0.7fr_0.7fr_0.9fr] gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-[11px] font-medium text-slate-500">
              <div>租户</div><div className="text-right">近30天成本</div><div className="text-right">积分余额</div>
              <div className="text-center">活跃/成员</div>
              <div className="text-center">状态</div><div className="text-right">创建时间</div>
            </div>
            {tenants.length === 0 ? (
              <div className="p-6"><EmptyState icon={Building2} title="还没有租户" desc="点击右上角「新建租户」创建第一个租户" /></div>
            ) : tenants.map((t) => (
              <button
                key={t.id}
                className="grid w-full grid-cols-[1.2fr_0.9fr_0.9fr_0.7fr_0.7fr_0.9fr] items-center gap-2 border-b border-slate-50 px-4 py-2.5 text-left text-sm transition last:border-0 hover:bg-slate-50"
                onClick={() => { setDetailId(t.id); setMsg(''); setErr('') }}
              >
                <div className="truncate font-medium text-slate-800">{t.name}</div>
                <div className="text-right tabular-nums text-slate-700">{fmtYuan(t.cost30 ?? 0)}</div>
                <div className="text-right font-semibold tabular-nums text-slate-800">{(t.credits ?? 0).toLocaleString()}</div>
                <div className="text-center tabular-nums text-slate-600">{t.active_members30 ?? 0}/{t.member_count}</div>
                <div className="text-center">
                  <span className={`badge ${t.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'}`}>
                    {t.status === 'active' ? '启用' : '停用'}
                  </span>
                </div>
                <div className="text-right text-xs text-slate-400">{relTime(t.created_at ?? undefined)}</div>
              </button>
            ))}
          </div>
        </>
      )}

      {tab === 'members' && (
        <MembersTab
          users={users}
          isPlatform={isPlatform}
          selfId={selfId}
          manageableTenants={manageableTenants}
          showNewUser={showNewUser}
          setShowNewUser={setShowNewUser}
          expandUser={expandUser}
          setExpandUser={setExpandUser}
          act={act}
          onOneTime={(un, p) => { setOneTime({ username: un, password: p }); setMsg('') }}
          onDone={(u, p) => { setShowNewUser(false); setOneTime({ username: u, password: p }); setMsg(''); reload() }}
          setErr={setErr}
        />
      )}
    </div>
  )
}

/* ---------- 总览：平台仪表盘 ---------- */

interface Health {
  status: string
  version: string
  db: boolean
  llm: { provider: string; model: string; configured: boolean }
  deps: { ffmpeg: boolean; docker: boolean }
}

function StatCard({
  icon: Icon,
  title,
  value,
  sub,
  ok,
  okText,
  badText,
  tone = 'sky',
}: {
  icon: LucideIcon
  title: string
  value: string
  sub?: string
  ok?: boolean
  okText?: string
  badText?: string
  tone?: 'sky' | 'emerald' | 'blue' | 'violet'
}) {
  const tones: Record<string, string> = {
    sky: 'bg-sky-100 text-sky-600',
    emerald: 'bg-emerald-100 text-emerald-600',
    blue: 'bg-blue-100 text-blue-600',
    violet: 'bg-indigo-100 text-indigo-600',
  }
  return (
    <div className="card card-hover p-4">
      <div className="flex items-center gap-2.5">
        <div className={`grid size-8 place-items-center rounded-lg ${tones[tone]}`}>
          <Icon size={15} />
        </div>
        <span className="text-xs font-medium text-slate-500">{title}</span>
        {ok !== undefined && (
          <span
            className={`badge ml-auto ${
              ok ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-600'
            }`}
          >
            <span className={`size-1.5 rounded-full ${ok ? 'bg-emerald-500' : 'bg-slate-500'}`} />
            {ok ? okText : badText}
          </span>
        )}
      </div>
      <div className="mt-3 text-lg font-semibold tracking-tight text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-400">{sub}</div>}
    </div>
  )
}

function OverviewTab({ onNavigate, onOpenTenant }: { onNavigate?: (k: SectionKey) => void; onOpenTenant?: (id: number) => void }) {
  const [ov, setOv] = useState<Overview | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api<Overview>('/admin/overview').then((d) => { setOv(d); setErr('') }).catch((e) => setErr(String(e)))
    fetch('/api/health').then((r) => r.json()).then(setHealth).catch(() => setHealth(null))
  }, [])
  if (err) return <ErrorLine text={err} />
  if (!ov) return <Busy text="平台统计加载中…" />

  const maxCost = Math.max(1e-9, ...ov.top_tenants.map((t) => t.cost))
  return (
    <div className="space-y-4">
      {/* KPI 六卡 */}
      <div className="grid grid-cols-3 gap-3 lg:grid-cols-6">
        <div className="card p-3">
          <div className="text-[11px] text-slate-400">租户</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{ov.tenants.total}</div>
          <div className="text-[11px] text-slate-400">启用 {ov.tenants.active} · 停用 {ov.tenants.total - ov.tenants.active}</div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-slate-400">成员</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{ov.members.total}</div>
          <div className="text-[11px] text-slate-400">30 天活跃 {ov.members.active30}</div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-slate-400">今日 LLM 成本</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{fmtYuan(ov.llm.today)}</div>
          <div className="text-[11px] tabular-nums text-slate-400">{ov.llm.calls_today} 次调用</div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-slate-400">30 天累计成本</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{fmtYuan(ov.llm.d30)}</div>
          <div className="text-[11px] text-slate-400">全部租户合计</div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-slate-400">今日任务</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{ov.jobs.today}</div>
          <div className="text-[11px] tabular-nums text-slate-400">
            成功率 {ov.jobs.success_rate === null ? '—' : `${(ov.jobs.success_rate * 100).toFixed(0)}%`}
          </div>
        </div>
        <div className="card p-3">
          <div className="text-[11px] text-slate-400">积分池总余额</div>
          <div className="mt-0.5 text-xl font-semibold tabular-nums text-slate-900">{ov.credits_total.toLocaleString()}</div>
          <div className="text-[11px] text-slate-400">≈ {fmtYuan(ov.credits_total / 100)}</div>
        </div>
      </div>

      {/* 平台状态（服务/LLM 通道/本地依赖，自工作台迁入） */}
      {health && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard
            icon={Database}
            tone="emerald"
            title="服务与数据库"
            value={health.status === 'ok' ? '运行中' : '异常'}
            sub={`v${health.version} · SQLite`}
            ok={health.db}
            okText="数据库正常"
            badText="数据库异常"
          />
          <StatCard
            icon={BrainCircuit}
            tone="violet"
            title="LLM 通道"
            value={health.llm.provider.toUpperCase()}
            sub={health.llm.model}
            ok={health.llm.configured}
            okText="已配置"
            badText="未配置密钥"
          />
          <StatCard
            icon={Terminal}
            tone="sky"
            title="本地依赖"
            value={health.deps.ffmpeg ? 'ffmpeg 可用' : 'ffmpeg 缺失'}
            sub={health.deps.docker ? 'docker 可用' : 'docker 缺失'}
            ok={health.deps.ffmpeg}
            okText="就绪"
            badText="brew install ffmpeg"
          />
        </div>
      )}

      {/* 趋势 + 排行 */}
      <div className="grid grid-cols-3 gap-4">
        <div className="card p-4 lg:col-span-2">
          <div className="mb-3 text-xs font-medium text-slate-500">近 14 天平台趋势（红=当日有失败任务）</div>
          <div className="mb-3">
            <div className="mb-1 text-[11px] text-slate-400">LLM 成本（元）</div>
            <MiniBars data={ov.trend.map((d) => ({ day: d.day, v: d.cost }))} color="#16a34a" height={56} />
          </div>
          <div className="border-t border-slate-100 pt-3">
            <div className="mb-1 text-[11px] text-slate-400">任务量</div>
            <MiniBars data={ov.trend.map((d) => ({ day: d.day, v: d.jobs, failed: d.failed }))} color="#94a3b8" height={56} failedKey />
          </div>
        </div>
        <div className="card p-4">
          <div className="mb-3 text-xs font-medium text-slate-500">租户成本排行（30 天，点击进租户详情）</div>
          {ov.top_tenants.length === 0 ? (
            <div className="text-xs text-slate-400">暂无消耗</div>
          ) : (
            <div className="space-y-2.5">
              {ov.top_tenants.map((t) => (
                <button key={t.id} className="block w-full text-left"
                  onClick={() => onOpenTenant?.(t.id)}>
                  <div className="mb-0.5 flex items-center justify-between text-xs">
                    <span className="truncate text-slate-700 hover:text-[#15803d]">{t.name}</span>
                    <span className="tabular-nums font-medium text-slate-800">{fmtYuan(t.cost)}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                    <div className="h-full rounded-full bg-[#16a34a]"
                      style={{ width: `${Math.max(2, (t.cost / maxCost) * 100)}%` }} />
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 最近动态 */}
      <div className="card overflow-hidden">
        <div className="border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-[11px] font-medium text-slate-500">
          最近动态（全平台任务事件，点击行进任务页）
        </div>
        {ov.feed.length === 0 ? (
          <div className="p-6"><EmptyState icon={ShieldCheck} title="还没有任务动态" desc="任务提交后会在这里实时出现" /></div>
        ) : ov.feed.map((f) => {
          const st = JOB_STATUS[f.status] ?? { label: f.status, cls: 'bg-slate-100 text-slate-500' }
          return (
            <button key={f.job_id}
              className="flex w-full items-center gap-3 border-b border-slate-50 px-4 py-2 text-left text-sm transition last:border-0 hover:bg-slate-50"
              onClick={() => onNavigate?.('tasks')}>
              <span className={`badge shrink-0 ${st.cls}`}>{st.label}</span>
              <span className="w-24 shrink-0 text-slate-700">{jobLabel(f.type)}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-slate-500">{f.tenant} · {f.user}</span>
              <span className="shrink-0 text-xs text-slate-400">{relTime(f.created_at ?? undefined)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

/* ---------- 租户详情（整页）：KPI / 趋势 / 分账+排行 / 充值+流水+任务 ---------- */

function TenantDetailPage({ tenant, onBack, onNavigate, act, setErr, setMsg, err, msg, reload, onOneTime }: {
  tenant: TenantRow
  onBack: () => void
  onNavigate?: (k: SectionKey) => void
  act: (fn: () => Promise<void>, okMsg: string) => Promise<void>
  setErr: (s: string) => void
  setMsg: (s: string) => void
  err: string
  msg: string
  reload: () => Promise<void>
  onOneTime: (username: string, password: string) => void
}) {
  const [usage, setUsage] = useState<TenantUsage | null>(null)
  const [usageErr, setUsageErr] = useState('')
  const [txns, setTxns] = useState<Txn[]>([])
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [points, setPoints] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      const [u, t, j] = await Promise.all([
        api<TenantUsage>(`/admin/tenants/${tenant.id}/usage?days=30`),
        api<{ transactions: Txn[] }>(`/admin/tenants/${tenant.id}/transactions`),
        api<{ items: JobItem[] }>(`/jobs?tenant_id=${tenant.id}&limit=12`),
      ])
      setUsage(u); setTxns(t.transactions); setJobs(j.items); setUsageErr('')
    } catch (e) {
      setUsageErr(String(e))
    }
  }, [tenant.id])
  useEffect(() => { load() }, [load])

  const submitTopup = async () => {
    const n = parseInt(points, 10)
    if (!n) return
    setBusy(true)
    await act(async () => {
      await api(`/admin/tenants/${tenant.id}/topup`, {
        method: 'POST',
        body: JSON.stringify({ points: n, reason }),
      })
      await Promise.all([load(), reload()])
    }, `已为「${tenant.name}」调整 ${n.toLocaleString()} 积分`)
    setPoints(''); setReason(''); setBusy(false)
  }

  const net = (usage?.credits.topup ?? 0) - (usage?.credits.consume ?? 0)

  // 租户品牌/人设（角标栏目名、头图署名、内容人设等九字段）——出片与生成按此渲染
  const saveBrand = async (payload: BrandShape) => {
    setBusy(true)
    await act(async () => {
      await api(`/admin/tenants/${tenant.id}/brand`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      await load()
    }, `「${tenant.name}」品牌已保存（新出片/新文章按此渲染）`)
    setBusy(false)
  }

  return (
    <div>
      {/* 页头带：面包屑返回 + 租户名 + 状态 */}
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <button className="btn-ghost" onClick={onBack}><ArrowLeft size={14} /> 返回租户列表</button>
          <div>
            <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-slate-900">
              {tenant.name}
              <span className={`badge ${tenant.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'}`}>
                {tenant.status === 'active' ? '启用' : '停用'}
              </span>
            </h2>
            <p className="text-xs text-slate-500">
              成员 {tenant.member_count} 人（30 天活跃 {tenant.active_members30}）· 创建于 {relTime(tenant.created_at ?? undefined)}
            </p>
          </div>
        </div>
      </div>

      {usageErr && <div className="mb-4"><ErrorLine text={usageErr} /></div>}
      {!usage && !usageErr && <Busy text="用量统计加载中…" />}
      {msg && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{msg}</div>
      )}

      {/* 租户品牌/人设：数字人角标、头图署名、内容生成人设（SaaS 产品化） */}
      <div className="card mt-4 space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold text-slate-800">租户品牌与内容人设</div>
          <span className="text-[11px] text-slate-400">数字人角标栏目名、头图署名、内容生成的人设口径——保存后新出片/新文章按此渲染</span>
        </div>
        <BrandForm initial={tenant.brand ?? {}} onSave={saveBrand} />

      </div>

      {/* 本租户成员：看人/加人/改本租户角色都在租户上下文完成（租户为纲） */}
      <div className="mt-4">
        <TenantMembersSection tenantId={tenant.id} onOneTime={onOneTime}
          onChanged={async () => { await Promise.all([load(), reload()]) }} />
      </div>
      {err && <div className="mb-4"><ErrorLine text={err} /></div>}

      {usage && (
        <div className="space-y-4">
          {/* KPI 四卡 */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <div className="card p-3">
              <div className="text-[11px] text-slate-400">LLM 成本估算（{usage.days}天）</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">{fmtYuan(usage.llm.cost_est)}</div>
              <div className="text-[11px] tabular-nums text-slate-400">{usage.llm.calls} 次调用</div>
            </div>
            <div className="card p-3">
              <div className="text-[11px] text-slate-400">任务成功率（{usage.days}天）</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">
                {usage.jobs.success_rate === null ? '—' : `${(usage.jobs.success_rate * 100).toFixed(0)}%`}
              </div>
              <div className="text-[11px] tabular-nums text-slate-400">共 {usage.jobs.total} 个任务</div>
            </div>
            <div className="card p-3">
              <div className="text-[11px] text-slate-400">数字人成本 · 服务商豆口径（{usage.days}天）</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-slate-900">{usage.beans.beans.toLocaleString()}</div>
              <div className="text-[11px] tabular-nums text-slate-400">{usage.beans.seconds} 秒成片</div>
            </div>
            <div className="card p-3">
              <div className="text-[11px] text-slate-400">积分净变动（{usage.days}天）</div>
              <div className={`mt-0.5 text-lg font-semibold tabular-nums ${net >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                {net >= 0 ? '+' : ''}{net.toLocaleString()}
              </div>
              <div className="text-[11px] tabular-nums text-slate-400">余 {usage.balance.toLocaleString()}</div>
            </div>
          </div>

          {/* 趋势：成本 + 任务量 */}
          <div className="card p-4">
            <div className="mb-3 text-xs font-medium text-slate-500">近 {usage.days} 天趋势</div>
            <div className="grid grid-cols-2 gap-6">
              <div>
                <div className="mb-1 text-[11px] text-slate-400">LLM 成本（元/日）</div>
                <MiniBars data={usage.jobs.by_day.map((d) => ({ day: d.day, v: d.cost }))} color="#16a34a" height={56} />
              </div>
              <div>
                <div className="mb-1 text-[11px] text-slate-400">任务量（红=当日有失败）</div>
                <MiniBars data={usage.jobs.by_day.map((d) => ({ day: d.day, v: d.count, failed: d.failed }))} color="#94a3b8" height={56} failedKey />
              </div>
            </div>
          </div>

          {/* 分账 + 成员排行 */}
          <div className="grid grid-cols-2 gap-4">
            <div className="card p-4">
              <div className="mb-2 text-xs font-medium text-slate-500">LLM 按用途分账（真实台账）</div>
              {usage.llm.by_purpose.length === 0 ? (
                <div className="text-xs text-slate-400">统计窗口内无 LLM 调用</div>
              ) : (
                <div className="space-y-1">
                  {usage.llm.by_purpose.map((p) => (
                    <div key={p.purpose} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate text-slate-600">{jobLabel(p.purpose)}</span>
                      <span className="tabular-nums text-slate-400">{p.calls} 次</span>
                      <span className="tabular-nums font-medium text-slate-700">{fmtYuan(p.cost_est)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="card p-4">
              <div className="mb-2 text-xs font-medium text-slate-500">成员使用排行（{usage.days}天，预扣积分口径）</div>
              {usage.members.every((m) => m.jobs === 0) ? (
                <div className="text-xs text-slate-400">统计窗口内无成员活动</div>
              ) : (
                <div className="space-y-0.5">
                  <div className="grid grid-cols-[1.2fr_0.6fr_0.8fr_1fr] gap-2 text-[11px] text-slate-400">
                    <div>成员</div><div className="text-right">任务</div><div className="text-right">预扣积分</div><div className="text-right">最近活跃</div>
                  </div>
                  {usage.members.map((m) => (
                    <div key={m.user_id} className={`grid grid-cols-[1.2fr_0.6fr_0.8fr_1fr] items-center gap-2 rounded-lg px-1 py-1 text-xs ${m.jobs > 0 ? 'hover:bg-slate-50' : 'opacity-45'}`}>
                      <div className="truncate text-slate-700">{m.display_name}</div>
                      <div className="text-right tabular-nums font-medium text-slate-800">{m.jobs}</div>
                      <div className="text-right tabular-nums text-slate-600">{m.points.toLocaleString()}</div>
                      <div className="text-right text-slate-400">{m.last_active_at ? relTime(m.last_active_at) : '—'}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* 充值 + 启停 */}
          <div className="flex flex-wrap items-center gap-2">
            <input className="input w-36" placeholder="积分数" value={points}
              onChange={(e) => setPoints(e.target.value)} inputMode="numeric" />
            <input className="input w-56" placeholder="备注（可选）" value={reason}
              onChange={(e) => setReason(e.target.value)} />
            <button className="btn-accent" disabled={busy || !parseInt(points, 10)} onClick={submitTopup}>
              确认
            </button>
            {tenant.status === 'active' ? (
              <button className="btn-danger btn-xs" disabled={busy}
                onClick={() => act(async () => {
                  await api(`/admin/tenants/${tenant.id}/status`, { method: 'POST', body: JSON.stringify({ status: 'disabled' }) })
                }, `租户「${tenant.name}」已停用（成员会话已清除）`)}>
                停用租户
              </button>
            ) : (
              <button className="btn-success btn-xs" disabled={busy}
                onClick={() => act(async () => {
                  await api(`/admin/tenants/${tenant.id}/status`, { method: 'POST', body: JSON.stringify({ status: 'active' }) })
                }, `租户「${tenant.name}」已启用`)}>
                启用租户
              </button>
            )}
            <span className="text-[11px] text-slate-400">正数充值、负数扣减；停用后全部成员立即下线</span>
          </div>

          {/* 危险区：导出 / 删除 */}
          <DangerZone tenant={tenant} onDeleted={() => { setMsg(''); onBack(); reload() }} act={act} setErr={setErr} />

          {/* 流水 + 最近任务 */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="mb-2 text-xs font-medium text-slate-500">积分流水（最近 50 条）</div>
              {txns.length === 0 ? (
                <div className="text-xs text-slate-400">暂无流水</div>
              ) : (
                <div className="card max-h-64 space-y-1 overflow-y-auto p-2">
                  {txns.map((x) => (
                    <div key={x.id} className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-xs hover:bg-slate-50">
                      <span className="min-w-0 flex-1 truncate text-slate-600">{x.reason}</span>
                      <span className={`tabular-nums font-semibold ${x.delta >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                        {x.delta >= 0 ? '+' : ''}{x.delta.toLocaleString()}
                      </span>
                      <span className="tabular-nums text-slate-400">余 {x.balance_after.toLocaleString()}</span>
                      <span className="w-20 text-right text-slate-300">{relTime(x.created_at ?? undefined)}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <div className="mb-2 text-xs font-medium text-slate-500">最近任务（点击进任务页）</div>
              {jobs.length === 0 ? (
                <div className="text-xs text-slate-400">暂无任务</div>
              ) : (
                <div className="card max-h-64 space-y-0.5 overflow-y-auto p-2">
                  {jobs.map((j) => {
                    const st = JOB_STATUS[j.status] ?? { label: j.status, cls: 'bg-slate-100 text-slate-500' }
                    return (
                      <button key={j.id}
                        className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-xs transition hover:bg-slate-50"
                        onClick={() => onNavigate?.('tasks')}>
                        <span className={`badge shrink-0 ${st.cls}`}>{st.label}</span>
                        <span className="w-20 shrink-0 text-left text-slate-700">{jobLabel(j.type)}</span>
                        <span className="min-w-0 flex-1 truncate text-left text-slate-400">
                          {j.error ? j.error.slice(0, 40) : j.message}
                        </span>
                        <span className="shrink-0 text-slate-300">{relTime(j.created_at ?? undefined)}</span>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------- 成员 tab：搜索 + 筛选 + 表格 + 行展开 ---------- */

function MembersTab({ users, isPlatform, selfId, manageableTenants, showNewUser, setShowNewUser,
                      expandUser, setExpandUser, act, onOneTime, onDone, setErr }: {
  users: UserRow[]
  isPlatform: boolean
  selfId: number
  manageableTenants: { id: number; name: string }[]
  showNewUser: boolean
  setShowNewUser: (v: boolean) => void
  expandUser: number | null
  setExpandUser: (v: number | null) => void
  act: (fn: () => Promise<void>, okMsg: string) => Promise<void>
  onOneTime: (username: string, password: string) => void
  onDone: (username: string, password: string) => void
  setErr: (s: string) => void
}) {
  const [q, setQ] = useState('')
  const [roleF, setRoleF] = useState('all')
  const [statusF, setStatusF] = useState('all')

  // 行内归属摘要：默认租户·身份（＋其余租户数）；平台管理员另挂全局徽章
  const memSummary = (u: UserRow) => {
    const ms = u.memberships ?? []
    if (ms.length === 0) return u.tenant_name || '—'
    const first = ms.find((m) => m.tenant_id === u.tenant_id) ?? ms[0]
    const rest = ms.length - 1
    return `${first.tenant_name || `租户 ${first.tenant_id}`} · ${first.role === 'tenant_admin' ? '管理员' : '成员'}${rest > 0 ? ` ＋${rest} 租户` : ''}`
  }

  // 全局角色位只剩 platform_admin/member；「租户管理员」= 任一归属里有 tenant_admin
  const matchRole = (u: UserRow, r: string) =>
    r === 'tenant_admin'
      ? u.role === 'tenant_admin' || (u.memberships ?? []).some((m) => m.role === 'tenant_admin')
      : u.role === r
  const filtered = users.filter((u) =>
    (roleF === 'all' || matchRole(u, roleF)) &&
    (statusF === 'all' || u.status === statusF) &&
    (!q.trim() || u.username.includes(q.trim()) || (u.display_name || '').includes(q.trim())))

  const chip = (key: string, label: string, active: string, set: (v: string) => void, count: number) => (
    <button key={key}
      className={`badge cursor-pointer ${active === key ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}
      onClick={() => set(key)}>
      {label} {count}
    </button>
  )
  const roleCount = (r: string) => users.filter((u) => matchRole(u, r)).length
  const statusCount = (s: string) => users.filter((u) => u.status === s).length

  return (
    <>
      <NewUserForm
        open={showNewUser}
        isPlatform={isPlatform}
        onClose={() => setShowNewUser(false)}
        onDone={onDone}
        setErr={setErr}
      />
      {/* 筛选行 */}
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input className="input w-56 py-1.5 pl-8 text-xs" placeholder="搜索用户名 / 显示名"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex items-center gap-1.5">
          {chip('all', '全部角色', roleF, setRoleF, users.length)}
          {isPlatform && chip('platform_admin', '平台管理员', roleF, setRoleF, roleCount('platform_admin'))}
          {chip('tenant_admin', '租户管理员', roleF, setRoleF, roleCount('tenant_admin'))}
          {chip('member', '成员', roleF, setRoleF, roleCount('member'))}
        </div>
        <div className="flex items-center gap-1.5">
          {chip('all', '全部状态', statusF, setStatusF, users.length)}
          {chip('active', '正常', statusF, setStatusF, statusCount('active'))}
          {chip('disabled', '停用', statusF, setStatusF, statusCount('disabled'))}
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="grid grid-cols-[1fr_1fr_0.9fr_0.9fr_0.7fr_1fr] gap-2 border-b border-slate-100 bg-slate-50/60 px-4 py-2 text-[11px] font-medium text-slate-500">
          <div>用户名</div><div>显示名</div><div>租户归属</div>
          {isPlatform ? <div>所属租户</div> : <div />}
          <div className="text-center">状态</div><div className="text-right">创建时间</div>
        </div>
        {filtered.length === 0 ? (
          <div className="p-6"><EmptyState icon={Users} title="没有匹配的成员" desc="调整搜索词或筛选条件" /></div>
        ) : filtered.map((u) => (
          <div key={u.id} className="border-b border-slate-50 last:border-0">
            <button
              className={`grid w-full grid-cols-[1fr_1fr_0.9fr_0.9fr_0.7fr_1fr] items-center gap-2 px-4 py-2.5 text-left text-sm transition hover:bg-slate-50 ${u.status === 'disabled' ? 'opacity-50' : ''}`}
              onClick={() => { setExpandUser(expandUser === u.id ? null : u.id) }}
            >
              <div className="truncate font-medium text-slate-800">
                {u.username}
                {(u.memberships?.length ?? 0) > 1 && (
                  <span className="badge ml-1.5 bg-sky-50 text-sky-700">{u.memberships.length} 租户</span>
                )}
              </div>
              <div className="truncate text-slate-600">{u.display_name}</div>
              <div className="truncate text-xs text-slate-600">
                {u.role === 'platform_admin' && <span className="badge mr-1 bg-violet-50 text-violet-700">平台</span>}
                {memSummary(u)}
              </div>
              {isPlatform ? <div className="truncate text-slate-600">{u.tenant_name}</div> : <div />}
              <div className="text-center">
                <span className={`badge ${u.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-600'}`}>
                  {u.status === 'active' ? '正常' : '停用'}
                </span>
              </div>
              <div className="text-right text-xs text-slate-400">{relTime(u.created_at ?? undefined)}</div>
            </button>
            {expandUser === u.id && (
              <UserDetail user={u} isPlatform={isPlatform} selfId={selfId} act={act}
                manageableTenants={manageableTenants}
                onOneTime={onOneTime} />
            )}
          </div>
        ))}
      </div>
    </>
  )
}

/* ---------- 成员展开：角色 / 重置密码 / 启停 + 个人用量 ---------- */

function UserDetail({ user, isPlatform, selfId, act, manageableTenants, onOneTime }: {
  user: UserRow
  isPlatform: boolean
  selfId: number
  act: (fn: () => Promise<void>, okMsg: string) => Promise<void>
  manageableTenants: { id: number; name: string }[]
  onOneTime: (username: string, password: string) => void
}) {
  const [usage, setUsage] = useState<UserUsage | null>(null)
  useEffect(() => {
    api<UserUsage>(`/admin/users/${user.id}/usage?days=30`).then(setUsage).catch(() => setUsage(null))
  }, [user.id])
  const isSelf = user.id === selfId
  const canTouch = !isSelf && !(isPlatform === false && user.role === 'platform_admin')
  // 归属管理：平台管理员可管理任何人（含自己）的归属；租户管理员限本租户且不能碰平台管理员账号
  const canManageMems = isPlatform || (canTouch && user.role !== 'platform_admin')
  const mems = user.memberships ?? []
  const addable = manageableTenants.filter((t) => !mems.some((m) => m.tenant_id === t.id))
  const [addTenant, setAddTenant] = useState<number | ''>('')
  const [addRole, setAddRole] = useState('member')
  return (
    <div className="space-y-3 border-t border-slate-100 bg-slate-50/40 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="text-xs text-slate-500">
          ID {user.id} · 默认租户「{user.tenant_name}」
          {user.must_change_password && <span className="ml-2 badge bg-amber-50 text-amber-700">待改密</span>}
        </div>
        {isPlatform && (
          <label className="flex items-center gap-2 text-xs text-slate-500">
            全局角色
            <select className="select" value={user.role} disabled={isSelf}
              onChange={(e) => act(async () => {
                await api(`/admin/users/${user.id}/role`, { method: 'POST', body: JSON.stringify({ role: e.target.value }) })
              }, `已将 ${user.username} 全局角色改为${ROLE_LABEL[e.target.value]}`)}>
              <option value="member">成员（全局）</option>
              <option value="platform_admin">平台管理员</option>
            </select>
          </label>
        )}
        <button className="btn-ghost btn-xs" disabled={!canTouch}
          onClick={() => act(async () => {
            const d = await api<{ one_time_password: string }>(`/admin/users/${user.id}/reset-password`, { method: 'POST' })
            onOneTime(user.username, d.one_time_password)
          }, `已重置 ${user.username} 的密码`)}>
          重置密码
        </button>
        {canTouch && (
          user.status === 'active' ? (
            <button className="btn-danger btn-xs"
              onClick={() => act(async () => {
                await api(`/admin/users/${user.id}/status`, { method: 'POST', body: JSON.stringify({ status: 'disabled' }) })
              }, `账号 ${user.username} 已停用（会话已清除）`)}>
              停用账号
            </button>
          ) : (
            <button className="btn-success btn-xs"
              onClick={() => act(async () => {
                await api(`/admin/users/${user.id}/status`, { method: 'POST', body: JSON.stringify({ status: 'active' }) })
              }, `账号 ${user.username} 已启用`)}>
              启用账号
            </button>
          )
        )}
        {isSelf && <span className="text-[11px] text-slate-400">自己的账号：改角色/停用需其他管理员操作</span>}
      </div>
      {/* 租户归属（一人多租户）：列表 + 添加归属（upsert 语义，已归属即改角色） */}
      <div className="rounded-lg bg-white px-3 py-2.5 text-xs">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="font-medium text-slate-500">租户归属</span>
          <span className="text-[11px] text-slate-400">默认租户=登录初始落点，切换租户不改变归属</span>
        </div>
        <div className="space-y-1">
          {mems.map((m) => (
            <div key={m.tenant_id} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-slate-700">
                {m.tenant_name || `租户 ${m.tenant_id}`}
                {m.tenant_id === user.tenant_id && <span className="badge ml-1.5 bg-slate-100 text-slate-500">默认</span>}
              </span>
              <select className="select w-32 py-0.5 text-xs" disabled={!canManageMems} value={m.role}
                onChange={(e) => act(async () => {
                  await api(`/admin/users/${user.id}/memberships`, {
                    method: 'POST',
                    body: JSON.stringify({ tenant_id: m.tenant_id, role: e.target.value }),
                  })
                }, `已将 ${user.username} 在「${m.tenant_name}」的角色改为${ROLE_LABEL[e.target.value] ?? e.target.value}`)}>
                <option value="member">成员</option>
                <option value="tenant_admin">租户管理员</option>
              </select>
              <button className="btn-ghost btn-xs text-red-600 disabled:opacity-40"
                disabled={!canManageMems || mems.length <= 1}
                title={mems.length <= 1 ? '至少保留一个租户归属' : '移除该归属'}
                onClick={() => act(async () => {
                  await api(`/admin/users/${user.id}/memberships/${m.tenant_id}`, { method: 'DELETE' })
                }, `已移除 ${user.username} 在「${m.tenant_name}」的归属`)}>
                移除
              </button>
            </div>
          ))}
          {mems.length === 0 && <div className="text-slate-400">（无归属记录）</div>}
        </div>
        {canManageMems && addable.length > 0 && (
          <div className="mt-2 flex items-center gap-2 border-t border-slate-100 pt-2">
            <select className="select w-44 py-1 text-xs" value={addTenant}
              onChange={(e) => setAddTenant(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">添加归属：选择租户…</option>
              {addable.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <select className="select w-32 py-1 text-xs" value={addRole}
              onChange={(e) => setAddRole(e.target.value)}>
              <option value="member">成员</option>
              <option value="tenant_admin">租户管理员</option>
            </select>
            <button className="btn-ghost btn-xs" disabled={addTenant === ''}
              onClick={() => act(async () => {
                await api(`/admin/users/${user.id}/memberships`, {
                  method: 'POST',
                  body: JSON.stringify({ tenant_id: addTenant, role: addRole }),
                })
                setAddTenant('')
              }, `已为 ${user.username} 添加租户归属`)}>
              添加归属
            </button>
          </div>
        )}
      </div>
      {/* 个人用量（30 天） */}
      {usage && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg bg-white px-3 py-2 text-xs">
          <span className="font-medium text-slate-500">近30天用量</span>
          <span className="tabular-nums text-slate-700">任务 <b>{usage.jobs_total}</b></span>
          <span className="tabular-nums text-slate-700">预扣积分 <b>{usage.points.toLocaleString()}</b></span>
          <span className="text-slate-500">最近活跃：{usage.last_active_at ? relTime(usage.last_active_at) : '—'}</span>
          {Object.keys(usage.by_type).length > 0 && (
            <span className="flex flex-wrap items-center gap-1">
              {Object.entries(usage.by_type).map(([t, n]) => (
                <span key={t} className="badge bg-slate-100 text-slate-600">{jobLabel(t)} ×{n}</span>
              ))}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/* ---------- 租户详情：成员管理（本租户归属视角，platform_admin） ---------- */

function TenantMembersSection({ tenantId, onOneTime, onChanged }: {
  tenantId: number
  onOneTime: (username: string, password: string) => void
  onChanged: () => Promise<void>
}) {
  const [users, setUsers] = useState<UserRow[]>([])
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [addId, setAddId] = useState<number | ''>('')
  const [addRole, setAddRole] = useState('member')
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDisplay, setNewDisplay] = useState('')
  const [newRole, setNewRole] = useState('member')

  const load = useCallback(async () => {
    try {
      const d = await api<{ users: UserRow[] }>('/admin/users')
      setUsers(d.users)
    } catch (e) { setErr(String(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  const actLocal = async (fn: () => Promise<void>, ok: string) => {
    setErr(''); setMsg(''); setBusy(true)
    try {
      await fn()
      setMsg(ok)
      await Promise.all([load(), onChanged()])
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  const memRows = users
    .map((u) => ({ u, m: (u.memberships ?? []).find((mm) => mm.tenant_id === tenantId) }))
    .filter((x) => x.m)
  const addable = users.filter((u) => !(u.memberships ?? []).some((mm) => mm.tenant_id === tenantId))

  const upsert = (userId: number, role: string, name: string) =>
    actLocal(async () => {
      await api(`/admin/users/${userId}/memberships`, {
        method: 'POST', body: JSON.stringify({ tenant_id: tenantId, role }),
      })
    }, `已将 ${name} 在本租户的角色改为${role === 'tenant_admin' ? '租户管理员' : '成员'}`)

  const remove = (userId: number, name: string) =>
    actLocal(async () => {
      await api(`/admin/users/${userId}/memberships/${tenantId}`, { method: 'DELETE' })
    }, `已移除 ${name} 的本租户归属（多归属用户保留账号）`)

  const addExisting = () =>
    actLocal(async () => {
      await api(`/admin/users/${addId}/memberships`, {
        method: 'POST', body: JSON.stringify({ tenant_id: tenantId, role: addRole }),
      })
      setAddId('')
    }, '已添加成员归属')

  const createNew = () =>
    actLocal(async () => {
      const d = await api<{ one_time_password: string }>('/admin/users', {
        method: 'POST',
        body: JSON.stringify({ username: newName.trim(), display_name: newDisplay.trim(), role: newRole, tenant_id: tenantId }),
      })
      onOneTime(newName.trim(), d.one_time_password)
      setShowNew(false); setNewName(''); setNewDisplay(''); setNewRole('member')
      await load()
    }, '成员已创建（一次性密码见顶部黄条）')

  return (
    <div className="card space-y-3 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold text-slate-800">成员（本租户归属）</div>
        <span className="text-[11px] text-slate-400">这里的角色=该用户在本租户的身份（管理员可管本租户成员）；多租户用户可在此移除本租户归属</span>
      </div>
      {msg && <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{msg}</div>}
      {err && <ErrorLine text={err} />}
      <div className="space-y-1">
        {memRows.map(({ u, m }) => (
          <div key={u.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-100 px-3 py-1.5 text-xs">
            <span className={`size-1.5 shrink-0 rounded-full ${u.status === 'active' ? 'bg-emerald-500' : 'bg-slate-300'}`} />
            <span className="min-w-0 flex-1 truncate">
              <b className="font-medium text-slate-800">{u.username}</b>
              <span className="ml-1.5 text-slate-500">{u.display_name}</span>
              {u.role === 'platform_admin' && <span className="badge ml-1.5 bg-violet-50 text-violet-700">平台管理员</span>}
              {m!.tenant_id === u.tenant_id && <span className="badge ml-1 bg-slate-100 text-slate-500">默认租户</span>}
            </span>
            <select className="select w-32 py-0.5 text-xs" disabled={busy} value={m!.role}
              onChange={(e) => upsert(u.id, e.target.value, u.username)}>
              <option value="member">成员</option>
              <option value="tenant_admin">租户管理员</option>
            </select>
            <button className="btn-ghost btn-xs text-red-600" disabled={busy}
              onClick={() => remove(u.id, u.username)}>
              移除归属
            </button>
          </div>
        ))}
        {memRows.length === 0 && <div className="text-xs text-slate-400">本租户还没有成员</div>}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-2.5">
        <select className="select w-52 py-1 text-xs" value={addId} disabled={busy}
          onChange={(e) => setAddId(e.target.value === '' ? '' : Number(e.target.value))}>
          <option value="">添加已有用户到本租户…</option>
          {addable.map((u) => <option key={u.id} value={u.id}>{u.username}（{u.display_name}）</option>)}
        </select>
        <select className="select w-32 py-1 text-xs" value={addRole} disabled={busy}
          onChange={(e) => setAddRole(e.target.value)}>
          <option value="member">成员</option>
          <option value="tenant_admin">租户管理员</option>
        </select>
        <button className="btn-ghost btn-xs" disabled={busy || addId === ''} onClick={addExisting}>添加归属</button>
        <span className="text-slate-300">｜</span>
        {showNew ? (
          <span className="flex flex-wrap items-center gap-2">
            <input className="input w-32 py-1 text-xs" placeholder="用户名" value={newName}
              onChange={(e) => setNewName(e.target.value)} />
            <input className="input w-28 py-1 text-xs" placeholder="显示名" value={newDisplay}
              onChange={(e) => setNewDisplay(e.target.value)} />
            <select className="select w-32 py-1 text-xs" value={newRole} disabled={busy}
              onChange={(e) => setNewRole(e.target.value)}>
              <option value="member">成员</option>
              <option value="tenant_admin">租户管理员</option>
            </select>
            <button className="btn-accent btn-xs" disabled={busy || !newName.trim()} onClick={createNew}>创建并加入</button>
            <button className="btn-ghost btn-xs" onClick={() => setShowNew(false)}>取消</button>
          </span>
        ) : (
          <button className="btn-ghost btn-xs" onClick={() => setShowNew(true)}>+ 新建成员</button>
        )}
      </div>
    </div>
  )
}

/* ---------- 新建租户 / 新建成员（行内表单） ---------- */

function NewTenantForm({ open, onClose, onDone, setErr }: {
  open: boolean
  onClose: () => void
  onDone: (username: string, password: string) => void
  setErr: (s: string) => void
}) {
  const [name, setName] = useState('')
  const [credits, setCredits] = useState('100000')
  const [adminUsername, setAdminUsername] = useState('')
  const [adminDisplay, setAdminDisplay] = useState('')
  const [busy, setBusy] = useState(false)
  if (!open) return null

  const submit = async () => {
    if (!name.trim()) return
    setBusy(true); setErr('')
    try {
      const d = await api<{ admin_username?: string; one_time_password?: string }>('/admin/tenants', {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(), credits: parseInt(credits, 10) || 0,
          admin_username: adminUsername.trim(), admin_display_name: adminDisplay.trim(),
        }),
      })
      setName(''); setCredits('100000'); setAdminUsername(''); setAdminDisplay('')
      onDone(d.admin_username ?? '', d.one_time_password ?? '')
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card mb-4 p-4">
      <div className="mb-3 text-sm font-medium text-slate-700">新建租户</div>
      <div className="grid grid-cols-4 gap-3">
        <div>
          <label className="mb-1 block text-xs text-slate-500">租户名称 *</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="如：某某传媒" autoFocus />
        </div>
        <div>
          <label className="mb-1 block text-xs text-slate-500">初始积分</label>
          <input className="input" value={credits} onChange={(e) => setCredits(e.target.value)} inputMode="numeric" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-slate-500">管理员用户名（可选）</label>
          <input className="input" value={adminUsername} onChange={(e) => setAdminUsername(e.target.value)} placeholder="3-32 位字母数字" />
        </div>
        <div>
          <label className="mb-1 block text-xs text-slate-500">管理员显示名</label>
          <input className="input" value={adminDisplay} onChange={(e) => setAdminDisplay(e.target.value)} />
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <button className="btn-accent" disabled={busy || !name.trim()} onClick={submit}>创建</button>
        <button className="btn-ghost" onClick={onClose}>取消</button>
      </div>
    </div>
  )
}

function NewUserForm({ open, isPlatform, onClose, onDone, setErr }: {
  open: boolean
  isPlatform: boolean
  onClose: () => void
  onDone: (username: string, password: string) => void
  setErr: (s: string) => void
}) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('member')
  const [asPlatform, setAsPlatform] = useState(false)
  const [tenantId, setTenantId] = useState<number | ''>('')
  const [tenants, setTenants] = useState<TenantRow[]>([])
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open && isPlatform) {
      api<{ tenants: TenantRow[] }>('/admin/tenants').then((d) => setTenants(d.tenants)).catch(() => {})
    }
  }, [open, isPlatform])
  if (!open) return null

  const submit = async () => {
    if (!username.trim()) return
    setBusy(true); setErr('')
    try {
      // 角色语义：租户内角色（member/tenant_admin）走 role；“平台管理员”是全局位，复选框叠加
      const finalRole = asPlatform ? 'platform_admin' : role
      const d = await api<{ one_time_password: string }>('/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          username: username.trim(), display_name: displayName.trim(), role: finalRole,
          tenant_id: tenantId === '' ? null : tenantId,
        }),
      })
      const u = username.trim()
      setUsername(''); setDisplayName(''); setRole('member'); setAsPlatform(false); setTenantId('')
      onDone(u, d.one_time_password)
    } catch (e) {
      setErr(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card mb-4 p-4">
      <div className="mb-3 text-sm font-medium text-slate-700">新建成员</div>
      <div className="grid grid-cols-4 gap-3">
        <div>
          <label className="mb-1 block text-xs text-slate-500">用户名 *</label>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)}
            placeholder="3-32 位字母数字" autoFocus />
        </div>
        <div>
          <label className="mb-1 block text-xs text-slate-500">显示名</label>
          <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs text-slate-500">租户内角色</label>
          <select className="select w-full" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">成员</option>
            <option value="tenant_admin">租户管理员</option>
          </select>
        </div>
        {isPlatform && (
          <div>
            <label className="mb-1 block text-xs text-slate-500">所属租户</label>
            <select className="select w-full" value={tenantId} onChange={(e) => setTenantId(e.target.value === '' ? '' : Number(e.target.value))}>
              <option value="">（选择租户）</option>
              {tenants.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
        )}
      </div>
      {isPlatform && (
        <label className="mt-2.5 flex items-center gap-1.5 text-xs text-slate-500">
          <input type="checkbox" className="size-3.5" checked={asPlatform}
            onChange={(e) => setAsPlatform(e.target.checked)} />
          同时设为平台管理员（全局角色，可跨租户管理；仍需选择所属租户作为默认落点）
        </label>
      )}
      <div className="mt-3 flex gap-2">
        <button className="btn-accent" disabled={busy || !username.trim()} onClick={submit}>创建并生成初始密码</button>
        <button className="btn-ghost" onClick={onClose}>取消</button>
      </div>
    </div>
  )
}

/* ---------- 危险区：数据导出 / 物理删除租户 ---------- */

function DangerZone({ tenant, onDeleted, act, setErr }: {
  tenant: TenantRow
  onDeleted: () => void
  act: (fn: () => Promise<void>, okMsg: string) => Promise<void>
  setErr: (s: string) => void
}) {
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [name, setName] = useState('')

  const doDelete = async () => {
    setErr('')
    try {
      await api(`/admin/tenants/${tenant.id}?confirm_name=${encodeURIComponent(name)}`, { method: 'DELETE' })
      setConfirmOpen(false)
      onDeleted()
    } catch (e) {
      setErr(String(e))
    }
  }

  return (
    <div className="card border-red-200 p-4">
      <div className="mb-2 text-xs font-medium text-red-500">危险区</div>
      <div className="flex flex-wrap items-center gap-2">
        <a className="btn-ghost btn-xs" href={`/api/admin/tenants/${tenant.id}/export`} download>
          导出租户数据（JSON）
        </a>
        {!confirmOpen ? (
          <button className="btn-danger btn-xs" onClick={() => { setConfirmOpen(true); setName('') }}>
            删除租户…
          </button>
        ) : (
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-slate-600">
              输入租户名 <b className="text-red-500">{tenant.name}</b> 以确认（清库删文件，自动先备份，不可撤销）：
            </span>
            <input className="input w-48 py-1 text-xs" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            <button className="btn-danger btn-xs" disabled={name !== tenant.name} onClick={doDelete}>
              确认删除
            </button>
            <button className="btn-ghost btn-xs" onClick={() => setConfirmOpen(false)}>取消</button>
          </span>
        )}
      </div>
    </div>
  )
}
