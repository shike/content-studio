import { useCallback, useEffect, useRef, useState } from 'react'
import { Trash2, UserRound, Video } from 'lucide-react'
import { api, Script, waitJob } from '../api'
import { Busy, EmptyState, ErrorLine, PageHeader, Pager } from '../components'

interface AvatarConfig {
  id: number
  name: string
  engine: string
  chanjing_person_id: string
  chanjing_audio_man: string
  chanjing_pic_url: string
  chanjing_preview_url: string
  speed_ratio: number
}

// 口播语速预设（用户 2026-09-27：原速太慢，定 1.5× 起更接近真人语速）
const SPEED_PRESETS = [1.0, 1.2, 1.5, 1.8]

interface AvatarVideoRow {
  id: number
  script_id: number
  avatar_id: number
  status: string
  video_path: string
  error: string | null
  beans_used: number
  points_used: number
  bigtext?: { text: string; day?: number; date?: string; start: number; end: number; fallback?: boolean }[]
  notes?: string
  seconds: number
  model: number
  title: string
  script_preview: string
  tele_len: number
  story_count: number
  title_count: number
  created_at?: string
}

interface Estimate {
  chars: number
  seconds: number
  beans: number
  yuan: number
  balance: number
  insufficient: boolean
  speed?: number
  points: number      // 本次扣的积分（按秒：基础 3/秒、高质 6/秒）
  credits: number     // 租户积分池余额
}

type Chip = 'all' | 'generating' | 'done' | 'failed'

const CHIPS: { key: Chip; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'generating', label: '生成中' },
  { key: 'done', label: '已完成' },
  { key: 'failed', label: '失败' },
]

