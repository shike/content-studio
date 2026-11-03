import { useState } from 'react'
import { api, waitJob } from '../api'
import Benchmarks from './Benchmarks'
import DiscoveredAccounts from './DiscoveredAccounts'
import Watch from './Watch'

type Line = 'persona' | 'leadgen'

/** 分线情报区（2026-11-03 IA 定版：情报中心解散并入产线）——每条产线的「情报」标签内容。
人设号=对标情报（拆解资料库/候选定夺/监控清单）；获客号=挖号情报（纯获客候选/获客监控/打法卡库）。
顶部贴链框是本线唯一粘贴入口：视频链接→拆解入库，主页分享/短链→判号入候选。 */
export default function LineIntel({ line }: { line: Line }) {
  const tabs: [string, string][] = line === 'persona'
    ? [['bench', '拆解资料库'], ['cand', '候选定夺'], ['mon', '监控清单']]
    : [['cand', '纯获客候选'], ['mon', '获客监控'], ['bench', '打法卡库']]
  const [tab, setTab] = useState(tabs[0][0])
  const [busy, setBusy] = useState('')
  const [verdict, setVerdict] = useState<{ name: string; fans: number; pure: boolean; reason: string } | null>(null)
  const [error, setError] = useState('')
  const [url, setUrl] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  const isVideoLink = (u: string) =>
    /\/video\/\d+/.test(u) || /share\/video\//.test(u)

  const paste = () =>
    (async () => {
      const u = url.trim()
      if (!u) return
      setBusy('解析中…（10~30 秒）')
      setVerdict(null)
      setError('')
      try {
        // 路由按线意图：获客号线=贴链判号（judge-account 本就兼容视频/主页/短链三种分享）；
        // 人设号线=视频/短链→拆解，主页链接→判号（v.douyin.com 短链主页/视频无法从外观区分，
        // 但人设线贴短链的常态是拆视频；要判作者用获客号线或直接贴 /user/ 主页链接）
        if (line === 'leadgen' || !isVideoLink(u)) {
          const r = await api<{ name: string; fans: number; pure: boolean; reason: string }>(
            '/watch/judge-account', { method: 'POST', body: JSON.stringify({ url: u }) })
          setVerdict(r)
          setBusy('')
        } else {
          const r = await api<{ job_id: number; crawl_warning?: string }>('/analyze/video', {
            method: 'POST',
            body: JSON.stringify({ url: u }),
          })
          await waitJob(r.job_id, (j) => setBusy(`拆解中：${j.progress}% ${j.message}`))
          setBusy('拆解完成，已进拆解资料库待定夺')
        }
        setUrl('')
        setReloadKey((k) => k + 1)
        setTimeout(() => setBusy(''), 5000)
      } catch (e) {
        setError(String(e).replace(/^Error:\s*/, ''))
        setBusy('')
      }
    })()

  return (
    <div>
      {/* 本线唯一贴链入口：视频链→拆解 · 主页链→判号 */}
      <div className="mb-3 flex items-center gap-3 rounded-xl border border-[#e8ecf0] px-4 py-2.5">
        <input value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') paste() }}
          placeholder="贴抖音链接回车：视频链接自动拆解入库 · 主页分享链接自动判号入候选"
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-300" />
        <button onClick={paste} disabled={!url.trim() || !!busy}
          className="btn-accent shrink-0 rounded-full px-4 py-1.5 text-xs disabled:opacity-40">入库</button>
      </div>
      {(busy || error) && (
        <div className={`mb-3 text-sm ${error ? 'text-red-600' : 'text-slate-500'}`}>{error || busy}</div>
      )}
      {verdict && (
        <div className="mb-3 rounded-xl border border-[#e8ecf0] bg-slate-50 px-4 py-3 text-sm">
          <b>{verdict.name}</b>（{verdict.fans} 粉）
          {verdict.pure
            ? <span className="ml-2 rounded bg-[#0a2f24] px-2 py-0.5 text-xs text-[#e8c97f]">纯获客 · 已入候选</span>
            : <span className="ml-2 rounded bg-slate-200 px-2 py-0.5 text-xs text-slate-500">非纯 · 已入候选待定夺</span>}
          <span className="ml-2 text-xs text-slate-400">{verdict.reason}</span>
        </div>
      )}

      <div className="mb-3 flex gap-1.5">
        {tabs.map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`rounded-full px-4 py-1.5 text-sm ${tab === k ? 'bg-zinc-900 text-white' : 'border border-[#e8ecf0] bg-white text-slate-500 hover:bg-slate-50'}`}>
            {label}
          </button>
        ))}
      </div>

      {/* reloadKey：贴链入库后让列表重拉（组件按 key 重建，取数 effect 随挂载执行） */}
      {tab === 'bench' && <Benchmarks key={`b${reloadKey}`} accountType={line} />}
      {tab === 'cand' && <DiscoveredAccounts key={`c${reloadKey}`} accountType={line} />}
      {tab === 'mon' && <Watch key={`m${reloadKey}`} accountType={line} />}
    </div>
  )
}
