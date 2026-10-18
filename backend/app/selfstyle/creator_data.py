"""自有账号日级运营指标抓取（创作者中心 overview/all，2026-10-12）。

数据源：creator.douyin.com/aweme/janus/creator/data/overview/all/（轻量 httpx 直连，
cookie+UA 即可，无需浏览器）。账号 = 同行监测「我的账号」上配置的 creator_cookie。
平台指标有 T-1 延迟且搜索类滞后数日——按日期列合并入库（不整行覆盖）。
失败只影响本任务（失败区留诊断），零连带。
"""
from __future__ import annotations

from datetime import date as _date, timedelta

import httpx
from sqlmodel import Session, select

from ..db import engine
from ..models import AccountMetricsDaily, WatchAccount
from ..settings import settings

_OVERVIEW_URL = "https://creator.douyin.com/aweme/janus/creator/data/overview/all/"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 接口字段 → 库列
_FIELDS = {
    "fans": "fans", "new_fans": "new_fans", "cancel_fans": "cancel_fans",
    "profile": "profile_views", "play": "play", "digg": "digg",
    "comment": "comment", "share": "share",
    "account_search": "account_search", "post_search": "post_search",
}


def fetch_account_overview(cookie: str, timeout: float = 15.0) -> dict:
    """拉账号数据总览。返回 {date: {field: value}}（含平台给的 7 天日序）。"""
    r = httpx.get(_OVERVIEW_URL, params={"period_days": 28},
                  headers={"User-Agent": _UA, "Cookie": cookie,
                           "Referer": "https://creator.douyin.com/creator-micro/home"},
                  timeout=timeout)
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


def upsert_metrics(tenant_id: int, rows: dict) -> int:
    """按日期合并入库（已有行只补空列）。返回写入天数。"""
    n = 0
    with Session(engine) as s:
        for d, vals in rows.items():
            row = s.exec(select(AccountMetricsDaily).where(
                AccountMetricsDaily.tenant_id == tenant_id,  # type: ignore[attr-defined]
                AccountMetricsDaily.date == d)).first()  # type: ignore[attr-defined]
            if row is None:
                row = AccountMetricsDaily(tenant_id=tenant_id, date=d)
            changed = False
            for col, val in vals.items():
                if getattr(row, col, 0) in (0, None) and val:
                    setattr(row, col, val)
                    changed = True
            if changed or row.id is None:
                s.add(row)
                n += 1
        s.commit()
    return n


def _self_cookie(tenant_id: int) -> str:
    with Session(engine) as s:
        row = s.exec(select(WatchAccount).where(
            WatchAccount.kind == "self",  # type: ignore[attr-defined]
            WatchAccount.enabled == True,  # noqa: E712
        )).first()  # type: ignore[union-attr]
        return (row.creator_cookie or "").strip() if row else ""


def collect_for_tenant(tenant_id: int) -> tuple[int, str]:
    """抓取并入库一个租户自有账号的指标。返回 (写入天数, 说明)。"""
    if not settings.creator_data_enabled:
        return 0, "开关关闭"
    cookie = _self_cookie(tenant_id)
    if not cookie:
        return 0, "未配置创作者中心 cookie"
    rows = fetch_account_overview(cookie)
    n = upsert_metrics(tenant_id, rows)
    return n, f"{len(rows)} 天（{min(rows)}~{max(rows)}）" if rows else "0 天"


def backfill_days(tenant_id: int, days: int = 7) -> int:
    """回填近 N 天（接口最多给 7 天日序，此函数用于显式补跑）。"""
    n, _ = collect_for_tenant(tenant_id)
    return n
