import { useCallback, useEffect, useState } from 'react'
import { BrainCircuit, Radar, RefreshCw, Save, Settings as SettingsIcon } from 'lucide-react'
import { api } from '../api'
import { BrandForm, type BrandShape } from '../brandForm'
import { Busy, ErrorLine, PageHeader } from '../components'

interface AppSettings {
  llm_model: string
  llm_configured: boolean
  zhipu_api_key_set: boolean
  zhipu_base_url: string
  coding_plan: boolean
  search_provider: string
  search_enabled: boolean
  asr_model: string
  watch_scan_enabled: boolean
  watch_scan_hour: number
  watch_discover_keywords: string
  auto_dissect_enabled: boolean
  auto_dissect_min_score: number
  auto_dissect_daily_cap: number
}

const MODELS = ['glm-5.3-flash', 'glm-5.3']
const SEARCH_PROVIDERS: { key: string; label: string; desc: string }[] = [
  { key: 'auto', label: '自动（优先 GLM）', desc: '智谱联网搜索优先，失败转 DDG' },
  { key: 'glm', label: '强制 GLM', desc: '仅按量付费通道支持（Coding Plan 套餐不含 web_search）' },
  { key: 'ddg', label: 'DuckDuckGo', desc: '免费、免 key（有限流，自动缓存+退避）' },
]

