import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiErrText } from './api'

/**
 * 页面级守卫：清错误 → 执行 → 失败 setError(apiErrText 人话) → finally 复位 busy。
 * 九页（Topics/Scripts/Watch/Benchmarks/Settings/Workshop/ContentItems/HotTargets/
 * DiscoveredAccounts）的本地 guard 副本统一起来；
 * onReset 供各页复位自有的局部 busy——Workshop 版 finally 还清 rowBusy/syncBusy 等，
 * 那是 2026-10-29 卡死 bug 修复的一部分，任何路径下局部 busy 必须复位，不可丢。
 */
export function usePageGuard(onReset?: () => void) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const resetRef = useRef(onReset)
  resetRef.current = onReset
  const guard = useCallback(async (fn: () => Promise<void>) => {
    setError('')
    try {
      await fn()
    } catch (e) {
      setError(apiErrText(e))
    } finally {
      setBusy('')
      resetRef.current?.()
    }
  }, [])
  return { guard, busy, setBusy, error, setError }
}

/**
 * 任务进度轮询（Topics 深研 / Scripts 生成共用）：enabled 时每 5s 拉一次
 * /jobs?limit=50&type=<types.join(',')>，把 running/queued 任务按 payload[idKey]
 * 聚成 { p, m }；失败静默下轮再取；disabled 时保留旧值不动（与原页内实现一致）。
 */
export function useJobProgress(types: string[], idKey: string, enabled: boolean) {
  const [prog, setProg] = useState<Record<number, { p: number; m: string }>>({})
  const typesKey = types.join(',')
  useEffect(() => {
    if (!enabled) return
    let stop = false
    const tick = async () => {
      try {
        const d = await api<{ items: { type: string; status: string; progress: number; message: string; payload: Record<string, any> }[] }>(
          `/jobs?limit=50&type=${typesKey}`,
        )
        const map: Record<number, { p: number; m: string }> = {}
        for (const j of d.items || []) {
          if (j.status !== 'running' && j.status !== 'queued') continue
          if (!types.includes(j.type)) continue
          const oid = j.payload?.[idKey]
          if (oid) map[oid] = { p: j.progress || 0, m: j.message || '' }
        }
        if (!stop) setProg(map)
      } catch { /* 静默，下轮再取 */ }
    }
    tick()
    const t = setInterval(tick, 5000)
    return () => { stop = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, typesKey, idKey])
  return prog
}

/**
 * 客户端分页（Watch 账号清单 / 候选定夺清单共用）：items 全量在手时按 size 切页，
 * 页码越界自动收敛（筛选变少时不空页）。切筛选条件时调用 setPage(0) 归位。
 */
export function useClientPager<T>(items: T[], size: number) {
  const [pageRaw, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(items.length / size))
  const page = Math.min(pageRaw, pages - 1)
  const slice = items.slice(page * size, (page + 1) * size)
  return { page, pages, setPage, slice }
}
