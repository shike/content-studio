"""审校组（2026-11-11 源文改编产线，一期两审+标题评分）：事实对源 / 读者弃读点 / 语言专业度 / 标题五维分。

与旧质量闸（单模型 5 维总分自评）的区别：每审是分工的批评者，返回**具体问题清单**
而非总分——问题清单可执行（定向修订有靶子），总分不可执行（7.5 分的稿子照样被毙）。
去AI味=机检（正则）+ 语言审校（LLM，2026-11-18 用户裁决：AI味/翻译腔/硬造词/口水）。审失败一律不阻塞成文（宁漏勿误）。"""
from __future__ import annotations


from ..llm import gateway
from ..prompts import render
from .quality import strip_fence as _strip_fence


async def review_facts(md: str, source: str) -> list[str]:
    """事实对源：关键断言与源文核对，返回问题清单（空=过）。审失败返回空（不阻塞）。

    源文必须给全（2026-11-19 事故：只喂前 9000 字，分章手册后半本的案例被当成编造——
    烘焙案例的 CFO/130 万/60 万全在源文 15000 字处，闸却判「源文没有」，整篇误杀）。"""
    try:
        v = await gateway.complete_json(
            [{"role": "user", "content": render("article_review_facts",
                                                DRAFT=md[:9000], SOURCE=source[:40000])}],
            max_tokens=1024, thinking="disabled")
        return [str(x).strip()[:160] for x in (v or {}).get("issues") or [] if str(x).strip()][:8]
    except Exception as e:  # noqa: BLE001
        print(f"[review] 事实对源失败（不阻塞）: {type(e).__name__}: {str(e)[:80]}")
        return []


async def review_reader(md: str, persona: str) -> list[str]:
    """读者审校：以目标读者身份标弃读点，返回问题清单（空=过）。审失败返回空。"""
    try:
        v = await gateway.complete_json(
            [{"role": "user", "content": render("article_review_reader", DRAFT=md[:9000])}],
            max_tokens=1024, thinking="disabled")
        return [str(x).strip()[:160] for x in (v or {}).get("drop_points") or [] if str(x).strip()][:8]
    except Exception as e:  # noqa: BLE001
        print(f"[review] 读者审校失败（不阻塞）: {type(e).__name__}: {str(e)[:80]}")
        return []


async def review_language(md: str) -> list[str]:
    """语言审校（专业严谨度）：AI 套话/翻译腔机翻痕迹/硬造词/口水空转，返回问题清单（空=过）。
    审失败返回空（不阻塞）；调用方按清单定向修订一次。"""
    try:
        v = await gateway.complete_json(
            [{"role": "user", "content": render("article_review_language", DRAFT=md[:9000])}],
            max_tokens=1024, thinking="disabled")
        return [str(x).strip()[:200] for x in (v or {}).get("issues") or [] if str(x).strip()][:8]
    except Exception as e:  # noqa: BLE001
        print(f"[review] 语言审校失败（不阻塞）: {type(e).__name__}: {str(e)[:80]}")
        return []


async def score_titles(candidates: list[str], persona: str) -> list[tuple[str, float]]:
    """标题五维评分，返回按分降序的 (标题, 分数)。失败返回原序 0 分（不阻塞）。"""
    cands = [c.strip() for c in candidates if c and c.strip()]
    if not cands:
        return []
    try:
        v = await gateway.complete_json(
            [{"role": "user", "content": render("article_title_score",
                                                CANDIDATES="\n".join(f"- {c}" for c in cands))}],
            max_tokens=1024, thinking="disabled")
        ranked = []
        seen = set()
        for row in (v or {}).get("ranked") or []:
            t = str(row.get("title") or "").strip()
            if t in cands and t not in seen:
                try:
                    ranked.append((t, round(float(row.get("score") or 0), 1)))
                except (TypeError, ValueError):
                    ranked.append((t, 0.0))
                seen.add(t)
        for c in cands:  # 评分漏掉的标题按 0 分补齐
            if c not in seen:
                ranked.append((c, 0.0))
        return sorted(ranked, key=lambda x: -x[1])
    except Exception as e:  # noqa: BLE001
        print(f"[review] 标题评分失败（不阻塞）: {type(e).__name__}: {str(e)[:80]}")
        return [(c, 0.0) for c in cands]


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
