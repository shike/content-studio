import { useCallback, useEffect, useState } from 'react'
import { Plus, RefreshCw, Trash2, UserRound } from 'lucide-react'
import { api, apiErrText } from '../api'
import { EmptyState, ErrorLine, relTime } from '../components'

/**
 * 数据 · 账号（R1-3 2026-11-22）：四渠道自有账号登记/编辑/指标历史/手动同步。
 * cookie 只写不读（红线）：表单 password 框，列表只回显 has_auth 徽章。
 * 抖音账号在画像页另有一份管理入口（/watch/accounts 同一实体），此处是全渠道视图。
 */

interface AccountRow {
  id: number
  platform: string
  name: string
  handle: string
  profile_url: string
  enabled: boolean
  last_sync_at: string | null
  has_auth: boolean
  note: string
}
interface MetricRow { date: string; fans: number; play: number; leads: number }

const PLATFORMS = [
  { id: 'douyin', label: '抖音' },
  { id: 'wechat', label: '公众号' },
  { id: 'xiaohongshu', label: '小红书' },
  { id: 'channels', label: '视频号' },
]
const PL_LABEL: Record<string, string> = Object.fromEntries(PLATFORMS.map((p) => [p.id, p.label]))

const PL_BADGE: Record<string, string> = {
  douyin: 'bg-slate-900 text-white', wechat: 'bg-emerald-100 text-emerald-700',
  xiaohongshu: 'bg-rose-100 text-rose-600', channels: 'bg-sky-100 text-sky-700',
}

