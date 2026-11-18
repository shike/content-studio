#!/usr/bin/env python3
"""知识库存量正文回填（2026-11-17）：只拉 ima 全文写 body，不做 LLM 评估（挖掘器已评估过的补料）。

用法（服务器仓库根）：PYTHONPATH=backend backend/.venv/bin/python backend/scripts/kb_backfill_body.py
幂等：只填 body 为空的行；ima 限流按 _READ_SLEEP 退避；失败篇留空下次再跑。
背景：挖掘器 2026-11-17 起入库即存原文（cap 12000），此前入库的文档没有正文——
视频脚本/小红书直建需要原文做写作原料。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.articles.ima_kb import fetch_full_doc, kb_config, topic_kb_id  # noqa: E402
from app.db import engine  # noqa: E402
from app.materials.service import clean_kb_body  # noqa: E402
from app.models import Material  # noqa: E402

_DOC_CAP = 30000
_BODY_CAP = 12000
_READ_SLEEP = 0.8


async def main() -> int:
    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            print("[kb-body] 未配置 ima 凭证（设置页 ima_*）", flush=True)
            return 1
        rows = s.exec(
            select(Material).where(Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]
        todo = [m for m in rows if not (m.body or "").strip() and (m.origin_id or "").strip()]
        print(f"[kb-body] 待回填 {len(todo)}/{len(rows)} 篇", flush=True)
        done = failed = 0
        client_id, api_key = cfg["client_id"], cfg["api_key"]
        kb_id = topic_kb_id(s)
        for i, m in enumerate(todo, 1):
            try:
                text = await asyncio.wait_for(
                    fetch_full_doc(client_id, api_key, str(m.origin_id), cap=_DOC_CAP), timeout=120)
            except Exception as e:  # noqa: BLE001 单篇失败留空，下次再跑
                failed += 1
                print(f"  [{i}/{len(todo)}] 拉取失败 {m.title[:24]}: {type(e).__name__}", flush=True)
                await asyncio.sleep(_READ_SLEEP)
                continue
            body = clean_kb_body(text)[:_BODY_CAP]
            if not body.strip():
                failed += 1
                print(f"  [{i}/{len(todo)}] 非文本内容，拒收 {m.title[:24]}", flush=True)
                await asyncio.sleep(_READ_SLEEP)
                continue
            m.body = body
            s.add(m)
            s.commit()
            done += 1
            if done % 20 == 0:
                print(f"  [{i}/{len(todo)}] 已回填 {done}", flush=True)
            await asyncio.sleep(_READ_SLEEP)
        print(f"[kb-body] 完成：回填 {done}，失败 {failed}（失败篇重跑本脚本可续）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
