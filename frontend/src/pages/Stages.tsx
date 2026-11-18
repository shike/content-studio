import HotTargets from './HotTargets'
import Materials from './Materials'
import LeadgenOre from './LeadgenOre'
import Scripts from './Scripts'
import Workshop from './Workshop'
import ContentItems from './ContentItems'
import ArticleStyle from './ArticleStyle'
import AccountTrends from './AccountTrends'

/**
 * 侧重版导航（重构 P2 2026-11-16：素材库成为唯一进料口，取代旧「监控」页——
 * 监控号/话题雷达/拆解库/账号候选全部收进素材库四个 tab）。
 * tab id 编码：`段内名` 或 `段内名:persona|leadgen`。
 */
type Line = 'persona' | 'leadgen'

interface TabDef {
  id: string
  label: string
  line?: boolean
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

/** 人设号（主线）：选题 / 视频稿 / 画像——全闭环一页 */
export function PersonaStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  return (
    <StageShell tab={props.tab} onTab={props.onTab} tabs={[
      { id: 'topics', label: '选题' },
      { id: 'scripts', label: '视频稿' },
      { id: 'own', label: '画像' },
    ]}>
      {({ id }) => (
        <>
          {id === 'topics' && <HotTargets onNavigate={props.onNavigate} />}
          {id === 'scripts' && <Scripts accountType="persona" />}
          {id === 'own' && <AccountTrends onNavigate={props.onNavigate} />}
        </>
      )}
    </StageShell>
  )
}

/** 获客（副线）：获客选题（获客矿+建稿） / 视频稿 */
export function LeadgenStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  return (
    <StageShell tab={props.tab} onTab={props.onTab} tabs={[
      { id: 'topics', label: '获客选题' },
      { id: 'scripts', label: '视频稿' },
    ]}>
      {({ id }) => (
        <>
          {id === 'topics' && <LeadgenOre onNavigate={props.onNavigate} />}
          {id === 'scripts' && <Scripts accountType="leadgen" />}
        </>
      )}
    </StageShell>
  )
}

/** 公众号（副线）：工场 / 清单 / 画像 */
export function ArticleStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  return (
    <StageShell tab={props.tab} onTab={props.onTab} tabs={[
      { id: 'workshop', label: '工场' },
      { id: 'items', label: '清单' },
      { id: 'style', label: '画像' },
    ]}>
      {({ id }) => (
        <>
          {id === 'workshop' && <Workshop />}
          {id === 'items' && <ContentItems onNavigate={props.onNavigate} />}
          {id === 'style' && <ArticleStyle />}
        </>
      )}
    </StageShell>
  )
}

/** 素材库（唯一进料口，重构 P2）：监控清单 / 知识库（2026-11-18 推荐选题移「今天」页） */
export function MaterialsStage(props: { tab: string; onTab: (t: string) => void; onNavigate: (k: string) => void }) {
  // 素材库自带两 tab（监控清单/知识库），不再叠 StageShell——旧版双排 tab 即用户指的「菜单重复」
  return <Materials onNavigate={props.onNavigate} tabId={props.tab} onTabChange={props.onTab} />
}
