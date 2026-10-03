import { FileSearch, Sparkles } from 'lucide-react'
import { Topic } from './api'

/* 选题详情正文：审阅卡片 / 选题库表格展开行 / 脚本工场「待写选题」展开行共用。
   只有一处实现，避免两个页面各写一份导致口径漂移。 */
export function DetailBody({ t, showHooks = true }: { t: Topic; showHooks?: boolean }) {
  if (!t.evidence?.search && !t.evidence?.hooks?.length && !t.evidence?.reason && !t.research_report)
    return null
  return (
    <div className="space-y-3.5">
      {t.evidence?.search && t.evidence.search.status !== 'skipped' && (
        <div
          className={`rounded-lg px-3 py-2 text-xs leading-relaxed ${
            t.evidence.search.status.startsWith('ok') ? 'bg-sky-50/70 text-slate-600' : 'bg-amber-50/70 text-amber-800'
          }`}
        >
          联网检索：
          {t.evidence.search.status.startsWith('ok')
            ? `命中 ${t.evidence.search.refs} 条参考（${
                t.evidence.search.provider === 'glm'
                  ? 'GLM 联网搜索'
                  : t.evidence.search.provider === 'cache'
                    ? '本地缓存'
                    : 'DuckDuckGo'
              }，查询「${t.evidence.search.query}」）`
            : `未生效（${t.evidence.search.status}），本报告为纯模型知识`}
          {t.evidence.search.note && `；${t.evidence.search.note}`}
        </div>
      )}
      {showHooks && t.evidence?.hooks?.length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
            <Sparkles size={12} className="text-sky-500" /> 钩子候选（点击复制）
          </div>
          <ul className="space-y-1.5">
            {t.evidence.hooks.map((h: string, i: number) => (
              <li key={i} className="rounded-lg border-l-2 border-sky-400 bg-sky-50/60 px-3 py-1.5 text-[13px] leading-relaxed text-slate-700">
                {h}
              </li>
            ))}
          </ul>
        </div>
      )}
      {t.evidence?.reason && <div className="text-xs leading-relaxed text-slate-500">评分理由：{t.evidence.reason}</div>}
      {t.research_report && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
            <FileSearch size={12} /> 深度研究报告 · {t.research_report.length} 字
          </div>
          <pre className="max-h-72 overflow-auto rounded-lg bg-slate-50 p-3.5 text-[13px] leading-relaxed whitespace-pre-wrap text-slate-700">
            {t.research_report}
          </pre>
        </div>
      )}
    </div>
  )
}
