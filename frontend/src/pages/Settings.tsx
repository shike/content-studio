import { useCallback, useEffect, useState } from 'react'
import { BrainCircuit, Radar, RefreshCw, Save, Settings as SettingsIcon } from 'lucide-react'
import { api, apiErrText } from '../api'
import { BrandForm, type BrandShape } from '../brandForm'
import { Busy, ErrorLine, PageHeader } from '../components'
import { usePageGuard } from '../hooks'

interface AppSettings {
  llm_model: string
  llm_configured: boolean
  zhipu_api_key_set: boolean
  zhipu_base_url: string
  coding_plan: boolean
  search_provider: string
  watch_scan_enabled: boolean
  watch_scan_hour: number
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
    api<{ brand?: Partial<BrandShape> }>('/tenant/brand')
      .then((d) => setOwnBrand(d.brand ?? {}))
      .catch(() => setOwnBrand({}))
  }, [])
  const saveOwnBrand = (payload: Partial<BrandShape>) =>
    api('/tenant/brand', { method: 'PUT', body: JSON.stringify(payload) })

  const [crawl, setCrawl] = useState<{
    last: { status: string; detail?: string; at?: string } | null
    history: { status: string; at: string }[]
    label: string
  } | null>(null)
  const [crawlBusy, setCrawlBusy] = useState(false)
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [keyMsg, setKeyMsg] = useState('')
  const [search, setSearch] = useState('')
  const [scanEnabled, setScanEnabled] = useState(true)
  const [scanHour, setScanHour] = useState(1)
  const [pingReply, setPingReply] = useState('')

  // 页面守卫（hooks.ts）：错误统一 apiErrText 人话，busy 由 hook 持有并 finally 复位
  const { guard, busy, setBusy, error, setError } = usePageGuard()

  const reload = useCallback(async () => {
    const c = await api<AppSettings>('/settings')
    setConf(c)
    try {
      const c = await api<{ last: { status: string; detail?: string; at?: string } | null; history: { status: string; at: string }[]; label: string }>('/crawl/status')
      setCrawl(c)
    } catch {
      setCrawl(null)
    }
    setModel(c.llm_model)
    setSearch(c.search_provider)
    setScanEnabled(c.watch_scan_enabled)
    setScanHour(c.watch_scan_hour)
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(apiErrText(e)))
  }, [reload])

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
      const body: Record<string, unknown> = { llm_model: model, search_provider: search, watch_scan_enabled: scanEnabled, watch_scan_hour: scanHour }
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

      <PublishTargetsCard />
      <PlaybookCard />
      <JudgementLinesCard />

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
                      setKeyMsg(apiErrText(e))
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
              <div className="text-xs font-medium text-slate-500">联网检索通道（账号发现用）</div>
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
                    const c = await api<{ last: { status: string; detail?: string; at?: string } | null; history: { status: string; at: string }[]; label: string }>('/crawl/status')
                    setCrawl(c)
                    setError('')
                  } catch (e) {
                    setError(apiErrText(e))
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
            </div>
          </div>
        </div>
      )}

      {/* 保存条 */}
      <div className="fixed inset-x-0 bottom-5 z-40 flex justify-center px-4">
        {conf && (model !== conf.llm_model || search !== conf.search_provider || scanEnabled !== conf.watch_scan_enabled || scanHour !== conf.watch_scan_hour) && (
          <div className="flex items-center gap-3 rounded-2xl bg-slate-900 px-5 py-3 text-sm text-slate-100 shadow-xl">
            <span>
              待保存：<b className="text-sky-400">{model}</b>
              {search !== conf.search_provider && <b className="text-sky-400"> · 检索 {search}</b>}
              {(scanEnabled !== conf.watch_scan_enabled || scanHour !== conf.watch_scan_hour) && <b className="text-sky-400"> · 扫描周期</b>}
            </span>
            <button onClick={save} className="btn-accent btn-xs">
              <Save size={13} /> 保存
            </button>
            <button onClick={() => { setModel(conf.llm_model); setSearch(conf.search_provider); setScanEnabled(conf.watch_scan_enabled); setScanHour(conf.watch_scan_hour) }} className="text-xs text-slate-400 hover:text-slate-200">
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
      setErr(apiErrText(e))
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


/** 创作手册（R2-1）：三线系统规范速览（只读）+ 号主附加手册（可编辑，生成时注入）。 */
const PL: { id: 'script' | 'note' | 'article'; label: string }[] = [
  { id: 'script', label: '口播' },
  { id: 'note', label: '小红书' },
  { id: 'article', label: '公众号' },
]
const PB_LABEL: Record<string, string> = { script: '口播', note: '小红书', article: '公众号' }

function PlaybookCard() {
  const [data, setData] = useState<Record<string, { brief: string[]; mine: string }> | null>(null)
  const [tab, setTab] = useState<'script' | 'note' | 'article'>('script')
  const [draft, setDraft] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api<Record<string, { brief: string[]; mine: string }>>('/settings/playbook')
      .then((d) => { setData(d); setDraft(d[tab]?.mine || '') })
      .catch((e) => setErr(apiErrText(e)))
  }, [tab])
  useEffect(load, [load])

  const save = async () => {
    setErr(''); setMsg('')
    try {
      const d = await api<Record<string, { brief: string[]; mine: string }>>('/settings/playbook', {
        method: 'PUT', body: JSON.stringify({ [tab]: draft }),
      })
      setData(d); setMsg('已保存，下次生成生效')
    } catch (e) { setErr(apiErrText(e)) }
  }

  const cur = data?.[tab]
  return (
    <div className="card mb-4 space-y-3 p-5">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <Radar size={16} className="text-emerald-600" /> 创作手册
        <span className="text-xs font-normal text-slate-400">你的附加规则生成时注入，优先级高于系统规范；与合规红线冲突时红线赢</span>
      </div>
      <div className="flex items-center gap-1.5">
        {PL.map((x) => (
          <button key={x.id} onClick={() => setTab(x.id)}
            className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${tab === x.id ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
            {x.label}
          </button>
        ))}
      </div>
      {cur && (
        <>
          <div className="rounded-lg bg-slate-50 px-3 py-2.5">
            <div className="text-[11px] font-medium tracking-wide text-slate-400">{PB_LABEL[tab]}线系统规范（只读速览，完整规范随提示词发版）</div>
            <ul className="mt-1.5 list-disc space-y-0.5 pl-4 text-[12.5px] leading-relaxed text-slate-600">
              {cur.brief.map((x, i) => <li key={i}>{x}</li>)}
            </ul>
          </div>
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={5}
            placeholder={`写你的${PB_LABEL[tab]}线附加规则（每行一条；例：结尾永远停在一个事实上；禁用词：躺赚）。留空=只用系统规范。`}
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-[13px] leading-relaxed outline-none focus:border-emerald-400" />
          <div className="flex items-center gap-3">
            <button onClick={save} className="btn-accent btn-sm"><Save size={13} /> 保存手册</button>
            {msg && <span className="text-[12px] text-emerald-600">{msg}</span>}
            {err && <span className="text-[12px] text-red-600">{err}</span>}
          </div>
        </>
      )}
    </div>
  )
}

/** 判卷线（R2-3）：六条线设置可调（AppSetting judgement_lines 覆盖，缺省=调研假设常数），PUT 即生效。 */
function JudgementLinesCard() {
  const [cur, setCur] = useState<Record<string, number> | null>(null)
  const [defaults, setDefaults] = useState<Record<string, number>>({})
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api<{ current: Record<string, number>; defaults: Record<string, number> }>('/settings/judgement-lines')
      .then((d) => {
        setCur(d.current); setDefaults(d.defaults)
        setDraft(Object.fromEntries(Object.entries(d.current).map(([k, v]) => [k, String(v)])))
      })
      .catch((e) => setErr(apiErrText(e)))
  }, [])
  useEffect(load, [load])

  const save = async () => {
    setErr(''); setMsg('')
    const lines: Record<string, number> = {}
    for (const [k, v] of Object.entries(draft)) {
      const n = Number(v)
      if (Number.isFinite(n) && n > 0 && cur && n !== cur[k]) lines[k] = n
    }
    if (!Object.keys(lines).length) { setMsg('没有改动'); return }
    try {
      const d = await api<{ current: Record<string, number> }>('/settings/judgement-lines', {
        method: 'PUT', body: JSON.stringify({ lines }),
      })
      setCur(d.current); setMsg('已生效（判卷/范本收割/晨报立即跟新线）')
    } catch (e) { setErr(apiErrText(e)) }
  }

  const FIELDS: { k: string; label: string; hint: string }[] = [
    { k: 'viral_play', label: '抖音爆款线（播放）', hint: '达线=进范本库' },
    { k: 'pass_play', label: '抖音过线（播放）', hint: '' },
    { k: 'xhs_viral_rate', label: '小红书爆文线（互动率 %）', hint: '存小数，如 0.03' },
    { k: 'xhs_pass_rate', label: '小红书过线（互动率 %）', hint: '' },
    { k: 'wechat_viral_rate', label: '公众号优质线（完读率 %）', hint: '存小数，如 0.65' },
    { k: 'wechat_pass_rate', label: '公众号合格线（完读率 %）', hint: '' },
  ]

  return (
    <div className="card mb-4 space-y-3 p-5">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <BrainCircuit size={16} className="text-violet-500" /> 判卷线
        <span className="text-xs font-normal text-slate-400">默认=调研假设值；跑 2~4 周后按自家数据分布改</span>
      </div>
      {cur && (
        <>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3">
            {FIELDS.map((f) => (
              <label key={f.k} className="block">
                <div className="text-xs text-slate-400">{f.label}</div>
                <input value={draft[f.k] ?? ''} onChange={(e) => setDraft({ ...draft, [f.k]: e.target.value })}
                  inputMode="decimal"
                  className="mt-0.5 w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                <div className="mt-0.5 text-[11px] text-slate-300">
                  {f.hint || `默认 ${defaults[f.k]}`}
                </div>
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={save} className="btn-accent btn-sm"><Save size={13} /> 保存判卷线</button>
            <button onClick={() => { setDraft(Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, String(v)]))) }}
              className="btn-ghost btn-sm">恢复默认值</button>
            {msg && <span className="text-[12px] text-emerald-600">{msg}</span>}
            {err && <span className="text-[12px] text-red-600">{err}</span>}
          </div>
        </>
      )}
    </div>
  )
}


