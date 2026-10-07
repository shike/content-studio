"""口播数字人 API（蝉镜引擎版，2026-09-24）。

配置=蝉镜克隆形象+配套音色；生成=定稿脚本 → 蝉镜 TTS 合成 → 成片入媒体库。
预估接口给前端生成前成本闸（秒数/豆/元 + 余额是否充足）。
"""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..avatar import chanjing
from ..auth import require_user
from ..credits import (CLONE_POINTS, avatar_video_points, get_balance,
                     require_points)
from ..db import engine
from ..jobs.runner import runner
from ..models import AvatarConfig, AvatarVideo, User
from ..settings import settings

router = APIRouter(prefix="/api/avatar")

_DIR = str(settings.data_dir / "avatars")


class ConfigIn(BaseModel):
    name: str
    chanjing_person_id: str = ""  # 空=用内置默认形象
    chanjing_audio_man: str = ""  # 空=用内置默认音色


class ConfigUpdate(BaseModel):
    name: str | None = None
    speed_ratio: float | None = None  # 口播语速倍数（0.5~2.0，越界夹取）
    chanjing_person_id: str | None = None
    chanjing_audio_man: str | None = None
    chanjing_pic_url: str | None = None
    chanjing_preview_url: str | None = None


@router.get("/configs")
def list_configs() -> dict:
    with Session(engine) as s:
        rows = s.exec(select(AvatarConfig).order_by(AvatarConfig.id.desc())).all()
        return {"items": [r.model_dump() for r in rows]}


@router.post("/configs", status_code=201)
def create_config(body: ConfigIn) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="名称不能为空")
    with Session(engine) as s:
        c = AvatarConfig(name=body.name.strip(), engine="chanjing",
                         chanjing_person_id=body.chanjing_person_id,
                         chanjing_audio_man=body.chanjing_audio_man)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.model_dump()


@router.put("/configs/{config_id}")
def update_config(config_id: int, body: ConfigUpdate) -> dict:
    """编辑数字人配置（改名/绑定克隆形象，None 字段不更新）。"""
    with Session(engine) as s:
        c = s.get(AvatarConfig, config_id)
        if c is None:
            raise HTTPException(status_code=404, detail="config not found")
        for k, v in body.model_dump(exclude_none=True).items():
            setattr(c, k, v)
        s.add(c)
        s.commit()
        s.refresh(c)
        return c.model_dump()


@router.delete("/configs/{config_id}")
def delete_config(config_id: int) -> dict:
    with Session(engine) as s:
        c = s.get(AvatarConfig, config_id)
        if c is None:
            raise HTTPException(status_code=404, detail="config not found")
        s.delete(c)
        s.commit()
    return {"deleted": config_id}


@router.get("/balance")
async def balance(user: User = Depends(require_user)) -> dict:
    """蝉镜豆余额：平台成本口径，仅平台管理员可读（租户侧看积分，见 /api/credits）。"""
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="平台成本口径，仅平台管理员可见")
    ok, why = chanjing.configured()
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    return {"beans": await chanjing.get_balance()}


@router.get("/usage")
def usage(user: User = Depends(require_user)) -> dict:
    """蝉豆消耗台账（累计）：成片数/总时长/总耗豆。

    金额（yuan）仅平台管理员可见——租户侧只看蝉豆，金额是平台内部口径。
    数据源=bean_ledger 流水（独立于视频行：删视频/清记录不影响消耗统计）；
    兼容回退：流水为空时回读 avatar_videos（老数据迁移前口径）。"""
    from sqlmodel import func

    from ..models import BeanLedger

    with Session(engine) as s:
        lq = select(func.count(BeanLedger.id), func.coalesce(func.sum(BeanLedger.seconds), 0),
                    func.coalesce(func.sum(BeanLedger.beans), 0),
                    func.coalesce(func.sum(BeanLedger.points), 0))
        if user.role != "platform_admin":  # 台账租户隔离：租户只见自己的流水合计
            lq = lq.where(BeanLedger.tenant_id == user.tenant_id)  # type: ignore[attr-defined]
        lrow = s.exec(lq).one()
        points = 0
        if int(lrow[0] or 0) > 0:
            videos, seconds, beans = int(lrow[0]), float(lrow[1] or 0), int(lrow[2] or 0)
            points = int(lrow[3] or 0)
        else:
            row = s.exec(
                select(func.count(AvatarVideo.id), func.coalesce(func.sum(AvatarVideo.seconds), 0),
                       func.coalesce(func.sum(AvatarVideo.beans_used), 0),
                       func.coalesce(func.sum(AvatarVideo.points_used), 0))
                .where(AvatarVideo.status == "done")  # type: ignore[attr-defined]
                .where(AvatarVideo.seconds > 0)  # type: ignore[attr-defined]
            ).one()
            videos, seconds, beans = int(row[0]), float(row[1] or 0), int(row[2] or 0)
            points = int(row[3] or 0)
    # 积分（租户口径）对所有人可见；豆/¥ 是平台成本口径
    out = {"videos": videos, "seconds": round(seconds, 1), "points": points}
    if user.role == "platform_admin":
        out["beans"] = beans
        out["yuan"] = round(beans * 0.03, 2)
    return out


