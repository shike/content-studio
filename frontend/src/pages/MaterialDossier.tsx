import { useCallback, useEffect, useState } from 'react'
import { ExternalLink, RefreshCw } from 'lucide-react'
import { api, apiErrText, waitJob } from '../api'
import { ConfirmDeleteButton, relTime } from '../components'

/**
 * 素材档案（拆解详情）：给热评卡片/素材行展开用——后拉 GET /api/materials/{id}，
 * 渲染原视频拆解（persona=钩子/结构/亮点/选题价值/可复刻点；leadgen=内容模式/CTA 拆解/可复制打法）
 * + 评论存档（高亮当前热评）+ 转写。未拆解的抖音视频可就地排队拆解。
 * 拆解 payload 由 benchmark_analyze 产出（LLM 可能漂移），渲染一律按可选字段防御。
 */

interface AnalysisItem { kind: string; version: number; payload: Record<string, any>; text?: string }

export interface Dossier {
  id: number
  title: string
  author: string
  origin_type: string
  origin_url: string
  url?: string
  status: string
  stats: Record<string, number>
  quality: Record<string, any>
  quality_score?: number
  comment_kind?: string
  published_at?: string | null
  created_at: string
  transcript: string
  top_comments: { t: string; d: number }[]
  analysis: Record<string, any>
  analyzed: boolean
  analyses?: AnalysisItem[]
}

const TYPE_LABEL: Record<string, string> = {
  douyin_video: '抖音视频', douyin_image: '抖音图文', kb_doc: '知识库', video: '本地视频',
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-[12.5px] leading-relaxed">
      <span className="w-20 shrink-0 text-slate-400">{k}</span>
      <span className="min-w-0 flex-1 text-slate-600">{v}</span>
    </div>
  )
}

