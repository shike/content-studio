import HotTargets from './HotTargets'
import TopicInsights from './TopicInsights'
import LeadgenOre from './LeadgenOre'
import Watch from './Watch'
import Benchmarks from './Benchmarks'
import DiscoveredAccounts from './DiscoveredAccounts'
import Scripts from './Scripts'
import Workshop from './Workshop'
import ContentItems from './ContentItems'
import ArticleStyle from './ArticleStyle'
import AccountTrends from './AccountTrends'

/**
 * 三段式阶段页容器（2026-11-14 用户定版：监控→选题→内容，菜单即流水线）。
 * 页内 tab 承载目的地（2026-11-14 用户批准破「目的地不藏页内 tab」旧规）；
 * 带线的 tab 页头右侧渲染 人设|获客 线切换——号型是视图属性，不是菜单结构。
 * tab id 编码：`段内名` 或 `段内名:persona|leadgen`（带线 tab 的初始线）。
 */
type Line = 'persona' | 'leadgen'

interface TabDef {
  id: string
  label: string
  line?: boolean  // 带线切换（人设|获客）
}

function parseTab(tab: string, tabs: TabDef[]): { id: string; line: Line } {
  const [rawId, rawLine] = tab.split(':')
  const def = tabs.find((t) => t.id === rawId) ?? tabs[0]
  return { id: def.id, line: rawLine === 'leadgen' ? 'leadgen' : 'persona' }
}

function StageShell(props: {
  tabs: TabDef[]
  tab: string
  onTab: (t: string) => void
  children: (c: { id: string; line: Line }) => React.ReactNode
}) {
  const { id, line } = parseTab(props.tab, props.tabs)
  const active = props.tabs.find((t) => t.id === id) ?? props.tabs[0]
  return (
    <div>
      <div className="mb-5 flex items-center gap-1.5 border-b border-[#E8ECF0] pb-2.5">
        <div className="flex flex-1 flex-wrap items-center gap-1.5">
          {props.tabs.map((t) => (
            <button key={t.id}
              onClick={() => props.onTab(t.line ? `${t.id}:${line}` : t.id)}
              className={`rounded-full px-3.5 py-1.5 text-[13px] font-medium transition ${
                id === t.id ? 'bg-slate-900 text-white' : 'text-slate-500 hover:bg-slate-100'
              }`}>
              {t.label}
            </button>
          ))}
        </div>
        {active.line && (
          <div className="flex shrink-0 items-center gap-1 rounded-full bg-slate-100 p-0.5">
            {(['persona', 'leadgen'] as Line[]).map((ln) => (
              <button key={ln}
                onClick={() => props.onTab(`${id}:${ln}`)}
                className={`rounded-full px-2.5 py-1 text-[12px] font-medium transition ${
                  line === ln ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'
                }`}>
                {ln === 'persona' ? '人设' : '获客'}
              </button>
            ))}
          </div>
        )}
      </div>
      {props.children({ id, line })}
    </div>
  )
}

/** 监控（一批人 + 一批话题）：监控号 / 话题雷达 / 拆解库 / 账号候选 */
export function MonitorStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  return (
    <StageShell tab={props.tab} onTab={props.onTab} tabs={[
      { id: 'watch', label: '监控号', line: true },
      { id: 'radar', label: '话题雷达' },
      { id: 'bench', label: '拆解库', line: true },
      { id: 'cand', label: '账号候选', line: true },
    ]}>
      {({ id, line }) => (
        <>
          {id === 'watch' && <Watch onNavigate={props.onNavigate} accountType={line} />}
          {id === 'radar' && <TopicInsights />}
          {id === 'bench' && <Benchmarks onNavigate={props.onNavigate} accountType={line} />}
          {id === 'cand' && <DiscoveredAccounts accountType={line} />}
        </>
      )}
    </StageShell>
  )
}

/** 选题（拍什么的唯一决策地）：人设靶子 / 获客选题（=获客矿素材流，分型路由的消费面） */
export function TopicStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  return (
    <StageShell tab={props.tab} onTab={props.onTab} tabs={[
      { id: 'persona', label: '人设靶子' },
      { id: 'leadgen', label: '获客选题' },
    ]}>
      {({ id }) => (
        <>
          {id === 'persona' && <HotTargets onNavigate={props.onNavigate} />}
          {id === 'leadgen' && <LeadgenOre />}
        </>
      )}
    </StageShell>
  )
}

/** 内容（怎么拍+发+数据回流）：视频稿 / 公众号三视图 / 打法卡库 / 经营数据 */
export function ContentStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  return (
    <StageShell tab={props.tab} onTab={props.onTab} tabs={[
      { id: 'scripts', label: '视频稿', line: true },
      { id: 'workshop', label: '公众号·工场' },
      { id: 'items', label: '公众号·清单' },
      { id: 'style', label: '公众号·画像' },
      { id: 'bench_leadgen', label: '打法卡库' },
      { id: 'own', label: '经营数据' },
    ]}>
      {({ id, line }) => (
        <>
          {id === 'scripts' && <Scripts onNavigate={props.onNavigate} accountType={line} />}
          {id === 'workshop' && <Workshop />}
          {id === 'items' && <ContentItems onNavigate={props.onNavigate} />}
          {id === 'style' && <ArticleStyle />}
          {id === 'bench_leadgen' && <Benchmarks onNavigate={props.onNavigate} accountType="leadgen" />}
          {id === 'own' && <AccountTrends onNavigate={props.onNavigate} />}
        </>
      )}
    </StageShell>
  )
}
