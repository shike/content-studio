"""全部表模型（架构契约见 docs/TDD.md）。import 即注册到 metadata。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """统一 naive UTC（不带时区）。

    全库按 naive UTC 比较/做差；写入若带时区，Sqlite 存成带偏移的字符串，
    且新版 SQLModel 读回带 tzinfo——与其它 naive 值比较即抛
    "can't compare offset-naive and offset-aware"，被后台循环吞掉后静默失效。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TimestampMixin(SQLModel):
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TenantScoped:
    """租户隔离标记 mixin：挂在需要按租户隔离查询的表上。

    db.install_tenant_scope() 据此做两件事——
    select 查询自动追加 tenant_id 过滤（platform_admin/系统上下文豁免）；
    flush 前对新行自动回填提交者租户（归属=ACTOR）。"""
    pass


class AppSetting(TimestampMixin, table=True):
    """平台级运行时配置（键值对，平台单例）。

    分工：`.env` 只留引导/部署项（host/port/data_dir、Cookie Secure、登录限流阈值）；
    运行时可改项（LLM 通道与密钥、检索通道、ASR 模型、扫描计划）存这里——
    随数据库每日备份一起进快照，服务器上无需改文件。
    """
    __tablename__ = "app_settings"
    key: str = Field(primary_key=True)
    value: str = ""  # JSON 编码（str/int/bool 统一）