export default function Digital({ role = 'member' }: { role?: string }) {
  const isPlatform = role === 'platform_admin'
  const [configs, setConfigs] = useState<AvatarConfig[]>([])
  const [videos, setVideos] = useState<AvatarVideoRow[]>([])
  const [scripts, setScripts] = useState<Script[]>([])
  const [credits, setCredits] = useState<number | null>(null)  // 租户积分池（租户口径：数字人按秒扣积分）
  const [bigtext, setBigtext] = useState(true)  // 左上角栏目角标（租户配置栏目名 + 暖黄框 + Day N，全程常显）
  const [usage, setUsage] = useState<{ videos: number; seconds: number; points: number; beans?: number; yuan?: number } | null>(null)
  const [chip, setChip] = useState<Chip>('all')
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [LIMIT] = useState(50)
  const [genScript, setGenScript] = useState<number | null>(null)
  const [genAvatar, setGenAvatar] = useState<number | null>(null)
  const [quality, setQuality] = useState(0)
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const [confirmDelVideo, setConfirmDelVideo] = useState<number | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [renamingId, setRenamingId] = useState<number | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [cloningId, setCloningId] = useState<number | null>(null)
  const [cloneProg, setCloneProg] = useState(0)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const cloneTarget = useRef<number | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const reload = useCallback(async () => {
    const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) })
    if (chip !== 'all') params.set('status', chip)
    const [c, v, s] = await Promise.all([
      api<{ items: AvatarConfig[] }>('/avatar/configs'),
      api<{ items: AvatarVideoRow[]; total: number; counts?: Record<string, number> }>(`/avatar/videos?${params}`),
      api<{ items: Script[] }>('/scripts?limit=200'),
    ])
    setConfigs(c.items)
    setVideos(v.items)
    setTotal(v.total)
    if (v.counts) setCounts(v.counts)
    setScripts(s.items)
    setGenAvatar((cur) => (cur != null && c.items.some((x) => x.id === cur) ? cur : c.items[0]?.id ?? null))
  }, [offset, chip, LIMIT])

  const refreshBalance = useCallback(async () => {
    // 成本口径（服务商豆/¥/凭证）已挪设置页「平台成本」卡；数字人页只看积分
    try {
      const c = await api<{ balance: number }>('/credits')
      setCredits(c.balance)
    } catch {
      setCredits(null)
    }
    try {
      setUsage(await api<{ videos: number; seconds: number; points: number; beans?: number; yuan?: number }>('/avatar/usage'))
    } catch {
      setUsage(null)
    }
  }, [])

  useEffect(() => {
    reload().catch((e) => setError(String(e)))
    refreshBalance()
  }, [reload, refreshBalance])

  useEffect(() => {
    setEstimate(null)
    if (!genScript) return
    api<Estimate>(`/avatar/estimate?script_id=${genScript}&model=${quality}&avatar_id=${genAvatar ?? 0}`)
      .then(setEstimate)
      .catch((e) => setError(String(e)))
  }, [genScript, quality])

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

  const createConfig = () =>
    guard(async () => {
      if (!newName.trim()) return
      await api('/avatar/configs', { method: 'POST', body: JSON.stringify({ name: newName.trim() }) })
      setShowNew(false)
      setNewName('')
      await reload()
    })

  const renameConfig = (id: number) =>
    guard(async () => {
      if (!renameValue.trim()) return
      await api(`/avatar/configs/${id}`, { method: 'PUT', body: JSON.stringify({ name: renameValue.trim() }) })
      setRenamingId(null)
      await reload()
    })

  const setSpeed = (id: number, speed: number) =>
    guard(async () => {
      await api(`/avatar/configs/${id}`, { method: 'PUT', body: JSON.stringify({ speed_ratio: speed }) })
      await reload()
    })

  const generate = () =>
    guard(async () => {
      if (!genScript || !genAvatar) return
      setBusy('提交生成…')
      const r = await api<{ avatar_video_id: number; job_id: number }>('/avatar/generate', {
        method: 'POST',
        body: JSON.stringify({ script_id: genScript, avatar_id: genAvatar, model: quality, bigtext }),
      })
      await waitJob(r.job_id, (j) => setBusy(`数字人视频：${j.progress}% ${j.message}`))
      setBusy('')
      await reload()
      refreshBalance()
    })

  // 克隆新形象：选出镜视频 → 上传 → API 克隆 → 轮询训练进度 → 回填缩略图/音色
  const startClone = (configId: number) => {
    cloneTarget.current = configId
    fileRef.current?.click()
  }

  const onVideoChosen = (file: File) =>
    guard(async () => {
      const configId = cloneTarget.current
      if (configId == null) return
      setBusy('上传出镜素材（含平台审核，约 1 分钟）…')
      const fd = new FormData()
      fd.append('file', file)
      const res = await fetch(`/api/avatar/configs/${configId}/clone_video`, { method: 'POST', body: fd })
      if (!res.ok) {
        const body = await res.text().catch(() => '')
        let detail = body.slice(0, 160)
        try { detail = (JSON.parse(body) as { detail?: string }).detail || detail } catch { /* 非 JSON 保持原文 */ }
        if (res.status === 402) throw new Error(`积分不足，无法克隆：${detail}`)
        throw new Error(`HTTP ${res.status}${detail ? `：${detail}` : ''}`)
      }
      const { person_id } = (await res.json()) as { person_id: string }
      setCloningId(configId)
      setCloneProg(0)
      for (let i = 0; i < 90; i++) {
        const d = await api<{ status: number; progress: number; pic_url: string; preview_url: string; audio_man_id: string }>(`/avatar/persons/${person_id}`)
        setCloneProg(d.progress || 0)
        if (d.status === 2) {
          // 训练完成：缩略图/预览/配套音色回填到配置
          await api(`/avatar/configs/${configId}`, {
            method: 'PUT',
            body: JSON.stringify({ chanjing_pic_url: d.pic_url || '', chanjing_preview_url: d.preview_url || '', chanjing_audio_man: d.audio_man_id || '' }),
          })
          setCloningId(null)
          await reload()
          setBusy('')
          return
        }
        setBusy(`克隆形象训练中（${d.progress || 0}%），完成后自动配置音色…`)
        await new Promise((r2) => setTimeout(r2, 10000))
      }
      setCloningId(null)
      throw new Error('克隆训练超时（15 分钟），请稍后重试')
    })

  const deleteVideo = (id: number) =>
    guard(async () => {
      await api(`/avatar/videos/${id}`, { method: 'DELETE' })
      setConfirmDelVideo(null)
      setExpanded((x) => (x === id ? null : x))
      await reload()
    })

  const removeConfig = (id: number) =>
    guard(async () => {
      await api(`/avatar/configs/${id}`, { method: 'DELETE' })
      setConfirmDel(null)
      await reload()
    })

  const finalScripts = scripts.filter((s) => s.status === 'final')
  const filtered = videos // 服务端已筛选分页
  const count = (c: Chip) =>
    c === 'all' ? Object.values(counts).reduce((a, b) => a + b, 0) : counts[c === 'done' ? 'done' : c === 'failed' ? 'failed' : 'generating'] || 0
  const genAvatarCfg = configs.find((c) => c.id === genAvatar) || null

  return (
    <div>
      <PageHeader
        icon={UserRound}
        title="口播数字人"
        desc="数字人克隆形象 + 配套音色 → 定稿脚本直接出片（分钟级 · 自动字幕）"
        actions={
          credits != null ? (
            <span className="badge bg-slate-100 text-slate-600">积分余额 {credits}</span>
          ) : undefined
        }
      />

      {busy && <div className="mt-3"><Busy text={busy} /></div>}
      {error && <div className="mt-3"><ErrorLine text={error} /></div>}

      {/* 生成卡：形象缩略图 + 脚本 + 画质档 + 成本预估 */}
      <div className="card mt-3 space-y-2 border-sky-200 bg-sky-50/40 p-4">
        <div className="flex flex-wrap items-center gap-2">
          {genAvatarCfg && (
            genAvatarCfg.chanjing_pic_url ? (
              <img src={genAvatarCfg.chanjing_pic_url} alt="" className="size-9 shrink-0 rounded-lg border border-slate-200 object-cover" />
            ) : (
              <div className="grid size-9 shrink-0 place-items-center rounded-lg border border-slate-200 bg-slate-100">
                <UserRound size={16} className="text-slate-300" />
              </div>
            )
          )}
          <select value={genScript ?? ''} onChange={(e) => setGenScript(Number(e.target.value) || null)} className="input max-w-72">
            <option value="">选择定稿脚本</option>
            {finalScripts.map((s) => (
              <option key={s.id} value={s.id}>#{s.id} {(s.final_text || '').slice(0, 18)}</option>
            ))}
          </select>
          <div className="segment shrink-0">
            <button onClick={() => setQuality(0)} className={`segment-item ${quality === 0 ? 'segment-item-active' : ''}`}>基础版 3 积分/秒</button>
            <button onClick={() => setQuality(1)} className={`segment-item ${quality === 1 ? 'segment-item-active' : ''}`}>高质版 6 积分/秒</button>
          </div>
          <div className="segment shrink-0">
            <button onClick={() => setBigtext(true)} title="左上角栏目角标（租户栏目名 + Day N），全程常显"
              className={`segment-item ${bigtext ? 'segment-item-active' : ''}`}>大字：开</button>
            <button onClick={() => setBigtext(false)} title="不加左上角角标，只保留平台底部字幕"
              className={`segment-item ${!bigtext ? 'segment-item-active' : ''}`}>大字：关</button>
          </div>
          <button onClick={generate} disabled={!genScript || !genAvatar || !!busy} className="btn-accent btn-xs disabled:opacity-40">
            {busy ? '处理中…' : '生成视频'}
          </button>
        </div>
        {estimate && (
          <div className={`rounded-lg px-3 py-2 text-xs leading-relaxed ${estimate.insufficient ? 'bg-red-50 text-red-700' : 'bg-white text-slate-600 border border-slate-100'}`}>
            预估：{estimate.chars} 字 ≈ <b>{estimate.seconds}s</b> ≈ <b>{estimate.points} 积分</b>
            （积分余额 {estimate.credits}）{estimate.speed && estimate.speed !== 1 ? ` · 语速 ${estimate.speed}×` : ''}{quality === 1 && ' · 高质版嘴型更精细'}
            {isPlatform && estimate.beans != null && (
              <span className="text-slate-400"> · 平台成本 ≈ {estimate.beans} 豆{estimate.yuan != null && ` ≈ ¥${estimate.yuan}`}</span>
            )}
            {estimate.insufficient && (isPlatform ? ' —— 积分不足，去「管理后台」给本租户充值' : ' —— 积分不足，请联系管理员充值')}
          </div>
        )}
      </div>

      {/* 我的数字人：形象卡片网格 */}
      <div className="card mt-4 p-0">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 py-2.5">
          <span className="text-sm font-semibold text-zinc-900">我的数字人</span>
          <span className="text-xs text-slate-400">点击卡片设为生成用形象</span>
          <button onClick={() => setShowNew((v) => !v)} className="btn-ghost btn-xs text-sky-600 ml-auto">+ 新建配置</button>
        </div>
        {showNew && (
          <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 bg-sky-50/40 px-4 py-2.5">
            <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="数字人名称" className="input max-w-48" />
            <button onClick={createConfig} disabled={!newName.trim()} className="btn-accent btn-xs disabled:opacity-40">创建</button>
            <span className="text-[11px] text-slate-400">创建后点卡片上的「克隆新形象」上传出镜视频{!isPlatform && '（预扣 300 积分，训练失败不退）'}</span>
          </div>
        )}
        {configs.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={UserRound}
              title="还没有数字人配置"
              desc="新建配置后即可用内置克隆形象生成；想换自己的新形象，点「克隆新形象」传出镜视频（15 秒以上正脸）。"
            />
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
            {configs.map((c) => {
              const isGen = genAvatar === c.id
              const cloning = cloningId === c.id
              return (
                <div
                  key={c.id}
                  onClick={() => setGenAvatar(c.id)}
                  className={`cursor-pointer rounded-xl border p-3.5 transition ${
                    isGen ? 'border-sky-400 bg-sky-50/50 shadow-sm ring-2 ring-sky-200' : 'border-slate-200 hover:border-slate-300'
                  }`}
                >
                  <div className="flex gap-3">
                    <div className="size-16 shrink-0 overflow-hidden rounded-lg border border-slate-200 bg-slate-100">
                      {c.chanjing_pic_url ? (
                        <img src={c.chanjing_pic_url} alt="" className="size-full object-cover" />
                      ) : (
                        <UserRound className="m-auto size-6 text-slate-300" />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        {renamingId === c.id ? (
                          <input
                            value={renameValue}
                            onChange={(e) => setRenameValue(e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            className="input max-w-36 py-1 text-xs"
                            autoFocus
                          />
                        ) : (
                          <span className="truncate text-sm font-semibold text-zinc-900">{c.name}</span>
                        )}
                        {isGen && <span className="badge shrink-0 bg-sky-100 text-sky-700">生成用</span>}
                      </div>
                      <div className="mt-0.5 truncate text-[11px] text-slate-400">
                        形象 {c.chanjing_person_id ? c.chanjing_person_id.slice(-8) : '默认'} · 音色 {c.chanjing_audio_man ? c.chanjing_audio_man.slice(-6) : '配套'}
                      </div>
                      <div className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-500" onClick={(e) => e.stopPropagation()}>
                        <span className="shrink-0">语速</span>
                        <div className="segment shrink-0">
                          {SPEED_PRESETS.map((sp) => (
                            <button
                              key={sp}
                              onClick={() => setSpeed(c.id, sp)}
                              title={sp === 1.0 ? '原速' : `语速 ${sp} 倍（说快 ${sp} 倍，时长与豆按此折算）`}
                              className={`segment-item ${(c.speed_ratio || 1) === sp ? 'segment-item-active' : ''}`}
                            >
                              {sp.toFixed(1)}×
                            </button>
                          ))}
                        </div>
                      </div>
                      {c.chanjing_preview_url && (
                        <a
                          href={c.chanjing_preview_url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-[11px] text-sky-600 hover:underline"
                        >
                          看形象预览
                        </a>
                      )}
                    </div>
                  </div>
                  {cloning ? (
                    <div className="mt-2.5">
                      <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-sky-500" style={{ width: `${cloneProg}%` }} />
                      </div>
                      <div className="mt-1 text-[11px] text-slate-400">克隆训练中 {cloneProg}%</div>
                    </div>
                  ) : renamingId === c.id ? (
                    <div className="mt-2.5 flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
                      <button onClick={() => renameConfig(c.id)} disabled={!renameValue.trim()} className="btn-accent btn-xs disabled:opacity-40">保存</button>
                      <button onClick={() => setRenamingId(null)} className="btn-ghost btn-xs">取消</button>
                    </div>
                  ) : (
                    <div className="mt-2.5 flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      <button onClick={() => startClone(c.id)} title={isPlatform ? undefined : '克隆将预扣 300 积分（训练失败不退）'}
                        className="btn-ghost btn-xs text-slate-500">
                        <Video size={12} /> 克隆新形象{!isPlatform && <span className="ml-1 text-[10px] text-amber-600">300分</span>}
                      </button>
                      <button
                        onClick={() => { setRenamingId(c.id); setRenameValue(c.name) }}
                        className="btn-ghost btn-xs text-slate-500"
                      >
                        改名
                      </button>
                      {confirmDel === c.id ? (
                        <button onClick={() => removeConfig(c.id)} className="btn-danger btn-xs ml-auto">确认删除</button>
                      ) : (
                        <button
                          onClick={() => { setConfirmDel(c.id); setTimeout(() => setConfirmDel((x) => (x === c.id ? null : x)), 3000) }}
                          className="ml-auto text-slate-300 hover:text-red-500"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 生成记录 */}
      <div className="card mt-4 overflow-hidden p-0">
        <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5">
          <span className="mr-1 text-sm font-semibold text-zinc-900">生成记录</span>
          {usage && (
            <span className="text-[11px] text-slate-400">
              累计 {usage.videos} 条 · {Math.round(usage.seconds)} 秒 · 消耗 {usage.points} 积分
            </span>
          )}
          {CHIPS.map((x) => (
            <button
              key={x.key}
              onClick={() => { setChip(x.key); setOffset(0) }}
              className={`badge cursor-pointer ${chip === x.key ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-500 hover:bg-slate-200'}`}
            >
              {x.label} {count(x.key)}
            </button>
          ))}
        </div>
        {filtered.length === 0 ? (
          <div className="p-6">
            <EmptyState
              icon={Video}
              title="还没有生成记录"
              desc="上面选一条定稿脚本，确认成本预估后点「生成视频」，约 1~2 分钟出片。"
            />
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {filtered.map((v) => {
              const exp = expanded === v.id
              return (
                <div key={v.id}>
                  <button
                    onClick={() => setExpanded(exp ? null : v.id)}
                    className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50/60"
                  >
                    <span className={`badge shrink-0 ${v.status === 'done' ? 'bg-emerald-100 text-emerald-700' : v.status === 'failed' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-800'}`}>
                      {v.status === 'done' ? '完成' : v.status === 'failed' ? '失败' : '生成中'}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                      <span className="font-medium text-zinc-900">{v.title}</span>
                      <span className="ml-1.5 text-xs text-slate-400">脚本 #{v.script_id}{v.model === 1 ? ' · 高质版' : ' · 基础版'}</span>
                      {v.status === 'done' && ` · ${Math.round(v.seconds)}s · ${v.points_used} 积分`}
                      {v.status === 'done' && (v.bigtext?.length ? ` · 角标 Day ${v.bigtext[0].day ?? ''}` : '')}
                      {v.status === 'failed' && ` · ${v.error || '失败'}`}
                    </span>
                    <span className="shrink-0 text-[11px] text-slate-400">{v.created_at ? new Date(v.created_at + 'Z').toLocaleString() : ''}</span>
                    {v.status !== 'generating' && (
                      confirmDelVideo === v.id ? (
                        <button
                          onClick={(e) => { e.stopPropagation(); deleteVideo(v.id) }}
                          className="btn-danger btn-xs shrink-0"
                        >
                          确认删除
                        </button>
                      ) : (
                        <button
                          onClick={(e) => {
                            e.stopPropagation()
                            setConfirmDelVideo(v.id)
                            setTimeout(() => setConfirmDelVideo((x) => (x === v.id ? null : x)), 3000)
                          }}
                          title="删除记录（连磁盘文件一起清）"
                          className="shrink-0 text-slate-300 hover:text-red-500"
                        >
                          <Trash2 size={13} />
                        </button>
                      )
                    )}
                  </button>
                  {exp && v.status === 'done' && (
                    <div className="grid grid-cols-1 gap-4 border-t border-slate-100 bg-slate-50/50 px-4 py-3 md:grid-cols-[minmax(0,320px)_1fr]">
                      <video src={`/api/avatar/videos/${v.id}/file`} controls className="w-full rounded-xl border border-slate-200" />
                      <div className="space-y-2 text-xs text-slate-500">
                        <div className="font-medium text-slate-700">{v.title}</div>
                        {v.script_preview && (
                          <div className="rounded-lg bg-white px-2.5 py-2 leading-relaxed text-slate-500 border border-slate-100">
                            脚本预览：{v.script_preview}…
                          </div>
                        )}
                        <div>时长 {Math.round(v.seconds)} 秒 · 消耗 {v.points_used} 积分 · {v.model === 1 ? '高质版' : '基础版'}{isPlatform && v.beans_used > 0 ? ` · 平台成本 ${v.beans_used} 豆` : ''}</div>
                        <div>三件套：提词器 {v.tele_len} 字 · 分镜 {v.story_count} 段 · 标题候选 {v.title_count} 条</div>
                        {v.bigtext?.length ? (
                          <div className="space-y-1">
                            <div className="font-medium text-slate-600">左上角栏目角标</div>
                            {v.bigtext.map((b, i) => (
                              <div key={i} className="flex items-center gap-2 rounded-lg bg-white px-2.5 py-1 border border-slate-100">
                                <span className="tabular-nums text-slate-400">
                                  {b.start < 0.5 && Math.abs((v.seconds || 0) - b.end) < 1.5 ? '全程' : `${Math.round(b.start)}~${Math.round(b.end)}s`}
                                </span>
                                <span className="font-medium text-slate-800">{b.text}</span>
                                {b.fallback && <span className="text-amber-600">按字数估算落位</span>}
                              </div>
                            ))}
                          </div>
                        ) : v.notes ? (
                          <div className="rounded-lg bg-amber-50 px-2.5 py-2 leading-relaxed text-amber-800">{v.notes}</div>
                        ) : null}
                        <div className="flex gap-2 pt-1">
                          <a href={`/api/avatar/videos/${v.id}/file`} target="_blank" rel="noreferrer" className="btn-ghost btn-xs text-sky-600">新窗口打开</a>
                          <a href={`/api/avatar/videos/${v.id}/file`} download={`digital_human_${v.id}.mp4`} className="btn-ghost btn-xs text-sky-600">下载</a>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
        <Pager total={total} limit={LIMIT} offset={offset} onPage={setOffset} />
      </div>

      <input
        ref={fileRef}
        type="file"
        accept="video/mp4,video/mov,video/quicktime"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) onVideoChosen(f)
          e.target.value = ''
        }}
      />
    </div>
  )
}
