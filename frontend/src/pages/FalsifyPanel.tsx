import { useCallback, useEffect, useState } from 'react'
import { ChevronDown, ChevronRight, X } from 'lucide-react'
import { api, apiErrText } from '../api'
import { EmptyState, ErrorLine, relTime } from '../components'
import { usePageGuard } from '../hooks'

/**
 * 实测打假（PRD R2.3，2026-11-18 用户裁决：从「人设号·选题」移到「素材库」独立 tab）：
 * 待实测清单=主张/对象/线索——实测是物理动作，系统管清单与成稿；
 * 状态推进：待实测 → 实测中 → 已实测（结论必填）→ 成稿（实测稿模板）→ 已成稿；
 * 工场每天自动猎取候选（只猎一人可验证的）。
 */

interface FalsifyItem {
  id: number
  claim: string
  target: string
  evidence_note: string
  result_note: string
  status: string // pending | testing | tested | scripted | dismissed
  auto_source: boolean
  created_at: string
}

const FSTATUS: Record<string, { label: string; cls: string }> = {
  pending: { label: '待实测', cls: 'bg-slate-100 text-slate-600' },
  testing: { label: '实测中', cls: 'bg-amber-100 text-amber-800' },
  tested: { label: '已实测', cls: 'bg-emerald-100 text-emerald-700' },
  scripted: { label: '已成稿', cls: 'bg-sky-100 text-sky-700' },
}

