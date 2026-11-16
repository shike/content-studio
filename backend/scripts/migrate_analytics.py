#!/usr/bin/env python3
"""P4 数据域一次性迁移：旧数据面 4 表 → accounts/metrics_snapshots/analytics_reports/pipeline_ledger。

用法（仓库根）：PYTHONPATH=backend backend/.venv/bin/python backend/scripts/migrate_analytics.py
幂等：按来源 id 指纹查重跳过已迁移行。

映射（TDD §15）：
- watch_accounts(kind=self)  → accounts(channel=persona, auth.creator_cookie)
- account_metrics_daily      → metrics_snapshots(subject=account, source=creator_center)
- video_weekly_reports       → analytics_reports(kind=weekly, period_start=week_start)
- daily_ledger               → pipeline_ledger(phase→pipeline 改名：mine→material_kb_mine、adapt→wechat_adapt)
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.channels.engine import channel_id_of, ensure_channels  # noqa: E402
from app.db import engine  # noqa: E402
from app.models import (Account, AnalyticsReport, MetricsSnapshot,  # noqa: E402
                        PipelineLedger)

_PHASE_MAP = {"mine": "material_kb_mine", "adapt": "wechat_adapt"}


def _legacy_rows(s: Session, table: str) -> list[dict]:
    res = s.execute(text(f"SELECT * FROM {table}"))
    return [dict(r._mapping) for r in res]


def _parse_dt(v):  # noqa: ANN001
    if v is None or isinstance(v, datetime):
        return v
    txt = str(v).strip()
    return datetime.fromisoformat(txt) if txt else None


def main() -> int:
    ensure_channels()
    counts: dict[str, int] = {}

    with Session(engine) as s:
        persona_ch = channel_id_of(s, "persona")

        # ---------- 1) accounts ← watch_accounts(kind=self) ----------
        acc_map: dict[int, int] = {}
        # 幂等键 = payload._legacy_id（旧表 id 与新表 id 不同空间，用主键查重会重复建号）
        have_legacy = {(r.payload or {}).get("_legacy_id") for r in s.exec(select(Account)).all()}
        n = 0
        for row in _legacy_rows(s, "watch_accounts"):
            if row.get("kind") != "self":
                continue
            old_id = int(row["id"])
            if old_id in have_legacy:
                got = next((a for a in s.exec(select(Account)).all()
                            if (a.payload or {}).get("_legacy_id") == old_id), None)
                if got is not None:
                    acc_map[old_id] = int(got.id)  # type: ignore[arg-type]
                continue
            a = Account(tenant_id=row.get("tenant_id") or 1, channel_id=persona_ch,
                        platform=row.get("platform") or "douyin",
                        name=str(row.get("name") or "")[:60],
                        handle=str(row.get("name") or "")[:40],
                        profile_url=row.get("url") or "",
                        auth={"creator_cookie": row.get("creator_cookie") or ""}
                        if row.get("creator_cookie") else {},
                        enabled=bool(row.get("enabled", True)),
                        last_sync_at=_parse_dt(row.get("last_scan_at")),
                        note=str(row.get("note") or "")[:300],
                        payload={"_legacy_id": old_id})
            s.add(a)
            s.flush()
            acc_map[old_id] = int(a.id)  # type: ignore[arg-type]
            n += 1
        counts["accounts"] = n

        # ---------- 2) metrics_snapshots ← account_metrics_daily ----------
        n = 0
        have_keys = {(r.subject_id, r.date) for r in s.exec(
            select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                MetricsSnapshot.subject_type == "account")).all()}  # type: ignore[attr-defined]
        _FIELDS = ("fans", "new_fans", "cancel_fans", "profile_views", "play",
                   "digg", "comment", "share", "account_search", "post_search")
        for row in _legacy_rows(s, "account_metrics_daily"):
            old_acc = int(row.get("id") or 0)  # 旧行无账号维度——全租户一账号口径
            aid = acc_map.get(old_acc) or next(iter(acc_map.values()), 0)
            d = str(row.get("date") or "")
            if not d or (aid, d) in have_keys:
                continue
            metrics = {k: int(row.get(k) or 0) for k in _FIELDS if row.get(k)}
            s.add(MetricsSnapshot(tenant_id=row.get("tenant_id") or 1,
                                  subject_type="account", subject_id=aid,
                                  platform="douyin", date=d, metrics=metrics,
                                  source="creator_center",
                                  created_at=_parse_dt(row.get("updated_at")) or datetime.utcnow()))
            have_keys.add((aid, d))
            n += 1
        counts["metrics_account"] = n

        # ---------- 3) analytics_reports ← video_weekly_reports ----------
        n = 0
        have_weeks = {r.period_start for r in s.exec(
            select(AnalyticsReport).where(  # type: ignore[attr-defined]
                AnalyticsReport.kind == "weekly")).all()}  # type: ignore[attr-defined]
        for row in _legacy_rows(s, "video_weekly_reports"):
            wk = str(row.get("week_start") or "")
            if not wk or wk in have_weeks:
                continue
            s.add(AnalyticsReport(tenant_id=row.get("tenant_id") or 1, kind="weekly",
                                  channel_id=persona_ch, period_start=wk,
                                  payload=row.get("report") or {},
                                  created_at=_parse_dt(row.get("created_at")) or datetime.utcnow()))
            have_weeks.add(wk)
            n += 1
        counts["reports_weekly"] = n

        # ---------- 4) pipeline_ledger ← daily_ledger ----------
        n = 0
        have_ledger = {(r.day, r.pipeline, r.produced) for r in s.exec(
            select(PipelineLedger)).all()}
        for row in _legacy_rows(s, "daily_ledger"):
            day = str(row.get("day") or "")
            pipeline = _PHASE_MAP.get(row.get("phase") or "", row.get("phase") or "")
            produced = row.get("produced") or ""
            if not day or (day, pipeline, produced) in have_ledger:
                continue
            s.add(PipelineLedger(tenant_id=row.get("tenant_id") or 1, day=day,
                                 pipeline=pipeline, produced=produced,
                                 reason=row.get("reason") or "",
                                 ok=bool(row.get("ok", True)),
                                 created_at=_parse_dt(row.get("created_at")) or datetime.utcnow()))
            have_ledger.add((day, pipeline, produced))
            n += 1
        counts["pipeline_ledger"] = n
        s.commit()

    print("[migrate-analytics]", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
