"""SQLite 引擎与会话。WAL 模式；库文件位于 DATA_DIR/studio.db。

租户隔离三件套（install_tenant_scope，进程级一次接入全站生效）：
- do_orm_execute 事件：所有 ORM select 自动追加 tenant_id 过滤（platform_admin/系统上下文豁免）
- before_flush 事件：新行 tenant_id=0 时自动回填提交者租户（归属=ACTOR）
- Session.get 补丁：get() 不走 do_orm_execute，按同规则命中他租户行时返回 None（端点自然 404）
"""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session as SASession, with_loader_criteria
from sqlmodel import Session, SQLModel, create_engine

from .settings import settings


def _set_pragma(dbapi_conn, _record):  # noqa: ANN001
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")  # 并发写锁等 5s 再报 locked，吸收验收 CLI/双连接抖动
    cursor.close()


settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False}
)
event.listen(engine, "connect", _set_pragma)


def install_datetime_normalizer() -> None:
    """统一 ORM 读出的时间为 naive UTC。

    背景：SQLModel 0.0.47 起 datetime 列读回带 UTC 时区（旧版为 naive），
    与全库按 naive 比较的代码混用直接抛 TypeError，且被后台循环/依赖静默吞掉
    （实测：调度中心派发失败、会话过期比对失败=登录报错）。
    在加载钩子里统一归一化，写入侧同口径（models.utcnow 为 naive）——
    库内时间在进程内恒为 naive UTC。
    """

    @event.listens_for(SASession, "loaded_as_persistent")
    def _normalize(session, instance):  # noqa: ANN001
        try:
            mapper = sa_inspect(type(instance))
        except Exception:  # noqa: BLE001 非 ORM 映射对象
            return
        for attr in mapper.column_attrs:
            try:
                v = getattr(instance, attr.key, None)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(v, datetime) and v.tzinfo is not None:
                setattr(instance, attr.key, v.astimezone(timezone.utc).replace(tzinfo=None))


install_datetime_normalizer()


def install_tenant_scope() -> None:
    """租户隔离三件套。依赖 auth.ACTOR（(tenant_id, user_id, role)），
    ACTOR 在各处理器内延迟导入（auth 与 db 互相引用，模块级会循环）。"""
    from .models import BeanLedger, TenantScoped

    @event.listens_for(SASession, "do_orm_execute")
    def _tenant_scope(orm_state):  # noqa: ANN001
        if not orm_state.is_select:
            return
        from .auth import ACTOR
        tenant_id, _uid, role = ACTOR.get()
        if not tenant_id or role == "platform_admin":
            return
        try:
            descs = orm_state.statement.column_descriptions or []
        except Exception:  # noqa: BLE001 非 ORM 语句不处理
            return
        for d in descs:
            ent = d.get("entity")
            if isinstance(ent, type) and issubclass(ent, TenantScoped):
                orm_state.statement = orm_state.statement.options(
                    with_loader_criteria(ent, ent.tenant_id == tenant_id,
                                         include_aliases=True))

    @event.listens_for(SASession, "before_flush")
    def _tenant_fill(session, _flush_context, _instances):  # noqa: ANN001
        from .auth import ACTOR
        tenant_id, _uid, _role = ACTOR.get()
        if not tenant_id:
            return
        for obj in session.new:
            if isinstance(obj, (TenantScoped, BeanLedger)) and not getattr(obj, "tenant_id", 0):
                obj.tenant_id = tenant_id

    _orig_get = SASession.get

    def _scoped_get(self, entity, ident, **kw):  # noqa: ANN001
        from .auth import ACTOR
        obj = _orig_get(self, entity, ident, **kw)
        tenant_id, _uid, role = ACTOR.get()
        if (obj is not None and tenant_id and role != "platform_admin"
                and isinstance(obj, TenantScoped) and obj.tenant_id != tenant_id):
            return None
        return obj

    SASession.get = _scoped_get


install_tenant_scope()


