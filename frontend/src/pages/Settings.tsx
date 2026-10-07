import { useCallback, useEffect, useState } from 'react'
import { BrainCircuit, FlaskConical, Globe, RefreshCw, Save, Settings as SettingsIcon } from 'lucide-react'
import { api } from '../api'
import { BrandForm, type BrandShape } from '../brandForm'
import { Busy, ErrorLine, PageHeader } from '../components'

interface AppSettings {
  llm_provider: string
  llm_model: string
  llm_configured: boolean
  zhipu_api_key_set: boolean
  deepseek_api_key_set: boolean
  zhipu_base_url: string
  coding_plan: boolean
  search_provider: string
  search_enabled: boolean
  asr_model: string
  watch_scan_enabled: boolean
  watch_scan_hour: number
  watch_discover_keywords: string
  chanjing_configured: boolean
}

interface UsageItem {
  purpose: string
  calls: number
  tokens_in: number
  tokens_out: number
  cost_est: number
}

interface Usage {
  calls: number
  tokens_in: number
  tokens_out: number
  cost_est: number
  by_purpose?: UsageItem[]
}

interface AvatarUsage {
  videos: number
  seconds: number
  points: number   // 租户侧按秒结算的积分合计
  beans?: number   // 蝉镜豆（平台成本口径，仅平台管理员返回）
  yuan?: number
}

const MODELS = ['glm-5', 'glm-5-turbo', 'glm-4.7', 'glm-5.3', 'glm-5.3-flash', 'deepseek-chat', 'deepseek-reasoner']
const SEARCH_PROVIDERS: { key: string; label: string; desc: string }[] = [
  { key: 'ddg', label: 'DuckDuckGo', desc: '免费、免 key（有限流，自动缓存+退避）' },
  { key: 'auto', label: '自动（优先 GLM）', desc: '有智谱按量余额时用 GLM 联网搜索，失败转 DDG' },
  { key: 'glm', label: '强制 GLM', desc: '仅按量付费通道支持（Coding Plan 套餐不含 web_search）' },
]

