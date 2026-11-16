"""SQLite 引擎与会话。WAL 模式；库文件位于 DATA_DIR/studio.db。

租户作用域三件套（install_tenant_scope，进程级一次接入全站生效；单用户版退化为 tenant_id=1 的自动归属，product 面已无多租户概念 2026-10-27）：
- do_orm_execute 事件：所有 ORM select 自动追加 tenant_id 过滤（platform_admin/系统上下文豁免）
- before_flush 事件：新行 tenant_id=0 时自动回填提交者租户（归属=ACTOR）
- Session.get 补丁：get() 不走 do_orm_execute，按同规则命中他租户行时返回 None（端点自然 404）
"""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, inspect as sa_inspect
from sqlalchemy.orm import Session as SASession, with_loader_criteria
from sqlmodel import SQLModel, create_engine

from .settings import settings


def _set_pragma(dbapi_conn, _record):  # noqa: ANN001
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=15000")  # 并发写锁等 15s 再报 locked（2026-10-27：5s 在轻任务并发+外部写入时会进入 BUSY 风暴，实测 15s 吸收）
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
    from .models import TenantScoped

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
            if isinstance(obj, TenantScoped) and not getattr(obj, "tenant_id", 0):
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
        wccols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(watch_candidates)"))]
        if wccols and "pure_reason" not in wccols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE watch_candidates ADD COLUMN pure_reason VARCHAR DEFAULT ''"))
        # 三库独立（2026-11-02 用户定版）：topics 加 line 硬隔离，存量按判定回填
        tcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(topics)"))]
        if tcols and "line" not in tcols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE topics ADD COLUMN line VARCHAR NOT NULL DEFAULT 'persona'"))
            conn.execute(sqlalchemy.text(
                "UPDATE topics SET line='leadgen' WHERE json_extract(evidence,'$.account_type')='leadgen'"))
        # 公众号独立报题线（2026-11-02）：articles 加 note（报题备注/参考链接）
        acols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(articles)"))]
        if acols and "note" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE articles ADD COLUMN note VARCHAR DEFAULT ''"))
        # ima 知识库取材写文（2026-11-08）：素材来源落库可审计
        if acols and "kb_sources" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE articles ADD COLUMN kb_sources JSON DEFAULT '[]'"))
        # 知识库选题线（2026-11-09）：选题绑定的依据文档
        # 源文改编产线（2026-11-11）：发布登记与 48h 回流落库
        if acols and "publish_url" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE articles ADD COLUMN publish_url VARCHAR DEFAULT ''"))
        if acols and "published_at" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE articles ADD COLUMN published_at VARCHAR DEFAULT ''"))
        if acols and "stats" not in acols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE articles ADD COLUMN stats JSON DEFAULT '{}'"))
        cicols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(content_items)"))]
        if cicols and "priority" not in cicols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE content_items ADD COLUMN priority INTEGER DEFAULT 0"))
        if cols and "creator_cookie" not in cols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE watch_accounts ADD COLUMN creator_cookie VARCHAR DEFAULT ''"))
        jcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(jobs)"))]
        if jcols and "history" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN history JSON DEFAULT '[]'"))
        if jcols and "error_fp" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN error_fp VARCHAR DEFAULT ''"))
        if jcols and "archived" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN archived INTEGER DEFAULT 0"))
        if jcols and "retry_of" not in jcols:
            conn.execute(sqlalchemy.text("ALTER TABLE jobs ADD COLUMN retry_of INTEGER DEFAULT 0"))
        bmvcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(benchmark_videos)"))]
        if bmvcols and "notes" not in bmvcols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE benchmark_videos ADD COLUMN notes VARCHAR DEFAULT ''"))
        if bmvcols and "quality_score" not in bmvcols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE benchmark_videos ADD COLUMN quality_score REAL DEFAULT 0"))
        if bmvcols and "account_type" not in bmvcols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE benchmark_videos ADD COLUMN account_type VARCHAR NOT NULL DEFAULT 'persona'"))
        if bmvcols and "top_comments" not in bmvcols:  # 高赞评论原文存档（R1.4 进料：观点的矿脉，2026-11-14）
            conn.execute(sqlalchemy.text(
                "ALTER TABLE benchmark_videos ADD COLUMN top_comments JSON DEFAULT '[]'"))
        if bmvcols and "comment_kind" not in bmvcols:  # 评论区分型路由（R1.4：求学型→获客线，2026-11-14）
            conn.execute(sqlalchemy.text(
                "ALTER TABLE benchmark_videos ADD COLUMN comment_kind VARCHAR DEFAULT ''"))
        if bmvcols and "comment_leadgen_share" not in bmvcols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE benchmark_videos ADD COLUMN comment_leadgen_share REAL DEFAULT 0"))
        tccols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(topic_cards)"))]
        if tccols and "takeaways" not in tccols:  # 评论要点留痕（主线原料，2026-11-14）
            conn.execute(sqlalchemy.text("ALTER TABLE topic_cards ADD COLUMN takeaways JSON DEFAULT '[]'"))
        # failure_patterns schema 漂移修复（2026-11-15）：旧部署残留 kind NOT NULL 列（模型已退役）——
        # 新指纹建档 INSERT 必炸并打死轻道失败登记。重建表去列，数据保留。
        # schema 漂移通用重建：生产表残留模型已退役的 NOT NULL 无默认列 → INSERT 必炸
        # （2026-11-15 连出两起：failure_patterns.kind、content_items.kb_id/folder_id——
        #   第三轮减负删模型字段时未查生产表约束）。重建表对齐当前模型，数据保留，索引搬运。
        def _rebuild_drop_legacy(conn, table: str, create_sql: str, copy_select: str) -> None:
            conn.execute(sqlalchemy.text(f"ALTER TABLE {table} RENAME TO {table}_legacy"))
            conn.execute(sqlalchemy.text(create_sql))
            conn.execute(sqlalchemy.text(copy_select))
            for (isql,) in conn.execute(sqlalchemy.text(
                    f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='{table}_legacy' "
                    "AND sql IS NOT NULL")):
                # IF NOT EXISTS：RENAME 后旧索引随表带走保留原名，同名重建必撞（2026-11-15 实测）
                conn.execute(sqlalchemy.text(
                    isql.replace(f"{table}_legacy", table).replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS")
                    .replace("CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS")))
            conn.execute(sqlalchemy.text(f"DROP TABLE {table}_legacy"))
            print(f"[migrate] {table} 重建：去模型外 NOT NULL 列（数据保留）")

        def _has_legacy_col(conn, table: str, col: str) -> bool:
            cols = [r[1] for r in conn.execute(sqlalchemy.text(f"PRAGMA table_info({table})"))]
            return col in cols

        if _has_legacy_col(conn, "content_items", "kb_id"):
            _rebuild_drop_legacy(conn, "content_items",
                "CREATE TABLE content_items ("
                "created_at DATETIME, updated_at DATETIME, tenant_id INTEGER DEFAULT 0,"
                " id INTEGER PRIMARY KEY, media_id VARCHAR DEFAULT '',"
                " folder_name VARCHAR DEFAULT '', title VARCHAR DEFAULT '',"
                " source_author VARCHAR DEFAULT '', quality_score REAL DEFAULT 0,"
                " why_good VARCHAR DEFAULT '', adapt_angle VARCHAR DEFAULT '',"
                " best_quotes TEXT, category VARCHAR DEFAULT '', priority INTEGER DEFAULT 0,"
                " status VARCHAR DEFAULT 'pending', article_id INTEGER, note VARCHAR DEFAULT '')",
                "INSERT INTO content_items (created_at, updated_at, tenant_id, id, media_id,"
                " folder_name, title, source_author, quality_score, why_good, adapt_angle,"
                " best_quotes, category, priority, status, article_id, note) "
                "SELECT created_at, updated_at, tenant_id, id, media_id, folder_name, title,"
                " source_author, quality_score, why_good, adapt_angle, best_quotes, category,"
                " priority, status, article_id, note FROM content_items_legacy")
        if _has_legacy_col(conn, "radar_topics", "new_since_last"):
            _rebuild_drop_legacy(conn, "radar_topics",
                "CREATE TABLE radar_topics ("
                "created_at DATETIME, updated_at DATETIME, id INTEGER PRIMARY KEY,"
                " tenant_id INTEGER DEFAULT 0, ch_id VARCHAR NOT NULL UNIQUE,"
                " name VARCHAR DEFAULT '', enabled BOOLEAN DEFAULT 1, notify BOOLEAN DEFAULT 1,"
                " seen_ids TEXT, hits TEXT, last_checked_at DATETIME)",
                "INSERT INTO radar_topics (created_at, updated_at, id, tenant_id, ch_id, name,"
                " enabled, notify, seen_ids, hits, last_checked_at) "
                "SELECT created_at, updated_at, id, tenant_id, ch_id, name, enabled, notify,"
                " seen_ids, hits, last_checked_at FROM radar_topics_legacy")
        # 2026-11-16 死列清算（schema 漂移闸上线后最后一波库有模型无）：七列四表
        if _has_legacy_col(conn, "tenants", "credits") or _has_legacy_col(conn, "tenants", "status"):
            _rebuild_drop_legacy(conn, "tenants",
                "CREATE TABLE tenants ("
                "created_at DATETIME, updated_at DATETIME, id INTEGER PRIMARY KEY,"
                " name VARCHAR DEFAULT '', brand JSON)",
                "INSERT INTO tenants (created_at, updated_at, id, name, brand) "
                "SELECT created_at, updated_at, id, name, brand FROM tenants_legacy")
        if _has_legacy_col(conn, "articles", "title_alts") or _has_legacy_col(conn, "articles", "kb_doc_ids"):
            _rebuild_drop_legacy(conn, "articles",
                "CREATE TABLE articles ("
                "created_at DATETIME, updated_at DATETIME, tenant_id INTEGER DEFAULT 0,"
                " id INTEGER PRIMARY KEY, script_id INTEGER,"
                " md VARCHAR DEFAULT '', title VARCHAR DEFAULT '', note VARCHAR DEFAULT '',"
                " kb_sources JSON, publish_url VARCHAR DEFAULT '', published_at VARCHAR DEFAULT '',"
                " stats JSON, html VARCHAR DEFAULT '', status VARCHAR DEFAULT 'draft')",
                "INSERT INTO articles (created_at, updated_at, tenant_id, id, script_id,"
                " md, title, note, kb_sources, publish_url, published_at, stats, html, status) "
                "SELECT created_at, updated_at, tenant_id, id, script_id,"
                " md, title, note, kb_sources, publish_url, published_at, stats, html, status FROM articles_legacy")
        if _has_legacy_col(conn, "users", "must_change_password"):
            # R11.2 首登强制改密流程 2026-11-16 全链退役（bootstrap 从不置位，403 分支不可达）
            _rebuild_drop_legacy(conn, "users",
                "CREATE TABLE users ("
                "created_at DATETIME, updated_at DATETIME, id INTEGER PRIMARY KEY,"
                " tenant_id INTEGER NOT NULL, username VARCHAR NOT NULL,"
                " password_hash VARCHAR DEFAULT '', role VARCHAR NOT NULL DEFAULT 'member',"
                " display_name VARCHAR DEFAULT '', status VARCHAR NOT NULL DEFAULT 'active')",
                "INSERT INTO users (created_at, updated_at, id, tenant_id, username,"
                " password_hash, role, display_name, status) "
                "SELECT created_at, updated_at, id, tenant_id, username,"
                " password_hash, role, display_name, status FROM users_legacy")
            print("[migrate] users 重建：去 must_change_password（流程退役）")
        if _has_legacy_col(conn, "scripts", "topic_id"):
            # 老选题库 topics 退役 2026-11-16 划线：scripts.topic_id 列清退（数据保留）
            _rebuild_drop_legacy(conn, "scripts",
                "CREATE TABLE scripts ("
                "created_at DATETIME, updated_at DATETIME, tenant_id INTEGER DEFAULT 0,"
                " id INTEGER PRIMARY KEY, card_id INTEGER, falsify_item_id INTEGER,"
                " ore_id INTEGER, title_hint VARCHAR DEFAULT '', versions JSON,"
                " final_text VARCHAR DEFAULT '', teleprompter_text VARCHAR DEFAULT '',"
                " storyboard JSON, title_candidates JSON,"
                " status VARCHAR DEFAULT 'generating',"
                " account_type VARCHAR NOT NULL DEFAULT 'persona')",
                "INSERT INTO scripts (created_at, updated_at, tenant_id, id, card_id,"
                " falsify_item_id, ore_id, title_hint, versions, final_text,"
                " teleprompter_text, storyboard, title_candidates, status, account_type) "
                "SELECT created_at, updated_at, tenant_id, id, card_id, falsify_item_id,"
                " ore_id, title_hint, versions, final_text, teleprompter_text,"
                " storyboard, title_candidates, status, account_type FROM scripts_legacy")
            print("[migrate] scripts 重建：去 topic_id（topics 退役划线）")
        if _has_legacy_col(conn, "watch_candidates", "leadgen_comments") or _has_legacy_col(conn, "watch_candidates", "recent_same"):
            # 重建前先把模型列补进老表（老库可能还没有 account_type/leadgen_density，
            # 由后置 ADD COLUMN 兜底的列不在此刻）——copy_select 引用的列必须先存在
            _wccols_old = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(watch_candidates)"))]
            for _col, _ddl in (("account_type", "VARCHAR NOT NULL DEFAULT 'persona'"),
                                ("leadgen_density", "REAL DEFAULT 0")):
                if _col not in _wccols_old:
                    conn.execute(sqlalchemy.text(f"ALTER TABLE watch_candidates ADD COLUMN {_col} {_ddl}"))
            _rebuild_drop_legacy(conn, "watch_candidates",
                "CREATE TABLE watch_candidates ("
                "created_at DATETIME, updated_at DATETIME, tenant_id INTEGER DEFAULT 0,"
                " id INTEGER PRIMARY KEY, keyword VARCHAR DEFAULT '', direction VARCHAR DEFAULT '',"
                " name VARCHAR DEFAULT '', sec_uid VARCHAR DEFAULT '', url VARCHAR DEFAULT '',"
                " signature VARCHAR DEFAULT '', follower_count INTEGER DEFAULT 0,"
                " sample_video_id VARCHAR DEFAULT '', sample_title VARCHAR DEFAULT '',"
                " sample_stats VARCHAR DEFAULT '', account_type VARCHAR NOT NULL DEFAULT 'persona',"
                " leadgen_density REAL DEFAULT 0, pure_reason VARCHAR DEFAULT '',"
                " status VARCHAR NOT NULL DEFAULT 'open')",
                "INSERT INTO watch_candidates (created_at, updated_at, tenant_id, id, keyword,"
                " direction, name, sec_uid, url, signature, follower_count, sample_video_id,"
                " sample_title, sample_stats, account_type, leadgen_density, pure_reason, status) "
                "SELECT created_at, updated_at, tenant_id, id, keyword, direction, name, sec_uid,"
                " url, signature, follower_count, sample_video_id, sample_title, sample_stats,"
                " COALESCE(account_type,'persona'), COALESCE(leadgen_density,0), pure_reason,"
                " COALESCE(status,'open') FROM watch_candidates_legacy")

        # 2026-11-16 二次裁决：老选题库 topics / idea 深研 research_reports 死档案全清
        # （唯二读者=老文章详情回显/watch 周报 topics_7d 已删；DROP 前有全库备份）
        for _dead_table in ("topics", "research_reports"):
            conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {_dead_table}"))

        fpcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(failure_patterns)"))]
        if fpcols and any(c in fpcols for c in ("kind", "last_type", "sample_job_id")):
            # 2026-11-16 改走 _rebuild_drop_legacy（原手工块同构）+ 并入两死列清算（写而不读）
            _rebuild_drop_legacy(conn, "failure_patterns",
                "CREATE TABLE failure_patterns ("
                "created_at DATETIME, updated_at DATETIME, id INTEGER PRIMARY KEY,"
                " fp VARCHAR NOT NULL UNIQUE, fail_class VARCHAR NOT NULL DEFAULT '',"
                " label VARCHAR NOT NULL DEFAULT '', root_cause VARCHAR NOT NULL DEFAULT '',"
                " status VARCHAR NOT NULL DEFAULT 'open', count INTEGER NOT NULL DEFAULT 0,"
                " sample_error VARCHAR NOT NULL DEFAULT '')",
                "INSERT INTO failure_patterns (created_at, updated_at, id, fp, fail_class, label,"
                " root_cause, status, count, sample_error) "
                "SELECT created_at, updated_at, id, fp, COALESCE(fail_class,''), COALESCE(label,''),"
                " COALESCE(root_cause,''), COALESCE(status,'open'), COALESCE(count,0),"
                " COALESCE(sample_error,'') FROM failure_patterns_legacy")
            print("[migrate] failure_patterns 重建：去 kind/last_type/sample_job_id（数据保留）")
        wacols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(watch_accounts)"))]
        if wacols and "account_type" not in wacols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE watch_accounts ADD COLUMN account_type VARCHAR NOT NULL DEFAULT 'persona'"))
        wccols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(watch_candidates)"))]
        if wccols and "account_type" not in wccols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE watch_candidates ADD COLUMN account_type VARCHAR NOT NULL DEFAULT 'persona'"))
        if wccols and "leadgen_density" not in wccols:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE watch_candidates ADD COLUMN leadgen_density REAL DEFAULT 0"))
        svcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(self_videos)"))]
        if svcols and "stats" not in svcols:
            conn.execute(sqlalchemy.text("ALTER TABLE self_videos ADD COLUMN stats JSON DEFAULT '{}'"))
        if svcols and "published_at" not in svcols:
            conn.execute(sqlalchemy.text("ALTER TABLE self_videos ADD COLUMN published_at VARCHAR DEFAULT ''"))
        if svcols and "duration" not in svcols:
            conn.execute(sqlalchemy.text("ALTER TABLE self_videos ADD COLUMN duration INTEGER DEFAULT 0"))
        scolsc = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(scripts)"))]
        if scolsc and "account_type" not in scolsc:
            conn.execute(sqlalchemy.text(
                "ALTER TABLE scripts ADD COLUMN account_type VARCHAR NOT NULL DEFAULT 'persona'"))
        if scolsc and "card_id" not in scolsc:  # 选题工场：靶子卡备稿（R1.4，2026-11-14）
            conn.execute(sqlalchemy.text("ALTER TABLE scripts ADD COLUMN card_id INTEGER"))
        if scolsc and "falsify_item_id" not in scolsc:  # 实测打假备稿（R1.4③，2026-11-14）
            conn.execute(sqlalchemy.text("ALTER TABLE scripts ADD COLUMN falsify_item_id INTEGER"))
        if scolsc and "ore_id" not in scolsc:  # 获客矿建稿锚（分型路由消费面，2026-11-14）
            conn.execute(sqlalchemy.text("ALTER TABLE scripts ADD COLUMN ore_id INTEGER"))
        if scolsc and "title_hint" not in scolsc:  # 生成来源标题快照（topics 退役后列表显示不再 join）
            conn.execute(sqlalchemy.text("ALTER TABLE scripts ADD COLUMN title_hint VARCHAR DEFAULT ''"))
            # 一次性回填：老脚本从 topics 表快照标题（表原地保留，此后不再读写增量）
            conn.execute(sqlalchemy.text(
                "UPDATE scripts SET title_hint=(SELECT title FROM topics WHERE topics.id=scripts.topic_id) "
                "WHERE (title_hint IS NULL OR title_hint='') AND topic_id IS NOT NULL"))
        # 引擎统一（2026-10-12）：parked 状态退役 → failed（next_retry_at 保留，沿退避梯子继续）
        jcols2 = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(jobs)"))]
        if jcols2 and "next_retry_at" in jcols2:
            cur = conn.execute(sqlalchemy.text(
                "UPDATE jobs SET status='failed' WHERE status='parked'"))
            if cur.rowcount:
                print(f"[migrate] parked→failed 迁移 {cur.rowcount} 行（next_retry_at 保留）")
        rtcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(radar_topics)"))]
        if rtcols and "hits" not in rtcols:
            conn.execute(sqlalchemy.text("ALTER TABLE radar_topics ADD COLUMN hits JSON DEFAULT '[]'"))
        acols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(articles)"))]
        if acols and "title" not in acols:
            conn.execute(sqlalchemy.text("ALTER TABLE articles ADD COLUMN title VARCHAR DEFAULT ''"))
        tcols = [r[1] for r in conn.execute(sqlalchemy.text("PRAGMA table_info(tenants)"))]
        if tcols and "brand" not in tcols:
            conn.execute(sqlalchemy.text("ALTER TABLE tenants ADD COLUMN brand JSON DEFAULT '{}'"))
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
        # 唯一索引带 tenant_id（多租户：不同租户的同 dedup_key 互不冲突；旧索引迁移替换）
        conn.execute(sqlalchemy.text("DROP INDEX IF EXISTS ix_jobs_active_dedup"))
        conn.execute(sqlalchemy.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_jobs_active_dedup ON jobs(tenant_id, type, dedup_key)"
            " WHERE dedup_key <> '' AND status IN ('queued','running','parked')"))
        # 多租户：业务表补 tenant_id（历史数据归租户 1）
        # （dismissals/harvest_profiles 两张死壳表已从建表管道退役 2026-11-10：模型已删，
        #   生产库里已有行原地保留，不再迁移维护）
        for table in ("articles", "benchmark_videos",
                      "radar_topics",
                      "scripts", "self_videos", "style_profiles",
                      "watch_accounts", "watch_candidates"):
            tcols = [r[1] for r in conn.execute(sqlalchemy.text(f"PRAGMA table_info({table})"))]
            if tcols and "tenant_id" not in tcols:
                conn.execute(sqlalchemy.text(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1"))
            if tcols:
                conn.execute(sqlalchemy.text(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_tenant ON {table}(tenant_id)"))
        # 数字人功能退役（2026-10-29 用户裁定）：两张表整体下线（迁移前快照已自动生成）
        for dead in ("avatar_configs", "avatar_videos"):
            conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {dead}"))
        # 拆积分/账本/多租户（2026-10-27 用户裁定 1A+2B）：四张死表退役
        # （删表前启动自动快照已保现场；历史数据如需追溯用 pre-migrate 快照）
        for dead in ("tenant_memberships", "credit_transactions", "bean_ledger", "llm_calls"):
            conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {dead}"))
