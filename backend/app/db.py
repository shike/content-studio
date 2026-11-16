"""PostgreSQL 引擎与会话（唯一业务库）。

租户作用域三件套（install_tenant_scope，进程级一次接入全站生效；单用户版退化为 tenant_id=1 的自动归属）：
- do_orm_execute 事件：所有 ORM select 自动追加 tenant_id 过滤（platform_admin/系统上下文豁免）
- before_flush 事件：新行 tenant_id=0 时自动回填提交者租户（归属=ACTOR）
- Session.get 补丁：get() 不走 do_orm_execute，按同规则命中他租户行时返回 None（端点自然 404）

schema 变更唯一通道=Alembic（backend/migrations）；本模块不再建表/补列。
"""
from datetime import datetime, timezone
from pathlib import Path
from subprocess import run

from sqlalchemy import event, inspect as sa_inspect, text
from sqlalchemy.orm import Session as SASession, with_loader_criteria
from sqlmodel import create_engine

from .settings import settings


def _make_engine():
    if not settings.database_url:
        raise RuntimeError(
            "缺少 DATABASE_URL：业务库已切到 PostgreSQL。"
            "在仓库根 .env 配置 DATABASE_URL=postgresql+psycopg://user:pass@127.0.0.1:5432/content_studio"
        )
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        connect_args={
            "application_name": "content-studio",
            "options": "-c timezone=UTC",  # 会话时区固定 UTC；业务列仍为 timestamp without time zone
        },
    )


settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = _make_engine()


def install_datetime_normalizer() -> None:
    """防御性归一化：ORM 读出的 datetime 若带时区一律转 naive UTC。

    业务列类型为 timestamp without time zone（恒 naive）；本钩子兜住驱动/未来
    类型变更引入的 aware 值，避免全库按 naive 比较时抛 TypeError 被后台循环吞掉。
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


def _pg_url() -> str:
    """SQLAlchemy URL → libpq URL（pg_dump 不认识 +psycopg 方言后缀）。"""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://")


def _pg_dump_exe() -> str:
    """优先 PATH 里的 pg_dump；本地开发用 pgserver 自带二进制兜底。"""
    import shutil

    exe = shutil.which("pg_dump")
    if exe:
        return exe
    try:
        import pgserver

        cand = Path(pgserver.__file__).parent / "pginstall" / "bin" / "pg_dump"
        if cand.exists():
            return str(cand)
    except Exception:  # noqa: BLE001
        pass
    return "pg_dump"


def backup_db(tag: str, keep: int = 14) -> Path:
    """PostgreSQL 逻辑备份（pg_dump -Fc），按 tag 滚动保留 keep 份。

    业务数据唯一恢复手段；备份文件落 DATA_DIR/backups/pg-{tag}-{ts}.dump。
    """
    bdir = settings.data_dir / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    dest = bdir / f"pg-{tag}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.dump"
    proc = run(
        [_pg_dump_exe(), "-Fc", "--no-owner", "--no-privileges", "-f", str(dest), _pg_url()],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump 失败（{proc.returncode}）：{proc.stderr.strip()[:400]}")
    for old in sorted(bdir.glob(f"pg-{tag}-*.dump"))[:-keep]:
        old.unlink(missing_ok=True)
    return dest


def init_db() -> None:
    """启动自检：库可连且已跑过 Alembic。不建表、不改结构。"""
    from . import models  # noqa: F401  确保所有表模型已注册（供其他模块 import 时序）

    with engine.connect() as conn:
        ok = conn.execute(text("SELECT to_regclass('public.users')")).scalar()
    if ok is None:
        raise RuntimeError(
            "数据库未初始化：先运行 alembic upgrade head（backend/.venv/bin/alembic -c backend/alembic.ini upgrade head）"
        )