export default function FalsifyPanel({ onNavigate }: { onNavigate?: (k: string) => void }) {
  const [items, setItems] = useState<FalsifyItem[]>([])
  const [expanded, setExpanded] = useState<number | null>(null)
  const [form, setForm] = useState({ evidence_note: '', result_note: '' })
  const [addOpen, setAddOpen] = useState(false)
  const [fresh, setFresh] = useState({ claim: '', target: '', evidence_note: '' })
  const [msg, setMsg] = useState('')
  const [booted, setBooted] = useState(false)
  const { guard, busy, error, setError } = usePageGuard()

  const load = useCallback(async () => {
    try {
      const d = await api<{ items: FalsifyItem[] }>('/falsify?limit=200')
      setItems(d.items || [])
      setError('')
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBooted(true)
    }
  }, [setError])
  useEffect(() => { load() }, [load])

  const add = () =>
    guard(async () => {
      await api('/falsify', {
        method: 'POST',
        body: JSON.stringify({ claim: fresh.claim.trim(), target: fresh.target.trim(), evidence_note: fresh.evidence_note.trim() }),
      })
      setAddOpen(false)
      setFresh({ claim: '', target: '', evidence_note: '' })
      setMsg('已进待实测清单 ✓')
      await load()
    })

  const update = (f: FalsifyItem, patch: { status?: string; result_note?: string; evidence_note?: string }) =>
    guard(async () => {
      await api(`/falsify/${f.id}`, { method: 'PUT', body: JSON.stringify(patch) })
      setMsg(patch.status === 'tested' ? '已实测 ✓ 可成稿' : '')
      await load()
    })

  const compose = (f: FalsifyItem) =>
    guard(async () => {
      const d = await api<{ job_id: number; script_id: number }>(`/falsify/${f.id}/compose`, { method: 'POST' })
      setExpanded(null)
      setMsg(`实测稿已受理（脚本 #${d.script_id}，任务 #${d.job_id}）——进「人设号 · 视频稿」待定稿`)
      await load()
    })

  const dismiss = (f: FalsifyItem) =>
    guard(async () => { await api(`/falsify/${f.id}/dismiss`, { method: 'POST' }); await load() })

  const open = (f: FalsifyItem) => {
    const next = expanded === f.id ? null : f.id
    setExpanded(next)
    setForm({ evidence_note: f.evidence_note || '', result_note: f.result_note || '' })
  }

  const list = items.filter((f) => f.status !== 'dismissed')

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button className="btn-ghost btn-sm" disabled={!!busy} onClick={() => setAddOpen(!addOpen)}>
          + 添加可疑主张
        </button>
        <span className="text-[12px] text-slate-400">
          共 {list.length} 条（待实测 {list.filter((f) => f.status === 'pending').length} · 已实测 {list.filter((f) => f.status === 'tested').length}）
        </span>
        {msg && <span className="text-[12.5px] text-sky-600">{msg}</span>}
      </div>
      <ErrorLine text={error} />

      {addOpen && (
        <div className="mb-3 rounded-2xl border border-violet-200 bg-violet-50/50 p-4 space-y-2">
          <input className="w-full rounded-xl border border-slate-200 px-3 py-2 text-[13px] outline-none focus:border-violet-400"
            value={fresh.claim} maxLength={60}
            placeholder="要打的主张（例：某课号称 7 天学会接单）"
            onChange={(e) => setFresh((v) => ({ ...v, claim: e.target.value }))} />
          <div className="flex items-center gap-2">
            <input className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-1.5 text-[12.5px] outline-none focus:border-violet-400"
              value={fresh.target} maxLength={40} placeholder="实测对象（例：某 AI 课）"
              onChange={(e) => setFresh((v) => ({ ...v, target: e.target.value }))} />
            <button className="btn-accent btn-sm shrink-0" disabled={!fresh.claim.trim() || !!busy} onClick={add}>进清单</button>
            <button className="btn-ghost btn-sm shrink-0" onClick={() => setAddOpen(false)}>取消</button>
          </div>
          <input className="w-full rounded-xl border border-slate-200 px-3 py-1.5 text-[12.5px] outline-none focus:border-violet-400"
            value={fresh.evidence_note} placeholder="线索/链接（可选）"
            onChange={(e) => setFresh((v) => ({ ...v, evidence_note: e.target.value }))} />
        </div>
      )}

      {!booted ? null : list.length === 0 ? (
        <EmptyState icon={ChevronRight} title="实测清单是空的"
          desc="把你想打假的对象一句话投进来；工场每天也会自动猎取候选（只猎一人可实测验证的：买得起、用得上、跑得完）。" />
      ) : (
        <div className="space-y-2">
          {list.map((f) => {
            const st = FSTATUS[f.status] || { label: f.status, cls: 'bg-slate-100 text-slate-500' }
            const exp = expanded === f.id
            return (
              <div key={f.id} className={`rounded-2xl border bg-white transition ${exp ? 'border-emerald-300 shadow-sm' : 'border-[#E8ECF0]'}`}>
                <div className="cursor-pointer px-4 py-3" onClick={() => open(f)}>
                  <div className="flex items-center gap-2">
                    {exp ? <ChevronDown size={15} className="shrink-0 text-slate-400" />
                      : <ChevronRight size={15} className="shrink-0 text-slate-400" />}
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${st.cls}`}>{st.label}</span>
                    {f.auto_source && (
                      <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[11px] text-violet-700" title="工场自动猎取">工场猎取</span>
                    )}
                    <span className="hidden shrink-0 text-xs text-slate-400 sm:inline">{relTime(f.created_at || '')}</span>
                  </div>
                  <div className="mt-1 pl-6">
                    <div className="truncate text-[14.5px] font-bold leading-snug text-slate-900">{f.claim}</div>
                    {f.target && <div className="truncate text-[12.5px] text-slate-500">实测对象：{f.target}</div>}
                  </div>
                </div>
                {exp && (
                  <div className="border-t border-[#E8ECF0] px-4 py-4 space-y-3">
                    <div>
                      <div className="text-[11px] tracking-wide text-slate-400">线索/链接（可补记）</div>
                      <textarea className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-[12.5px] outline-none focus:border-emerald-400"
                        rows={2} value={form.evidence_note}
                        onChange={(e) => setForm((v) => ({ ...v, evidence_note: e.target.value }))} />
                    </div>
                    <div>
                      <div className="text-[11px] tracking-wide text-slate-400">实测结论（过程+关键数字=成稿论据；没实测别填）</div>
                      <textarea className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-[12.5px] outline-none focus:border-emerald-400"
                        rows={3} value={form.result_note}
                        onChange={(e) => setForm((v) => ({ ...v, result_note: e.target.value }))} />
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {f.status === 'pending' && (
                        <button className="btn-ghost btn-sm" disabled={!!busy}
                          onClick={(e) => { e.stopPropagation(); update(f, { status: 'testing' }) }}>开始实测</button>
                      )}
                      {(f.status === 'pending' || f.status === 'testing') && (
                        <button className="btn-accent btn-sm" disabled={!!busy || !form.result_note.trim()}
                          onClick={(e) => { e.stopPropagation(); update(f, { status: 'tested', result_note: form.result_note, evidence_note: form.evidence_note }) }}>
                          记完结论（已实测）
                        </button>
                      )}
                      {f.status === 'tested' && (
                        <>
                          <button className="btn-accent btn-sm" disabled={!!busy}
                            onClick={(e) => { e.stopPropagation(); compose(f) }}>成稿（实测稿模板）</button>
                          <button className="btn-ghost btn-sm" disabled={!!busy}
                            onClick={(e) => { e.stopPropagation(); update(f, { result_note: form.result_note }) }}>保存结论</button>
                        </>
                      )}
                      {f.status === 'scripted' ? (
                        <button className="btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); onNavigate?.('p_content') }}>去视频稿 →</button>
                      ) : (
                        <button className="btn-ghost btn-sm flex items-center gap-1 text-slate-500" disabled={!!busy}
                          onClick={(e) => { e.stopPropagation(); dismiss(f) }}><X size={13} /> 不做</button>
                      )}
                      {f.status === 'tested' && !f.result_note.trim() && (
                        <span className="text-[11px] text-orange-600">结论为空不可成稿（没有实测事实=编造）</span>
                      )}
                    </div>
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