export default function MaterialDossier({ materialId, highlightIdx, onDeleted }: {
  materialId: number
  highlightIdx?: number
  onDeleted?: () => void
}) {
  const [d, setD] = useState<Dossier | null>(null)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setError('')
    api<Dossier>(`/materials/${materialId}`)
      .then(setD)
      .catch((e) => setError(apiErrText(e)))
  }, [materialId])
  useEffect(() => { load() }, [load])

  const remove = async () => {
    setError(''); setBusy(true)
    try {
      await api(`/materials/${materialId}`, { method: 'DELETE' })
      onDeleted?.()
    } catch (err) {
      setError(`删除失败：${apiErrText(err)}`)
    } finally { setBusy(false) }
  }

  const enrich = async (e: React.MouseEvent) => {
    e.stopPropagation()
    setMsg('')
    setBusy(true)
    try {
      const r = await api<{ job_id: number }>(`/materials/${materialId}/enrich`, { method: 'POST' })
      setMsg(`拆解任务 #${r.job_id} 已排队`)
      await waitJob(r.job_id, (j) => setMsg(`拆解中 ${j.progress}%：${j.message || ''}`))
      setMsg('拆解完成')
      load()
    } catch (err) {
      setError(`拆解失败：${apiErrText(err)}`)
    } finally { setBusy(false) }
  }

  if (error) return <div className="mt-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12.5px] text-red-700">{error}</div>
  if (!d) return <div className="mt-2 text-[12.5px] text-slate-400">读详情中…</div>

  const a = d.analysis || {}
  const isLeadgen = String(a.content_type || '') === 'leadgen'
  const ctas: { type?: string; position_text?: string; position_sec?: number }[] =
    Array.isArray(a.cta_analysis?.ctas) ? a.cta_analysis.ctas : []
  const structure: { step?: string; content?: string }[] =
    Array.isArray(a.structure) ? a.structure : []
  const highlights: string[] = Array.isArray(a.highlights) ? a.highlights : []
  const replicable: string[] = Array.isArray(a.replicable_points) ? a.replicable_points : []
  const playbook: string[] = Array.isArray(a.replicable_playbook) ? a.replicable_playbook : []
  const hook = a.hook || null
  // 拆机制五问键名中文化（2026-11-22 P2）
  const TD_LABEL: Record<string, string> = {
    hook_stop: '第一句靠什么停', hook_catch: '钩子被什么接住', attraction: '吸引力类型',
    audience_width: '人群宽度', ending_reason: '结尾给的理由',
  }

  return (
    <div className="mt-3 space-y-3 border-t border-[#E8ECF0] pt-3" onClick={(e) => e.stopPropagation()}>
      {/* 原视频档案 */}
      <div className="rounded-lg bg-[#f7f8fa] px-3 py-2">
        <Row k="原视频" v={<span className="text-slate-800">{d.title || `素材 #${d.id}`}</span>} />
        <Row k="来源" v={<>
          {d.author || '未知作者'} · {TYPE_LABEL[d.origin_type] || d.origin_type} · 发布于 {relTime(d.published_at || d.created_at)}
          {(d.origin_url || d.url) && (
            <a href={d.origin_url || d.url} target="_blank" rel="noreferrer"
              className="ml-2 inline-flex items-center gap-0.5 text-sky-600 hover:underline">
              原链接 <ExternalLink size={11} />
            </a>
          )}
        </>} />
        <Row k="互动" v={
          <>赞 {d.stats?.digg ?? '—'} · 评 {d.stats?.comment ?? '—'} · 藏 {d.stats?.collect ?? '—'} · 转 {d.stats?.share ?? '—'}</>
        } />
        <Row k="热度" v={<>{d.quality_score ?? '—'}{d.comment_kind === 'leadgen_ore' ? ` · 获客矿 ${Math.round((d.quality?.leadgen_share ?? 0) * 100)}%` : d.comment_kind === 'persona_ore' ? ' · 人设矿' : ''}</>} />
      </div>

      {/* 拆解 */}
      {!d.analyzed ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50/50 px-3 py-2">
          <span className="text-[12.5px] text-amber-800">这条视频还没拆解（转写 + 结构 + 打法）</span>
          {d.origin_type === 'douyin_video' && (
            <button onClick={enrich} disabled={busy}
              className="rounded-full bg-emerald-600 px-3 py-1 text-[12px] font-medium text-white transition hover:bg-emerald-700 disabled:opacity-40">
              <RefreshCw size={11} className={`inline ${busy ? 'animate-spin' : ''}`} /> {busy ? '拆解中' : '排队拆解'}
            </button>
          )}
        </div>
      ) : (
        <div>
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="text-[11px] tracking-wide text-slate-400">原视频拆解</span>
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${isLeadgen ? 'bg-violet-100 text-violet-700' : 'bg-emerald-100 text-emerald-700'}`}>
              {isLeadgen ? '获客型' : '人设型'}
            </span>
            {a.score != null && <span className="num-serif text-[12px] font-bold text-[#0a2f24]">{a.score} 分</span>}
            <span className="text-[11px] text-slate-300">转写 {d.transcript ? `${d.transcript.length} 字` : '无'}</span>
          </div>
          <div className="space-y-1.5">
            {a.summary && <Row k="讲什么" v={a.summary} />}
            {hook && (
              <Row k="钩子" v={
                <>[{hook.type || '—'}] 「{hook.position_text || ''}」
                  {hook.position_sec != null && <span className="text-slate-400"> @{hook.position_sec}s</span>}</>
              } />
            )}
            {isLeadgen ? (
              <>
                {a.content_pattern && <Row k="内容模式" v={a.content_pattern} />}
                {ctas.length > 0 && (
                  <Row k="CTA 拆解" v={
                    <ul className="space-y-1">
                      {ctas.map((c, i) => (
                        <li key={i}>
                          <span className="mr-1.5 rounded bg-violet-50 px-1.5 py-0.5 text-[11px] text-violet-700">{c.type || '引导'}</span>
                          「{c.position_text || ''}」
                          {c.position_sec != null && <span className="text-slate-400"> @{c.position_sec}s</span>}
                        </li>
                      ))}
                    </ul>
                  } />
                )}
                {a.cta_analysis?.density && <Row k="引导密度" v={a.cta_analysis.density} />}
                {a.cta_analysis?.path && <Row k="留资路径" v={a.cta_analysis.path} />}
                {playbook.length > 0 && (
                  <Row k="可复制打法" v={<ul className="list-disc space-y-0.5 pl-4">{playbook.map((t, i) => <li key={i}>{t}</li>)}</ul>} />
                )}
              </>
            ) : (
              <>
                {structure.length > 0 && (
                  <Row k="结构" v={
                    <ol className="space-y-1">
                      {structure.map((s, i) => (
                        <li key={i}>
                          <span className="mr-1 font-medium text-slate-700">{i + 1}. {s.step || '环节'}</span>
                          {s.content}
                        </li>
                      ))}
                    </ol>
                  } />
                )}
                {highlights.length > 0 && (
                  <Row k="亮点" v={<ul className="list-disc space-y-0.5 pl-4">{highlights.map((t, i) => <li key={i}>{t}</li>)}</ul>} />
                )}
                {a.topic_value && <Row k="选题价值" v={a.topic_value} />}
                {replicable.length > 0 && (
                  <Row k="可复刻点" v={<ul className="list-disc space-y-0.5 pl-4">{replicable.map((t, i) => <li key={i}>{t}</li>)}</ul>} />
                )}
                {a.teardown && Object.keys(a.teardown).length > 0 && (
                  <Row k="为什么爆（机制）" v={
                    <ul className="list-disc space-y-0.5 pl-4">
                      {Object.entries(a.teardown as Record<string, string>).filter(([, v]) => v).map(([k, v]) => (
                        <li key={k}>{TD_LABEL[k] || k}：{v}</li>
                      ))}
                    </ul>
                  } />
                )}
                {a.borrow_check && Object.keys(a.borrow_check).length > 0 && (
                  <Row k="借不借（三关）" v={
                    <ul className="list-disc space-y-0.5 pl-4">
                      {Object.entries(a.borrow_check as Record<string, string>).filter(([, v]) => v).map(([k, v]) => (
                        <li key={k}>{k}：{v}</li>
                      ))}
                    </ul>
                  } />
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* 评论存档（高亮当前热评） */}
      {d.top_comments.length > 0 && (
        <div>
          <div className="text-[11px] tracking-wide text-slate-400">评论存档（按赞降序，共 {d.top_comments.length} 条）</div>
          <ul className="mt-1.5 max-h-56 space-y-1 overflow-y-auto pr-1">
            {d.top_comments.map((c, i) => (
              <li key={i}
                className={`flex gap-2 rounded px-1.5 py-1 text-[12.5px] ${i === highlightIdx ? 'bg-amber-50 text-slate-800' : 'text-slate-600'}`}>
                <span className="num-serif w-12 shrink-0 text-right text-slate-400">{c.d}</span>
                <span className="min-w-0 flex-1">{c.t}{i === highlightIdx && <span className="ml-1.5 text-[11px] text-amber-600">当前热评</span>}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 转写 */}
      {d.transcript && (
        <details className="group">
          <summary className="cursor-pointer text-[11px] tracking-wide text-slate-400 hover:text-slate-600">
            转写全文（{d.transcript.length} 字）
          </summary>
          <div className="mt-1.5 max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg bg-[#f7f8fa] px-3 py-2 text-[12.5px] leading-relaxed text-slate-600">
            {d.transcript}
          </div>
        </details>
      )}

      <div className="flex items-center justify-between">
        <span className="text-[12px] text-emerald-700">{msg}</span>
        <ConfirmDeleteButton onConfirm={remove} title="删除这条素材（含评论存档与拆解）" confirmText="删除素材" />
      </div>
    </div>
  )
}
