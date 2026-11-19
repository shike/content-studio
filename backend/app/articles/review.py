"""审校（2026-11-19 收拢）：合并审校一份问题清单 + 定向修订。

原「事实对源/语言审校/读者审校/标题评分」四路分工审（2026-11-11 版）串行调用，
单篇最多 10+ 次 LLM；2026-11-19 用户裁决收拢为一次合并审校：
事实/归属（含张冠李戴与身份冒名）+ 章节覆盖 + 语言口吻（AI味/翻译腔/造词/口水/口语口号腔）。
审失败一律不阻塞成文（宁漏勿误）；调用方按清单定向修订一次，仍不过退回清单。
"""
from __future__ import annotations

from ..llm import gateway
from ..prompts import render
from .quality import strip_fence as _strip_fence


async def review_adapt(md: str, source: str, chapters: list[str]
                       ) -> tuple[list[str], list[str], list[str]]:
    """合并审校：返回 (hard 问题, soft 建议, 未实质覆盖的章名)。

    hard=必须改，二次仍不过退回清单；soft=语言/表述建议，残余只落 note 不拦发布。
    源文必须给全（2026-11-19 事故：只喂前 9000 字，分章手册后半本的案例被当成编造——
    烘焙案例的 CFO/130 万/60 万全在源文 15000 字处，闸却判「源文没有」，整篇误杀）。
    """
    try:
        v = await gateway.complete_json(
            [{"role": "user", "content": render(
                "article_adapt_review",
                CHAPTERS="\n".join(f"- {c}" for c in chapters) or "（非分章文档）",
                DRAFT=md[:9000], SOURCE=source[:40000])}],
            max_tokens=2048, thinking="disabled")
        def _pick(key: str, cap: int, limit: int = 200) -> list[str]:
            return [str(x).strip()[:limit] for x in (v or {}).get(key) or []
                    if str(x).strip()][:cap]
        return (_pick("hard", 6), _pick("soft", 8),
                _pick("missing_chapters", 6, 40))
    except Exception as e:  # noqa: BLE001
        print(f"[review] 合并审校失败（不阻塞）: {type(e).__name__}: {str(e)[:80]}")
        return [], [], []


async def revise_once(md: str, issues: list[str]) -> str | None:
    """按问题清单定向修订一次。失败返回 None（调用方保留原稿）。"""
    try:
        body = await gateway.complete(
            [{"role": "user", "content": render("article_adapt_revise",
                                                ISSUES="\n".join(f"- {x}" for x in issues),
                                                DRAFT=md)}],
            max_tokens=8192, thinking="disabled")
        body = _strip_fence(body)
        lines = body.split("\n")
        if not lines or not lines[0].lstrip().startswith("# "):
            return None
        return "\n".join(lines[1:]).strip() or None
    except Exception as e:  # noqa: BLE001
        print(f"[review] 定向修订失败（保留原稿）: {type(e).__name__}: {str(e)[:80]}")
        return None
