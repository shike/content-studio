"""全部表模型（架构契约见 docs/TDD.md）。import 即注册到 metadata。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """统一 naive UTC（不带时区）。

    全库按 naive UTC 比较/做差；Postgres 列类型为 timestamp without time zone，
    写入/读回均为 naive；若混入带时区值，比较即抛
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
    # 活跃任务去重唯一（部分索引）：同租户同类型同 dedup_key 只能有一个 in-flight
    __table_args__ = (
        Index("ix_jobs_active_dedup", "tenant_id", "type", "dedup_key", unique=True,
              postgresql_where=text("dedup_key <> '' AND status IN ('queued','running')")),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    status: str = Field(default="queued", index=True)  # queued|running|succeeded|failed(parked 已退役→failed)|superseded(重启后已被新任务承接)
    progress: int = 0
    message: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    result: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    history: list = Field(default_factory=list, sa_column=Column(JSONB))  # 阶段轨迹 [{p,m,t}]
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


class Tenant(TimestampMixin, table=True):
    """租户（单用户版退化为「工作室档案」）：承载名称与品牌九字段。
    credits/status 两列已废（积分拆除、单管理员无禁用通道）：模型不再声明，
    生产库旧列原地保留，新库不再建列。"""
    __tablename__ = "tenants"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    brand: dict = Field(default_factory=dict, sa_column=Column(JSONB))  # 租户品牌/人设：栏目名、署名、人设描述（SaaS 产品化，2026-09-30）


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


class UserSession(TimestampMixin, table=True):
    """服务端会话（HttpOnly Cookie 携带 token，库存哈希）。tenant_id=用户所属工作室（单管理员恒 1）。"""
    __tablename__ = "user_sessions"
    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True)
    user_id: int = Field(index=True)
    tenant_id: int = Field(index=True)
    expires_at: datetime = Field(index=True)


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
    style_analysis: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    analyzed_at: Optional[datetime] = None
    stats: dict = Field(default_factory=dict, sa_column=Column(JSONB))  # 互动数（扫描时捕获）
    published_at: str = Field(default="")  # 发布时间（创作者中心回写；周报用）
    duration: int = 0                      # 时长秒


class Source(TenantScoped, TimestampMixin, table=True):
    """采集源（素材域唯一来源登记，PRD R1.3）：同行账号/话题雷达/知识库目录/热点源/手动。
    账号候选=同一张表 status=candidate（发现产出，人工定夺后转 active）；dismissed 走 config 标记。"""
    __tablename__ = "sources"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(default="peer_account", index=True)  # peer_account|radar_topic|kb_folder|hotlist|manual
    platform: str = Field(default="douyin")  # douyin|wechat|xiaohongshu|web|ima
    name: str = ""
    handle: str = ""  # 平台句柄（昵称/话题名/目录名）
    ext_id: str = Field(default="", index=True)  # 平台侧唯一 id（sec_uid/ch_id/media 目录）——发现去重锚
    url: str = ""
    status: str = Field(default="active", index=True)  # candidate|active|disabled
    config: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # config 约定：peer_account={account_type, scores, leadgen_density, keyword, signature,
    #   sample_*, dismissed}; radar_topic={seen_ids, hits, notify}; kb_folder={folders, kb_id};
    #   candidate 样本={stats:{digg,comment,...}}
    last_sync_at: Optional[datetime] = None
    note: str = ""


