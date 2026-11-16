"""自有账号日级运营指标抓取（创作者中心 overview/all，2026-10-12）。

数据源：creator.douyin.com/aweme/janus/creator/data/overview/all/（轻量 httpx 直连，
cookie+UA 即可，无需浏览器）。账号 = 同行监测「我的账号」上配置的 creator_cookie。
平台指标有 T-1 延迟且搜索类滞后数日——按日期列合并入库（不整行覆盖）。
失败只影响本任务（失败区留诊断），零连带。
"""
from __future__ import annotations

from datetime import date as _date

import httpx
from sqlmodel import Session, select

from ..browser import ua
from ..db import engine
from ..models import Account, MetricsSnapshot
from ..settings import settings

_OVERVIEW_URL = "https://creator.douyin.com/aweme/janus/creator/data/overview/all/"

# 接口字段 → 库列
_FIELDS = {
    "fans": "fans", "new_fans": "new_fans", "cancel_fans": "cancel_fans",
    "profile": "profile_views", "play": "play", "digg": "digg",
    "comment": "comment", "share": "share",
    "account_search": "account_search", "post_search": "post_search",
}


def fetch_account_overview(cookie: str, timeout: float = 15.0) -> dict:
    """拉账号数据总览。返回 {date: {field: value}}（含平台给的 7 天日序）。

    出口走 douyin_proxy（登录态采集，2026-11-13 修正：此前 httpx 裸连不吃任何代理，
    登录态请求从机房 IP 直出的口子与出口一致性设计相悖）。"""
    from ..browser import httpx_proxy

    r = httpx.get(_OVERVIEW_URL, params={"period_days": 28},
                  headers={"User-Agent": ua(), "Cookie": cookie,
                           "Referer": "https://creator.douyin.com/creator-micro/home"},
                  timeout=timeout, proxy=httpx_proxy(login=True))
    r.raise_for_status()
    body = r.json()
    if body.get("status_code") not in (0, None):
        raise RuntimeError(f"overview 接口 status_code={body.get('status_code')}")
    data = body.get("data") or {}
    out: dict[str, dict] = {}
    for api_key, col in _FIELDS.items():
        block = data.get(api_key) or {}
        cur = block.get("current_count")
        for opt in (block.get("option_list") or []):
            d = str(opt.get("date") or "").strip()
            if not d:
                continue
            out.setdefault(d, {})[col] = int(opt.get("count") or 0)
        # current_count 归到今天（option 序列通常缺当日）
        today = _date.today().isoformat()
        if cur is not None and cur != "":
            out.setdefault(today, {})[col] = int(cur or 0)
    return out


def upsert_metrics(tenant_id: int, rows: dict, account_id: int | None = None) -> int:
    """指标事实表按日期合并入库（P4）：同主体同日一行，metrics JSONB 按列合并（空值不覆盖）。

    account_id=None 时落到该租户第一个自有账号（单自有账号版主路径）。返回写入天数。"""
    n = 0
    with Session(engine) as s:
        aid = account_id
        if aid is None:
            acc = s.exec(select(Account).where(  # type: ignore[attr-defined]
                Account.tenant_id == tenant_id)).first()  # type: ignore[attr-defined]
            aid = int(acc.id) if acc else 0  # type: ignore[arg-type]
        for d, vals in rows.items():
            row = s.exec(select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                MetricsSnapshot.tenant_id == tenant_id,  # type: ignore[attr-defined]
                MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
                MetricsSnapshot.subject_id == aid,  # type: ignore[attr-defined]
                MetricsSnapshot.date == d)).first()  # type: ignore[attr-defined]
            if row is None:
                row = MetricsSnapshot(tenant_id=tenant_id, subject_type="account",
                                      subject_id=aid, platform="douyin", date=d,
                                      metrics={}, source="creator_center")
            merged = dict(row.metrics or {})
            changed = False
            for col, val in vals.items():
                if not merged.get(col) and val:
                    merged[col] = int(val)
                    changed = True
            if changed or row.id is None:
                row.metrics = merged
                row.source = "creator_center"
                s.add(row)
                n += 1
        s.commit()
    return n


def _self_cookie() -> tuple[int, str]:
    """自有账号登录态（P4：accounts.auth.creator_cookie）。返回 (account_id, cookie)。"""
    with Session(engine) as s:
        row = s.exec(select(Account)).first()  # type: ignore[attr-defined]
        if row is None:
            return 0, ""
        return int(row.id or 0), str((row.auth or {}).get("creator_cookie") or "").strip()


def collect_for_tenant(tenant_id: int) -> tuple[int, str]:
    """抓取并入库一个租户自有账号的指标。返回 (写入天数, 说明)。"""
    if not settings.creator_data_enabled:
        return 0, "开关关闭"
    account_id, cookie = _self_cookie()
    if not cookie:
        return 0, "未配置创作者中心 cookie"
    rows = fetch_account_overview(cookie)
    n = upsert_metrics(tenant_id, rows, account_id)
    return n, f"{len(rows)} 天（{min(rows)}~{max(rows)}）" if rows else "0 天"
