import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api } from './api'
import { fetchMenuStats } from './counts'
import SharpPicks from './SharpPicks'

interface OpenCand { id: number; name: string; follower_count: number; signature: string; keyword: string }

/** 手机轻量页（2026-11-05 ⑤，/?view=m）：今日三锐（批准定审）· 贴链入库（视频→拆解 · 主页→判号）
· 候选一键定夺 · 今日长文状态。只做拍板动作，产线与设置留在电脑端。moxt 主题。 */
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
    <div className="min-h-full bg-[#f5f6f8]">
      {/* 顶栏：moxt 墨绿 */}
      <header className="bg-[#0a2f24] px-5 pb-5 pt-6">
        <div className="mx-auto flex max-w-md items-center gap-3">
          <img src="/icon.svg" alt="" className="size-9 shrink-0 rounded-xl" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-[#f0e9d5]">跃迁内容工作室</div>
            <div className="text-[10px] text-[#9dbba9]/80">移动拍板 · 三锐与贴链</div>
          </div>
          <button onClick={onExit} className="shrink-0 rounded-full border border-[rgba(240,233,213,0.25)] px-3 py-1 text-[11px] text-[#cfe0d6]">
            回电脑版
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-md px-4 pb-14 pt-4">
        {error && (
          <div className="mb-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
        )}

        {/* 今日长文 */}
        <section className="rounded-2xl border border-[#e8ecf0] bg-white p-4 shadow-[0_1px_2px_rgba(23,25,41,0.04)]">
          <div className="mb-2 flex items-center gap-2">
            <span className="rounded bg-[#0a2f24] px-2 py-0.5 text-[10px] font-medium tracking-wide text-[#e8c97f]">每日一篇</span>
            <span className="text-[11px] text-slate-400">凌晨直出 · 改稿发布在电脑端</span>
          </div>
          {article
            ? <>
              <div className="text-[15px] font-semibold leading-snug text-slate-900">{article.title}</div>
              <div className={`mt-1.5 text-xs ${article.status === 'generating' ? 'text-sky-600' : 'text-emerald-600'}`}>
                {article.status === 'generating' ? '生成中——明早来看成稿' : '已成文 · 电脑端公众号线改稿渲染后发布'}
              </div>
            </>
            : <div className="text-[13px] text-slate-400">今天还没有直出长文（没有值得写的热点会空过）</div>}
        </section>

        {/* 三锐 */}
        <div className="mt-4 space-y-3">
          <SharpPicks line="persona" label="三锐 · 人设号" compact onApprove={reload} />
          <SharpPicks line="leadgen" label="三锐 · 获客号" compact onApprove={reload} />
          <div className="px-1 text-[11px] text-slate-400">
            三锐池：人设 {counts.sharpP} · 获客 {counts.sharpL}（评分器每晚打分，锐度≥7 进池）
          </div>
        </div>

        {/* 贴链 */}
        <section className="mt-5 rounded-2xl border border-[#e8ecf0] bg-white p-4 shadow-[0_1px_2px_rgba(23,25,41,0.04)]">
          <div className="mb-2.5 flex items-center gap-2">
            <span className="text-sm font-semibold text-slate-900">贴链入库</span>
            <span className="text-[11px] text-slate-400">视频链接→拆解 · 主页分享→判号</span>
          </div>
          <div className="flex gap-2">
            <input value={paste} onChange={(e) => setPaste(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') pasteGo() }}
              placeholder="贴抖音分享链接"
              className="min-w-0 flex-1 rounded-xl border border-[#e8ecf0] bg-white px-3 py-2 text-sm outline-none placeholder:text-slate-300 focus:border-sky-300" />
            <button onClick={pasteGo} disabled={!paste.trim() || !!busy}
              className="btn-accent btn-sm shrink-0 disabled:opacity-40">入库</button>
          </div>
          {busy && (
            <div className="mt-2 flex items-center gap-1.5 text-xs text-sky-600">
              <RefreshCw size={12} className="animate-spin" /> {busy}
            </div>
          )}
          {msg && <div className="mt-2 rounded-lg bg-emerald-50 px-2.5 py-1.5 text-xs text-emerald-800">{msg}</div>}
        </section>

        {/* 候选定夺 */}
        {cands.length > 0 && (
          <section className="mt-5">
            <div className="mb-2 flex items-center gap-2 px-1">
              <span className="text-sm font-semibold text-slate-900">候选待定夺</span>
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">{cands.length}</span>
              <span className="text-[11px] text-slate-400">监测→进电脑端监控清单</span>
            </div>
            <div className="space-y-1.5">
              {cands.map((c) => (
                <div key={c.id} className="rounded-2xl border border-[#e8ecf0] bg-white px-3.5 py-3 shadow-[0_1px_2px_rgba(23,25,41,0.03)]">
                  <div className="flex items-center gap-2.5">
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
          </section>
        )}

        <div className="mt-6 px-1 text-center text-[10px] text-slate-300">
          发布永远人工 · 内容与情报凌晨自动运转
        </div>
      </main>
    </div>
  )
}