class Material(TenantScoped, TimestampMixin, table=True):
    """统一素材（PRD R1.1/R1.2）：外部原料+一手自有素材；成稿不进本表。
    status=素材可用性（inbox 收件箱|ready 可用|ignored 忽略|archived 归档）；
    stage=来源特有流程态（test_record: pending/testing/tested/scripted/dismissed；
    kb_doc: pending/running/done/published/dismissed/low）——两条轴互不污染。"""
    __tablename__ = "materials"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: Optional[int] = Field(default=None, foreign_key="sources.id", index=True)
    origin_type: str = Field(default="douyin_video", index=True)
    # douyin_video|douyin_image|wechat_article|web_page|kb_doc|manual_note|test_record|image|audio|video
    origin_id: str = Field(default="", index=True)  # 平台侧 id（aweme id / ima media_id）——去重锚
    origin_url: str = ""
    author: str = ""
    title: str = ""
    body: str = ""        # 图文正文 / 知识库要点 / 实测结论
    transcript: str = ""  # 视频/音频转写（拆解产物流回）
    media: dict = Field(default_factory=dict, sa_column=Column(JSONB))  # {path, duration, cover}
    stats: dict = Field(default_factory=dict, sa_column=Column(JSONB))  # {digg, comment, share, collect, followers}
    tags: list = Field(default_factory=list, sa_column=Column(JSONB))
    quality: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # quality 约定：{heat 热度分, comment_kind persona_ore|leadgen_ore, leadgen_share,
    #   gate_reason 闸判定留痕, stats_source, account_type persona|leadgen,
    #   kb: score/why_good/adapt_angle/best_quotes/category/priority,
    #   falsify: target, claim_norm}
    stage: str = Field(default="", index=True)
    status: str = Field(default="inbox", index=True)
    content_hash: str = Field(default="", index=True)  # origin 去重哈希（origin_type+origin_id/url+title）
    collected_at: datetime = Field(default_factory=utcnow)
    published_at: Optional[datetime] = None  # 内容发布时间（新鲜窗/回填窗的判据）
    note: str = ""


class MaterialAnalysis(TimestampMixin, table=True):
    """素材加工产物版本留痕（PRD R1.5）：拆解/评论存档/摘要/风格标注按 kind+version 追加。
    同 material+kind 取最新 version；写入不覆盖旧行（分析可回溯）。"""
    __tablename__ = "material_analyses"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="materials.id", index=True)
    kind: str = Field(index=True)  # breakdown 拆解|comments 评论存档|style 风格|summary 摘要
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    text: str = ""
    model: str = ""
    version: int = 1


class MaterialUsage(TenantScoped, TimestampMixin, table=True):
    """素材引用追溯（PRD R1.6）：哪条素材被哪个选题/稿件用了（只追溯，不做效果归因）。"""
    __tablename__ = "material_usages"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    material_id: int = Field(foreign_key="materials.id", index=True)
    target_type: str = Field(default="content", index=True)  # idea|content
    target_id: int = Field(default=0, index=True)
    role: str = "source"  # source|evidence|quote|inspiration


# ── 内容域（重构 P3，2026-11-16）：channels/ideas/contents/publications/channel_styles ──
# 旧内容表（topic_cards/scripts/articles/style_profiles）模型已删：数据迁新表后原地只读保留
# （schema_audit 白名单，P5 收口统一 DROP）。内容状态机（四渠道共用，PRD R0.8）：
# idea: draft→approved→consumed（discarded/jail 旁路）；content: drafting→review→ready→published（failed 可重试）。


class Channel(TimestampMixin, table=True):
    """渠道注册表（PRD R8.2）：四条内容线的差异全部进 config（取料规则/提示词组/闸线/
    产物形态/发布方式/节奏），新增渠道不改代码结构。种子 4 行（migrate_content 播种）。"""
    __tablename__ = "channels"
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True)  # persona|leadgen|wechat|xhs
    name: str = ""
    platform: str = "douyin"  # douyin|wechat|xiaohongshu
    enabled: bool = True
    config: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # config 约定：{positioning, audience, persona_note, prompt_set, gates:{...},
    #   publish_mode: manual|wechat_api, cadence, cta_policy}