/** 发布节奏（R3-1）：每周各平台计划发布条数（0=不限）；今天页晨报按它报偏差。 */
function PublishTargetsCard() {
  const [cur, setCur] = useState<Record<string, number> | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(() => {
    api<{ targets: Record<string, number> }>('/settings/publish-targets')
      .then((d) => {
        setCur(d.targets)
        setDraft(Object.fromEntries(Object.entries(d.targets).map(([k, v]) => [k, String(v)])))
      })
      .catch((e) => setErr(apiErrText(e)))
  }, [])
  useEffect(load, [load])

  const save = async () => {
    setErr(''); setMsg('')
    const targets: Record<string, number> = {}
    for (const [k, v] of Object.entries(draft)) {
      const n = parseInt(v, 10)
      if (!Number.isFinite(n) || n < 0 || n > 99) { setErr('节奏必须是 0~99 的整数'); return }
      targets[k] = n
    }
    try {
      const d = await api<{ targets: Record<string, number> }>('/settings/publish-targets', {
        method: 'PUT', body: JSON.stringify({ targets }),
      })
      setCur(d.targets)
      setMsg('已生效（今天页晨报开始按目标报偏差）')
    } catch (e) { setErr(apiErrText(e)) }
  }

  const FIELDS: { k: string; label: string }[] = [
    { k: 'douyin_week', label: '抖音 · 每周条数' },
    { k: 'wechat_week', label: '公众号 · 每周篇数' },
    { k: 'xhs_week', label: '小红书 · 每周篇数' },
  ]
  return (
    <div className="card mb-4 space-y-3 p-5">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <Radar size={16} className="text-sky-500" /> 发布节奏
        <span className="text-xs font-normal text-slate-400">每周目标条数（0=不限）；实发在「数据 · 排期」看，晨报报偏差</span>
      </div>
      {cur && (
        <>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {FIELDS.map((f) => (
              <label key={f.k} className="block">
                <div className="text-xs text-slate-400">{f.label}</div>
                <input value={draft[f.k] ?? ''} onChange={(e) => setDraft({ ...draft, [f.k]: e.target.value })}
                  inputMode="numeric" placeholder="0=不限"
                  className="mt-0.5 w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
              </label>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={save} className="btn-accent btn-sm"><Save size={13} /> 保存节奏</button>
            {msg && <span className="text-[12px] text-emerald-600">{msg}</span>}
            {err && <span className="text-[12px] text-red-600">{err}</span>}
          </div>
        </>
      )}
    </div>
  )
}
