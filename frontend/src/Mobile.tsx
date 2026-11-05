import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { fetchMenuStats } from './counts'
import SharpPicks from './SharpPicks'

interface OpenCand { id: number; name: string; follower_count: number; signature: string; keyword: string }

/** 手机轻量页（2026-11-05 ⑤，/?view=m）：今日三锐（批准备定审）· 贴链入库（视频→拆解 · 主页→判号）
· 候选一键定夺 · 今日长文状态。只做拍板动作，产线与设置留在电脑端。 */
export default function Mobile({ onExit }: { onExit: () => void }) {
  const [busy, setBusy] = useState('')
  const [paste, setPaste] = useState('')
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const [cands, setCands] = useState<OpenCand[]>([])
  const [article, setArticle] = useState<{ title: string; status: string } | null>(null)
  const [counts, setCounts] = useState<{ sharpP: number; sharpL: number }>({ sharpP: 0, sharpL: 0 })

  const reload = useCallback(async () => {
    try {
      const [c, a, s] = await Promise.all([
        api<{ items: OpenCand[] }>('/watch/discover/candidates?status=open'),
        api<{ items: { title: string; status: string }[] }>('/articles?limit=5'),
        fetchMenuStats(),
      ])
      setCands((c.items || []).slice(0, 10))
      const gen = (a.items || []).find((x) => x.status === 'generating')
      const ready = (a.items || []).find((x) => ['draft', 'edited', 'rendered'].includes(x.status))
      setArticle(gen ? { title: gen.title, status: 'generating' }
        : ready ? { title: ready.title, status: 'ready' } : null)
      setCounts({ sharpP: s.sharpPersona ?? 0, sharpL: s.sharpLeadgen ?? 0 })
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    }
  }, [])

  useEffect(() => { reload() }, [reload])

  const pasteGo = () =>
    (async () => {
      const u = paste.trim()
      if (!u) return
      setBusy('解析中…（10~30 秒）')
      setMsg('')
      setError('')
      try {
        if (/\/video\/\d+/.test(u) || /share\/video\//.test(u) || /v\.douyin\.com/i.test(u)) {
          const r = await api<{ job_id: number }>('/analyze/video', {
            method: 'POST', body: JSON.stringify({ url: u }),
          })
          setMsg(`拆解任务已排队 #${r.job_id}，电脑端产线可看`)
        } else {
          const r = await api<{ name: string; pure: boolean; reason: string }>('/watch/judge-account', {
            method: 'POST', body: JSON.stringify({ url: u }),
          })
          setMsg(`判号：${r.name}（${r.pure ? '纯获客' : '非纯'}）${r.reason}——已入候选`)
        }
        setPaste('')
        await reload()
      } catch (e) {
        setError(String(e).replace(/^Error:\s*/, ''))
      } finally {
        setBusy('')
      }
    })()

  const candAct = (id: number, act: 'add' | 'dismiss') =>
    (async () => {
      try {
        await api(`/watch/discover/candidates/${id}/${act}`, { method: 'POST' })
        setCands((m) => m.filter((x) => x.id !== id))
      } catch (e) {
        setError(String(e).replace(/^Error:\s*/, ''))
      }
    })()

  return (
    <div className="mx-auto max-w-md px-4 pb-16 pt-6">
      <div className="mb-4 flex items-center justify-between">
        <b className="text-base text-zinc-900">跃迁 · 移动拍板</b>
        <button onClick={onExit} className="text-xs text-slate-400 underline">回电脑版</button>
      </div>

      {/* 今日长文 */}
      <div className="mb-3 rounded-2xl border border-[#e8ecf0] bg-white p-4">
        <div className="mb-1 text-xs text-slate-400">今日长文（凌晨直出）</div>
        {article
          ? <>
            <div className="truncate text-sm font-medium text-slate-800">{article.title}</div>
            <div className={`mt-1 text-xs ${article.status === 'generating' ? 'text-sky-600' : 'text-emerald-600'}`}>
              {article.status === 'generating' ? '生成中…明早可改' : '已成文——电脑端改稿渲染后发布'}
            </div>
          </>
          : <div className="text-xs text-slate-400">今天还没有直出长文</div>}
      </div>

      {/* 三锐 */}
      <div className="space-y-2">
        <SharpPicks line="persona" label="三锐 · 人设号" compact onApprove={reload} />
        <SharpPicks line="leadgen" label="三锐 · 获客号" compact onApprove={reload} />
        <div className="px-1 text-[11px] text-slate-400">
          三锐池：人设 {counts.sharpP} · 获客 {counts.sharpL}（评分器每晚打分）
        </div>
      </div>

      {/* 贴链 */}
      <div className="mt-5 rounded-2xl border border-[#e8ecf0] bg-white p-4">
        <div className="mb-2 text-xs text-slate-400">刷到对标随手贴：视频链接→拆解 · 主页分享→判号</div>
        <div className="flex gap-2">
          <input value={paste} onChange={(e) => setPaste(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') pasteGo() }}
            placeholder="贴抖音分享链接"
            className="min-w-0 flex-1 rounded-xl border border-[#e8ecf0] px-3 py-2 text-sm outline-none placeholder:text-slate-300" />
          <button onClick={pasteGo} disabled={!paste.trim() || !!busy}
            className="btn-accent btn-sm shrink-0 disabled:opacity-40">入库</button>
        </div>
        {busy && <div className="mt-2 text-xs text-sky-600">{busy}</div>}
        {msg && <div className="mt-2 text-xs text-emerald-700">{msg}</div>}
      </div>

      {/* 候选定夺 */}
      {cands.length > 0 && (
        <div className="mt-5">
          <div className="mb-2 flex items-center gap-2">
            <b className="text-sm text-zinc-900">候选待定夺</b>
            <span className="text-xs text-slate-400">{cands.length} 个</span>
          </div>
          <div className="space-y-1.5">
            {cands.map((c) => (
              <div key={c.id} className="rounded-xl border border-[#eef1f4] bg-white px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium text-slate-800">{c.name}</div>
                    <div className="truncate text-[11px] text-slate-400">{c.signature || c.keyword}</div>
                  </div>
                  <button onClick={() => candAct(c.id, 'add')} className="btn-accent btn-xs shrink-0">监测</button>
                  <button onClick={() => candAct(c.id, 'dismiss')} className="btn-ghost btn-xs shrink-0">忽略</button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {error && <div className="mt-4 text-xs text-red-600">{error}</div>}
    </div>
  )
}