export default function Settings({ role = 'member' }: { role?: string }) {
  const isPlatform = role === 'platform_admin'
  const [ownBrand, setOwnBrand] = useState<Partial<BrandShape> | null>(null)
  const [ownBrandTenant, setOwnBrandTenant] = useState('')
  useEffect(() => {
    if (role === 'member') return
    api<{ brand?: Partial<BrandShape>; tenant_name?: string }>('/tenant/brand')
      .then((d) => { setOwnBrand(d.brand ?? {}); setOwnBrandTenant(d.tenant_name ?? '') })
      .catch(() => setOwnBrand({}))
  }, [role])
  const saveOwnBrand = async (payload: BrandShape) => {
    await api('/tenant/brand', { method: 'PUT', body: JSON.stringify(payload) })
  }
  const [conf, setConf] = useState<AppSettings | null>(null)
  const [usage, setUsage] = useState<Usage | null>(null)
  const [avatarUsage, setAvatarUsage] = useState<AvatarUsage | null>(null)
  // 抖音采集探针（每 15 分钟自动跑一次；手动可立即探测）
  const [crawl, setCrawl] = useState<{
    last: { status: string; detail?: string; at?: string } | null
    history: { status: string; at: string }[]
    label: string
    recent_failures: number
  } | null>(null)
  const [crawlBusy, setCrawlBusy] = useState(false)
  // 数字人服务商成本（平台管理员专属）：豆余额/凭证
  const [svcBalance, setSvcBalance] = useState<number | null>(null)
  const [svcAppId, setSvcAppId] = useState('')
  const [svcSecret, setSvcSecret] = useState('')
  const [svcBusy, setSvcBusy] = useState(false)
  const [usageAll, setUsageAll] = useState(false)
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [keyMsg, setKeyMsg] = useState('')
  const [search, setSearch] = useState('')
  const [scanEnabled, setScanEnabled] = useState(true)
  const [scanHour, setScanHour] = useState(1)
  const [discoverKw, setDiscoverKw] = useState('')
  const [pingReply, setPingReply] = useState('')
  const [probe, setProbe] = useState<{
    query: string
    glm: { http?: number; web_search_field?: unknown; content_head?: string; error?: string }
    glm_verdict: string
    ddg: { status?: string; results?: { title: string; url: string }[] }
    ddg_verdict: string
  } | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const [c, u, a] = await Promise.all([
      api<AppSettings>('/settings'),
      api<Usage>('/llm/usage'),
      api<AvatarUsage>('/avatar/usage').catch(() => null),
    ])
    setConf(c)
    setUsage(u)
    setAvatarUsage(a)
    try {
      const c = await api<{ last: { status: string; detail?: string; at?: string } | null; history: { status: string; at: string }[]; label: string; recent_failures: number }>('/crawl/status')
      setCrawl(c)
    } catch {
      setCrawl(null)
    }
    if (isPlatform) {
      try {
        const b = await api<{ beans: number }>('/avatar/balance')
        setSvcBalance(b.beans)   // 服务商成本余额（仅平台管理员接口）
      } catch {
        setSvcBalance(null)
      }
    }
    setModel(c.llm_model)
    setSearch(c.search_provider)
    setScanEnabled(c.watch_scan_enabled)
    setScanHour(c.watch_scan_hour)
    setDiscoverKw(c.watch_discover_keywords ?? '')
  }, [isPlatform])

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

  const searchProbe = () =>
    guard(async () => {
      setBusy('检索通道实测中（同一查询分别走 GLM 与 DDG）…')
      const r = await api<NonNullable<typeof probe>>('/llm/search-probe', {
        method: 'POST',
        body: JSON.stringify({ query: '智谱 GLM 最新发布 模型' }),
      })
      setProbe(r)
    })

  const save = () =>
    guard(async () => {
      if (!conf) return
      setBusy('保存中（运行时生效并写入 .env）…')
      const body: Record<string, unknown> = { llm_model: model, search_provider: search, watch_scan_enabled: scanEnabled, watch_scan_hour: scanHour, watch_discover_keywords: discoverKw }
      const r = await api<AppSettings>('/settings', { method: 'PUT', body: JSON.stringify(body) })
      setConf(r)
      setPingReply('')
    })

  return (
    <div>
      <PageHeader
        icon={SettingsIcon}
        title="设置"
        desc="品牌人设 · LLM 通道 · 联网检索 · 用量台账"
      />

      {busy && <div className="mb-3"><Busy text={busy} /></div>}
      {error && <div className="mb-3"><ErrorLine text={error} /></div>}

      {role !== 'member' && (
        <div className="card mb-4 space-y-3 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-sm font-semibold text-slate-800">品牌与内容人设</div>
            {ownBrandTenant && <span className="badge bg-slate-100 text-slate-600">当前租户：{ownBrandTenant}</span>}
            <span className="text-[11px] text-slate-400">角标栏目、头图署名/口号、内容人设、受众域、主色与 ASR 词表——保存后新出片/新文章按此渲染</span>
          </div>
          {/* 必须等配置取回再渲染表单：BrandForm 的 useState 只在首挂载取初值，
          带 {} 先挂载会停留在空字段（2026-10-03 用户报设置里配置为空的根因） */}
          {ownBrand === null ? <Busy text="品牌配置加载中…" /> : <BrandForm initial={ownBrand} onSave={saveOwnBrand} />}
        </div>
      )}

      {conf && (
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
          {/* LLM 通道 */}
          <div className="card space-y-4 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <BrainCircuit size={16} className="text-sky-500" /> LLM 通道
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-slate-400">服务商</div>
                <div className="mt-0.5 font-medium text-slate-800">
                  {conf.llm_provider === 'glm' ? '智谱 GLM' : conf.llm_provider}
                  {conf.llm_configured ? (
                    <span className="badge ml-2 bg-emerald-100 text-emerald-700">已配置</span>
                  ) : (
                    <span className="badge ml-2 bg-red-100 text-red-600">缺密钥</span>
                  )}
                </div>
              </div>
              <div>
                <div className="text-xs text-slate-400">端点</div>
                <div className="mt-0.5 truncate font-medium text-slate-800" title={conf.zhipu_base_url}>
                  {conf.coding_plan ? 'Coding Plan 套餐' : '按量付费 PaaS'}
                </div>
              </div>
            </div>
            <label className="block space-y-1">
              <span className="text-xs text-slate-400">
                API Key（只写不读回；存数据库，随每日备份）
                {conf.zhipu_api_key_set
                  ? <span className="badge ml-2 bg-emerald-100 text-emerald-700">已配置</span>
                  : <span className="badge ml-2 bg-amber-100 text-amber-700">未配置</span>}
              </span>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder={conf.zhipu_api_key_set ? '••••••（已设置，粘贴新值可覆盖；留空不修改）' : '粘贴智谱 API Key'}
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
              <span className="text-xs text-slate-400">写作模型（切换立即生效，存入数据库）</span>
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
            {conf.coding_plan && (
              <div className="rounded-lg bg-sky-50/70 px-3 py-2 text-xs leading-relaxed text-sky-800">
                当前 LLM 生成走 Coding Plan 套餐端点（额度内免费）；联网检索与此解耦——
                {isPlatform ? '走智谱按量搜索端点独立计费（约 0.01 元/次），DDG 为兜底。' : '检索走智谱搜索端点，DDG 为兜底。'}
              </div>
            )}
          </div>

          {/* 联网检索 */}
          <div className="card space-y-4 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
              <Globe size={16} className="text-sky-500" /> 联网检索通道
            </div>
            <div className="space-y-2">
              {SEARCH_PROVIDERS.map((p) => (
                <button
                  key={p.key}
                  onClick={() => setSearch(p.key)}
                  className={`flex w-full flex-col rounded-xl border px-4 py-2.5 text-left transition ${
                    search === p.key
                      ? 'border-sky-400 bg-sky-50/50 ring-2 ring-sky-200'
                      : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <span className="text-sm font-medium text-slate-800">{p.label}</span>
                  <span className="mt-0.5 text-xs text-slate-400">{p.desc}</span>
                </button>
              ))}
            </div>
            <details className="rounded-xl border border-slate-200 px-3.5 py-3">
              <summary className="flex cursor-pointer items-center gap-2 text-xs font-medium text-slate-500">
                <FlaskConical size={13} /> 检索 A/B 实测（同一查询分别走 GLM 与 DDG）
              </summary>
              <button onClick={searchProbe} className="btn-ghost btn-xs mt-3">开始实测</button>
              {probe && (
                <div className="mt-3 space-y-2 text-xs">
                  <div className="font-medium text-slate-600">查询：{probe.query}</div>
                  <div className={`rounded-lg px-3 py-2 leading-relaxed ${probe.glm_verdict.includes('有') ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>
                    <b>GLM（{conf?.coding_plan ? '套餐端点' : '按量端点'}）：{probe.glm_verdict}</b>
                    <div className="mt-1 text-slate-500">web_search 字段：{probe.glm.web_search_field ? JSON.stringify(probe.glm.web_search_field).slice(0, 200) : 'null（响应中无搜索来源）'}</div>
                    {probe.glm.content_head && (
                      <details className="mt-1">
                        <summary className="cursor-pointer text-sky-600">模型回答开头</summary>
                        <div className="mt-1 text-slate-600">{probe.glm.content_head}</div>
                      </details>
                    )}
                  </div>
                  <div className={`rounded-lg px-3 py-2 leading-relaxed ${probe.ddg.results?.length ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>
                    <b>DDG：{probe.ddg_verdict}</b>
                    {probe.ddg.results?.length ? (
                      <ul className="mt-1 space-y-0.5 text-slate-600">
                        {probe.ddg.results.slice(0, 3).map((r2) => (
                          <li key={r2.url}>· {r2.title}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                </div>
              )}
            </details>
          </div>

          {/* 用量台账 */}
          <div className="card space-y-4 p-5">
            <div className="text-sm font-semibold text-slate-800">LLM 用量台账（本租户累计）</div>
            {usage && (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { label: '调用次数', value: String(usage.calls) },
                  { label: '输入 tokens', value: usage.tokens_in.toLocaleString() },
                  { label: '输出 tokens', value: usage.tokens_out.toLocaleString() },
                  ...(isPlatform ? [{ label: '成本估算', value: `¥${usage.cost_est}` }] : []),
                ].map((x) => (
                  <div key={x.label} className="rounded-xl bg-slate-50 px-3.5 py-3">
                    <div className="text-lg font-bold tabular-nums text-slate-900">{x.value}</div>
                    <div className="mt-0.5 text-[11px] text-slate-400">{x.label}</div>
                  </div>
                ))}
              </div>
            )}
            {usage?.by_purpose && usage.by_purpose.length > 0 && (
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <div className="text-xs font-semibold text-slate-500">按用途分账（成本降序）</div>
                  {usage.by_purpose.length > 10 && (
                    <button
                      onClick={() => setUsageAll((v) => !v)}
                      className="text-[11px] text-sky-600 hover:underline"
                    >
                      {usageAll ? '收起' : `展开全部 ${usage.by_purpose.length} 项`}
                    </button>
                  )}
                </div>
                {(usageAll ? usage.by_purpose : usage.by_purpose.slice(0, 10)).map((u) => (
                  <div key={u.purpose} className="flex items-center gap-3 rounded-lg bg-slate-50 px-3 py-1.5 text-xs">
                    <span className="w-32 shrink-0 truncate font-medium text-slate-700">{u.purpose}</span>
                    <span className="tabular-nums text-slate-500">{u.calls} 次</span>
                    <span className="tabular-nums text-slate-400">{(u.tokens_in + u.tokens_out).toLocaleString()} tok</span>
                    {isPlatform && <span className="ml-auto tabular-nums font-medium text-slate-700">¥{u.cost_est}</span>}
                  </div>
                ))}
              </div>
            )}
            {avatarUsage && (
              <div className="flex items-center gap-3 rounded-lg bg-sky-50/60 px-3 py-1.5 text-xs">
                <span className="w-32 shrink-0 font-medium text-slate-700">数字人（按秒）</span>
                <span className="tabular-nums text-slate-500">{avatarUsage.videos} 条成片</span>
                <span className="tabular-nums text-slate-400">{Math.round(avatarUsage.seconds)}s</span>
                <span className="ml-auto tabular-nums font-medium text-slate-700">
                  {isPlatform
                    ? <>{avatarUsage.beans ?? 0} 豆{avatarUsage.yuan != null && ` ≈ ¥${avatarUsage.yuan}`}<span className="ml-2 text-slate-400">租户侧记 {avatarUsage.points} 积分</span></>
                    : <>{avatarUsage.points} 积分</>}
                </span>
              </div>
            )}
            <div className="text-xs leading-relaxed text-slate-400">
              成本为牌价估算；套餐额度内不实际扣费。
            </div>
          </div>

          {/* 数据采集状态：探针 15 分钟一轮 + 手动立即探测 */}
          <div className="card space-y-3 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <div className="text-sm font-semibold text-slate-800">数据采集（抖音）</div>
              {crawl && crawl.last ? (
                <span className={`badge ${crawl.last.status === 'ok' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-800'}`}>
                  {crawl.label}
                </span>
              ) : (
                <span className="badge bg-slate-100 text-slate-500">尚未探测</span>
              )}
              {crawl && crawl.last?.at && <span className="text-[11px] text-slate-400">上次探测 {crawl.last.at}</span>}
              {crawl && crawl.recent_failures > 0 && (
                <span className="text-[11px] text-amber-600">近 1 小时爬取失败 {crawl.recent_failures} 次</span>
              )}
              <button
                onClick={async () => {
                  setCrawlBusy(true)
                  try {
                    const r = await api<{ label: string }>('/crawl/probe', { method: 'POST' })
                    const c = await api<{ last: { status: string; detail?: string; at?: string } | null; history: { status: string; at: string }[]; label: string; recent_failures: number }>('/crawl/status')
                    setCrawl(c)
                    setError('')
                    void r
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
                <span className="text-[11px] text-slate-400">最近 8 次自动探测（✓ 正常 / ✗ 异常）</span>
              </div>
            )}
            {crawl?.last?.detail && (
              <div className="rounded-lg bg-slate-50 px-3 py-2 text-xs leading-relaxed text-slate-600">{crawl.last.detail}</div>
            )}
            <div className="text-xs leading-relaxed text-slate-400">
              每 15 分钟自动用真实视频页探测一次采集链路（隧道 / 浏览器 / 风控）；异常时新拆解可能失败，可走「本地视频拆解」上传。
            </div>
          </div>

          {/* 平台成本 · 数字人服务商（仅平台管理员）：余额/凭证；租户不渲染 */}
          {isPlatform && (
            <div className="card space-y-3 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <div className="text-sm font-semibold text-slate-800">平台成本 · 数字人</div>
                <span className="text-[11px] text-slate-400">平台口径，租户不可见</span>
                {svcBalance != null && (
                  <span className="badge bg-slate-100 text-slate-600">
                    成本余额 {svcBalance} 豆{avatarUsage?.yuan != null && ` ≈ ¥${(svcBalance * 0.03).toFixed(2)}`}
                  </span>
                )}
                {avatarUsage && (
                  <span className="badge bg-slate-100 text-slate-600">
                    累计 {avatarUsage.videos} 条 · {avatarUsage.beans ?? 0} 豆{avatarUsage.yuan != null && ` ≈ ¥${avatarUsage.yuan}`}
                  </span>
                )}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <input value={svcAppId} onChange={(e) => setSvcAppId(e.target.value)} placeholder="数字人服务 App ID" className="input max-w-44" />
                <input type="password" value={svcSecret} onChange={(e) => setSvcSecret(e.target.value)} placeholder="SecretKey（只写不回显）" className="input max-w-52" />
                <button
                  onClick={async () => {
                    if (!svcAppId.trim() || !svcSecret.trim()) return
                    setSvcBusy(true)
                    try {
                      await api('/settings', {
                        method: 'PUT',
                        body: JSON.stringify({ chanjing_app_id: svcAppId.trim(), chanjing_secret_key: svcSecret.trim() }),
                      })
                      setSvcAppId(''); setSvcSecret('')
                      const r = await api<{ beans: number }>('/avatar/balance')
                      setSvcBalance(r.beans)
                    } catch (e) {
                      setError(String(e))
                    } finally {
                      setSvcBusy(false)
                    }
                  }}
                  disabled={svcBusy || !svcAppId.trim() || !svcSecret.trim()}
                  className="btn-accent btn-xs disabled:opacity-40"
                >
                  {svcBusy ? '保存中…' : '保存凭证'}
                </button>
                <span className="text-[11px] text-slate-400">数字人服务商控制台获取；保存后运行时生效</span>
              </div>
            </div>
          )}

          {/* 同行扫描周期 */}
          <div className="card space-y-3 p-5">
            <div className="text-sm font-semibold text-slate-800">同行扫描周期</div>
            <label className="flex items-center gap-2 text-sm text-slate-700">
              <input
                type="checkbox"
                checked={scanEnabled}
                onChange={(e) => setScanEnabled(e.target.checked)}
                className="size-4 accent-sky-500"
              />
              每天定时自动扫描同行监测账号（含自我账号）
            </label>
            <div className="flex items-center gap-2 text-sm text-slate-700">
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
              <span className="text-xs text-slate-400">失败后 1 小时自动重试，最多 3 次</span>
            </div>
            <div className="space-y-1">
              <label className="block text-sm text-slate-700">每日发现的关键词池</label>
              <input
                value={discoverKw}
                onChange={(e) => setDiscoverKw(e.target.value)}
                placeholder="逗号分隔，如：行业动态, 商业观察, 技术应用（按你的行业填）（留空用平台默认）"
                className="input w-full"
              />
              <span className="text-xs text-slate-400">每日 2 点的「同行发现」按天轮换其中一个去挖清单外高赞同行；改词即日生效（下次轮换用新池）</span>
            </div>
            <div className="text-xs leading-relaxed text-slate-400">
              扫描只入库新作品到拆解库「待定夺」，不会自动消耗拆解额度。
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
            <button onClick={() => { setModel(conf.llm_model); setSearch(conf.search_provider);  }} className="text-xs text-slate-400 hover:text-slate-200">
              撤销
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
