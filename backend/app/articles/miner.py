"""知识库挖掘器（2026-11-11 源文改编产线）：逐篇评估 ima 源文，高质量入内容清单。

流程与深读线（已停用）同构但出口不同：深读是「读一批→跨文档综合→直接成文」，
一步到稿；挖掘只做评估与抽取——清单项 = 一篇具体源文（为什么好/改编角度/逐字原话），
成文交给改编器（article_adapt）二次加工。低分源文不落行，只在当日账里计数。
游标 app_settings.adapt_cursor="idx:pos" 与深读线同款：大目录分日读完、读完换下一个。"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import AppSetting, ContentItem, DailyLedger
from ..prompts import render

_PER_DAY_DOCS = 18   # 每日评估篇数（游标续读，453 篇编目约 25 天盘完）
_DOC_CAP = 30000     # 单篇全文上限（防异常大文件）
_READ_SLEEP = 0.8    # ima 限流退避（密集调用 403 教训）
_EVAL_SLEEP = 0.4    # 评估调用间隔
_DEFAULT_GEN_LINE = 7.5  # 入清单线（AppSetting.adapt_gen_line 可覆盖）

# 目录 → 栏目兜底映射（评估 prompt 自判优先，自判不出时按目录归）
_FOLDER_CATEGORY = {
    "案例-交付实战复盘": "交付现场", "案例-案例集与行业标杆": "交付现场",
    "案例-行业场景与趋势": "行业拆局", "案例-方法论与工作包": "交付现场",
    "岗位认知与转型路径": "转行真话", "接单报价与个人品牌": "变现报价",
    "市场与人才数据": "行业拆局", "研究报告与白皮书": "行业拆局",
    "能力模型与角色认知": "转行真话", "企业落地与组织": "交付现场",
    "行业观察与源流": "行业拆局", "工程规范与工具箱": "交付现场",
    "交付方法论冲刺散篇": "交付现场", "社群与直播运营": "现场实录",
}
_CATEGORIES = set(_FOLDER_CATEGORY.values())


def _gen_line(s: Session) -> float:
    row = s.get(AppSetting, "adapt_gen_line")
    try:
        return float(row.value) if row is not None and row.value else _DEFAULT_GEN_LINE
    except (TypeError, ValueError):
        return _DEFAULT_GEN_LINE


def _load_cursor(s: Session) -> tuple[int, int]:
    row = s.get(AppSetting, "adapt_cursor")
    raw = (row.value or "").strip() if row is not None else ""
    m = re.match(r"^(\d+):(\d+)$", raw)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _save_cursor(s: Session, idx: int, pos: int) -> None:
    row = s.get(AppSetting, "adapt_cursor")
    val = f"{idx}:{pos}"
    if row is None:
        s.add(AppSetting(key="adapt_cursor", value=val))
    else:
        row.value = val
        s.add(row)
    s.commit()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@register_executor("article_kb_miner")
async def article_kb_miner(ctx: JobContext, payload: dict) -> dict:
    """每日挖一批源文：全文拉取 → 逐篇评估 → ≥生成线入内容清单。"""
    from .ima_kb import fetch_full_doc, kb_config, list_folder_docs, topic_folders, topic_kb_id

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("挖掘器未启用：缺少 ima_kb_enabled / 凭证配置")
        folders = [f for f in topic_folders(s) if f[0]]
        kb_id = topic_kb_id(s)
        idx, pos = _load_cursor(s)
        line = _gen_line(s)
        existing = set(s.exec(select(ContentItem.media_id)).all())  # type: ignore[attr-defined]
    if not folders:
        raise RuntimeError("挖掘目录为空：请配置 ima_topic_folders")
    client_id, api_key = cfg["client_id"], cfg["api_key"]

    # ── 定位本批（空目录/越界顺延，最多绕一圈）──
    folder_name = ""
    batch: list[dict] = []
    for _ in range(len(folders) + 1):
        idx = idx % len(folders)
        fid, fname = folders[idx]
        docs = await list_folder_docs(client_id, api_key, kb_id, fid)
        if pos >= len(docs):  # 本目录盘完 → 下一个
            idx, pos = idx + 1, 0
            continue
        folder_name = fname
        batch = docs[pos:pos + _PER_DAY_DOCS]
        break
    if not batch:
        with Session(engine) as s:
            _save_cursor(s, 0, 0)
            s.add(DailyLedger(day=_today(), phase="mine", ok=True,
                              produced="全库盘完一轮", reason="游标已重置，等待扩库"))
            s.commit()
        ctx.set_progress(100, "全库盘完一轮，游标已重置")
        return {"evaluated": 0, "added": 0, "message": "全库盘完一轮"}

    # ── 逐篇：拉全文 → 评估 → 达线入清单 ──
    ctx.set_progress(4, f"目录「{folder_name}」第 {pos + 1}~{pos + len(batch)} 篇，逐篇评估")
    evaluated = added = skipped_low = failed = already = 0
    for i, d in enumerate(batch, 1):
        mid = str(d.get("media_id") or "")
        title = str(d.get("title") or f"文档{i}")
        if not mid or mid in existing:
            already += 1
            continue
        try:
            text = await fetch_full_doc(client_id, api_key, mid, cap=_DOC_CAP)
        except Exception as e:  # noqa: BLE001 单篇拉取失败跳过（当日缺席，不阻塞整批）
            print(f"[kb-miner] 全文拉取失败（{title[:24]}）: {type(e).__name__}: {str(e)[:80]}")
            text = ""
        if not text.strip():
            failed += 1
            await asyncio.sleep(_READ_SLEEP)
            continue
        try:
            note = await gateway.complete_json(
                [{"role": "user", "content": render("article_kb_miner_eval",
                                                    DOC=f"《{title}》\n\n{text}",
                                                    # 2026-11-13：评分线可被 AppSetting.adapt_gen_line
                                                    # 覆盖，prompt 内同步注入真实线（原写死 7.5）
                                                    GEN_LINE=f"{line:g}")}],
                max_tokens=2000, thinking="disabled")
        except Exception as e:  # noqa: BLE001 单篇评估失败按跳过计（不阻塞整批）
            print(f"[kb-miner] 评估失败（{title[:24]}）: {type(e).__name__}: {str(e)[:80]}")
            failed += 1
            await asyncio.sleep(_EVAL_SLEEP)
            continue
        evaluated += 1
        try:
            score = round(float((note or {}).get("quality_score") or 0), 1)
        except (TypeError, ValueError):
            score = 0.0
        if score >= line:
            quotes = [str(q).strip() for q in (note or {}).get("best_quotes") or []
                      if str(q).strip()][:3]
            cat = str((note or {}).get("category") or "").strip()
            if cat not in _CATEGORIES:
                cat = _FOLDER_CATEGORY.get(folder_name, "交付现场")
            with Session(engine) as s:
                again = s.exec(select(ContentItem).where(  # type: ignore[attr-defined]
                    ContentItem.media_id == mid)).first()  # type: ignore[attr-defined]
                if again is None:  # 双保险：批内重复评估不重入
                    s.add(ContentItem(
                        media_id=mid, kb_id=kb_id, folder_id=folders[idx][0],
                        folder_name=folder_name, title=title[:120],
                        source_author=str((note or {}).get("source_author") or "")[:60],
                        quality_score=score, why_good=str((note or {}).get("why_good") or "")[:200],
                        adapt_angle=str((note or {}).get("adapt_angle") or "")[:300],
                        best_quotes=quotes, category=cat, status="pending"))
                    s.commit()
                    added += 1
        else:
            skipped_low += 1
            with Session(engine) as s:
                again = s.exec(select(ContentItem).where(  # type: ignore[attr-defined]
                    ContentItem.media_id == mid)).first()  # type: ignore[attr-defined]
                if again is None:  # 低分也落行（status=low）：整库盘完回卷时不重复评估
                    s.add(ContentItem(
                        media_id=mid, kb_id=kb_id, folder_id=folders[idx][0],
                        folder_name=folder_name, title=title[:120],
                        quality_score=score, status="low",
                        note=f"评分 {score} 低于生成线 {line}，未入改编队列"))
                    s.commit()
        ctx.set_progress(4 + int(90 * i / len(batch)),
                         f"评估 {i}/{len(batch)}：《{title[:20]}》 {score}")
        await asyncio.sleep(_EVAL_SLEEP)

    # ── 当日账 + 游标推进（读完这批才算数；全拉失败则留在原地走梯子）──
    if evaluated == 0 and failed >= len([d for d in batch if str(d.get("media_id") or "")]):
        raise TransientError(
            f"本批 {failed} 篇全文/评估全部失败（ima 限流或网络），游标未推进，稍后自动重试")
    with Session(engine) as s:
        _save_cursor(s, idx, pos + len(batch))
        s.add(DailyLedger(
            day=_today(), phase="mine", ok=True,
            produced=(f"评 {evaluated} 篇 → 入清单 {added} 项"
                      + (f" · 低分跳过 {skipped_low}" if skipped_low else "")
                      + (f" · 拉取失败 {failed}" if failed else "")),
            reason="" if (added or evaluated) else (
                "本批均已入清单" if already else f"本批无可评估源文（失败 {failed} 篇）")))
        s.commit()
    ctx.set_progress(100, f"挖掘完成：评 {evaluated} 篇，入清单 {added} 项，跳过低分 {skipped_low}")
    return {"evaluated": evaluated, "added": added, "skipped_low": skipped_low,
            "failed": failed, "folder": folder_name, "line": line}
