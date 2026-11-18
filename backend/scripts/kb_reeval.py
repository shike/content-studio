#!/usr/bin/env python3
"""知识库全量重评（2026-11-18 评分标准 v2）：方法含量定档。

旧尺子（v1）以「能否直接改写成稿」打分，政策/报告/长手册被系统性压分（70% 判低分；
长文只读前 3 万字，3.9 万字的生存手册被打 2~3 分）。v2 全库重打分：
- 逐篇从 ima 重拉全文（cap 10 万字，不再腰斩）→ 新 prompt 评估 → 覆盖 quality；
- 保留原 folder / priority；category 字段废除；写入 rubric="v2" 标记；
- 分数 ≥ 线（adapt_gen_line）→ stage=pending/status=ready，否则 stage=low/ignored；
- 断点续跑：已带 rubric=v2 的行跳过，中断重跑不重复耗 LLM；
- stage=running 的行跳过（公众号改编在途，不互踩）。

用法（服务器仓库根）：
  PYTHONPATH=backend nohup backend/.venv/bin/python backend/scripts/kb_reeval.py [--limit N] >> data/kb_reeval.log 2>&1 &
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.articles.ima_kb import fetch_full_doc, kb_config  # noqa: E402
from app.db import engine  # noqa: E402
from app.llm import gateway  # noqa: E402
from app.models import AppSetting, Material, PipelineLedger  # noqa: E402
from app.prompts import render  # noqa: E402
from app.settings_store import load_overrides  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

# 与服务进程同口径：DB 里的运行时配置（LLM 密钥等）覆盖 .env 默认值——
# 不调用它脚本进程 settings.zhipu_api_key 是空的（密钥只存 app_settings）
load_overrides()

_DOC_CAP = 100000   # 与 v2 挖掘器同口径：全文评估，不再 3 万字腰斩
_FETCH_SLEEP = 1.0  # ima 逐篇限速（密集 403 教训）
_EVAL_SLEEP = 0.4
_FETCH_WAIT = 120
_EVAL_WAIT = 600


def _gen_line(s: Session) -> float:
    row = s.get(AppSetting, "adapt_gen_line")
    try:
        return float(row.value) if row is not None and row.value else 7.5
    except (TypeError, ValueError):
        return 7.5


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def main() -> int:
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            print("ima 未配置凭证，退出", flush=True)
            return 1
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc").order_by(Material.id)).all()  # type: ignore[attr-defined]
        line = _gen_line(s)
    todo = [m for m in rows
            if (m.quality or {}).get("rubric") != "v2" and m.stage != "running"]
    ready_before = sum(1 for m in rows if m.status == "ready")
    if limit:
        todo = todo[:limit]
    print(f"[reeval] 全库 {len(rows)} 篇，待重评 {len(todo)} 篇（rubric=v2 已覆盖其余），"
          f"线 {line:g}，入选现状 {ready_before}", flush=True)
    if not todo:
        print("[reeval] 没有待重评的行", flush=True)
        return 0

    from app.tenant_brand import brand_of
    persona = brand_of(1)["persona"]
    client_id, api_key = cfg["client_id"], cfg["api_key"]

    done = fetch_fail = eval_fail = 0
    consec_bad = 0
    for i, m in enumerate(todo, 1):
        text = ""
        try:
            text = await asyncio.wait_for(
                fetch_full_doc(client_id, api_key, m.origin_id or "", cap=_DOC_CAP),
                timeout=_FETCH_WAIT)
        except Exception as e:  # noqa: BLE001 单篇失败不动分数，续跑
            print(f"[reeval] {i}/{len(todo)} 拉取失败（{m.title[:24]}）: {type(e).__name__}", flush=True)
        if not text.strip():
            fetch_fail += 1
            consec_bad += 1
        else:
            note = None
            try:
                note = await asyncio.wait_for(
                    gateway.complete_json(
                        [{"role": "user", "content": render("article_kb_miner_eval",
                                                            PERSONA=persona,
                                                            DOC=f"《{m.title}》\n\n{text}",
                                                            GEN_LINE=f"{line:g}")}],
                        max_tokens=2000, thinking="disabled"),
                    timeout=_EVAL_WAIT)
            except Exception as e:  # noqa: BLE001
                print(f"[reeval] {i}/{len(todo)} 评估失败（{m.title[:24]}）: {type(e).__name__}: {str(e)[:80]}", flush=True)
                eval_fail += 1
            if note:
                consec_bad = 0
                try:
                    score = round(float((note or {}).get("quality_score") or 0), 1)
                except (TypeError, ValueError):
                    score = 0.0
                quotes = [str(q).strip() for q in (note or {}).get("best_quotes") or []
                          if str(q).strip()][:3]
                with Session(engine) as s:
                    row = s.get(Material, m.id)
                    if row is not None and row.stage != "running":
                        old = row.quality or {}
                        row.quality = {"score": score,
                                       "folder": str(old.get("folder") or ""),
                                       "why_good": str((note or {}).get("why_good") or "")[:200],
                                       "adapt_angle": str((note or {}).get("adapt_angle") or "")[:300],
                                       "best_quotes": quotes,
                                       "priority": int(old.get("priority") or 0),
                                       "rubric": "v2"}
                        if score >= line:
                            row.stage, row.status = "pending", "ready"
                        else:
                            row.stage, row.status = "low", "ignored"
                        s.add(row)
                        s.commit()
                done += 1
            else:
                consec_bad += 1
        if consec_bad and consec_bad % 8 == 0:
            print(f"[reeval] 连续 {consec_bad} 篇失败，退避 180s（ima 惩罚窗/网抖）", flush=True)
            await asyncio.sleep(180)
        if consec_bad >= 24:
            print("[reeval] 连续 24 篇失败，停止——修复后重跑即可断点续评", flush=True)
            break
        if i % 50 == 0:
            with Session(engine) as s:
                s.add(PipelineLedger(day=_today(), pipeline="kb_reeval", ok=True,
                                  produced=f"重评进度 {i}/{len(todo)}（成 {done} / 拉取败 {fetch_fail} / 评估败 {eval_fail}）"))
                s.commit()
            print(f"[reeval] 进度 {i}/{len(todo)}：成 {done} / 拉取败 {fetch_fail} / 评估败 {eval_fail}", flush=True)
        await asyncio.sleep(_FETCH_SLEEP if not text.strip() else _EVAL_SLEEP)

    with Session(engine) as s:
        after = s.exec(select(Material.status).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]  # 单列 select 返回标量行
        ready_after = sum(1 for x in after if x == "ready")
        s.add(PipelineLedger(day=_today(), pipeline="kb_reeval", ok=True,
                          produced=(f"全量重评收尾：重打分 {done}（拉取败 {fetch_fail} / 评估败 {eval_fail}），"
                                    f"入选 {ready_before}→{ready_after}")))
        s.commit()
    print(f"[reeval] 收尾：重打分 {done}（拉取败 {fetch_fail} / 评估败 {eval_fail}），"
          f"入选 {ready_before}→{ready_after}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
