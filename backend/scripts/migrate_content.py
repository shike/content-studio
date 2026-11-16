#!/usr/bin/env python3
"""P3 内容域一次性迁移：旧内容面 4 表 → channels/ideas/contents/publications/channel_styles。

用法（仓库根）：PYTHONPATH=backend backend/.venv/bin/python backend/scripts/migrate_content.py
幂等：每段按内容指纹/来源 id 查重跳过已迁移行；重复跑只补增量。

映射（TDD §15）：
- channels                     ← 种子 4 行（ensure_channels，幂等）
- topic_cards                  → ideas(channel=persona)：pool→payload.pool、script_id→content_id（重映射）
- scripts                      → contents(kind=video_script)：status 映射 + payload.script_state 旧串
- articles                     → contents(kind=long_article) + publications（有 publish_url 的行）
- style_profiles               → channel_styles(channel=persona，account_name→payload)
- 重映射：idea.content_id / contents.parent_content_id / materials.quality.article_id /
  materials.quality.script_id（旧脚本/文章 id → 新 contents id）
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from app.channels.engine import ensure_channels  # noqa: E402
from app.db import engine  # noqa: E402
from app.materials.service import content_hash_of  # noqa: E402
from app.models import Channel, ChannelStyle, Content, Idea, Publication  # noqa: E402


def _legacy_rows(s: Session, table: str) -> list[dict]:
    res = s.execute(text(f"SELECT * FROM {table}"))
    return [dict(r._mapping) for r in res]


def _parse_dt(v):  # noqa: ANN001
    if v is None or isinstance(v, datetime):
        return v
    txt = str(v).strip()
    return datetime.fromisoformat(txt) if txt else None


SCRIPT_STATUS_MAP = {  # 旧 scripts.status → (contents.status, payload.script_state)
    "generating": ("drafting", "generating"), "polishing": ("drafting", "polishing"),
    "generated": ("review", "generated"), "final": ("ready", "final"),
}
ARTICLE_STATUS_MAP = {  # 旧 articles.status → (contents.status, payload.article_state)
    "pending": ("review", "pending"), "generating": ("drafting", "generating"),
    "failed": ("failed", "failed"), "draft": ("review", "draft"),
    "edited": ("review", "edited"), "rendered": ("ready", "rendered"),
    "published": ("published", "published"),
}


def main() -> int:
    ensure_channels()
    counts: dict[str, int] = {}

    with Session(engine) as s:
        ch = {c.key: int(c.id) for c in s.exec(select(Channel)).all()}
        persona_ch = ch["persona"]

        # ---------- 1) ideas ← topic_cards ----------
        idea_map: dict[int, int] = {}
        n = 0
        for row in _legacy_rows(s, "topic_cards"):
            old_id = int(row["id"])
            if old_id in idea_map:
                continue
            dup = s.exec(select(Idea).where(Idea.tenant_id == (row.get("tenant_id") or 1))).all()  # type: ignore[attr-defined]
            dup_hit = any((i.payload or {}).get("_legacy_id") == old_id for i in dup)
            if dup_hit:
                got = next(i for i in dup if (i.payload or {}).get("_legacy_id") == old_id)
                idea_map[old_id] = int(got.id)  # type: ignore[arg-type]
                continue
            i = Idea(tenant_id=row.get("tenant_id") or 1, channel_id=persona_ch,
                     batch_date=row.get("batch_date") or "", source=row.get("source") or "",
                     spine=row.get("spine") or "", hook=row.get("hook") or "",
                     spice=row.get("spice") or "",
                     takeaways=row.get("takeaways") or [], judge=row.get("judge") or {},
                     gates=row.get("gates") or [],
                     payload={"pool": row.get("pool") or {}, "_legacy_id": old_id},
                     status=row.get("status") or "draft",
                     superseded_by=row.get("superseded_by"))
            s.add(i)
            s.flush()
            idea_map[old_id] = int(i.id)  # type: ignore[arg-type]
            n += 1
        counts["ideas"] = n
        s.commit()

        # ---------- 2) contents ← scripts（video_script） ----------
        script_map: dict[int, int] = {}
        n = 0
        for row in _legacy_rows(s, "scripts"):
            old_id = int(row["id"])
            status = row.get("status") or "generating"
            canon, state = SCRIPT_STATUS_MAP.get(status, ("drafting", status))
            dedup = content_hash_of("script", str(old_id), str(row.get("title_hint") or ""))
            dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.kind == "video_script")).all()  # type: ignore[attr-defined]
            dup_hit = next((c for c in dup if (c.payload or {}).get("_legacy_id") == old_id), None)
            if dup_hit is not None:
                script_map[old_id] = int(dup_hit.id)  # type: ignore[arg-type]
                continue
            versions = row.get("versions") or []
            body = row.get("final_text") or ((versions[0] or {}).get("body") if versions else "") or ""
            c = Content(tenant_id=row.get("tenant_id") or 1, channel_id=ch["leadgen"]
                        if (row.get("account_type") == "leadgen") else persona_ch,
                        idea_id=idea_map.get(int(row["card_id"])) if row.get("card_id") else None,
                        kind="video_script",
                        title=str(row.get("title_hint") or "")[:60], body=body,
                        versions=versions,
                        payload={"script_state": state, "teleprompter_text": row.get("teleprompter_text") or "",
                                 "storyboard": row.get("storyboard") or [],
                                 "title_candidates": row.get("title_candidates") or [],
                                 "account_type": row.get("account_type") or "persona",
                                 "title_hint": row.get("title_hint") or "",
                                 "falsify_item_id": row.get("falsify_item_id"),
                                 "ore_id": row.get("ore_id"),
                                 "_legacy_id": old_id},
                        status=canon,
                        created_at=_parse_dt(row.get("created_at")) or datetime.utcnow())
            s.add(c)
            s.flush()
            script_map[old_id] = int(c.id)  # type: ignore[arg-type]
            n += 1
        counts["contents_script"] = n

        # ---------- 3) contents ← articles（long_article）+ publications ----------
        article_map: dict[int, int] = {}
        n = n_pub = 0
        for row in _legacy_rows(s, "articles"):
            old_id = int(row["id"])
            status = row.get("status") or "draft"
            canon, state = ARTICLE_STATUS_MAP.get(status, ("review", status))
            dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.kind == "long_article")).all()  # type: ignore[attr-defined]
            dup_hit = next((c for c in dup if (c.payload or {}).get("_legacy_id") == old_id), None)
            if dup_hit is not None:
                article_map[old_id] = int(dup_hit.id)  # type: ignore[arg-type]
                continue
            c = Content(tenant_id=row.get("tenant_id") or 1, channel_id=ch["wechat"],
                        parent_content_id=script_map.get(int(row["script_id"])) if row.get("script_id") else None,
                        kind="long_article", title=str(row.get("title") or "")[:80],
                        body=row.get("md") or "",
                        payload={"article_state": state,
                                 "note": row.get("note") or "",
                                 "kb_sources": row.get("kb_sources") or [],
                                 "html": row.get("html") or "",
                                 "stats": row.get("stats") or {},
                                 "_legacy_id": old_id},
                        status=canon,
                        created_at=_parse_dt(row.get("created_at")) or datetime.utcnow())
            s.add(c)
            s.flush()
            article_map[old_id] = int(c.id)  # type: ignore[arg-type]
            n += 1
            if row.get("publish_url"):
                s.add(Publication(tenant_id=row.get("tenant_id") or 1, content_id=c.id,
                                  platform="wechat", url=row["publish_url"][:300],
                                  published_at=row.get("published_at") or "",
                                  publish_mode="manual"))
                n_pub += 1
        counts["contents_article"] = n
        counts["publications"] = n_pub

        # ---------- 4) channel_styles ← style_profiles ----------
        n = 0
        have_versions = {(r.payload or {}).get("_legacy_id") for r in s.exec(
            select(ChannelStyle)).all()}
        for row in _legacy_rows(s, "style_profiles"):
            old_id = int(row["id"])
            if old_id in have_versions:
                continue
            s.add(ChannelStyle(tenant_id=row.get("tenant_id") or 1, channel_id=persona_ch,
                               version=int(row.get("version") or 1),
                               digest=row.get("digest") or "",
                               traits=row.get("traits") or [],
                               exemplars=row.get("exemplars") or [],
                               based_on=int(row.get("based_on") or 0),
                               payload={"account_name": row.get("account_name") or "",
                                        "_legacy_id": old_id}))
            have_versions.add(old_id)
            n += 1
        counts["channel_styles"] = n
        s.commit()

    # ---------- 5) id 重映射（SQL 批量） ----------
    with Session(engine) as s:
        # idea.content_id ← 旧 script_id（topic_cards.script_id 经 script_map）
        n = n2 = n3 = 0
        for row in _legacy_rows(s, "topic_cards"):
            old_script = row.get("script_id")
            if not old_script:
                continue
            idea_new = s.exec(select(Idea).where(  # type: ignore[attr-defined]
                Idea.payload.op('->>')('_legacy_id') == str(row["id"]))).first()  # type: ignore[attr-defined]
            new_script = script_map.get(int(old_script))
            if idea_new is not None and new_script:
                if idea_new.content_id != new_script:
                    idea_new.content_id = new_script
                    s.add(idea_new)
                    n += 1
        # falsify 素材 quality.script_id 重映射
        from app.models import Material

        for m in s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "test_record")).all():  # type: ignore[attr-defined]
            q = m.quality or {}
            old_sid = q.get("script_id")
            if old_sid and int(old_sid) in script_map and q.get("script_id") != script_map[int(old_sid)]:
                q["script_id"] = script_map[int(old_sid)]
                m.quality = q
                s.add(m)
                n2 += 1
        # kb 素材 quality.article_id 重映射
        for m in s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "kb_doc")).all():  # type: ignore[attr-defined]
            q = m.quality or {}
            old_aid = q.get("article_id")
            if old_aid and int(old_aid) in article_map and q.get("article_id") != article_map[int(old_aid)]:
                q["article_id"] = article_map[int(old_aid)]
                m.quality = q
                s.add(m)
                n3 += 1
        s.commit()
        counts["idea_content_remapped"] = n
        counts["falsify_script_remapped"] = n2
        counts["kb_article_remapped"] = n3

    print("[migrate-content]", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