class Idea(TenantScoped, TimestampMixin, table=True):
    """统一选题/靶子卡（PRD R0.8）：四渠道共用一条「素材→选题→稿件」链的选题层。
    status：draft 待定夺 | approved 已备稿 | consumed 稿件已定稿 | discarded 不做 |
    jail 法官弃卡（窗口内可捞）。人设号字段语义与旧靶子卡一致（judge/gates/spice）。"""
    __tablename__ = "ideas"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channels.id", index=True)
    batch_date: str = Field(default="", index=True)  # YYYY-MM-DD 批次（本地自然日）
    source: str = ""  # douyin_peer|radar|douyin_hot|external|self_story|manual|hotspot|kb_item|material
    title_hint: str = ""  # 选题标题快照（手动/热点/笔记线；人设线空=钩子即题）
    angle: str = ""  # 打法/切角（手动选题可带）
    spine: str = ""  # 主线半句话（写作纪律不出口）
    hook: str = ""  # 三秒钩子
    spice: str = ""  # low|mid|high
    takeaways: list = Field(default_factory=list, sa_column=Column(JSONB))
    judge: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    gates: list = Field(default_factory=list, sa_column=Column(JSONB))
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # payload 约定：persona={pool 四证}；wechat={hotspot, why_now, angle_note}；xhs={material_id}
    status: str = Field(default="draft", index=True)
    superseded_by: Optional[int] = None
    content_id: Optional[int] = Field(default=None, index=True)  # 备稿产出的稿件（原 script_id 语义）
    motif_id: Optional[int] = Field(default=None, foreign_key="motifs.id", index=True)  # 派生母题（M9）
    note: str = ""


class Motif(TenantScoped, TimestampMixin, table=True):
    """母题（M9 2026-11-22）：一个内容主题派生四线成品（口播/长文/小红书/朋友圈）。

    各成品独立走各线生命周期；母题层管派生、追踪矩阵、跨线不重做。"""
    __tablename__ = "motifs"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = ""
    note: str = ""  # 切角/备注
    status: str = Field(default="active", index=True)  # active|archived
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # payload 约定：{source_kind(素材类型), source_id(素材 id), angle_note}


class Content(TenantScoped, TimestampMixin, table=True):
    """统一稿件（PRD R0.8）：kind=video_script|long_article|note。
    status：drafting 生成/打磨中 | review 待定稿/待决策 | ready 定稿/待发 |
    published 已发布登记 | failed 失败可重试 | archived 归档。
    渠道旧状态串保存在 payload.*_state（门面输出零漂移；迁移映射见 TDD §15）。"""
    __tablename__ = "contents"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channels.id", index=True)
    idea_id: Optional[int] = Field(default=None, foreign_key="ideas.id", index=True)
    parent_content_id: Optional[int] = Field(default=None, index=True)  # 派生来源（脚本→长文）
    kind: str = Field(default="video_script", index=True)
    title: str = ""  # 主标题（脚本=title_hint 快照；长文=正题；笔记=笔记标题）
    body: str = ""  # 正文（脚本=终稿台词；长文=md；笔记=正文）
    versions: list = Field(default_factory=list, sa_column=Column(JSONB))  # 脚本多版本
    status: str = Field(default="drafting", index=True)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # payload 约定：
    #   video_script: {script_state(generating/polishing/generated/final/failed), teleprompter_text,
    #     storyboard, title_candidates, hook, spine, spice, angle, falsify_item_id, ore_id,
    #     account_type, title_hint}
    #   long_article: {article_state(pending/generating/draft/edited/rendered/published/failed),
    #     note, kb_sources, html, stats(48h 回流), edited}
    #   note: {note_state(drafting/review/ready/published/failed), tags, cover, material_ids}
    note: str = ""


class Publication(TenantScoped, TimestampMixin, table=True):
    """发布登记（PRD R0.4/R4.5/R5.4）：人工发布登记链接，或公众号 API 发布回执。
    发布永远可回退人工；external_id=公众号 draft media_id（API 路径）。"""
    __tablename__ = "publications"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    content_id: int = Field(foreign_key="contents.id", index=True)
    platform: str = "wechat"  # wechat|xiaohongshu|douyin|channels
    url: str = ""
    external_id: str = ""  # 平台侧 id（公众号 draft media_id）
    published_at: str = ""  # 发布时间（YYYY-MM-DD HH:MM，登记口径与旧 articles 一致）
    publish_mode: str = "manual"  # manual|wechat_draft|wechat_submit
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))