export default function AccountAdmin() {
  const [rows, setRows] = useState<AccountRow[]>([])
  const [filter, setFilter] = useState('')
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [busy, setBusy] = useState(false)
  // 登记表单
  const [form, setForm] = useState({ platform: 'douyin', name: '', handle: '', profile_url: '', note: '', creator_cookie: '' })
  // 行内编辑
  const [editId, setEditId] = useState<number | null>(null)
  const [edit, setEdit] = useState({ name: '', handle: '', profile_url: '', note: '', creator_cookie: '' })
  // 指标历史
  const [mAcc, setMAcc] = useState<number | null>(null)
  const [mRows, setMRows] = useState<MetricRow[]>([])

  const load = useCallback(() => {
    api<{ items: AccountRow[] }>('/accounts')
      .then((d) => { setRows(d.items || []); setLoaded(true) })
      .catch((e) => { setError(apiErrText(e)); setLoaded(true) })
  }, [])
  useEffect(() => { load() }, [load])

  const create = async () => {
    setError('')
    setBusy(true)
    try {
      await api('/accounts', {
        method: 'POST',
        body: JSON.stringify({ ...form, name: form.name.trim() }),
      })
      setForm({ platform: form.platform, name: '', handle: '', profile_url: '', note: '', creator_cookie: '' })
      load()
    } catch (e) { setError(apiErrText(e)) } finally { setBusy(false) }
  }

  const saveEdit = async (id: number) => {
    setError('')
    try {
      const patch: Record<string, string> = {}
      if (edit.name.trim()) patch.name = edit.name.trim()
      if (edit.handle.trim()) patch.handle = edit.handle.trim()
      if (edit.profile_url.trim()) patch.profile_url = edit.profile_url.trim()
      if (edit.note.trim()) patch.note = edit.note.trim()
      if (edit.creator_cookie.trim()) patch.creator_cookie = edit.creator_cookie.trim()
      if (Object.keys(patch).length) {
        await api(`/accounts/${id}`, { method: 'PUT', body: JSON.stringify(patch) })
      }
      setEditId(null); setEdit({ name: '', handle: '', profile_url: '', note: '', creator_cookie: '' })
      load()
    } catch (e) { setError(apiErrText(e)) }
  }

  const removeOne = async (id: number) => {
    setError('')
    try { await api(`/accounts/${id}`, { method: 'DELETE' }); load() }
    catch (e) { setError(apiErrText(e)) }
  }

  const syncOne = async (id: number) => {
    setError('')
    try { await api(`/accounts/${id}/sync`, { method: 'POST' }); load() }
    catch (e) { setError(apiErrText(e)) }
  }

  const openMetrics = async (id: number) => {
    if (mAcc === id) { setMAcc(null); return }
    setMAcc(id); setMRows([])
    try {
      const d = await api<{ items: MetricRow[] }>(`/accounts/${id}/metrics?days=30`)
      setMRows((d.items || []).slice(-14))
    } catch (e) { setError(apiErrText(e)) }
  }

  const shown = filter ? rows.filter((r) => r.platform === filter) : rows

  return (
    <div>
      <ErrorLine text={error} />
      {/* 平台 chips */}
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <button onClick={() => setFilter('')}
          className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${filter === '' ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
          全部 {rows.length}
        </button>
        {PLATFORMS.map((p) => {
          const n = rows.filter((r) => r.platform === p.id).length
          return (
            <button key={p.id} onClick={() => setFilter(p.id)}
              className={`rounded-full px-3 py-1 text-[12.5px] font-medium transition ${filter === p.id ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
              {p.label} {n}
            </button>
          )
        })}
      </div>

      {/* 登记表单 */}
      <div className="mb-4 rounded-xl border border-[#E8ECF0] bg-white px-4 py-3">
        <div className="mb-2 text-[12px] font-medium tracking-wide text-slate-400">
          登记自有账号（判卷/账号趋势/创作者数据扫描的数据主体）
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })}
            className="rounded-lg border border-[#E8ECF0] bg-white px-2.5 py-1.5 text-[13px] outline-none focus:border-emerald-400">
            {PLATFORMS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="账号名 *"
            className="w-36 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={form.handle} onChange={(e) => setForm({ ...form, handle: e.target.value })} placeholder="handle/ID"
            className="w-36 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          <input value={form.profile_url} onChange={(e) => setForm({ ...form, profile_url: e.target.value })} placeholder="主页链接"
            className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          {form.platform === 'douyin' && (
            <input value={form.creator_cookie} onChange={(e) => setForm({ ...form, creator_cookie: e.target.value })}
              type="password" placeholder="创作者中心 cookie（只写不读）"
              className="w-56 rounded-lg border border-[#E8ECF0] px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
          )}
          <button onClick={create} disabled={busy || !form.name.trim()}
            className="rounded-full bg-[#0a2f24] px-3.5 py-1.5 text-[12.5px] font-medium text-[#f0e9d5] transition hover:opacity-90 disabled:opacity-40">
            <Plus size={13} className="inline" /> 登记
          </button>
        </div>
      </div>

      {!loaded ? null : shown.length === 0 ? (
        <EmptyState icon={UserRound} title="还没有登记账号"
          desc="登记四渠道自有账号后，账号趋势/单号历史/手动同步才有主体；抖音账号可配创作者中心 cookie（数据回流的钥匙）。" />
      ) : (
        <div className="space-y-2">
          {shown.map((a) => {
            const open = editId === a.id
            return (
              <div key={a.id}
                className={`cursor-pointer rounded-xl border bg-white px-4 py-3 transition ${open ? 'border-emerald-300' : 'border-[#E8ECF0] hover:border-slate-300'}`}
                onClick={() => { setEditId(open ? null : a.id); setEdit({ name: a.name, handle: a.handle, profile_url: a.profile_url, note: '', creator_cookie: '' }); if (mAcc === a.id) setMAcc(null) }}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${PL_BADGE[a.platform] || 'bg-slate-100 text-slate-600'}`}>
                    {PL_LABEL[a.platform] || a.platform}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[14px] font-semibold text-slate-900">{a.name}</span>
                  {a.handle && <span className="shrink-0 text-[12px] text-slate-400">{a.handle}</span>}
                  {a.platform === 'douyin' && (
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] ${a.has_auth ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-800'}`}>
                      {a.has_auth ? 'cookie 已配' : 'cookie 未配'}
                    </span>
                  )}
                  <span className="shrink-0 text-[11px] text-slate-400">
                    {a.last_sync_at ? `同步 ${relTime(a.last_sync_at)}` : '未同步'}
                  </span>
                </div>
                {open && (
                  <div className="mt-3 space-y-2.5 border-t border-[#E8ECF0] pt-3" onClick={(e) => e.stopPropagation()}>
                    <div className="flex flex-wrap items-center gap-2">
                      <input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })}
                        placeholder="账号名" className="w-36 rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                      <input value={edit.handle} onChange={(e) => setEdit({ ...edit, handle: e.target.value })}
                        placeholder="handle（空=不改）" className="w-36 rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                      <input value={edit.profile_url} onChange={(e) => setEdit({ ...edit, profile_url: e.target.value })}
                        placeholder="主页链接（空=不改）" className="w-56 rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                      {a.platform === 'douyin' && (
                        <input value={edit.creator_cookie} onChange={(e) => setEdit({ ...edit, creator_cookie: e.target.value })}
                          type="password" placeholder="新 cookie（空=不改）"
                          className="w-56 rounded-lg border border-slate-200 px-3 py-1.5 text-[13px] outline-none focus:border-emerald-400" />
                      )}
                      <button onClick={() => saveEdit(a.id)} className="btn-accent btn-sm">保存</button>
                      <button onClick={() => syncOne(a.id)} className="btn-ghost btn-sm">
                        <RefreshCw size={12} className="inline" /> 手动同步
                      </button>
                      <button onClick={() => openMetrics(a.id)} className="btn-ghost btn-sm">指标历史</button>
                      <button onClick={() => removeOne(a.id)} className="btn-ghost btn-sm text-red-500">
                        <Trash2 size={12} className="inline" /> 删除
                      </button>
                    </div>
                    {mAcc === a.id && (
                      mRows.length === 0 ? (
                        <div className="text-[12.5px] text-slate-400">近 30 天没有指标快照（创作者数据扫描回流或手动补录后可见）</div>
                      ) : (
                        <table className="w-full text-[12px]">
                          <thead>
                            <tr className="text-left text-slate-400">
                              <th className="py-1 font-medium">日期</th><th className="py-1 font-medium">粉丝</th>
                              <th className="py-1 font-medium">播放</th><th className="py-1 font-medium">线索</th>
                            </tr>
                          </thead>
                          <tbody className="num-serif tabular-nums text-slate-700">
                            {mRows.map((m) => (
                              <tr key={m.date} className="border-t border-slate-100">
                                <td className="py-1">{m.date}</td><td className="py-1">{m.fans.toLocaleString()}</td>
                                <td className="py-1">{m.play.toLocaleString()}</td><td className="py-1">{m.leads}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
