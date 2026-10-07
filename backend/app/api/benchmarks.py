"""同行拆解 API。"""
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..credits import job_points, require_points
from ..db import engine
from ..jobs.runner import runner
from ..models import BenchmarkVideo, ScriptTemplate, Topic, User
from ..settings import settings

router = APIRouter()

_MAX_UPLOAD = 2 * 1024 * 1024 * 1024  # 2GB


class AnalyzeIn(BaseModel):
    url: str


@router.post("/api/analyze/video", status_code=202)
async def analyze_video(body: AnalyzeIn,
                        user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("benchmark_analyze"), "同行拆解")
    from ..benchmarks.downloader import extract_douyin_url, is_douyin_url
    url = extract_douyin_url(body.url.strip())
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    if not is_douyin_url(url):  # SSRF 闸门：服务端会主动下载该链接
        raise HTTPException(status_code=400, detail="仅支持抖音视频链接（v.douyin.com / douyin.com）")
    with Session(engine) as s:
        b = s.exec(select(BenchmarkVideo).where(BenchmarkVideo.url == url)).first()
        if b is None:  # 实体去重：同一链接重复贴不建重复行
            b = BenchmarkVideo(url=url, source="douyin")
            s.add(b)
            s.commit()
            s.refresh(b)
        benchmark_id = b.id
    job = await runner.submit("benchmark_analyze", {"benchmark_id": benchmark_id},
                              dedup_key=f"bm:{benchmark_id}",
                              points=job_points("benchmark_analyze"))
    out = {"benchmark_id": benchmark_id, "job_id": job.id}
    # 采集预警：最近一次探针异常且未过久 → 提交时就告诉用户可能失败（省一轮空等）
    try:
        from ..crawler_probe import read_state

        last = (read_state().get("last") or {})
        if last.get("status") not in (None, "ok"):
            out["crawl_warning"] = f"抖音采集当前异常（{str(last.get('detail', ''))[:40]}，探测于 {last.get('at', '')}），任务可能失败"
    except Exception:  # noqa: BLE001 预警失败不影响提交
        pass
    return out


@router.post("/api/analyze/local", status_code=202)
async def analyze_local(file: UploadFile,
                        user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("benchmark_analyze"), "同行拆解")
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件")
    suffix = Path(file.filename).suffix or ".mp4"
    out_dir = settings.data_dir / "benchmarks"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"local_{int(time.time() * 1000)}{suffix}"
    size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_UPLOAD:
                f.close()
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="文件超过 2GB 上限")
            f.write(chunk)
    with Session(engine) as s:
        b = BenchmarkVideo(url="", source="local", media_path=str(path),
                           title=Path(file.filename).stem)
        s.add(b)
        s.commit()
        s.refresh(b)
        benchmark_id = b.id
    job = await runner.submit("benchmark_analyze", {"benchmark_id": benchmark_id},
                              dedup_key=f"bm:{benchmark_id}",
                              points=job_points("benchmark_analyze"))
    out = {"benchmark_id": benchmark_id, "job_id": job.id}
    # 采集预警：最近一次探针异常且未过久 → 提交时就告诉用户可能失败（省一轮空等）
    try:
        from ..crawler_probe import read_state

        last = (read_state().get("last") or {})
        if last.get("status") not in (None, "ok"):
            out["crawl_warning"] = f"抖音采集当前异常（{str(last.get('detail', ''))[:40]}，探测于 {last.get('at', '')}），任务可能失败"
    except Exception:  # noqa: BLE001 预警失败不影响提交
        pass
    return out


@router.get("/api/benchmarks/authors")
def benchmark_authors(user: User = Depends(require_user)) -> dict:
    """拆解库作者清单（全库 distinct，供筛选下拉——此前取自当前页 20 条，新作者选不到）。"""
    with Session(engine) as s:
        authors = sorted({a for a in s.exec(
            select(BenchmarkVideo.author).distinct()  # type: ignore[attr-defined]
        ).all() if a})
    return {"authors": authors}


