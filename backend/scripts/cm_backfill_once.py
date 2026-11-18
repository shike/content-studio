"""监控号存量回填一次性驱动（2026-11-18 用户裁决·扩量2：跑一轮 material_backfill）。

独立进程提交的任务进不了应用内存队列（runner 是进程内的），故本驱动只做三件事：
列视频 → 过窗过闸 → 落 ready 行（不建 ignored 行，闸外直接跳过，免得几百条噪声）。
拆解入队交给应用内 reaper 的 owed 声明（owed_materials 对 ready 无 breakdown 自动补
material_analyze，≤一个巡检周期内入队）——按钮版 gate_rows 直 submit，此处延迟一个
巡检周期，终态一致。

北京 03:30 后才开始建行：晚间是号主生成高峰（heavy 串行会被拆解 backlog 堵住），
凌晨跑。断点安全：逐账号落行即时生效，重跑按 origin_url 幂等跳过。
用法（服务器）：nohup backend/.venv/bin/python backend/scripts/cm_backfill_once.py \
  >> data/cm_backfill_once.log 2>&1 < /dev/null &
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, install_tenant_scope  # noqa: E402
from app.materials import service  # noqa: E402
from app.materials.collectors import douyin_list  # noqa: E402
from app.models import Material, Source, utcnow  # noqa: E402
from app.settings import settings  # noqa: E402

install_tenant_scope()

RUN_AT_BEIJING = "03:30"  # 建行开始时刻（北京）；早于此则等待


def _wait_until_run_time() -> None:
    bj_now = datetime.now(timezone(timedelta(hours=8)))
    target = bj_now.replace(hour=int(RUN_AT_BEIJING[:2]), minute=int(RUN_AT_BEIJING[3:]),
                             second=0, microsecond=0)
    if bj_now >= target:
        target += timedelta(days=1)
    wait_s = (target - bj_now).total_seconds()
    print(f"[backfill] 北京 {bj_now:%m-%d %H:%M}，等到北京 {target:%m-%d} {RUN_AT_BEIJING} 再建行"
          f"（{wait_s / 3600:.1f}h）——晚间生成高峰不插队", flush=True)
    time.sleep(max(0, wait_s))


async def main() -> int:
    _wait_until_run_time()
    cmin = max(1, int(settings.dissect_comment_min or 20))
    dmin = max(1, int(settings.dissect_digg_min or 300))
    days = max(1, int(settings.backfill_window_days or 30))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    with Session(engine) as s:
        sources = s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "peer_account",  # type: ignore[attr-defined]
            Source.status == "active",  # type: ignore[attr-defined]
            Source.platform == "douyin")).all()  # type: ignore[attr-defined]
        known = {b.origin_id: b.id for b in s.exec(  # type: ignore[attr-defined]
            select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "douyin_video")).all()  # type: ignore[attr-defined]
            if b.origin_id}
    print(f"[backfill] 开始：{len(sources)} 个启用账号，追溯窗 {days} 天，"
          f"闸=评论≥{cmin} 或 赞≥{dmin}", flush=True)
    created = skipped_gate = skipped_dup = 0
    notes: list[str] = []
    for i, src in enumerate(sources, 1):
        entries, err, _ = await asyncio.to_thread(
            douyin_list._list_author_videos_via_anchor, src.url, 20)
        if entries is None:
            entries, err2, _ = await asyncio.to_thread(
                douyin_list._list_author_videos_via_share_page, src.url, 20)
        if entries is None:
            notes.append(f"{src.name} 列表未生效：{(err or '')[:50]}")
            await asyncio.sleep(2)
            continue
        n_src = 0
        for e in entries:
            vid = str(e.get("id") or "")
            if not vid or vid in known:
                skipped_dup += (1 if vid else 0)
                continue
            ctime = int(e.get("ctime") or 0)
            pub = (datetime.fromtimestamp(ctime, tz=timezone.utc).replace(tzinfo=None)
                   if ctime else None)
            if pub and pub < cutoff:
                continue  # 超追溯窗
            st = e.get("stats") or {}
            c, dg = int(st.get("comment") or 0), int(st.get("digg") or 0)
            if not st.get("comment") and not st.get("digg"):
                continue  # 列表没带互动数：不逐条 probe（夜间省窗口），放弃该条
            passed, why = service.gate_verdict(st)
            if not passed:
                skipped_gate += 1
                continue
            url = e.get("url") or f"https://www.douyin.com/video/{vid}"
            with Session(engine) as s:
                dup = s.exec(select(Material).where(  # type: ignore[attr-defined]
                    Material.origin_url == url)).first()  # type: ignore[attr-defined]
                if dup is not None:
                    skipped_dup += 1
                    continue
                m, _new = service.upsert_material(
                    s, tenant_id=src.tenant_id or 1, origin_type="douyin_video",
                    origin_id=vid, origin_url=url, author=src.name,
                    title=str(e.get("title") or "")[:80], stats=st,
                    source_id=src.id, published_at=pub,
                    quality={"account_type": (src.config or {}).get("account_type") or "persona",
                             "heat": service.heat_score(st)},
                    status="ready",
                    note=f"监控号存量回填（{days} 天：{why}）")
                if m.id:
                    known[vid] = m.id
                created += 1
                n_src += 1
        notes.append(f"{src.name}：列 {len(entries)}，建行 {n_src}")
        print(f"[backfill] ({i}/{len(sources)}) {notes[-1]}", flush=True)
        await asyncio.sleep(2)
    print(f"[backfill] 完成：建行 {created}（闸外跳过 {skipped_gate}，重复 {skipped_dup}）；"
          f"ready 无 breakdown 的行由应用内 owed 自动补拆解任务（≤30 分钟入队）", flush=True)
    for n in notes:
        print(f"[backfill] · {n}", flush=True)
    return created


if __name__ == "__main__":
    asyncio.run(main())
