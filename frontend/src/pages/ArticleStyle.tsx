import { useCallback, useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { api, waitJob } from '../api'
import { ErrorLine, PageHeader } from '../components'

/** 公众号风格画像（2026-11-07 产线树拆叶）：从已发长文提取的表达习惯，
生成长文时自动注入。原为 Articles 页内卡片，随产线树拆出为独立叶子页。 */
export default function ArticleStyle() {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const st = await api<{ note: string }>('/articles/style-summary')
    setNote(st.note || '')
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e).replace(/^Error:\s*/, '')))
  }, [reload])

  const extract = () =>
    (async () => {
      try {
        setBusy('风格提取中…')
        setError('')
        const { job_id } = await api<{ job_id: number }>('/articles/extract-style', { method: 'POST' })
        const job = await waitJob(job_id, (j) => setBusy(`风格提取：${j.progress}% ${j.message}`))
        setNote(String(job.result?.note ?? ''))
        setBusy('')
      } catch (e) {
        setError(String(e).replace(/^Error:\s*/, ''))
        setBusy('')
      }
    })()

  return (
    <div>
      <PageHeader icon={Sparkles} title="公众号风格画像" desc="从已发长文提取的表达习惯，写长文时自动注入" />
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}
      <div className="card mt-4 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-sm font-semibold text-slate-800">长文表达画像</div>
          <button onClick={extract} disabled={!!busy} className="btn-ghost btn-xs ml-auto disabled:opacity-40">
            {busy ? busy : note ? '重新提取' : '从已发长文提取'}
          </button>
        </div>
        {note ? (
          <div className="mt-2 rounded-xl bg-slate-50 px-3 py-2 text-[13px] leading-relaxed text-slate-600">{note}</div>
        ) : (
          !busy && <div className="mt-2 text-xs text-slate-400">还没有画像——写出第一篇后点「从已发长文提取」。</div>
        )}
      </div>
    </div>
  )
}