def backup_db(tag: str, keep: int = 14) -> "Path":
    """SQLite 在线备份（.backup API 对 WAL 安全），按 tag 滚动保留 keep 份。

    业务数据（选题/拆解/画像/台账）只有这一份库文件——磁盘故障/迁移事故/
    灾难性误删的唯一恢复手段就是它。"""
    import sqlite3

    from datetime import datetime, timezone

    bdir = settings.data_dir / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"studio-{tag}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.db"
    if settings.db_path.exists():
        src = sqlite3.connect(str(settings.db_path))
        dst = sqlite3.connect(str(dest))
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
        snaps = sorted(bdir.glob(f"studio-{tag}-*.db"))
        for old_snap in snaps[:-keep]:
            old_snap.unlink(missing_ok=True)
    return dest


def init_db() -> None:
    from . import models  # noqa: F401  确保所有表模型已注册

    if settings.db_path.exists():
        backup_db("pre-migrate", keep=5)  # 迁移前快照：启动变更库结构前先保住现场
    SQLModel.metadata.create_all(engine)
    _migrate(engine)


def _migrate(engine) -> None:
    """轻量迁移：老库补列（create_all 不改已有表）。"""
    import sqlalchemy

    # 存量配图迁移：平铺文件归属租户 1（与库内历史数据同口径）
    img_root = settings.data_dir / "article_images"
    if img_root.exists():
        legacy = [p for p in img_root.iterdir() if p.is_file()]
        if legacy:
            tgt = img_root / "1"
            tgt.mkdir(exist_ok=True)
            for p in legacy:
                p.rename(tgt / p.name)

    with engine.begin() as conn:
        cols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(watch_accounts)"))]
        if cols and "kind" not in cols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE watch_accounts ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'competitor'"))
        jcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(jobs)"))]
        if jcols and "history" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN history JSON DEFAULT '[]'"))
        if jcols and "error_fp" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN error_fp VARCHAR DEFAULT ''"))
        if jcols and "archived" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN archived INTEGER DEFAULT 0"))
        if jcols and "retry_of" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN retry_of INTEGER DEFAULT 0"))
        acols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(avatar_configs)"))]
        if acols and "visual_resource_id" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN visual_resource_id VARCHAR DEFAULT ''"))
        if acols and "performance_prompt" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN performance_prompt VARCHAR DEFAULT ''"))
        for table in ("benchmark_videos", "self_videos"):
            scols = [r[1] for r in conn.execute(sqlalchemy.text(f"PRAGMA table_info({table})"))]
            if scols and "scan_status" not in scols:
                conn.execute(sqlalchemy.text(
                    f"ALTER TABLE {table} ADD COLUMN scan_status VARCHAR NOT NULL DEFAULT 'approved'"))
        svcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(self_videos)"))]
        if svcols and "stats" not in svcols:
            conn.execute(sqlalchemy.text("ALTER TABLE self_videos ADD COLUMN stats JSON DEFAULT '{}'"))
        rtcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(radar_topics)"))]
        if rtcols and "hits" not in rtcols:
            conn.execute(sqlalchemy.text("ALTER TABLE radar_topics ADD COLUMN hits JSON DEFAULT '[]'"))
        acols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(articles)"))]
        if acols and "title" not in acols:
            conn.execute(sqlalchemy.text("ALTER TABLE articles ADD COLUMN title VARCHAR DEFAULT ''"))
        if acols and "title_alts" not in acols:
            conn.execute(sqlalchemy.text("ALTER TABLE articles ADD COLUMN title_alts JSON DEFAULT '[]'"))
        acols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(avatar_configs)"))]
        if acols and "engine" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN engine VARCHAR NOT NULL DEFAULT 'chanjing'"))
        if acols and "chanjing_person_id" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN chanjing_person_id VARCHAR DEFAULT ''"))
        if acols and "chanjing_audio_man" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN chanjing_audio_man VARCHAR DEFAULT ''"))
        if acols and "chanjing_pic_url" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN chanjing_pic_url VARCHAR DEFAULT ''"))
        if acols and "chanjing_preview_url" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE avatar_configs ADD COLUMN chanjing_preview_url VARCHAR DEFAULT ''"))
        vcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(avatar_videos)"))]
        if vcols and "beans_used" not in vcols:
            conn.execute(sqlalchemy.text("ALTER TABLE avatar_videos ADD COLUMN beans_used INTEGER DEFAULT 0"))
        if vcols and "seconds" not in vcols:
            conn.execute(sqlalchemy.text("ALTER TABLE avatar_videos ADD COLUMN seconds REAL DEFAULT 0"))
        if vcols and "model" not in vcols:
            conn.execute(sqlalchemy.text("ALTER TABLE avatar_videos ADD COLUMN model INTEGER DEFAULT 0"))
        bcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(bean_ledger)"))]
        if bcols and "points" not in bcols:
            conn.execute(sqlalchemy.text("ALTER TABLE bean_ledger ADD COLUMN points INTEGER DEFAULT 0"))
        if vcols and "bigtext" not in vcols:
            conn.execute(sqlalchemy.text("ALTER TABLE avatar_videos ADD COLUMN bigtext JSON DEFAULT '[]'"))
        if vcols and "notes" not in vcols:
            conn.execute(sqlalchemy.text("ALTER TABLE avatar_videos ADD COLUMN notes VARCHAR DEFAULT ''"))
        tcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(tenants)"))]
        if tcols and "brand" not in tcols:
            conn.execute(sqlalchemy.text("ALTER TABLE tenants ADD COLUMN brand JSON DEFAULT '{}'"))
        if vcols and "points_used" not in vcols:
            conn.execute(sqlalchemy.text("ALTER TABLE avatar_videos ADD COLUMN points_used INTEGER DEFAULT 0"))
        # 任务体系 P1 骨架：五要素列 + 活跃任务去重唯一索引
        jcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(jobs)"))]
        for col, ddl in (
            ("dedup_key", "ALTER TABLE jobs ADD COLUMN dedup_key VARCHAR DEFAULT ''"),
            ("lane", "ALTER TABLE jobs ADD COLUMN lane VARCHAR DEFAULT 'heavy'"),
            ("retry_count", "ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0"),
            ("max_retries", "ALTER TABLE jobs ADD COLUMN max_retries INTEGER DEFAULT 0"),
            ("next_retry_at", "ALTER TABLE jobs ADD COLUMN next_retry_at DATETIME"),
            ("fail_class", "ALTER TABLE jobs ADD COLUMN fail_class VARCHAR DEFAULT ''"),
            ("tenant_id", "ALTER TABLE jobs ADD COLUMN tenant_id INTEGER DEFAULT 0"),
            ("user_id", "ALTER TABLE jobs ADD COLUMN user_id INTEGER DEFAULT 0"),
        ):
            if jcols and col not in jcols:
                conn.execute(sqlalchemy.text(ddl))
        # 历史任务归属回填：归属列晚于数据存在，旧任务全部 tenant_id=0 →
        # 按业务表同款约定归租户 1；功能上线（2026-09-26）后 tenant_id=0 才专指系统任务
        conn.execute(sqlalchemy.text(
            "UPDATE jobs SET tenant_id=1 WHERE tenant_id=0 AND created_at < '2026-09-26'"))
        conn.execute(sqlalchemy.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_jobs_active_dedup ON jobs(type, dedup_key)"
            " WHERE dedup_key <> '' AND status IN ('queued','running','parked')"))
        # 多租户：业务表补 tenant_id（历史数据归租户 1）
        for table in ("articles", "avatar_configs", "avatar_videos", "benchmark_videos",
                      "dismissals", "llm_calls", "media_assets", "radar_topics",
                      "research_reports", "scripts", "self_videos", "style_profiles",
                      "topics", "video_projects", "watch_accounts", "watch_candidates",
                      "bean_ledger"):
            tcols = [r[1] for r in conn.execute(sqlalchemy.text(f"PRAGMA table_info({table})"))]
            if tcols and "tenant_id" not in tcols:
                conn.execute(sqlalchemy.text(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1"))
            if tcols:
                conn.execute(sqlalchemy.text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant ON {table}(tenant_id)"))
        # 一人多租户：存量用户按默认租户回填成员关系（表空才跑，幂等）
        mcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(tenant_memberships)"))]
        if mcols:
            n = conn.execute(sqlalchemy.text("SELECT COUNT(*) FROM tenant_memberships")).scalar() or 0
            if n == 0:
                conn.execute(sqlalchemy.text(
                    "INSERT INTO tenant_memberships (user_id, tenant_id, role, created_at, updated_at)"
                    " SELECT id, tenant_id,"
                    " CASE WHEN role='tenant_admin' THEN 'tenant_admin' ELSE 'member' END,"
                    " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM users"))


def get_session():
    with Session(engine) as session:
        yield session
