#!/usr/bin/env python3
"""P2 素材域一次性迁移：旧素材面 5 表 → sources/materials/material_analyses（PG 内）。

用法（仓库根）：PYTHONPATH=backend backend/.venv/bin/python backend/scripts/migrate_materials.py
幂等：每段按 origin/content_hash 查重跳过已迁移行；重复跑只补增量。

映射（TDD §15）：
- watch_accounts(competitor) → sources(kind=peer_account, status=active/disabled)
- watch_accounts(self)       → 原表保留（P4 迁 accounts），本脚本不动
- watch_candidates           → sources(status=candidate；added 且已有同 sec_uid 源=跳过；
                                dismissed → config.dismissed=true)
- radar_topics               → sources(kind=radar_topic, ext_id=ch_id, config{seen_ids,hits,notify})
- benchmark_videos           → materials(douyin_video/video) + analyses(breakdown/comments)
- content_items              → materials(kb_doc)（quality 承载评估字段，stage=改编流状态）
- falsify_items              → materials(test_record)（stage=实测流状态）
- scripts.ore_id/falsify_item_id → 重映射为 materials.id
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.materials.service import content_hash_of  # noqa: E402
from app.models import Material, MaterialAnalysis, Source  # noqa: E402


def _sec_of(url: str) -> str:
    m = re.search(r"share/user/([A-Za-z0-9_-]{20,})", url or "") or \
        re.search(r"sec_uid=([A-Za-z0-9_-]{20,})", url or "")
    return m.group(1) if m else ""


def _aweme_id(url: str) -> str:
    m = re.search(r"/video/(\d{6,})", url or "")
    return m.group(1) if m else ""


def _legacy_rows(s: Session, table: str) -> list[dict]:
    """旧表只读拉全量（模型已删，走 text SQL + dict 行）。"""
    res = s.execute(text(f"SELECT * FROM {table}"))
    return [dict(r._mapping) for r in res]


def _parse_dt(v) -> datetime | None:  # noqa: ANN001
    if v is None or isinstance(v, datetime):
        return v
    txt = str(v).strip()
    return datetime.fromisoformat(txt) if txt else None


def main() -> int:
    counts: dict[str, int] = {}

    with Session(engine) as s:
        # ---------- 1) sources：同行账号（competitor） ----------
        sec_to_source: dict[str, int] = {}
        existing = {r.ext_id: r for r in s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "peer_account")).all() if r.ext_id}  # type: ignore[attr-defined]
        n = 0
        for row in _legacy_rows(s, "watch_accounts"):
            if row["kind"] != "competitor":
                continue  # self 行留在原表（P4 迁 accounts）
            sec = _sec_of(row["url"] or "")
            if sec and sec in existing:
                sec_to_source[sec] = existing[sec].id
                continue
            cfg = {"account_type": row.get("account_type") or "persona"}
            if row.get("note"):
                cfg["legacy_note"] = str(row["note"])[:300]
            r = Source(tenant_id=row.get("tenant_id") or 1, kind="peer_account",
                       platform=row.get("platform") or "douyin",
                       name=str(row["name"] or "")[:60], handle=str(row["name"] or "")[:60],
                       ext_id=sec, url=row["url"] or "", status="active" if row.get("enabled") else "disabled",
                       config=cfg, last_sync_at=_parse_dt(row.get("last_scan_at")))
            s.add(r)
            s.flush()
            if sec:
                sec_to_source[sec] = r.id
                existing[sec] = r
            n += 1
        counts["sources_peer"] = n

        # ---------- 2) sources：账号候选 ----------
        n = 0
        # 去重键（幂等重跑）：sec_uid 优先，无名无链回退名称
        existing_cand_keys = {(r.ext_id or f"n:{r.name}") for r in existing.values()}
        for row in _legacy_rows(s, "watch_candidates"):
            sec = row.get("sec_uid") or ""
            status = row.get("status") or "open"
            name = str(row.get("name") or "")[:60]
            cand_key = sec or f"n:{name}"
            if cand_key in existing_cand_keys:
                continue  # 幂等：sec/名称已在（含上轮已迁候选/正式源）
            cfg = {"keyword": row.get("keyword") or "", "direction": row.get("direction") or "",
                   "signature": str(row.get("signature") or "")[:100],
                   "account_type": row.get("account_type") or "persona",
                   "leadgen_density": float(row.get("leadgen_density") or 0),
                   "follower_count": int(row.get("follower_count") or 0),
                   "sample_video_id": row.get("sample_video_id") or "",
                   "sample_title": row.get("sample_title") or "",
                   "pure_reason": str(row.get("pure_reason") or "")[:90]}
            try:
                cfg["stats"] = json.loads(row.get("sample_stats") or "{}")
            except Exception:  # noqa: BLE001
                cfg["stats"] = {}
            if status == "dismissed":
                cfg["dismissed"] = True
            r = Source(tenant_id=row.get("tenant_id") or 1, kind="peer_account", platform="douyin",
                       name=name, handle=name,
                       ext_id=sec, url=row.get("url") or "", status="candidate", config=cfg)
            s.add(r)
            s.flush()
            existing_cand_keys.add(cand_key)
            if sec:
                existing.setdefault(sec, r)
            n += 1
        counts["sources_candidates"] = n

        # ---------- 3) sources：话题雷达 ----------
        n = 0
        have_chids = {r.ext_id for r in s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "radar_topic")).all()}  # type: ignore[attr-defined]
        for row in _legacy_rows(s, "radar_topics"):
            ch = str(row.get("ch_id") or "")
            if not ch or ch in have_chids:
                continue
            r = Source(tenant_id=row.get("tenant_id") or 1, kind="radar_topic", platform="douyin",
                       name=str(row.get("name") or "")[:40], ext_id=ch,
                       handle=str(row.get("name") or "")[:40],
                       status="active" if row.get("enabled") else "disabled",
                       config={"seen_ids": row.get("seen_ids") or [], "hits": row.get("hits") or [],
                               "notify": bool(row.get("notify", True))},
                       last_sync_at=_parse_dt(row.get("last_checked_at")))
            s.add(r)
            n += 1
        counts["sources_radar"] = n
        s.commit()

    with Session(engine) as s:
        # ---------- 4) materials：拆解库（benchmark_videos） ----------
        have = {r.content_hash for r in s.exec(select(Material)).all()}  # type: ignore[attr-defined]
        have_urls = {r.origin_url for r in s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video")).all()}  # type: ignore[attr-defined]
        bm_mat: dict[int, int] = {}
        n = n_an = 0
        for row in _legacy_rows(s, "benchmark_videos"):
            url = row.get("url") or ""
            origin_type = "video" if row.get("source") == "local" else "douyin_video"
            oid = _aweme_id(url)
            chash = content_hash_of(origin_type, oid, url, str(row.get("title") or ""))
            if chash in have or url in have_urls:
                # 幂等重跑：仍要补 id 映射（scripts 重映射依赖）
                m = None
                if oid:
                    m = s.exec(select(Material).where(  # type: ignore[attr-defined]
                        Material.origin_type == "douyin_video",  # type: ignore[attr-defined]
                        Material.origin_id == oid)).first()  # type: ignore[attr-defined]
                if m is None and url:
                    m = s.exec(select(Material).where(  # type: ignore[attr-defined]
                        Material.origin_url == url)).first()  # type: ignore[attr-defined]
                if m is not None:
                    bm_mat[int(row["id"])] = m.id
                continue
            scan = row.get("scan_status") or "pending"
            status = {"pending": "inbox", "approved": "ready", "ignored": "ignored"}.get(scan, "inbox")
            quality = {"heat": float(row.get("quality_score") or 0),
                       "comment_kind": row.get("comment_kind") or "",
                       "leadgen_share": float(row.get("comment_leadgen_share") or 0),
                       "account_type": row.get("account_type") or "persona"}
            notes = str(row.get("notes") or "")
            m = Material(tenant_id=row.get("tenant_id") or 1,
                         origin_type=origin_type, origin_id=oid, origin_url=url,
                         author=str(row.get("author") or "")[:60],
                         title=str(row.get("title") or "")[:80],
                         transcript=row.get("transcript") or "",
                         media={"path": row.get("media_path") or ""} if row.get("media_path") else {},
                         stats=row.get("stats") or {}, tags=[],
                         quality=quality, status=status, content_hash=chash,
                         collected_at=_parse_dt(row.get("created_at")) or datetime.utcnow(),
                         note=notes[:300])
            s.add(m)
            s.flush()
            bm_mat[int(row["id"])] = m.id
            have.add(chash)
            if url:
                have_urls.add(url)
            n += 1
            analysis = row.get("analysis")
            if isinstance(analysis, dict) and analysis:
                s.add(MaterialAnalysis(tenant_id=m.tenant_id, material_id=m.id, kind="breakdown",
                                       payload=analysis, version=1))
                n_an += 1
            tc = row.get("top_comments")
            if isinstance(tc, list) and tc:
                s.add(MaterialAnalysis(tenant_id=m.tenant_id, material_id=m.id, kind="comments",
                                       payload={"items": tc}, version=1))
                n_an += 1
            if n % 200 == 0:
                s.commit()
        counts["materials_bm"] = n
        counts["analyses_bm"] = n_an
        s.commit()

        # ---------- 5) materials：知识库清单（content_items） ----------
        n = 0
        have_kb = {r.origin_id for r in s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc")).all()}  # type: ignore[attr-defined]
        for row in _legacy_rows(s, "content_items"):
            mid = str(row.get("media_id") or "")
            if not mid or mid in have_kb:
                continue
            stage = row.get("status") or "pending"
            status = {"pending": "ready", "running": "ready", "done": "ready",
                      "published": "ready", "dismissed": "ignored", "low": "ignored"}.get(stage, "ready")
            quality = {"score": float(row.get("quality_score") or 0),
                       "folder": row.get("folder_name") or "",
                       "why_good": str(row.get("why_good") or "")[:200],
                       "adapt_angle": str(row.get("adapt_angle") or "")[:300],
                       "best_quotes": row.get("best_quotes") or [],
                       "category": row.get("category") or "",
                       "priority": int(row.get("priority") or 0)}
            if stage == "low":
                quality["gate_reason"] = row.get("note") or "低于生成线"
            m = Material(tenant_id=row.get("tenant_id") or 1, origin_type="kb_doc",
                         origin_id=mid, title=str(row.get("title") or "")[:120],
                         author=str(row.get("source_author") or "")[:60],
                         quality=quality, stage=stage, status=status,
                         content_hash=content_hash_of("kb_doc", mid, "", ""),
                         collected_at=_parse_dt(row.get("created_at")) or datetime.utcnow(),
                         note=str(row.get("note") or "")[:300])
            s.add(m)
            have_kb.add(mid)
            n += 1
        counts["materials_kb"] = n

        # ---------- 6) materials：实测打假（falsify_items） ----------
        n = 0
        for row in _legacy_rows(s, "falsify_items"):
            chash = content_hash_of("test_record", str(row["id"]), "", str(row.get("claim") or ""))
            if chash in have:
                continue
            q = {"target": str(row.get("target") or "")[:40],
                 "auto_source": bool(row.get("auto_source")),
                 "script_id": row.get("script_id")}
            m = Material(tenant_id=row.get("tenant_id") or 1, origin_type="test_record",
                         title=str(row.get("claim") or "")[:60],
                         body=row.get("result_note") or "",
                         note=str(row.get("evidence_note") or "")[:300],
                         quality=q, stage=row.get("status") or "pending",
                         status="ignored" if row.get("status") == "dismissed" else "ready",
                         content_hash=chash,
                         collected_at=_parse_dt(row.get("created_at")) or datetime.utcnow())
            s.add(m)
            have.add(chash)
            n += 1
        counts["materials_falsify"] = n
        s.commit()

    # ---------- 7) scripts.ore_id / falsify_item_id 重映射 ----------
    with Session(engine) as s:
        bm_map = {int(k): v for k, v in bm_mat.items()}
        # falsify 映射：旧 item id → 新 material id（quality.script_id 回读不可靠，用 claim 标题匹配？）
        # 直接用迁移时的顺序不可靠 → 用 content_hash 反查：materials(test_record) 由 falsify_items
        # 逐行迁来，hash=sha256("test_record:{old_id}:")[:32]
        fs_map = {}
        for row in _legacy_rows(s, "falsify_items"):
            chash = content_hash_of("test_record", str(row["id"]), "", str(row.get("claim") or ""))
            m = s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "test_record",  # type: ignore[attr-defined]
                Material.content_hash == chash)).first()  # type: ignore[attr-defined]
            if m is not None:
                fs_map[int(row["id"])] = m.id
        from app.models import Script

        n_ore = n_fs = 0
        for sc in s.exec(select(Script)).all():
            if sc.ore_id and sc.ore_id in bm_map:
                sc.ore_id = bm_map[sc.ore_id]
                n_ore += 1
            if sc.falsify_item_id and sc.falsify_item_id in fs_map:
                sc.falsify_item_id = fs_map[sc.falsify_item_id]
                n_fs += 1
            s.add(sc)
        s.commit()
        counts["scripts_ore_remapped"] = n_ore
        counts["scripts_falsify_remapped"] = n_fs

    # ---------- 8) usages：旧 article/脚本引用补登记（best effort） ----------
    with Session(engine) as s:
        from app.models import Article

        n = 0
        for art in s.exec(select(Article).where(Article.kb_sources.isnot(None))).all():  # type: ignore[attr-defined]
            for src in (art.kb_sources or []):
                mid = (src or {}).get("media_id")
                if not mid:
                    continue
                m = s.exec(select(Material).where(  # type: ignore[attr-defined]
                    Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
                    Material.origin_id == mid)).first()  # type: ignore[attr-defined]
                if m is None:
                    continue
                from app.models import MaterialUsage

                dup = s.exec(select(MaterialUsage).where(  # type: ignore[attr-defined]
                    MaterialUsage.material_id == m.id,  # type: ignore[attr-defined]
                    MaterialUsage.target_type == "content",  # type: ignore[attr-defined]
                    MaterialUsage.target_id == art.id)).first()  # type: ignore[attr-defined]
                if dup is None:
                    s.add(MaterialUsage(tenant_id=art.tenant_id or 1, material_id=m.id,
                                        target_type="content", target_id=art.id, role="source"))
                    n += 1
        s.commit()
        counts["usages_kb"] = n

    print("[migrate-materials]", counts)
    total = sum(v for k, v in counts.items() if not k.startswith("scripts_"))
    print(f"[migrate-materials] 完成：新增/补映射合计 {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
