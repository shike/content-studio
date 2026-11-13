import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

interface OpenCand { id: number; name: string; follower_count: number; signature: string; keyword: string }

/** 手机轻量页（2026-11-05 ⑤，/?view=m）：候选一键定夺 · 今日长文状态。
只做拍板动作，产线与设置留在电脑端。三锐/贴链已砍（2026-11-07 用户逐组拍板）。moxt 主题。 */
export default function Mobile({ onExit }: { onExit: () => void }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [cands, setCands] = useState<OpenCand[]>([])
  const [article, setArticle] = useState<{ title: string; status: string } | null>(null)

  const reload = useCallback(async () => {
    try {
      const [c, a] = await Promise.all([
        api<{ items: OpenCand[] }>('/watch/discover/candidates?status=open'),
        api<{ items: { title: string; status: string }[] }>('/articles?limit=5'),
      ])
      setCands((c.items || []).slice(0, 10))
      const gen = (a.items || []).find((x) => x.status === 'generating')
      const ready = (a.items || []).find((x) => ['draft', 'edited', 'rendered'].includes(x.status))
      setArticle(gen ? { title: gen.title, status: 'generating' }
        : ready ? { title: ready.title, status: 'ready' } : null)
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ''))
    }
  }, [])

  useEffect(() => { reload() }, [reload])

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
            <div className="text-[10px] text-[#9dbba9]/80">移动拍板 · 候选与长文</div>
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