@router.get("/api/benchmarks")
def list_benchmarks(limit: int = 20, offset: int = 0, author: str = "",
                    analyzed: str = "", source: str = "", scan_status: str = "",
                    user: User = Depends(require_user)) -> dict:
    """分页 + 服务端筛选（analyzed: done|todo；source: douyin|local；
    scan_status: pending|approved|ignored；默认返回 approved+done 的已定夺内容）。

    counts 为全量口径（不受分页影响），供前端 chips 显示。
    """

    def _analyzed(b: BenchmarkVideo) -> bool:
        a = b.analysis or {}
        return bool(a.get("hook") or a.get("structure") or a.get("score") is not None)

    with Session(engine) as s:
        all_rows = s.exec(select(BenchmarkVideo).order_by(
            BenchmarkVideo.id.desc())).all()  # type: ignore[attr-defined]
        # counts 必须全库口径（chips 数字不随作者/来源筛选漂移——此前在过滤后统计）
        counts = {
            "all": len(all_rows),
            "pending": sum(1 for b in all_rows if b.scan_status == "pending"),
            "todo": sum(1 for b in all_rows if b.scan_status == "approved" and not _analyzed(b)),
            "done": sum(1 for b in all_rows if _analyzed(b)),
            "ignored": sum(1 for b in all_rows if b.scan_status == "ignored"),
            "online": sum(1 for b in all_rows if b.source != "local" and b.scan_status != "pending"),
            "local": sum(1 for b in all_rows if b.source == "local"),
        }
        q = select(BenchmarkVideo)
        if author:
            q = q.where(BenchmarkVideo.author == author)
        if source:
            q = q.where(BenchmarkVideo.source == source)
        rows = s.exec(q.order_by(BenchmarkVideo.id.desc())).all()  # type: ignore[attr-defined]
        if scan_status == "all":
            pass  # 显式看全部状态（Watch 展开卡：该号最近作品含待定夺）
        elif scan_status in ("pending", "approved", "ignored"):
            rows = [b for b in rows if b.scan_status == scan_status]
        elif not scan_status:
            # 默认视图 = 已定夺内容（批准中 + 已拆解），待定夺与已忽略单独看
            rows = [b for b in rows if b.scan_status == "approved"]
        if analyzed == "done":
            rows = [b for b in rows if _analyzed(b)]
        elif analyzed == "todo":
            rows = [b for b in rows if not _analyzed(b)]
        total = len(rows)
        page = rows[max(0, offset): max(0, offset) + max(1, min(limit, 100))]
        # 爆款判定（R3.4b 数据归因的规则面）：作者近 videos 的点赞中位数 ×3 且样本 ≥3 条
        import statistics as _st
        by_author: dict[str, list[int]] = {}
        for b in rows:
            d = (b.stats or {}).get("digg")
            if isinstance(d, int) and b.author:
                by_author.setdefault(b.author, []).append(d)
        median_map = {a: (_st.median(v) if len(v) >= 3 else 0) for a, v in by_author.items()}
        items = []
        for r in page:
            d = r.model_dump()
            digg = (r.stats or {}).get("digg")
            med = median_map.get(r.author or "", 0)
            d["hot"] = bool(isinstance(digg, int) and med and digg >= 3 * med)
            d["digg"] = digg
            items.append(d)
        return {"items": items, "total": total, "counts": counts}


class ResolveIn(BaseModel):
    action: str  # approve | ignore
    author: str = ""  # 可选：只处理该作者的待定夺行
    ignore_already_analyzed: bool = True  # 批量忽略时跳过已拆解行


@router.post("/api/benchmarks/{benchmark_id}/resolve", status_code=202)
async def resolve_benchmark(benchmark_id: int, body: ResolveIn,
                            user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("benchmark_analyze"), "同行拆解")
    """人工定夺：批准（排队拆解）或忽略。"""
    if body.action not in ("approve", "ignore", "pending"):
        raise HTTPException(status_code=400, detail="action 必须是 approve / ignore / pending")
    with Session(engine) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        if b is None:
            raise HTTPException(status_code=404, detail="benchmark not found")
        b.scan_status = {"approve": "approved", "ignore": "ignored", "pending": "pending"}[body.action]
        s.add(b)
        s.commit()
    if body.action == "approve":
        job = await runner.submit("benchmark_analyze", {"benchmark_id": benchmark_id},
                                  dedup_key=f"bm:{benchmark_id}")
        return {"status": "approved", "job_id": job.id}
    return {"status": "ignored"}


@router.post("/api/benchmarks/resolve-pending")
async def resolve_pending(body: ResolveIn,
                          user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("benchmark_analyze"), "同行拆解")
    """批量定夺当前所有待定夺行（可按作者过滤）。"""
    if body.action not in ("approve", "ignore"):
        raise HTTPException(status_code=400, detail="action 必须是 approve / ignore")
    with Session(engine) as s:
        q = select(BenchmarkVideo).where(  # type: ignore[attr-defined]
            BenchmarkVideo.scan_status == "pending")  # type: ignore[attr-defined]
        if body.author:
            q = q.where(BenchmarkVideo.author == body.author)  # type: ignore[attr-defined]
        rows = s.exec(q).all()
        resolved = 0
        job_ids = []
        for b in rows:
            b.scan_status = "approved" if body.action == "approve" else "ignored"
            s.add(b)
            resolved += 1
        s.commit()
        if body.action == "approve":
            for b in rows:
                job = await runner.submit("benchmark_analyze", {"benchmark_id": b.id},
                                          dedup_key=f"bm:{b.id}",
                                          points=job_points("benchmark_analyze"))
                job_ids.append(job.id)
    return {"resolved": resolved, "job_ids": job_ids}


@router.get("/api/benchmarks/{benchmark_id}")
def get_benchmark(benchmark_id: int) -> dict:
    with Session(engine) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        if b is None:
            raise HTTPException(status_code=404, detail="benchmark not found")
        return b.model_dump()


@router.delete("/api/benchmarks/{benchmark_id}")
def delete_benchmark(benchmark_id: int) -> dict:
    """删除拆解样本：连带删除其产出的待审选题候选，解绑结构模板引用，删除已下载的媒体文件。"""
    import os
    from ..db import engine as _eng
    from sqlmodel import Session as _S, select as _sel
    with _S(_eng) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        if b is None:
            from fastapi import HTTPException
            raise HTTPException(404, detail="benchmark not found")
        # 连带删除本样本产出的待审选题候选（已定审/已产出的选题保留）
        cands = s.exec(_sel(Topic).where(
            Topic.source_type == "benchmark",
            Topic.source_ref == f"benchmark:{benchmark_id}",
            Topic.status == "draft")).all()
        for t in cands:
            s.delete(t)
        # 解绑结构模板
        for tpl in s.exec(_sel(ScriptTemplate).where(
                ScriptTemplate.source_benchmark_id == benchmark_id)).all():
            tpl.source_benchmark_id = None
            s.add(tpl)
        media_path = b.media_path
        s.delete(b)
        s.commit()
    if media_path and os.path.exists(media_path):
        try:
            os.remove(media_path)
        except OSError:
            pass
    return {"ok": True, "removed_candidates": len(cands)}
