"""数字人生成执行器：定稿脚本文本 → 蝉镜 API（克隆形象+配套音色+自动字幕）→ 成片。

2026-09-24 引擎定版：蝉镜 API 为唯一主线（老引擎 OmniHuman/智能视觉/静态基线已下线，
代码随 git 历史保留在旧版本中）。超长脚本由 chanjing.render_long 自动分段出片再拼接。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..models import AvatarConfig, AvatarVideo, Script
from ..settings import settings
from . import chanjing

_AVATAR_DIR = settings.data_dir / "avatars"


def _avatar_video_fail(payload: dict, exc: Exception) -> None:
    with Session(engine) as s:  # 任务失败要把视频行标失败，否则前端永远显示生成中
        row = s.get(AvatarVideo, int(payload.get("video_row_id") or 0))
        if row is not None and row.status == "generating":
            row.status = "failed"
            row.error = str(exc)[:500]
            s.add(row)
            s.commit()


@register_executor("avatar_video", on_fail=_avatar_video_fail)
async def avatar_video(ctx: JobContext, payload: dict) -> dict:
    return await _run(ctx, payload["video_row_id"], int(payload.get("model") or 0),
                      pre_points=int(payload.get("points") or 0),
                      want_bigtext=bool(payload.get("bigtext", True)))


async def _run(ctx: JobContext, video_row_id: int, model: int = 0,
               pre_points: int = 0, want_bigtext: bool = True) -> dict:
    ok, why = chanjing.configured()
    if not ok:
        raise RuntimeError(why)
    with Session(engine) as s:
        row = s.get(AvatarVideo, video_row_id)
        if row is None:
            raise RuntimeError(f"avatar_video {video_row_id} 不存在")
        avatar = s.get(AvatarConfig, row.avatar_id)
        if avatar is None:
            raise RuntimeError("数字人配置不存在")
        script = s.get(Script, row.script_id)
        if script is None or not (script.final_text or "").strip():
            raise RuntimeError("脚本没有定稿文本，先在脚本工场定稿")
        text = script.final_text
        avatar_id = avatar.id
        if not avatar.chanjing_person_id:
            raise RuntimeError(
                "该数字人还没有克隆形象：请先在数字人页「克隆新形象」上传出镜视频（SaaS 模式下不使用平台默认形象）")
        person = avatar.chanjing_person_id
        audio_man = avatar.chanjing_audio_man or ""
        speed = chanjing.clamp_speed(avatar.speed_ratio)  # 口播语速倍数（配置级）

    # 克隆新形象后配套音色未回填：从形象详情取并持久化（新脸配新声，绝不落回默认音色）
    if not audio_man and person != chanjing.DEFAULT_PERSON_ID:
        detail = await chanjing.person_detail(person)
        audio_man = (detail.get("audio_man_id") or "").strip()
        if not audio_man:
            raise RuntimeError("克隆形象的配套音色尚未训练完成，稍后再试（或重新克隆）")
        with Session(engine) as s:
            cfg = s.get(AvatarConfig, avatar_id)
            cfg.chanjing_audio_man = audio_man
            s.add(cfg)
            s.commit()
    if not audio_man:
        raise RuntimeError(
            "该数字人还没有配套音色：克隆训练完成后自动回填，稍后再试（或重新克隆）")

    # 按形象原生尺寸渲染（2026-09-28 修）：硬拉 1080×1920 会让 720p 形象上采样变软
    size = await chanjing.person_size(person)

    # 检查点续跑（P4）：run_dir 按视频行确定，重试落同一目录续接已提交/已完成的分段
    run_dir = _AVATAR_DIR / f"v{video_row_id}"

    # 蝉豆计账：前后余额差 = 本次全部消耗（含分段）
    beans_before = await chanjing.get_balance()

    def _stage(stage: float, msg: str) -> None:
        ctx.set_progress(int(max(5, min(95, stage))), msg)

    final, info = await chanjing.render_long(person, audio_man, text, run_dir,
                                             on_stage=_stage, model=model, speed=speed, size=size)
    beans_after = await chanjing.get_balance()
    duration = _video_duration(final)
    bigtext_items: list[dict] = []
    bigtext_note = ""

    # 左上角栏目角标（租户品牌两行 + 暖黄框 + 日期/Day N，全程常显）：
    # 平台 create_video 没有文字层 → 本地叠一张透明 PNG（不重跑数字人、不再花豆）。
    # 栏目名 = 租户品牌配置（label_lines_of），期号 = 本租户第 N 条成片，日期取北京时间的当天。
    if want_bigtext and duration > 0:
        try:
            with Session(engine) as s:
                vid_row = s.get(AvatarVideo, video_row_id)
                tid = vid_row.tenant_id if vid_row else 0
                done_before = len(s.exec(
                    select(AvatarVideo).where(AvatarVideo.tenant_id == tid,
                                              AvatarVideo.status == "done",
                                              AvatarVideo.id < video_row_id)).all())
            day = done_before + 1
            from datetime import datetime, timedelta, timezone

            date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y/%m/%d")
            ctx.set_progress(96, f"烧录栏目角标（Day {day}）")
            from ..tenant_brand import accent_rgb_of, label_font_path, label_lines_of

            label_lines = label_lines_of(tid)
            font_file = label_font_path(tid, "".join(label_lines))
            burned = await asyncio.to_thread(
                chanjing.burn_label, final, run_dir, day, date_str,
                lines=label_lines, font_path=font_file, accent=accent_rgb_of(tid))
            if burned is not None:
                final = burned
                bigtext_items = [{"text": "".join(label_lines), "day": day,
                                  "date": date_str, "start": 0.0, "end": round(duration, 1)}]
                bigtext_note = f"栏目角标：{'/'.join(label_lines)} · Day {day} · {date_str}"
            else:
                bigtext_note = "栏目角标：烧录失败，已交付无角标版本"
        except Exception as e:  # noqa: BLE001 角标是增强：失败降级并说明，不让成片背锅
            bigtext_note = f"栏目角标失败（{type(e).__name__}）：{str(e)[:80]}，已交付无角标版本"
            print(f"[label] {bigtext_note}")
    if not want_bigtext:
        bigtext_note = "左上角短标：本片关闭"

    with Session(engine) as s:
        row = s.get(AvatarVideo, video_row_id)
        row.video_path = str(final)
        row.status = "done"
        row.beans_used = max(0, beans_before - beans_after)
        row.seconds = duration
        row.bigtext = bigtext_items
        if bigtext_note:
            row.notes = bigtext_note
        beans_used = row.beans_used
        s.add(row)
        s.commit()
        # 会话已关，本地变量兜住后续要用的值（detached 实例属性不再回读）
        tenant_id = row.tenant_id
        pre_stored = int(row.points_used or 0)

    # 按秒结算租户积分：预扣按预估秒数，成片后多退少补（差额进账本，可对账）
    points_settled = 0
    try:
        from ..credits import avatar_video_points, settle

        pre = pre_points or pre_stored
        actual = avatar_video_points(duration, model)
        if pre > 0 and tenant_id:
            settle(tenant_id, pre, actual, f"数字人视频结算 #{video_row_id}")
        else:
            actual = pre or actual  # 老任务没有预扣记录：不结算，只记口径
        with Session(engine) as s:
            r2 = s.get(AvatarVideo, video_row_id)
            if r2 is not None:
                r2.points_used = actual
                s.add(r2)
                s.commit()
        points_settled = actual
    except Exception as e:  # noqa: BLE001 结算失败不能让成片丢失
        print(f"[avatar] 积分结算失败 video#{video_row_id}: {type(e).__name__}: {str(e)[:120]}")

    # 蝉豆流水记账（独立于视频行：删视频不影响消耗统计）
    try:
        from ..models import BeanLedger

        with Session(engine) as s:
            s.add(BeanLedger(video_id=video_row_id, beans=beans_used, points=points_settled,
                             seconds=duration, model=model))
            s.commit()
    except Exception as e:  # noqa: BLE001 记账失败不影响成片
        print(f"[avatar] 蝉豆流水记账失败: {type(e).__name__}: {str(e)[:100]}")

    ctx.set_progress(100, "完成")
    return {"video_id": video_row_id, "video_path": str(final),
            "segments": info.get("segments", 1), "beans_used": beans_used,
            "seconds": duration}


def _video_duration(path: Path) -> float:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:  # 坏文件会挂死 ffprobe，超时兜底防堵队列
        return 0.0
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0
