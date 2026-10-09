"""全部表模型（docs/技术方案.md §5）。import 即注册到 metadata。"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, UniqueConstraint
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
    运行时可改项（LLM 通道与密钥、检索通道、ASR 模型、数字人凭证、扫描计划）存这里——
    随数据库每日备份一起进快照，服务器上无需改文件。
    """
    __tablename__ = "app_settings"
    key: str = Field(primary_key=True)
    value: str = ""  # JSON 编码（str/int/bool 统一）


class Job(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "jobs"
    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    status: str = Field(default="queued", index=True)  # queued|running|parked(额度挂起)|succeeded|failed|superseded(重启后已被新任务承接)
    progress: int = 0
    message: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    history: list = Field(default_factory=list, sa_column=Column(JSON))  # 阶段轨迹 [{p,m,t}]
    error: Optional[str] = None
    # 任务体系五要素（P1 骨架）：去重键 / 分道 / 重试策略状态
    dedup_key: str = Field(default="", index=True)  # 类型内去重键（空=不去重）；活跃任务(queued/running/parked)中唯一
    lane: str = Field(default="heavy")  # heavy(LLM串行) | light(并发池) | external(外部渲染) | scheduled(定时)
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
    kind: str = Field(default="job", index=True)  # job=任务失败 | entity=实体孤儿（无在途任务的失败实体）
    fail_class: str = ""  # 最近一次的失败分类
    label: str = ""  # 人类可读类别名
    root_cause: str = ""  # 根因与修复说明（人工沉淀）
    status: str = Field(default="open", index=True)  # open 待处置 | fixed 已修复 | ignored 已忽略
    count: int = 0
    sample_error: str = ""  # 最近一次完整错误（诊断线索）
    sample_job_id: int = 0
    last_type: str = ""  # 最近一次出现的任务类型


class LLMCall(TimestampMixin, table=True):
    __tablename__ = "llm_calls"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    purpose: str = Field(default="", index=True)
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_est: float = 0.0
    latency_ms: int = 0
    ok: bool = True


class Topic(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "topics"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = ""
    angle: str = ""
    audience: str = "both"  # boss | fde | both
    source_type: str = "manual"  # idea | benchmark | manual
    source_ref: str = ""
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON))
    score: Optional[float] = None
    score_breakdown: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "draft"  # draft | approved | rejected | produced


class ResearchReport(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "research_reports"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    content: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_est: float = 0.0


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
    source: str = "douyin"  # douyin | local
    scan_status: str = Field(default="approved")  # pending(待定夺) | approved(已批准) | ignored(已忽略)


class Tenant(TimestampMixin, table=True):
    """租户：数据隔离边界。租户内全部成员共享数据与积分池。"""
    __tablename__ = "tenants"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    credits: int = Field(default=0)  # 共享积分池（1 积分 = ¥0.01 成本锚点）
    status: str = Field(default="active", index=True)  # active | disabled
    brand: dict = Field(default_factory=dict, sa_column=Column(JSON))  # 租户品牌/人设：栏目名、署名、人设描述（SaaS 产品化，2026-09-30）


class User(TimestampMixin, table=True):
    """租户内用户。角色：platform_admin（平台）| tenant_admin（租户管理）| member。"""
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(index=True)
    username: str = Field(unique=True)
    password_hash: str = ""
    role: str = Field(default="member", index=True)  # platform_admin | tenant_admin | member
    display_name: str = ""
    status: str = Field(default="active", index=True)  # active | disabled
    must_change_password: bool = Field(default=False)


class UserSession(TimestampMixin, table=True):
    """服务端会话（HttpOnly Cookie 携带 token，库存哈希）。

    tenant_id = 会话的**当前活跃租户**（一人多租户切换的落点，服务端存储制：
    切换租户=改本行，cookie 不变；登录时填默认租户 users.tenant_id）。"""
    __tablename__ = "user_sessions"
    id: Optional[int] = Field(default=None, primary_key=True)
    token_hash: str = Field(unique=True)
    user_id: int = Field(index=True)
    tenant_id: int = Field(index=True)
    expires_at: datetime = Field(index=True)


class TenantMembership(TimestampMixin, table=True):
    """成员关系：一个用户可归属多个租户（一人多租户），每租户一个角色。

    users.tenant_id 保留为「默认租户」（登录初始落点）；users.role 只承载
    platform_admin 全局位，本表的 role 才是租户内的实际角色。"""
    __tablename__ = "tenant_memberships"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    tenant_id: int = Field(index=True)
    role: str = Field(default="member")  # tenant_admin | member（platform_admin 不入此列）
    __table_args__ = (UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),)


class CreditTransaction(TimestampMixin, table=True):
    """积分流水：充值/消耗/管理员调整，全量可审计。"""
    __tablename__ = "credit_transactions"
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(index=True)
    delta: int  # 正=充值/调整增加，负=消耗
    balance_after: int
    reason: str = ""
    kind: str = Field(default="adjust", index=True)  # consume | topup | adjust
    ref: str = ""


