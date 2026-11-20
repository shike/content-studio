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


async def review_adapt(md: str, source: str, own: bool = False) -> tuple[list[str], list[str]]:
    """合并审校：返回 (hard 问题, soft 建议)。

    hard=必须改，二次仍不过退回清单；soft=语言/表述建议，残余只落 note 不拦发布。
    源文必须给全（2026-11-19 事故：只喂前 9000 字，分章手册后半本的案例被当成编造——
    烘焙案例的 CFO/130 万/60 万全在源文 15000 字处，闸却判「源文没有」，整篇误杀）。
    own=True：源文是号主自己的稿子，第一人称与「我」的归属规则反转（2026-11-20 用户确认）。
    """
    identity = (
        "这是号主自己的稿子：源文里的「我」就是号主。正文用第一人称；出现「手册作者」"
        "「源文里写」「作者记录」这类把自己的话转述成别人的写法，按身份错误报。"
        if own else
        "源文作者的第一人称不是号主的经历：正文若用第一人称讲源文作者的亲历，按身份错误报；"
        "引用原话必须标明说话人。"
    )
    try:
        v = await gateway.complete_json(
            [{"role": "user", "content": render(
                "article_adapt_review", DRAFT=md[:9000], SOURCE=source[:40000],
                IDENTITY=identity)}],
            max_tokens=2048, thinking="disabled")
        def _pick(key: str, cap: int, limit: int = 200) -> list[str]:
            return [str(x).strip()[:limit] for x in (v or {}).get(key) or []
                    if str(x).strip()][:cap]
        return _pick("hard", 10), _pick("soft", 8)
    except Exception as e:  # noqa: BLE001
        print(f"[review] 合并审校失败（不阻塞）: {type(e).__name__}: {str(e)[:80]}")
        return [], []


async def revise_once(md: str, issues: list[str], source: str = "",
                      own: bool = False) -> str | None:
    """按问题清单定向修订一次。失败返回 None（调用方保留原稿）。

    2026-11-20 修：修订必须看到源文。此前只喂问题清单，改「凭空事实」时模型
    无法核对，只能换一种说法保留编造内容，两轮修订原地打转（kz2 实测 hard 6→5）。
    """
    identity = (
        "这是号主自己的稿子：源文里的「我」就是号主。正文用第一人称；"
        "出现「手册作者」「源文里写」「作者记录」这类把自己的话转述成别人的写法，要改回「我」。"
        if own else
        "源文作者的第一人称不是号主的经历：正文若用第一人称讲源文作者的亲历，要改成"
        "第三人称或标明说话人。"
    )
    try:
        body = await gateway.complete(
            [{"role": "user", "content": render("article_adapt_revise",
                                                ISSUES="\n".join(f"- {x}" for x in issues),
                                                DRAFT=md, SOURCE=source[:40000],
                                                IDENTITY=identity)}],
            max_tokens=8192, thinking="disabled")
        body = _strip_fence(body)
        lines = body.split("\n")
        if not lines or not lines[0].lstrip().startswith("# "):
            return None
        return "\n".join(lines[1:]).strip() or None
    except Exception as e:  # noqa: BLE001
        print(f"[review] 定向修订失败（保留原稿）: {type(e).__name__}: {str(e)[:80]}")
        return None