class ChannelStyle(TenantScoped, TimestampMixin, table=True):
    """渠道风格画像版本（PRD X12）：按渠道版本化，历史可回溯。
    （迁移自 style_profiles；account_name 进 payload——画像按渠道不再按账号名区分。）"""
    __tablename__ = "channel_styles"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channels.id", index=True)
    version: int = 1
    digest: str = ""  # 200~300 字可执行风格描述
    traits: list = Field(default_factory=list, sa_column=Column(JSONB))
    exemplars: list = Field(default_factory=list, sa_column=Column(JSONB))
    based_on: int = 0  # 基于多少条视频
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))


# ── 数据域（重构 P4，2026-11-16）：accounts/metrics_snapshots/analytics_reports/pipeline_ledger ──
# 旧数据面 4 表（watch_accounts/account_metrics_daily/video_weekly_reports/daily_ledger）模型已删，
# 数据迁新表后原地只读保留（schema_audit 白名单，P5 收口统一 DROP）。


class Account(TenantScoped, TimestampMixin, table=True):
    """自有账号（PRD R6.1）：四个渠道各登记账号——数据主体，也是自有素材/风格研究来源。
    auth JSONB 只写不读（创作者中心登录态等，红线：仅自有账号数据回流）。"""
    __tablename__ = "accounts"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="channels.id", index=True)
    platform: str = "douyin"  # douyin|wechat|xiaohongshu|channels
    name: str = ""
    handle: str = ""
    ext_id: str = Field(default="", index=True)  # 平台侧账号 id（sec_uid 等）
    profile_url: str = ""
    auth: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # auth 约定：douyin={creator_cookie}（创作者中心登录态，仅自有账号数据回流）
    enabled: bool = True
    last_sync_at: Optional[datetime] = None
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # payload 约定：{_legacy_id 迁移指纹, 渠道扩展位}
    note: str = ""


class MetricsSnapshot(TenantScoped, TimestampMixin, table=True):
    """统一指标事实表（PRD R6.2）：主体（账号/作品）× 日期 × JSONB 指标。
    同主体同日一行，按列合并补齐（空值不覆盖，不整行覆盖）；缺失日不补零。"""
    __tablename__ = "metrics_snapshots"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    subject_type: str = Field(default="account", index=True)  # account|publication
    subject_id: int = Field(default=0, index=True)
    platform: str = "douyin"
    date: str = Field(default="", index=True)  # YYYY-MM-DD（平台口径，通常 T-1）
    metrics: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # account: {fans,new_fans,cancel_fans,profile_views,play,digg,comment,share,
    #           account_search,post_search,leads}
    # publication(wechat): {reads,welook,follows,leads}
    # publication(douyin/xhs): {exposure,play,likes,comments,shares,collects,follows,leads}
    source: str = "manual"  # creator_center|manual|public


class AnalyticsReport(TenantScoped, TimestampMixin, table=True):
    """分析报告缓存（PRD R6.4）：周报/阶段报告（数字=事实表重算，summary=LLM 解读）。
    channel_id=0 表示全渠道。可随时重算，不散落字段。"""
    __tablename__ = "analytics_reports"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(default="weekly", index=True)  # weekly|monthly
    channel_id: int = Field(default=0, index=True)  # 0=全渠道
    period_start: str = Field(default="", index=True)  # 周一日期 YYYY-MM-DD
    payload: dict = Field(default_factory=dict, sa_column=Column(JSONB))


class PipelineLedger(TenantScoped, TimestampMixin, table=True):
    """产线当日账（PRD R4.2 当日账必落）：每环节收尾写一行，次日晨检对账——
    任务成功但无产出且无原因 = 异常（堵「成功但空转」的洞）。"""
    __tablename__ = "pipeline_ledger"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    day: str = Field(default="", index=True)  # UTC 日期 YYYY-MM-DD
    pipeline: str = ""  # material_kb_mine | wechat_adapt | persona_ideas | wechat_hotspot | xhs_notes
    produced: str = ""
    reason: str = ""
    ok: bool = True
