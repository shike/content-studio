import { useCallback, useEffect, useState } from 'react'
import { ClipboardCheck, PackageOpen, Send } from 'lucide-react'
import { api, Script, Topic } from '../api'
import { Busy, ErrorLine, PageHeader } from '../components'

interface Package {
  douyin_title: string
  douyin_title_alt: string
  douyin_tags: string[]
  pinned_comment?: string
  topic_groups?: string[]
  channels_caption: string
  cover_text: string
  wechat_html: string
}

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false)
  if (!value) return null
  return (
    <div className="flex items-center gap-2 rounded-lg bg-slate-50 px-3 py-2">
      <span className="w-16 shrink-0 text-xs text-slate-500">{label}</span>
      <span className="min-w-0 flex-1 truncate text-sm text-zinc-800">{value}</span>
      <button
        onClick={async () => {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
        className="btn-ghost btn-xs shrink-0"
      >
        {copied ? '已复制' : '复制'}
      </button>
    </div>
  )
}

export default function Publishing() {
  const [scripts, setScripts] = useState<Script[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [scriptId, setScriptId] = useState<number | null>(null)
  const [pkg, setPkg] = useState<Package | null>(null)
  const [articles, setArticles] = useState<{ id: number; script_id: number | null; title: string }[]>([])
  const [posterPack, setPosterPack] = useState<{ title: string; items: { slot: number; caption: string; title: string; layout: string; prompt: string }[]; mode: string } | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    const [s, t] = await Promise.all([
      api<{ items: Script[] }>('/scripts?limit=200'),
      api<{ items: Topic[] }>('/topics?limit=200'),
    ])
    setScripts(s.items)
    setTopics(t.items)
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    api<{ items: { id: number; script_id: number | null; title: string }[] }>('/articles?limit=100')
      .then((r) => setArticles(r.items))
      .catch(() => {})
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

  const titleOf = (s: Script) => {
    const t = topics.find((x) => x.id === s.topic_id)
    return t?.title || s.versions?.[0]?.hook?.slice(0, 26) || `脚本 #${s.id}`
  }

  const genPosterPack = () =>
    guard(async () => {
      if (!scriptId) return
      const art = articles.find((x) => x.script_id === scriptId)
      if (!art) {
        setError('该脚本还没有长文——先到「公众号」用此脚本生成文章，才有配图提示词')
        return
      }
      setBusy('生成配图提示词包…（LLM 抽取全部配图位）')
      // method 必须显式 POST：api() 默认 GET，GET 打到 POST-only 路由会落 404 兜底
      const r = await api<{ title: string; items: { slot: number; caption: string; title: string; layout: string; prompt: string }[]; mode: string }>(
        `/articles/${art.id}/poster-pack`, { method: 'POST' })
      setPosterPack({ title: r.title, items: r.items, mode: r.mode })
    })

  const gen = (id: number) =>
    guard(async () => {
      setBusy('生成物料包…（LLM 生成话题标签）')
      const p = await api<Package>('/publishing/packages', {
        method: 'POST',
        body: JSON.stringify({ asset_type: 'script', asset_id: id }),
      })
      setPkg(p)
    })

  const finalScripts = scripts.filter((s) => s.status === 'final')

  return (
    <div>
      <PageHeader
        icon={Send}
        title="发布台"
        desc="物料包一键复制 · 发布永远人工 · 数据在平台后台看"
      />

      <div className="card border-sky-200/70 bg-gradient-to-br from-sky-50/80 to-white p-4">
        <div className="flex flex-col gap-2 sm:flex-row">
          <select
            value={scriptId ?? ''}
            onChange={(e) => {
              setScriptId(e.target.value ? Number(e.target.value) : null)
              setPkg(null)
            }}
            className="input flex-1 border-sky-200"
          >
            <option value="">— 选择定稿脚本，生成发布物料包 —</option>
            {finalScripts.map((s) => (
              <option key={s.id} value={s.id}>
                [{s.id}] {titleOf(s).slice(0, 40)}
              </option>
            ))}
          </select>
          <button
            onClick={() => scriptId && gen(scriptId)}
            disabled={!scriptId || !!busy}
            className="btn-accent justify-center disabled:opacity-40 sm:w-36"
          >
            <PackageOpen size={15} /> 生成物料包
          </button>
          <button
            onClick={() => scriptId && genPosterPack()}
            disabled={!scriptId || !!busy}
            className="btn-ghost btn-xs text-violet-600 disabled:opacity-40 sm:w-24"
            title="生成该脚本对应长文的全部配图位即梦提示词（复制去即梦/豆包出图）"
          >
            配图提示词
          </button>
        </div>
      </div>

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {posterPack && (
        <div className="card mt-4 space-y-2 p-5">
          <div className="flex items-center gap-2">
            <div className="text-sm font-semibold text-zinc-800">配图提示词包 · {posterPack.items.length} 条</div>
            <span className="text-[10px] text-slate-400">{posterPack.mode === 'llm' ? 'LLM 精修' : '模板'}</span>
            <button
              onClick={async () => {
                await navigator.clipboard.writeText(
                  posterPack.items.map((it, i) => `【${i + 1}】${it.title || it.caption || `配图 ${it.slot + 1}`}（${it.layout}）\n${it.prompt}`).join('\n\n'))
              }}
              className="btn-ghost btn-xs ml-auto text-violet-600"
            >
              一键复制全部
            </button>
          </div>
          {posterPack.items.map((it, i) => (
            <CopyField key={i} label={`配图 ${i + 1}（${it.layout}）`} value={it.prompt} />
          ))}
          <div className="text-[11px] text-slate-400">用法：复制单条 → 打开即梦 jimeng.jianying.com 或豆包 → 粘贴出图 → 下载后上传公众号对应位置。</div>
        </div>
      )}

      {pkg && (
        <div className="card mt-4 space-y-2 p-5">
          <div className="text-sm font-semibold text-zinc-800">发布物料包</div>
          <CopyField label="抖音标题" value={pkg.douyin_title} />
          {pkg.douyin_title_alt && <CopyField label="备选标题" value={pkg.douyin_title_alt} />}
          <CopyField label="话题标签" value={pkg.douyin_tags.map((t) => `#${t}`).join(' ')} />
          {pkg.pinned_comment && <CopyField label="置顶评论" value={pkg.pinned_comment} />}
          {(pkg.topic_groups?.length ?? 0) > 0 && (
            <>
              {pkg.topic_groups!.map((g, i) => (
                <CopyField key={i} label={`话题组合${i + 1}`} value={g} />
              ))}
            </>
          )}
          <CopyField label="视频号文案" value={pkg.channels_caption} />
          <CopyField label="封面大字" value={pkg.cover_text} />
          <div className="pt-1 text-xs text-slate-400">
            发布时记得勾选平台的「AI 生成内容」声明；标题与话题可在发布页微调。
          </div>
        </div>
      )}

      {finalScripts.length === 0 && (
        <div className="card mt-4 p-8 text-center text-sm text-slate-400">
          还没有定稿脚本——到「脚本工场」定稿后回来生成物料包。
        </div>
      )}

      <div className="mt-6 rounded-xl border border-dashed border-slate-300 p-4 text-xs leading-relaxed text-slate-400">
        <div className="mb-1 flex items-center gap-1.5 font-medium text-slate-500">
          <ClipboardCheck size={13} /> 发布原则
        </div>
        发布动作永远人工：复制物料包 → 平台发布（勾选 AI 声明）→ 效果数据在抖音/视频号后台看。
        发布台账与数据回流已按需求移除，不再回写系统。
      </div>
    </div>
  )
}