class Job(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "jobs"
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    status: str = Field(default="queued", index=True)  # queued|running|succeeded|failed(parked 已退役→failed)|superseded(重启后已被新任务承接)
    progress: int = 0
    message: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    history: list = Field(default_factory=list, sa_column=Column(JSON))  # 阶段轨迹 [{p,m,t}]
    error: Optional[str] = None
    # 任务体系五要素（P1 骨架）：去重键 / 分道 / 重试策略状态
    dedup_key: str = Field(default="", index=True)  # 类型内去重键（空=不去重）；活跃任务(queued/running)中唯一（部分唯一索引兜底）
    lane: str = Field(default="heavy")  # heavy(单 worker LLM 串行) | light(并发池 Semaphore 2)——实际只此两道
    retry_count: int = 0  # 已自动重试/挂起次数
    max_retries: int = 0  # 提交时按类型策略写入
    next_retry_at: Optional[datetime] = None  # 到点由 runner 重试循环放行
    fail_class: str = Field(default="")  # transient | quota | captcha | deterministic
    error_fp: str = Field(default="", index=True)  # 错误指纹（任务中台：同类失败聚类/按指纹复活）
    archived: int = 0  # 1=已归档（终态证据保全，列表默认不显示，不物理删）
    retry_of: int = 0  # 血缘：由哪个失败任务重试而来（闭环链路可视化）
    tenant_id: int = Field(default=0, index=True)  # 提交者租户（0=系统调度/自动重试）
    user_id: int = Field(default=0, index=True)  # 提交者用户（0=系统；人工重试继承原任务归属）


class FailurePattern(TimestampMixin, table=True):
    """错误指纹库（任务中台中枢）：同类失败的根因沉淀与处置状态。

    任务失败时 runner 自动按指纹 upsert（count 累计）；指纹处置状态：
    open=待处置（修完代码标 fixed 后可按指纹复活受影响任务）/ ignored=已知不修。
    """
    __tablename__ = "failure_patterns"
    id: Optional[int] = Field(default=None, primary_key=True)
    fp: str = Field(unique=True)  # 归一化错误指纹，如 llm:empty-body / asr:vocab / code:bug
    fail_class: str = ""  # 最近一次的失败分类
    label: str = ""  # 人类可读类别名
    root_cause: str = ""  # 根因与修复说明（人工沉淀）
    status: str = Field(default="open", index=True)  # open 待处置 | fixed 已修复 | ignored 已忽略
    count: int = 0
    sample_error: str = ""  # 最近一次完整错误（诊断线索）
    sample_job_id: int = 0
    last_type: str = ""  # 最近一次出现的任务类型


class Topic(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "topics"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    line: str = Field(default="persona", index=True)  # 选题线（三库独立硬隔离：persona 人设库 | leadgen 获客库；2026-11-02）
    title: str = ""
    angle: str = ""
    audience: str = "both"  # boss | fde | both
    source_type: str = "manual"  # idea | benchmark | manual | trending | self_story
    source_ref: str = ""
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    score: Optional[float] = None
    score_breakdown: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "draft"  # draft | approved | rejected | produced


class TopicCard(TenantScoped, TimestampMixin, table=True):
    """人设号靶子卡（选题工场，PRD R1.4 三原则定版 2026-11-14）。

    一张卡四面：池子证据（pool）/主线半句话（spine，写作纪律不出口）/三秒钩子（hook，
    观众听到的第一句）/裁决记录（gates 程序闸 + judge 法官三维与理由，可追溯）。
    status：draft 待定夺 | approved 已备稿 | discarded 不做 | jail 法官弃卡（窗口内可捞）。"""
    __tablename__ = "topic_cards"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_date: str = Field(default="", index=True)  # YYYY-MM-DD 批次（本地自然日）
    source: str = ""  # douyin_peer | radar | douyin_hot | external | self_story | manual
    pool: dict = Field(default_factory=dict, sa_column=Column(JSON))
    spine: str = ""  # 主线半句话（单判断；台词里禁止出现）
    hook: str = ""   # 三秒钩子（≤3秒×语速口径字数线，观点说清+抓人）
    spice: str = ""  # low | mid | high（辣度不设限，如实标注，传生成端）
    judge: dict = Field(default_factory=dict, sa_column=Column(JSON))
    gates: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="draft", index=True)
    superseded_by: Optional[int] = None  # 批次替代链（当日无下游旧卡让位）
    script_id: Optional[int] = None  # 备稿后回填（与 scripts.card_id 双向锚）


class FalsifyItem(TenantScoped, TimestampMixin, table=True):
    """实测打假清单（PRD R1.4③，2026-11-14 定版）：实测是物理动作，系统只管清单与成稿。

    result_note=实测结论自由文本（过程+关键数字），是成稿的唯一事实来源——
    没回填结论不可成稿（没有实测事实的打假稿=编造）。"""
    __tablename__ = "falsify_items"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    claim: str = ""       # 要打的主张一句话（≤60）
    target: str = ""      # 实测对象（≤40：某课/某工具/某说法）
    evidence_note: str = ""  # 线索/链接（自由文本）
    result_note: str = ""    # 实测结论（自由文本）
    status: str = Field(default="pending", index=True)  # pending|testing|tested|scripted|dismissed
    auto_source: bool = Field(default=False)  # hunt 自动猎取 vs 手动添加
    script_id: Optional[int] = None  # 成稿后回填（与 scripts.falsify_item_id 双向锚）


class ResearchReport(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "research_reports"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    content: str = ""
    model: str = ""


class BenchmarkVideo(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "benchmark_videos"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = ""
    author: str = ""
    title: str = ""
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))
    transcript: str = ""
    analysis: dict = Field(default_factory=dict, sa_column=Column(JSON))
    media_path: str = ""
    quality_score: float = 0.0  # 内容热度分 0~10（评论/2+赞/60 封顶 10，_heat_score 2026-11-11 定版）
    account_type: str = Field(default="persona")  # persona(人设) | leadgen(获客)——拆解 prompt 分叉依据
    notes: str = ""  # 自动拆解闸标记等（自动批准原因/雷达来源）
    source: str = "douyin"  # douyin | local
    scan_status: str = Field(default="approved")  # pending(待定夺) | approved(已批准) | ignored(已忽略)


class Tenant(TimestampMixin, table=True):
    """租户（单用户版退化为「工作室档案」）：承载名称与品牌九字段。
    credits/status 两列已废（积分拆除、单管理员无禁用通道）：模型不再声明，
    生产库旧列原地保留，新库不再建列。"""
    __tablename__ = "tenants"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    brand: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 租户品牌/人设：栏目名、署名、人设描述（SaaS 产品化，2026-09-30）


class User(TimestampMixin, table=True):
    """用户（单管理员定版 2026-10-27）：bootstrap 只建 platform_admin。"""
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(index=True)
    username: str = Field(unique=True)
    password_hash: str = ""
    role: str = Field(default="member", index=True)  # platform_admin（单管理员定版；tenant_admin/member 为多租户旧值，历史行兼容）
    display_name: str = ""
    status: str = Field(default="active", index=True)  # active | disabled
    must_change_password: bool = Field(default=False)


class UserSession(TimestampMixin, table=True):
    """服务端会话（HttpOnly Cookie 携带 token，库存哈希）。tenant_id=用户所属工作室（单管理员恒 1）。"""
    __tablename__ = "user_sessions"
    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True)
    user_id: int = Field(index=True)
    tenant_id: int = Field(index=True)
    expires_at: datetime = Field(index=True)


class RadarTopic(TenantScoped, TimestampMixin, table=True):
    """话题雷达：监控的抖音话题（泛 AI 类），按日巡检话题下新视频测热度。

    热度代理=新增视频速度（话题接口免登录 但无互动数字）；单次巡检新增
    ≥5 条判为飙升（通知仅 Mac 生效，服务器静默跳过）。seen_ids 记已见视频防重复计数。"""
    __tablename__ = "radar_topics"
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=0, index=True)
    ch_id: str = Field(unique=True)
    name: str = ""
    enabled: bool = True
    notify: bool = True
    seen_ids: list = Field(default_factory=list, sa_column=Column(JSON))  # 已见视频 id（尾端截断 300）
    hits: list = Field(default_factory=list, sa_column=Column(JSON))  # 低粉高赞清单 [{id, desc, author, digg, followers, ratio}]
    last_checked_at: Optional[datetime] = None


