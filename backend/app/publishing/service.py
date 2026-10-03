"""发布台：物料包生成（封面文案+话题标签）+ 忽略清单；发布动作完全人工（R6.4，sau 集成已移除）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlmodel import Session, or_, select

from ..db import engine
from ..llm import gateway
from ..articles.renderer import sanitize_html
from ..models import (Article, Script, Topic)
from ..prompts import load
from ..settings import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FALLBACK_TAGS = ["AI落地", "企业数字化转型", "人工智能"]


def build_package(asset_type: str, asset_id: int) -> dict:
    script, topic, article = _load_assets(asset_type, asset_id)

    titles = script.title_candidates if script else []
    douyin_titles = [t["title"] for t in titles if t.get("platform") == "douyin"]
    channels = next((t["title"] for t in titles if t.get("platform") == "channels"), "")
    cover = next((t["title"] for t in titles if t.get("platform") == "cover"), "")

    material = {
        "选题": {"标题": topic.title if topic else "", "受众": topic.audience if topic else ""},
        "标题候选": titles,
        "口播稿": (script.final_text if script else "") or (article.md[:300] if article else ""),
    }
    tags, llm_cover, extra = _gen_tags(material)
    return {
        "asset_type": asset_type,
        "asset_id": asset_id,
        "douyin_title": (douyin_titles + [topic.title if topic else ""])[0],
        "douyin_title_alt": douyin_titles[1] if len(douyin_titles) > 1 else "",
        "douyin_tags": tags,
        "channels_caption": channels,
        "cover_text": cover or llm_cover or (topic.title[:8] if topic else ""),
        "pinned_comment": extra.get("pinned_comment", ""),
        "topic_groups": extra.get("topic_groups", []),
        "wechat_html": sanitize_html(article.html) if article and article.html else "",
    }


def _load_assets(asset_type: str, asset_id: int):
    script: Optional[Script] = None
    topic: Optional[Topic] = None
    article: Optional[Article] = None
    with Session(engine) as s:
        if asset_type == "script":
            script = s.get(Script, asset_id)
            if script is None:
                raise ValueError(f"script {asset_id} 不存在")
            if script.topic_id:
                topic = s.get(Topic, script.topic_id)
        elif asset_type == "article":
            article = s.get(Article, asset_id)
            if article is None:
                raise ValueError(f"article {asset_id} 不存在")
            if article.topic_id:
                topic = s.get(Topic, article.topic_id)
            if article.script_id:
                script = s.get(Script, article.script_id)
        else:
            raise ValueError("asset_type 必须是 script / article")
    return script, topic, article


def _gen_tags(material: dict) -> tuple[list, str]:
    if not gateway.is_configured():
        return list(_FALLBACK_TAGS), ""
    try:
        data = gateway.complete_json(
            [{"role": "system", "content": load("publish_tags")},
             {"role": "user", "content": json.dumps(material, ensure_ascii=False)[:4000]}],
            purpose="publish_tags", max_tokens=1024)
        tags = [str(t).lstrip("#") for t in (data.get("douyin_tags") or []) if str(t).strip()]
        groups = [str(g).strip() for g in (data.get("topic_groups") or []) if str(g).strip()]
        return (tags or list(_FALLBACK_TAGS)), str(data.get("cover_text") or ""), {
            "pinned_comment": str(data.get("pinned_comment") or ""),
            "topic_groups": groups,
        }
    except Exception as e:  # noqa: BLE001 物料包不因标签生成失败而失败（降级可见）
        print(f"[publishing] 标签生成失败，用内置兜底标签: {type(e).__name__}: {str(e)[:100]}")
        return list(_FALLBACK_TAGS), "", {}


