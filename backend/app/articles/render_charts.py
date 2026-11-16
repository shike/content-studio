"""长文配图：数据图表渲染插入、头图生成（2026-11-16 从 service.py 拆出，只挪不改行为）。

专职步骤，不依赖分节写作顺带输出；提取不到就不加，绝不编造；单张失败跳过不阻塞成文。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..llm import gateway
from ..prompts import load


async def attach_charts(md_text: str, tenant_id: int, log_prefix: str) -> str:
    """数据图表后处理（两条成文执行器共用）：从成文提取数据对比点 → matplotlib 渲染
    → 按节插入。专职步骤，不依赖分节写作顺带输出（模型依从性不稳定）；
    提取不到就不加，绝不编造；单张失败跳过不阻塞成文。"""
    from ..articles.charts import render_chart, validate_spec

    if not md_text.strip():
        return md_text
    try:
        spec_resp = await gateway.complete_json(
            [{"role": "system", "content": load("article_charts")},
             {"role": "user", "content": md_text[:8000]}],
            max_tokens=4000,
            thinking="disabled")
        specs = [x for x in (spec_resp.get("charts") or []) if isinstance(x, dict)][:3]
    except Exception as e:  # noqa: BLE001
        specs = []
        print(f"{log_prefix} 图表提取失败（跳过）: {type(e).__name__}: {str(e)[:80]}")
    rendered = []
    for k, sp in enumerate(specs):
        try:
            sp = validate_spec(json.dumps(sp))
            sp.setdefault("after_section", k + 1)
            from ..tenant_brand import primary_of

            path = await asyncio.to_thread(render_chart, sp, primary_of(tenant_id))
            rendered.append((min(int(sp.get("after_section") or k + 1), 6), sp, Path(path).name))
        except Exception as e:  # noqa: BLE001
            print(f"{log_prefix} 图表 {k} 渲染失败跳过: {type(e).__name__}: {str(e)[:60]}")
    for sec_no, sp, name in sorted(rendered, key=lambda x: -x[0]):
        t = str(sp.get("title") or "图表").replace('"', '')
        md_text = insert_chart_after_section(md_text, sec_no, t, name)
    return md_text


async def attach_cover(md_text: str, title: str, tenant_id: int, log_prefix: str) -> str:
    """头图（深靛渐变+白字标题+账号名）：渲染并插入 md 顶部；失败不影响正文。"""
    try:
        from ..articles.charts import render_cover
        from ..tenant_brand import brand_of, cover_slogan_of, primary_of

        signature = brand_of(tenant_id)["signature"]  # 租户品牌署名（头图右下）
        cover_path = await asyncio.to_thread(
            render_cover, title or "深度分析", signature, primary_of(tenant_id),
            cover_slogan_of(tenant_id))
        md_text = f"![头图](cover:{Path(cover_path).name})\n\n" + md_text
    except Exception as e:  # noqa: BLE001 头图失败不影响正文
        print(f"{log_prefix} 头图渲染失败: {type(e).__name__}: {str(e)[:80]}")
    return md_text


def insert_chart_after_section(md: str, sec_no: int, title: str, filename: str) -> str:
    """把图表图片标记插入第 sec_no 个小节（1 基）的末尾；越界则追加到文末。"""
    lines = md.split("\n")
    out: list[str] = []
    cnt = 0
    inserted = False
    i = 0
    while i < len(lines):
        out.append(lines[i])
        if lines[i].startswith("## "):
            cnt += 1
            if cnt == sec_no:
                j = i + 1
                while j < len(lines) and not lines[j].startswith("## "):
                    out.append(lines[j])
                    j += 1
                out.append(f"\n![图表：{title}](chart:{filename})\n")
                inserted = True
                i = j
                continue
        i += 1
    if not inserted:
        out.append(f"\n![图表：{title}](chart:{filename})")
    return "\n".join(out)