class BeanLedger(TimestampMixin, table=True):
    """蝉豆消耗流水：独立于视频行记账——删除视频/清理记录不影响消耗统计。

    avatar_videos 被删除后其历史消耗无从追溯（旧账已失），自本表启用起，
    每次成功渲染记一行，设置页蝉豆合计以流水为准。"""
    __tablename__ = "bean_ledger"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = 0
    beans: int = 0  # 蝉镜成本口径（平台）
    points: int = 0  # 租户实际结算积分（按秒：基础 3/秒、高质 6/秒）
    seconds: float = 0.0
    model: int = 0  # 画质档：0 基础版 | 1 高质版


class RadarTopic(TenantScoped, TimestampMixin, table=True):
    """话题雷达：监控的抖音话题（泛 AI 类），按日巡检话题下新视频测热度。

    热度代理=新增视频速度（话题接口免登录 但无互动数字）；单次巡检新增
    ≥5 条判为飙升，发 macOS 通知。seen_ids 记已见视频防重复计数。"""
    __tablename__ = "radar_topics"
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(default=0, index=True)
    ch_id: str = Field(unique=True)
    name: str = ""
    enabled: bool = True
    notify: bool = True
    seen_ids: list = Field(default_factory=list, sa_column=Column(JSON))  # 已见视频 id（尾端截断 300）
    recent: list = Field(default_factory=list, sa_column=Column(JSON))  # 上次巡检新视频 [{id, desc}]
    hits: list = Field(default_factory=list, sa_column=Column(JSON))  # 低粉高赞清单 [{id, desc, author, digg, followers, ratio}]
    new_since_last: int = 0
    last_checked_at: Optional[datetime] = None


class Script(TenantScoped, TimestampMixin, table=True):
    __tablename__ = "scripts"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    topic_id: Optional[int] = Field(default=None, foreign_key="topics.id", index=True)
    versions: list = Field(default_factory=list, sa_column=Column(JSON))
    final_text: str = ""
    teleprompter_text: str = ""
    storyboard: list = Field(default_factory=list, sa_column=Column(JSON))
    title_candidates: list = Field(default_factory=list, sa_column=Column(JSON))
    status: str = "generating"  # generating | polishing | final


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
    title_alts: list = Field(default_factory=list, sa_column=Column(JSON))  # 备选标题
    html: str = ""
    status: str = "draft"  # draft | edited | rendered | published


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


class Dismissal(TenantScoped, TimestampMixin, table=True):
    """队列「不做」出口：把某个资产从待写长文/待发布/待补数据队列里永久忽略。"""
    __tablename__ = "dismissals"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    scope: str = Field(index=True)  # article | publish | metrics
    asset_id: int = Field(index=True)


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


class AvatarConfig(TenantScoped, TimestampMixin, table=True):
    """口播数字人配置：蝉镜克隆形象 + 配套音色（2026-09-24 引擎定版）。"""
    __tablename__ = "avatar_configs"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    engine: str = "chanjing"  # 蝉镜为唯一主线引擎（老引擎已下线）
    chanjing_person_id: str = ""  # API 克隆形象 ID（空=用内置默认形象）
    chanjing_audio_man: str = ""  # 配套音色 ID（空=用内置默认音色）
    chanjing_pic_url: str = ""  # 形象缩略图（克隆训练完成后回填）
    chanjing_preview_url: str = ""  # 形象预览视频（克隆训练完成后回填）
    image_path: str = ""  # 遗留：老引擎形象照路径，新流程不用
    voice_type: str = ""  # 遗留列
    speaker: str = ""  # 遗留列
    speed_ratio: float = 1.0
    bg_color: str = "#1a1a2e"
    visual_resource_id: str = ""  # 遗留列
    performance_prompt: str = ""  # 遗留列


class AvatarVideo(TenantScoped, TimestampMixin, table=True):
    """数字人视频生成记录。"""
    __tablename__ = "avatar_videos"
    tenant_id: int = Field(default=0, index=True)
    id: Optional[int] = Field(default=None, primary_key=True)
    script_id: int = 0
    avatar_id: int = 0
    audio_path: str = ""
    video_path: str = ""
    status: str = "generating"  # generating | done | failed
    error: Optional[str] = None
    beans_used: int = 0  # 蝉豆消耗（前后余额差，分段含全部段；平台成本口径）
    points_used: int = 0  # 本次结算的租户积分（按秒：基础 3/秒、高质 6/秒）
    seconds: float = 0.0  # 成片时长
    bigtext: list = Field(default_factory=list, sa_column=Column(JSON))  # 本片用到的要点大字（含起止秒）
    notes: str = ""  # 大字降级/关闭等说明（可见降级）
    model: int = 0  # 画质档：0 基础版 | 1 高质版 lip-sync pro