@router.get("/estimate")
async def estimate(script_id: int, avatar_id: int = 0, model: int = 0,
                   user: User = Depends(require_user)) -> dict:
    """生成前成本闸：按实测语速估时长/豆，并核对余额（元仅平台管理员可见）。"""
    ok, why = chanjing.configured()
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    from ..models import Script  # 局部导入避免循环
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None or not (script.final_text or "").strip():
            raise HTTPException(status_code=400, detail="脚本没有定稿文本，先在脚本工场定稿")
        # 语速取自配置（avatar_id 缺省时取列表首条，与前端默认选中一致）：说快 1.5 倍 → 秒与豆按 1/1.5 折算
        cfg = (s.get(AvatarConfig, avatar_id) if avatar_id
               else s.exec(select(AvatarConfig).order_by(AvatarConfig.id.desc())).first())
        speed = chanjing.clamp_speed(cfg.speed_ratio) if cfg else 1.0
        est = chanjing.estimate(script.final_text, model=model, speed=speed)
        est["speed"] = speed
        # 租户口径：本次扣多少积分（按秒）+ 自己积分池余额；豆/¥ 是平台成本口径
        est["points"] = avatar_video_points(est["seconds"], model)
        est["credits"] = get_balance(user.tenant_id)
        # 闸门口径 = 租户积分（提交时 require_points 卡的就是它）；豆余额只作平台成本展示
        est["balance"] = await chanjing.get_balance()
        est["insufficient"] = est["credits"] < est["points"]
        if user.role != "platform_admin":
            # 租户侧只留积分口径（points/credits）；豆、¥、蝉镜余额都是平台成本口径
            est.pop("yuan", None)
            est.pop("beans", None)
            est.pop("balance", None)
        return est


@router.post("/generate", status_code=202)
async def generate(body: dict,
                  user: User = Depends(require_user)) -> dict:
    script_id = body.get("script_id")
    avatar_id = body.get("avatar_id")
    if not script_id or not avatar_id:
        raise HTTPException(status_code=400, detail="script_id 与 avatar_id 必填")
    model = int(body.get("model") or 0)
    with Session(engine) as s:
        from ..models import Script
        sc = s.get(Script, int(script_id))
        if sc is None:
            raise HTTPException(status_code=404, detail="script not found")
        if not (sc.final_text or "").strip():
            raise HTTPException(status_code=400, detail="该脚本没有定稿文本，先在脚本工场定稿")
        cfg = s.get(AvatarConfig, int(avatar_id))
        speed = chanjing.clamp_speed(cfg.speed_ratio) if cfg else 1.0
    # 按秒预扣：用预估秒数先扣（成片后按实际秒数多退少补，见 avatar.service 结算）
    est_seconds = chanjing.estimate(sc.final_text, model=model, speed=speed)["seconds"]
    points = avatar_video_points(est_seconds, model)
    require_points(user.tenant_id, points, "数字人视频")
    with Session(engine) as s:
        row = AvatarVideo(script_id=int(script_id), avatar_id=int(avatar_id), status="generating",
                          model=model, points_used=points)
        s.add(row)
        s.commit()
        s.refresh(row)
        row_id = row.id
    job = await runner.submit("avatar_video",
                              {"video_row_id": row_id, "model": model, "points": points,
                               "bigtext": bool(body.get("bigtext", True))},
                              dedup_key=f"avatar:{script_id}",
                              points=points)
    return {"avatar_video_id": row_id, "job_id": job.id}