export default function Settings({ displayName = '', role = 'member', onLogout }: {
  displayName?: string
  role?: string
  onLogout?: () => void
}) {
  const [conf, setConf] = useState<AppSettings | null>(null)
  const [ownBrand, setOwnBrand] = useState<Partial<BrandShape> | null>(null)
  useEffect(() => {
    if (role === 'member') return
    api<{ brand?: Partial<BrandShape> }>('/tenant/brand')
      .then((d) => setOwnBrand(d.brand ?? {}))
      .catch(() => setOwnBrand({}))
  }, [role])
  const saveOwnBrand = (payload: Partial<BrandShape>) =>
    api('/tenant/brand', { method: 'PUT', body: JSON.stringify(payload) })

  const [crawl, setCrawl] = useState<{
    last: { status: string; detail?: string; at?: string } | null
    history: { status: string; at: string }[]
    label: string
    recent_failures: number
  } | null>(null)
  const [crawlBusy, setCrawlBusy] = useState(false)
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [keyMsg, setKeyMsg] = useState('')
  const [search, setSearch] = useState('')
  const [scanEnabled, setScanEnabled] = useState(true)
  const [scanHour, setScanHour] = useState(1)
  const [discoverKw, setDiscoverKw] = useState('')
  const [autoDissect, setAutoDissect] = useState(true)
  const [autoMinScore, setAutoMinScore] = useState(6)
  const [autoCap, setAutoCap] = useState(6)
  const [pingReply, setPingReply] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const c = await api<AppSettings>('/settings')
    setConf(c)
    try {
      const c = await api<{ last: { status: string; detail?: string; at?: string } | null; history: { status: string; at: string }[]; label: string; recent_failures: number }>('/crawl/status')
      setCrawl(c)
    } catch {
      setCrawl(null)
    }
    setModel(c.llm_model)
    setSearch(c.search_provider)
    setScanEnabled(c.watch_scan_enabled)
    setScanHour(c.watch_scan_hour)
    setDiscoverKw(c.watch_discover_keywords ?? '')
    setAutoDissect(c.auto_dissect_enabled)
    setAutoMinScore(c.auto_dissect_min_score)
    setAutoCap(c.auto_dissect_daily_cap)
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
  }, [reload])

  const guard = async (fn: () => Promise<void>) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy('')
    }
  }

  const ping = () =>
    guard(async () => {
      setBusy('LLM 通道体检中（真实调用，约数秒）…')
      const r = await api<{ reply: string }>('/llm/ping', {
        method: 'POST',
        body: JSON.stringify({ prompt: '只回复两个字：就绪' }),
      })
      setPingReply(r.reply)
    })

  const save = () =>
    guard(async () => {
      if (!conf) return
      setBusy('保存中（运行时生效）…')
      const body: Record<string, unknown> = { llm_model: model, search_provider: search, watch_scan_enabled: scanEnabled, watch_scan_hour: scanHour, watch_discover_keywords: discoverKw, auto_dissect_enabled: autoDissect, auto_dissect_min_score: autoMinScore, auto_dissect_daily_cap: autoCap }
      const r = await api<AppSettings>('/settings', { method: 'PUT', body: JSON.stringify(body) })
      setConf(r)
      setPingReply('')
    })

  return (
    <div>
      <PageHeader
        icon={SettingsIcon}
        title="设置"
        desc="全部改动保存后运行时生效"
        actions={<AccountBar displayName={displayName} role={role} onLogout={onLogout} />}
      />

      {busy && <div className="mb-3"><Busy text={busy} /></div>}
      {error && <div className="mb-3"><ErrorLine text={error} /></div>}

      <div className="card mb-4 space-y-3 p-5">
        <div className="text-sm font-semibold text-slate-800">品牌与内容人设</div>
        {/* 必须等配置取回再渲染表单：BrandForm 的 useState 只在首挂载取初值，
        带 {} 先挂载会停留在空字段（2026-10-03 用户报设置里配置为空的根因） */}
        {ownBrand === null ? <Busy text="品牌配置加载中…" /> : <BrandForm initial={ownBrand} onSave={saveOwnBrand} />}
      </div>

      {conf && (
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
          {/* LLM 与联网检索（原两张卡合并，2026-10-29 版面收敛） */}
          <div className="card space-y-4 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <BrainCircuit size={16} className="text-sky-500" /> LLM 与联网检索
            </div>
            <div>
              <div className="text-xs text-slate-400">生成端点</div>
              <div className="mt-0.5 font-medium text-slate-800" title={conf.zhipu_base_url}>
                {conf.coding_plan ? 'Coding Plan 套餐' : '按量付费 PaaS'}
                {conf.llm_configured ? (
                  <span className="badge ml-2 bg-emerald-100 text-emerald-700">已配置</span>
                ) : (
                  <span className="badge ml-2 bg-red-100 text-red-600">缺密钥</span>
                )}
              </div>
            </div>
            <label className="block space-y-1">
              <span className="text-xs text-slate-400">
                API Key
                {conf.zhipu_api_key_set
                  ? <span className="badge ml-2 bg-emerald-100 text-emerald-700">已配置</span>
                  : <span className="badge ml-2 bg-amber-100 text-amber-700">未配置</span>}
              </span>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={conf.zhipu_api_key_set ? '已设置，粘贴新值可覆盖' : '粘贴智谱 API Key'}
                  className="input flex-1"
                />
                <button
                  className="btn-accent btn-xs"
                  disabled={!apiKey.trim()}
                  onClick={async () => {
                    setKeyMsg('')
                    try {
                      await api('/settings', { method: 'PUT', body: JSON.stringify({ zhipu_api_key: apiKey.trim() }) })
                      setApiKey('')
                      setKeyMsg('密钥已保存并生效')
                      await reload()
                    } catch (e) {
                      setKeyMsg(String(e))
                    }
                  }}
                >保存</button>
              </div>
              {keyMsg && <span className="text-[11px] text-emerald-600">{keyMsg}</span>}
            </label>
            <label className="block space-y-1">
              <span className="text-xs text-slate-400">写作模型</span>
              <select value={model} onChange={(e) => setModel(e.target.value)} className="select w-full">
                {MODELS.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </label>
            <div className="flex items-center gap-2">
              <button onClick={ping} className="btn-ghost btn-xs">
                <RefreshCw size={13} /> 通道体检
              </button>
              {pingReply && (
                <span className="badge bg-emerald-100 text-emerald-700">真实调用返回：{pingReply.slice(0, 20)}</span>
              )}
            </div>
            <div className="space-y-2 border-t border-slate-100 pt-3">
              <div className="text-xs font-medium text-slate-500">联网检索通道（深研/热点/发现共用）</div>
              <div className="flex flex-wrap gap-2">
                {SEARCH_PROVIDERS.map((p) => (
                  <button
                    key={p.key}
                    title={p.desc}
                    onClick={() => setSearch(p.key)}
                    className={`rounded-full border px-3 py-1.5 text-xs transition ${
                      search === p.key
                        ? 'border-sky-400 bg-sky-50/60 font-medium text-sky-700 ring-2 ring-sky-200'
                        : 'border-slate-200 text-slate-600 hover:border-slate-300'
                    }`}
                  >{p.label}</button>
                ))}
              </div>
            </div>
          </div>

          {/* 数据采集与同行扫描（原两张卡合并，2026-10-29 版面收敛） */}
          <div className="card space-y-3 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
                <Radar size={16} className="text-sky-500" /> 数据采集与同行扫描
              </div>
              {crawl && crawl.last ? (
                <span className={`badge ${crawl.last.status === 'ok' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-800'}`}>
                  {crawl.label}
                </span>
              ) : (
                <span className="badge bg-slate-100 text-slate-500">尚未探测</span>
              )}
              <button
                onClick={async () => {
                  setCrawlBusy(true)
                  try {
                    await api('/crawl/probe', { method: 'POST' })
                    const c = await api<{ last: { status: string; detail?: string; at?: string } | null; history: { status: string; at: string }[]; label: string; recent_failures: number }>('/crawl/status')
                    setCrawl(c)
                    setError('')
                  } catch (e) {
                    setError(String(e))
                  } finally {
                    setCrawlBusy(false)
                  }
                }}
                disabled={crawlBusy}
                className="btn-ghost btn-xs text-sky-600 ml-auto disabled:opacity-40"
              >
                {crawlBusy ? '探测中…（约 15 秒）' : '立即检测'}
              </button>
            </div>
            {crawl && crawl.last && (
              <div className="flex flex-wrap items-center gap-1.5">
                {(crawl.history || []).slice(0, 8).map((h, i) => (
                  <span
                    key={i}
                    title={`${h.at} ${h.status}`}
                    className={`badge ${h.status === 'ok' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-700'}`}
                  >
                    {h.status === 'ok' ? '✓' : '✗'}
                  </span>
                ))}
              </div>
            )}
            {crawl?.last?.detail && (
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">{crawl.last.detail}</div>
            )}
            <div className="space-y-3 border-t border-slate-100 pt-3">
              <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={scanEnabled}
                    onChange={(e) => setScanEnabled(e.target.checked)}
                    className="size-4 accent-sky-500"
                  />
                  每天定时扫描同行监测账号（含自我账号）
                </label>
                <span className="flex items-center gap-2">
                  触发时间
                  <select
                    value={scanHour}
                    onChange={(e) => setScanHour(Number(e.target.value))}
                    className="input max-w-24"
                  >
                    {Array.from({ length: 24 }, (_, h) => (
                      <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
                    ))}
                  </select>
                </span>
              </div>
              <div className="space-y-1.5 rounded-lg bg-slate-50 px-3 py-2.5">
                <label className="flex items-center gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={autoDissect}
                    onChange={(e) => setAutoDissect(e.target.checked)}
                    className="size-4 accent-sky-500"
                  />
                  质量闸自动拆解
                </label>
                <div className="flex items-center gap-2 text-sm text-slate-700">
                  门槛（热度分≥）
                  <input
                    type="number"
                    value={autoMinScore}
                    onChange={(e) => setAutoMinScore(Number(e.target.value) || 0)}
                    className="input max-w-20"
                  />
                  每日上限
                  <input
                    type="number"
                    value={autoCap}
                    onChange={(e) => setAutoCap(Number(e.target.value) || 0)}
                    className="input max-w-20"
                  />
                  条
                </div>
              </div>
              <div className="space-y-1">
                <label className="block text-sm text-slate-700">每日发现的关键词池</label>
                <input
                  value={discoverKw}
                  onChange={(e) => setDiscoverKw(e.target.value)}
                  placeholder="逗号分隔，如：行业动态, 商业观察, 技术应用（按你的行业填）（留空用平台默认）"
                  className="input w-full"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 保存条 */}
      <div className="fixed inset-x-0 bottom-5 z-40 flex justify-center px-4">
        {conf && (model !== conf.llm_model || search !== conf.search_provider || scanEnabled !== conf.watch_scan_enabled || scanHour !== conf.watch_scan_hour || autoDissect !== conf.auto_dissect_enabled || autoMinScore !== conf.auto_dissect_min_score || autoCap !== conf.auto_dissect_daily_cap) && (
          <div className="flex items-center gap-3 rounded-2xl bg-slate-900 px-5 py-3 text-sm text-slate-100 shadow-xl">
            <span>
              待保存：<b className="text-sky-400">{model}</b>
              {search !== conf.search_provider && <b className="text-sky-400"> · 检索 {search}</b>}
              {(scanEnabled !== conf.watch_scan_enabled || scanHour !== conf.watch_scan_hour) && <b className="text-sky-400"> · 扫描周期</b>}
            </span>
            <button onClick={save} className="btn-accent btn-xs">
              <Save size={13} /> 保存
            </button>
            <button onClick={() => { setModel(conf.llm_model); setSearch(conf.search_provider);  }} className="text-xs text-slate-400 hover:text-slate-200">
              撤销
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/** 右上角账号簇（原侧栏用户卡→设置页卡片→页头右上，2026-10-29）：头像+身份+改密+退出 */
function AccountBar({ displayName, role, onLogout }: {
  displayName: string
  role: string
  onLogout?: () => void
}) {
  const [pwdOpen, setPwdOpen] = useState(false)
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const logout = async () => {
    try { await fetch('/api/auth/logout', { method: 'POST' }) } catch { /* 忽略 */ }
    onLogout?.()
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
    <>
      <div className="flex items-center gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-full bg-linear-to-br from-[#e8c97f] to-[#c9a25c] text-sm font-semibold text-[#0d3b2d]">
          {(displayName || '?').slice(0, 1)}
        </div>
        <div className="min-w-0">
          <div className="max-w-40 truncate text-sm font-medium text-slate-800">{displayName}</div>
          <div className="text-[11px] text-slate-400">{role === 'platform_admin' ? '管理员' : (role || '')}</div>
        </div>
        <button onClick={logout}
          className="rounded-full border border-slate-200 px-3 py-1.5 text-xs text-slate-600 transition hover:bg-slate-50">
          退出登录
        </button>
      </div>
      {pwdOpen ? (
        <div className="flex w-full basis-full flex-wrap items-center justify-end gap-2">
          <input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)}
            placeholder="原密码"
            className="w-40 rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-emerald-500" />
          <input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)}
            placeholder="新密码（至少 8 位）"
            className="w-48 rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:border-emerald-500" />
          <button disabled={busy || !oldPwd || newPwd.length < 8} onClick={changePwd}
            className="btn-accent btn-xs disabled:opacity-40">
            保存新密码
          </button>
          {msg && <span className="text-xs text-emerald-600">{msg}</span>}
          {err && <span className="text-xs text-red-600">{err}</span>}
          <button onClick={() => setPwdOpen(false)} className="text-xs text-slate-400 hover:text-slate-600">收起</button>
        </div>
      ) : (
        <button className="text-xs text-slate-500 transition hover:text-slate-800" onClick={() => { setPwdOpen(true); setMsg(''); setErr('') }}>
          修改密码
        </button>
      )}
    </>
  )
}
