"""内容域引擎（PRD R0.8）：渠道注册、统一状态机、门面整形器。

四渠道共用一条「素材→选题→稿件→发布登记→数据回流」链，渠道差异在 channels.config。
旧内容表（topic_cards/scripts/articles）已停读写——本模块 + 各产线模块（cards/scripts/
articles/falsify）读写 ideas/contents/publications，API 门面保持旧契约（前端零漂移）。

状态机：
    idea:    draft → approved → consumed（discarded / jail 旁路，jail 可捞回）
    content: drafting → review → ready → published（failed 可重试，archived 收档）
渠道旧状态串存 payload.{script_state|article_state|note_state}——门面直出，迁移零漂移。
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from ..db import engine
from ..models import Channel, Content

CHANNEL_KEYS = ("persona", "leadgen", "wechat", "xhs")

CHANNEL_SEEDS: list[dict] = [
    {"key": "persona", "name": "人设号", "platform": "douyin",
     "config": {"positioning": "观点怼热点，立人设（选题工场 R2.2）",
                "publish_mode": "manual", "cta_policy": "软引导（禁扣字/私信导流）"}},
    {"key": "leadgen", "name": "获客号", "platform": "douyin",
     "config": {"positioning": "获客矿需求直出，强制获客引导（PRD R3.2）",
                "publish_mode": "manual", "cta_policy": "强化获客引导（合规红线内）"}},
    {"key": "wechat", "name": "公众号", "platform": "wechat",
     "config": {"positioning": "深度长文：热点直出 + 源文改编（PRD R4）",
                "publish_mode": "wechat_api", "cta_policy": "自然收尾不加引导"}},
    {"key": "xhs", "name": "小红书", "platform": "xiaohongshu",
     "config": {"positioning": "图文笔记：知识库/热点/一手案例的口语化改写（PRD R5）",
                "publish_mode": "manual", "cta_policy": "自然收尾"},
     "enabled": True},
]

_VIDEO_KIND = "video_script"
_ARTICLE_KIND = "long_article"
_NOTE_KIND = "note"


def ensure_channels() -> None:
    """启动播种（幂等）：四个渠道行按 key 补齐；config 已存在的不覆盖。"""
    with Session(engine) as s:
        have = {c.key: c for c in s.exec(select(Channel)).all()}
        for seed in CHANNEL_SEEDS:
            row = have.get(seed["key"])
            if row is None:
                s.add(Channel(key=seed["key"], name=seed["name"],
                              platform=seed["platform"],
                              enabled=bool(seed.get("enabled", True)),
                              config=seed.get("config") or {}))
        s.commit()


def channel_id_of(s: Session, key: str) -> int:
    row = s.exec(select(Channel).where(Channel.key == key)).first()  # type: ignore[attr-defined]
    if row is None:
        raise RuntimeError(f"渠道未播种：{key}（ensure_channels 未运行？）")
    return int(row.id)  # type: ignore[arg-type]


def channel_key_of(s: Session, channel_id: int) -> str:
    row = s.get(Channel, channel_id)
    return row.key if row else ""


# ── 稿件创建（各产线共用）──

def new_script_content(s: Session, *, tenant_id: int, channel_key: str,
                       idea_id: Optional[int] = None, title_hint: str = "",
                       payload: Optional[dict] = None) -> Content:
    """建视频稿（generating 态）：payload.script_state 承载渠道旧状态串。"""
    p = dict(payload or {})
    p.setdefault("script_state", "generating")
    c = Content(tenant_id=tenant_id, channel_id=channel_id_of(s, channel_key),
                idea_id=idea_id, kind=_VIDEO_KIND,
                title=(title_hint or "")[:60], body="", versions=[],
                payload=p, status="drafting")
    s.add(c)
    s.flush()
    return c


def new_article_content(s: Session, *, tenant_id: int, title: str = "",
                        note: str = "", state: str = "pending",
                        parent_content_id: Optional[int] = None,
                        payload: Optional[dict] = None) -> Content:
    """建长文稿：state=pending（待写）| generating（生成中）。"""
    p = dict(payload or {})
    p["article_state"] = state
    if note:
        p["note"] = note[:300]
    c = Content(tenant_id=tenant_id, channel_id=channel_id_of(s, "wechat"),
                kind=_ARTICLE_KIND, parent_content_id=parent_content_id,
                title=(title or "")[:80], body="", payload=p,
                status="drafting" if state != "pending" else "review")
    s.add(c)
    s.flush()
    return c


# ── 门面整形器（旧契约直出；P5 旧前端退役后随迁）──

def script_out(c: Content, channel_key: str = "") -> dict:
    """Content(video_script) → 旧 /api/scripts 行形状。

    channel_key 由调用方预取传入（列表页一张 map，杜绝 N+1 与嵌套会话）。"""
    p = c.payload or {}
    ch_key = channel_key
    return {
        "id": c.id, "idea_id": c.idea_id, "card_id": c.idea_id,  # 旧面别名
        "channel_id": c.channel_id, "channel": ch_key,
        "parent_content_id": c.parent_content_id,
        "title": c.title, "title_hint": p.get("title_hint", c.title),
        "versions": c.versions or [],
        "final_text": c.body or "",
        "teleprompter_text": p.get("teleprompter_text", ""),
        "storyboard": p.get("storyboard") or [],
        "title_candidates": p.get("title_candidates") or [],
        "status": p.get("script_state", _content_status_to_script(c.status)),
        "account_type": p.get("account_type") or ("leadgen" if ch_key == "leadgen" else "persona"),
        "falsify_item_id": p.get("falsify_item_id"),
        "ore_id": p.get("ore_id"),
        "kb_id": p.get("kb_id"),
        "hot_comment": p.get("hot_comment"),
        "note": c.note or "",
        "created_at": c.created_at, "updated_at": c.updated_at,
    }


def _content_status_to_script(status: str) -> str:
    return {"drafting": "generating", "review": "generated",
            "ready": "final", "failed": "failed", "published": "final"}.get(status, status)


def article_out(c: Content, *, sanitize_html=None) -> dict:
    """Content(long_article) → 旧 /api/articles 行形状。"""
    p = c.payload or {}
    d = {
        "id": c.id, "channel_id": c.channel_id,
        "script_id": c.parent_content_id,  # 旧面别名（派生来源）
        "parent_content_id": c.parent_content_id,
        "title": c.title, "md": c.body or "",
        "note": p.get("note", ""), "kb_sources": p.get("kb_sources") or [],
        "html": p.get("html", ""), "stats": p.get("stats") or {},
        "status": p.get("article_state", _content_status_to_article(c.status)),
        "created_at": c.created_at, "updated_at": c.updated_at,
        "publish_url": "", "published_at": "",
    }
    pub = p.get("publication") or {}
    d["publish_url"] = pub.get("url", "")
    d["published_at"] = pub.get("published_at", "")
    if sanitize_html and d["html"]:
        d["html"] = sanitize_html(d["html"])
    return d


def _content_status_to_article(status: str) -> str:
    return {"drafting": "generating", "review": "draft", "ready": "rendered",
            "published": "published", "failed": "failed"}.get(status, status)


def note_out(c: Content) -> dict:
    """Content(note) → 小红书笔记行。"""
    p = c.payload or {}
    return {
        "id": c.id, "channel_id": c.channel_id, "idea_id": c.idea_id,
        "title": c.title, "body": c.body or "",
        "tags": p.get("tags") or [], "cover": p.get("cover", ""),
        "issues": p.get("issues") or [],  # 轻质量闸红提示（2026-11-21 阶段 3，不拦发布）
        "status": p.get("note_state", _content_status_to_note(c.status)),
        "status_canonical": c.status,
        "material_ids": p.get("material_ids") or [],
        "publication": p.get("publication") or {},
        "created_at": c.created_at, "updated_at": c.updated_at,
    }


def _content_status_to_note(status: str) -> str:
    return {"drafting": "drafting", "review": "review", "ready": "ready",
            "published": "published", "failed": "failed"}.get(status, status)