class Script(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "scripts"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    card_id: Optional[int] = Field(default=None, foreign_key="topic_cards.id", index=True)  # 靶子卡备稿（选题工场 R1.4）
    falsify_item_id: Optional[int] = Field(default=None, foreign_key="falsify_items.id", index=True)  # 实测稿备稿（R1.4③）
    versions: list = Field(default_factory=list, sa_column=Column(JSON))
    final_text: str = ""
    teleprompter_text: str = ""
    storyboard: list = Field(default_factory=list, sa_column=Column(JSON))
    title_candidates: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "generating"  # generating | polishing | generated（待定稿，徽章计数依赖）| final
    account_type: str = Field(default="persona")  # persona | leadgen（两类号：内容看板过滤/风格分叉）


class ScriptTemplate(TimestampMixin, table=True):
    __tablename__ = "script_templates"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    structure: list = Field(default_factory=list, sa_column=Column(JSON))
    source_benchmark_id: Optional[int] = Field(
        default=None, foreign_key="benchmark_videos.id"
    )
    usage_count: int = 0





class Article(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "articles"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id")
    script_id: Optional[int] = Field(default=None, foreign_key="scripts.id")
    md: str = ""
    title: str = ""  # 公众号主标题
    note: str = ""  # 产线注记（热点：/改编：/深读： 来源标签 + 审校未过记录）
    kb_sources: list = Field(default_factory=list, sa_column=Column(JSON))  # ima 知识库素材来源（2026-11-08 取材写文）
    publish_url: str = ""  # 发布登记：公众号文章链接（发布永远人工，系统只记账，2026-11-11 源文改编产线）
    published_at: str = ""  # 发布登记时间
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 48h 回流 {reads,welook,follows,leads,filled_at}
    html: str = ""
    status: str = "draft"  # pending 待写 | generating | failed | draft | edited | rendered | published


# kb_documents / kb_topics 两表（2026-11-09 知识库选题线）模型已随选题池整池退役删除
# （2026-11-13 用户裁决）：模型删=新库不再建，生产库旧表不动，对齐 harvest_profiles 划线口径。
# 同批划线退役的列：research_reports.tokens_in/tokens_out/cost_est（成本台账）、
# tenants.status、watch_candidates.leadgen_comments/recent_same、radar_topics.recent/
# new_since_last、articles.title_alts/kb_doc_ids——生产库旧列原地保留，新库不再建。


class ContentItem(TenantScoped, TimestampMixin, table=True):
    """内容清单（2026-11-11 源文改编产线）：挖掘器逐篇评估知识库，高质量源文入清单，
    改编器每日取一项改编成文——清单项 = 一篇具体源文（保骨架保原话，换判断口径）。"""
    __tablename__ = "content_items"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: str = Field(default="", index=True)  # ima 源文 id（库内唯一，防重挖重入）
    folder_name: str = ""                 # 产出来源目录
    title: str = ""                       # 源文标题
    source_author: str = ""               # 来源号/作者
    quality_score: float = 0.0            # 挖掘器评分（≥生成线才入清单；低分不落行只记日账）
    why_good: str = ""                    # 为什么值得发（一句）
    adapt_angle: str = ""                 # 改编角度（保什么骨架/换什么口径/接什么钩子）
    best_quotes: list = Field(default_factory=list, sa_column=Column(JSON))  # 逐字原话 [str]（改编时保留引用）
    category: str = Field(default="", index=True)  # 栏目：转行真话|交付现场|行业拆局|变现报价|现场实录
    priority: int = Field(default=0, index=True)  # 人工置顶权重（「明天改编」=9，取项先看它再看质量分）
    status: str = Field(default="pending", index=True)  # pending 待改编 | running 改编中 | done 已改编 | published 已发布 | dismissed 不做
    article_id: Optional[int] = Field(default=None, index=True)  # 改编产出的文章 id
    note: str = ""                        # 备注（空过原因等）


class DailyLedger(TenantScoped, TimestampMixin, table=True):
    """产线当日账（2026-11-11）：每环节收尾写一行，次日晨检对账——
    任务成功但无产出且无原因 = 异常（堵「成功但空转」的洞）。"""
    __tablename__ = "daily_ledger"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    day: str = Field(default="", index=True)  # UTC 日期 YYYY-MM-DD（runner 全线 naive-UTC）
    phase: str = ""                           # mine 挖掘 | adapt 改编
    produced: str = ""                        # 产出摘要（入清单 N 项 / 文章 #id）
    reason: str = ""                          # 空过/异常原因（有产出留空）
    ok: bool = True


class WatchAccount(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "watch_accounts"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = "douyin"  # douyin | channels
    name: str = ""
    url: str = ""  # 账号主页链接（抖音：App 内分享主页→复制链接）
    enabled: bool = True
    kind: str = Field(default="competitor")  # competitor | self（我的账号，走自我研究链）
    creator_cookie: str = Field(default="")  # 创作者中心登录态（仅 self；有它全量抓播放/点赞，无它公开路线）
    note: str = ""
    account_type: str = Field(default="persona")  # persona(立人设) | leadgen(获客)（2026-10-28 两类号策略）
    last_scan_at: Optional[datetime] = None


class WatchCandidate(TenantScoped, TimestampMixin, table=True):
    """关键词搜出的候选对标账号（watch_discover 产出，人工定夺后入监测清单）。"""
    __tablename__ = "watch_candidates"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    keyword: str = ""
    direction: str = ""
    name: str = ""
    sec_uid: str = Field(default="", index=True)
    url: str = ""  # 分享主页链接（iesdouyin share/user，扫描链路免登录 可用）
    signature: str = ""
    follower_count: int = 0
    sample_video_id: str = ""
    sample_title: str = ""
    sample_stats: str = ""  # JSON：digg/comment/share/collect
    account_type: str = Field(default="persona")  # persona | leadgen（评论区求带密度判定）
    leadgen_density: float = 0.0  # 评论区求带密度 0~1（获客信号，2026-10-28）
    pure_reason: str = ""  # 纯获客判定依据（名字/签名/近期作品命中的引流词，v3 挖号先行 2026-11-02）
    status: str = Field(default="open", index=True)  # open 待定夺 | added 已加入 | dismissed 已忽略


class ScheduleState(TimestampMixin, table=True):
    """统一调度中心运行态：注册表（代码）定义周期任务，本表存开关/钟点覆盖/上次下次。"""
    __tablename__ = "schedules"
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True)
    enabled: bool = True
    hour: Optional[int] = None  # 钟点覆盖（None=用代码默认/设置页全局钟点）
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    last_status: str = ""
    run_count: int = 0


class WeeklyReport(TenantScoped, TimestampMixin, table=True):
    """视频表现周报（按周缓存；数字为确定性聚合，summary 为 LLM 解读）。"""
    __tablename__ = "video_weekly_reports"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    week_start: str = Field(default="", index=True)  # 周一日期 YYYY-MM-DD
    report: dict = Field(default_factory=dict, sa_column=Column(JSON))


class AccountMetricsDaily(TenantScoped, TimestampMixin, table=True):
    """自有账号的日级运营指标（创作者中心 overview/all，2026-10-12 课程定位北极星）。

    每日一快照；搜索类指标平台有数日延迟，缺失日按列合并补齐（不整行覆盖）。"""
    __tablename__ = "account_metrics_daily"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str = Field(default="", index=True)  # YYYY-MM-DD（平台口径，通常 T-1）
    fans: int = 0            # 粉丝总量
    new_fans: int = 0        # 净增粉丝
    cancel_fans: int = 0     # 取关
    profile_views: int = 0   # 主页访问（课程转化北极星）
    play: int = 0            # 播放
    digg: int = 0
    comment: int = 0
    share: int = 0
    account_search: int = 0  # 账号搜索次数
    post_search: int = 0     # 作品搜索次数


class SelfVideo(TenantScoped, TimestampMixin, table=True):
    """我的账号视频 + 逐条风格标注（R9.2）。不进拆解库、不产选题候选。"""
    __tablename__ = "self_videos"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = ""
    aweme_id: str = Field(default="", index=True)
    title: str = ""
    media_path: str = ""
    transcript: str = ""
    scan_status: str = Field(default="approved")  # pending | approved | ignored
    style_analysis: dict = Field(default_factory=dict, sa_column=Column(JSON))
    analyzed_at: Optional[datetime] = None
    stats: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 互动数（扫描时捕获）
    published_at: str = Field(default="")  # 发布时间（创作者中心回写；周报用）
    duration: int = 0                      # 时长秒


class StyleProfile(TenantScoped, TimestampMixin, table=True):
    """风格画像版本快照（R9.3）。保留历史，看风格演化。"""
    __tablename__ = "style_profiles"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    account_name: str = ""
    version: int = 1
    digest: str = ""  # 200~300 字可执行风格描述
    traits: list = Field(default_factory=list, sa_column=Column(JSON))
    exemplars: list = Field(default_factory=list, sa_column=Column(JSON))
    based_on: int = 0  # 基于多少条视频