@router.post("/configs/{config_id}/clone_video")
async def clone_video(config_id: int, file: UploadFile, name: str = "",
                      user: User = Depends(require_user)) -> dict:
    """上传出镜视频 → 蝉镜 API 克隆新形象并绑定到配置（约 10 分钟内训练完成）。

    克隆预扣 300 积分（平台实际成本约 240 分=81 豆，训练失败不退）；
    扣减在形象提交成功后执行，提交失败不扣。"""
    require_points(user.tenant_id, CLONE_POINTS, "数字人克隆")
    ok, why = chanjing.configured()
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    with Session(engine) as s:
        c = s.get(AvatarConfig, config_id)
        if c is None:
            raise HTTPException(status_code=404, detail="config not found")
        config_name = c.name
    # 流式落盘 + 体积上限：直接边读边写临时文件（原实现 chunks 累积到内存再 join——
    # 500MB 素材 = 请求期间 500MB+ RAM，几个并发就把 3.6G 服务器打爆）
    max_bytes = 500 * 1024 * 1024  # 克隆素材 500MB 上限（出镜视频几十 MB 足够）
    src = Path(_DIR) / f"clone_src_{config_id}_{uuid.uuid4().hex[:6]}{Path(file.filename or 'v.mp4').suffix or '.mp4'}"
    src.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with src.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(status_code=413, detail="视频超过 500MB 上限，请压缩后再传")
                f.write(chunk)
    except HTTPException:
        src.unlink(missing_ok=True)
        raise
    if total == 0:
        src.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="视频文件为空")
    file_id = await chanjing.upload_video(src.name, src.read_bytes())
    person_name = (name or f"{config_name}-克隆").strip()
    person_id = await chanjing.create_person(person_name, file_id)
    from ..credits import deduct
    deduct(user.tenant_id, CLONE_POINTS, reason="数字人克隆预扣", ref=person_id)
    with Session(engine) as s:
        c = s.get(AvatarConfig, config_id)
        c.chanjing_person_id = person_id
        c.chanjing_audio_man = ""  # 新形象的配套音色训练完成后自动生效（详情接口带回）
        s.add(c)
        s.commit()
    return {"person_id": person_id, "name": person_name, "file_id": file_id}


@router.get("/persons/{person_id}")
async def person(person_id: str) -> dict:
    """克隆形象训练状态/详情代理（status 2=就绪）。"""
    ok, why = chanjing.configured()
    if not ok:
        raise HTTPException(status_code=400, detail=why)
    return await chanjing.person_detail(person_id)


@router.delete("/videos/{video_id}")
def delete_video(video_id: int) -> dict:
    """删除生成记录：连带清磁盘分段/成片（生成中不可删）。"""
    with Session(engine) as s:
        v = s.get(AvatarVideo, video_id)
        if v is None:
            raise HTTPException(status_code=404, detail="video not found")
        if v.status == "generating":
            raise HTTPException(status_code=400, detail="生成中的记录不可删除，请等任务结束")
        video_path = v.video_path
        s.delete(v)
        s.commit()
    import shutil
    if video_path:
        run_dir = Path(video_path).parent
        shutil.rmtree(run_dir, ignore_errors=True)  # 连检查点分段一起清
    return {"deleted": video_id}


@router.get("/videos/{video_id}/file")
def video_file(video_id: int):
    with Session(engine) as s:
        v = s.get(AvatarVideo, video_id)
        if v is None or not v.video_path or not Path(v.video_path).exists():
            raise HTTPException(status_code=404, detail="video not found")
        return FileResponse(v.video_path, media_type="video/mp4")


@router.get("/videos")
def list_videos(status: str = "", limit: int = 50, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(AvatarVideo).order_by(AvatarVideo.id.desc())  # type: ignore[attr-defined]
        if status:
            q = q.where(AvatarVideo.status == status)
        total = len(s.exec(q).all())
        rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()  # type: ignore[attr-defined]
        from sqlmodel import func
        counts = {st: n for st, n in s.exec(
            select(AvatarVideo.status, func.count(AvatarVideo.id)).group_by(AvatarVideo.status)  # type: ignore[arg-type]
        ).all()}
        # 富化：定稿标题（标题候选>选题标题）+ 脚本预览 + 三件套统计
        from ..models import Script, Topic
        sids = {r.script_id for r in rows}
        script_map = {x.id: x for x in s.exec(select(Script).where(Script.id.in_(sids))).all()} if sids else {}
        tids = {x.topic_id for x in script_map.values() if x.topic_id}
        topic_map = {x.id: x for x in s.exec(select(Topic).where(Topic.id.in_(tids))).all()} if tids else {}
        items = []
        for r in rows:
            d = r.model_dump()
            sc = script_map.get(r.script_id)
            title = ""
            preview = ""
            tele_len = story_count = title_count = 0
            if sc is not None:
                tc = sc.title_candidates or []
                if tc:
                    first = tc[0]
                    title = first.get("title", "") if isinstance(first, dict) else str(first)
                if not title and sc.topic_id and sc.topic_id in topic_map:
                    title = topic_map[sc.topic_id].title
                preview = (sc.final_text or "")[:120]
                tele_len = len(sc.teleprompter_text or "")
                story_count = len(sc.storyboard or [])
                title_count = len(sc.title_candidates or [])
            d["title"] = title or f"脚本 #{r.script_id}"
            d["script_preview"] = preview
            d["tele_len"] = tele_len
            d["story_count"] = story_count
            d["title_count"] = title_count
            items.append(d)
        return {"items": items, "total": total, "counts": counts}
