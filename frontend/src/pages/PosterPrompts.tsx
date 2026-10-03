import { useState } from 'react'
import { Copy, Wand2 } from 'lucide-react'
import { api } from '../api'
import { Busy, ErrorLine, PageHeader } from '../components'

/** 海报提示词工场：任意内容/大纲 → 即梦(Seedream)图文一体海报的完整提示词。
 *  出图走即梦网页版/豆包（免费额度）或火山方舟 API；本页只负责提示词。 */

interface Poster {
  title: string
  layout: string
  prompt: string
}

const TYPES: { key: string; label: string; hint: string }[] = [
  { key: 'knowledge', label: '知识长图（3:4）', hint: '奶油色知识卡片版式：标题+分节要点+结论条，信息密度高' },
  { key: 'recruit', label: '招生/活动海报（9:16）', hint: '深蓝+橙金，大号渐变数字标题，编号卡片，价格与二维码位' },
  { key: 'marketing', label: '营销长图（9:16）', hint: '深色科技背景+手机样机/3D 元素，利益点图标行+流程+CTA' },
  { key: 'banner', label: '服务横幅（16:9）', hint: '浅蓝紫科技横幅，三大卡片横排，品牌位' },
]

export default function PosterPrompts() {
  const [content, setContent] = useState('')
  const [posterType, setPosterType] = useState('knowledge')
  const [count, setCount] = useState(3)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [posters, setPosters] = useState<Poster[] | null>(null)

  const generate = async () => {
    setError('')
    setBusy(true)
    setPosters(null)
    try {
      const r = await api<{ posters: Poster[] }>('/poster/freetext', {
        method: 'POST',
        body: JSON.stringify({ content: content.trim(), poster_type: posterType, count }),
      })
      setPosters(r.posters)
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  const copyOne = async (p: Poster) => {
    await navigator.clipboard.writeText(p.prompt)
  }

  return (
    <div>
      <PageHeader
        icon={Wand2}
        title="海报提示词"
        desc="贴入文章/大纲 → 生成即梦(Seedream)图文一体海报的完整提示词 → 复制去即梦/豆包免费出图"
      />

      <div className="card space-y-3 p-4">
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="贴入文章全文、课件大纲或一段知识要点（至少 30 字）。系统会提炼标题、要点与结论，写成即梦可用的完整提示词（含全部图内中文文案）。"
          className="h-40 w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-4 text-[13px] leading-relaxed outline-none focus:border-slate-400 focus:bg-white"
        />
        <div className="flex flex-wrap items-center gap-2">
          <div className="segment shrink-0">
            {TYPES.map((t) => (
              <button
                key={t.key}
                onClick={() => setPosterType(t.key)}
                title={t.hint}
                className={`segment-item ${posterType === t.key ? 'segment-item-active' : ''}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
            数量
            <select value={count} onChange={(e) => setCount(Number(e.target.value))} className="input w-16 py-1.5">
              {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          <button
            onClick={generate}
            disabled={busy || content.trim().length < 30}
            className="btn-accent btn-xs disabled:opacity-40"
          >
            <Wand2 size={13} /> {busy ? '生成中…（约 30 秒）' : '生成提示词'}
          </button>
          <span className="text-[11px] text-slate-400">出图：复制提示词 → 即梦 jimeng.jianying.com 或豆包（免费额度）→ 下载后上传公众号</span>
        </div>
      </div>

      {busy && <div className="mt-3"><Busy text="提炼标题与要点，编写海报提示词…" /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {posters && posters.length > 0 && (
        <div className="mt-4 space-y-3">
          {posters.map((p, i) => (
            <div key={i} className="card p-4">
              <div className="flex items-center gap-2">
                <span className="badge shrink-0 bg-violet-100 text-violet-700">海报 {i + 1}</span>
                <span className="text-sm font-semibold text-zinc-800">{p.title}</span>
                <span className="ml-auto text-[11px] text-slate-400">{p.layout}</span>
                <button
                  onClick={() => navigator.clipboard.writeText(p.prompt)}
                  className="btn-ghost btn-xs text-violet-600"
                >
                  <Copy size={12} /> 复制提示词
                </button>
              </div>
              <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-[12.5px] leading-relaxed text-slate-600">{p.prompt}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
