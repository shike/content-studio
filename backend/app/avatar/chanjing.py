"""蝉镜开放 API 适配器（数字人主线引擎，2026-09-24 实测定版）。

流程：定稿脚本文本 → create_video(audio.type=tts 配套音色) → 轮询 → 下载。
超长脚本自动分段（每段独立出片）→ 本地 ffmpeg 拼接。

实测踩坑（改代码前先读，别回退）：
- 鉴权：POST /access_token，真实参数名是 app_id/secret_key（文档"scecretKey"是笔误）。
- 音频必须走 audio.type=tts（配套音色）或 wav_url 直链；file_id 通道会静默丢音轨
  （字幕 ASR 正常但成片无声，audio_urls=null）。
- audio.volume 必传，缺了报"音量不能小于1"。
- 公共形象必带 figure_type + 显式宽高；API 克隆形象走 source=0。
- 主站（Web）克隆的形象在 API 侧不可达（详情返回空壳），别再用 source=1。
- 免费版就有 API 权限；计费 1 豆/秒（基础版 model=0）。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import httpx

from ..settings import settings

_BASE = "https://open-api.chanjing.cc/open/v1"

# 创始租户的克隆形象与配套音色（2026-09-24）。2026-09-30 SaaS 产品化后**不再作为
# 出片回落**——租户无形象/音色时明确报错，防止用创始人脸出商用片。仅存档备查。
DEFAULT_PERSON_ID = "C-1ae4da71e4be49c592da0c18452a4f7e"
DEFAULT_AUDIO_MAN = "C-1116d2b82bf24f009de4ad563a64d379"

SCREEN_W, SCREEN_H = 1080, 1920  # 兜底尺寸（拿不到形象原生尺寸时用）
MODEL = 0  # 0=基础版 lip-sync（1 豆/秒）、1=高质版（2 豆/秒）
VOLUME = 100
# 字幕/短标参数按 1080 宽标定，实际渲染时按形象原生宽度等比缩放（见 _scaled_subtitle）
SUBTITLE = {  # 白字、底部居中（约 83% 高度）、全宽——v2 样片验收通过的参数
    "show": True, "asr_type": 0, "color": "#FFFFFF",
    "font_size": 56, "x": 0, "y": 1600, "width": 1080, "height": 240,
}


def _scaled_subtitle(w: int) -> dict:
    """字幕参数按实际画幅宽度等比缩放（1080 为标定基准）。

    2026-09-28 修：形象原生 720×1280 曾被我们硬拉到 1080×1920 渲染（上采样 → 画面变软），
    改为按原生尺寸渲染后，字幕/短标坐标必须同步缩放，否则字幕会跑到画面外。
    """
    k = w / 1080.0
    sub = dict(SUBTITLE)
    sub["font_size"] = max(12, int(SUBTITLE["font_size"] * k))
    sub["y"] = int(SUBTITLE["y"] * k)
    sub["width"] = w
    sub["height"] = max(60, int(SUBTITLE["height"] * k))
    return sub

# 蝉镜配套音色实测：88 字（含标点）≈18.8s ≈ 4.7 字/秒
CHARS_PER_SEC = 4.7

SPEED_MIN, SPEED_MAX = 0.5, 2.0


def clamp_speed(speed: float | None) -> float:
    """语速倍数夹取：蝉镜接口只声明 number、未标范围，超出 0.5~2.0 一律夹住防呆。"""
    try:
        v = float(speed if speed is not None else 1.0)
    except (TypeError, ValueError):
        return 1.0
    return min(SPEED_MAX, max(SPEED_MIN, round(v, 2)))
# 1000 豆 ≈ ¥30（App 内购档位折算），仅用于展示预估
YUAN_PER_BEAN = 0.03
SEG_MAX_CHARS = 1200  # 超过则分段出片再拼（600~900 字口播保持单段）

_token_cache: dict = {"key": None, "token": "", "at": 0.0}


def configured() -> tuple[bool, str]:
    if not (settings.chanjing_app_id and settings.chanjing_secret_key):
        return False, "数字人服务未配置（需管理员在平台侧配置凭证）"
    return True, ""


def _creds() -> tuple[str, str]:
    return settings.chanjing_app_id or "", settings.chanjing_secret_key or ""


async def _token(force: bool = False) -> str:
    key = _creds()
    if not force and _token_cache["key"] == key and _token_cache["token"] \
            and time.time() - _token_cache["at"] < 1800:
        return _token_cache["token"]
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        r = await c.post(f"{_BASE}/access_token",
                         json={"app_id": key[0], "secret_key": key[1]})
        r.raise_for_status()
        body = r.json()
    if body.get("code") != 0:
        raise RuntimeError(f"数字人服务鉴权失败：{body.get('msg')} (code={body.get('code')})")
    _token_cache.update(key=key, token=body["data"]["access_token"], at=time.time())
    return _token_cache["token"]


async def _call(method: str, path: str, *, params: dict | None = None,
                json_body: dict | None = None) -> dict:
    """带 10400（token 失效）自动重签一次的统一请求。"""
    for force in (False, True):
        token = await _token(force=force)
        async with httpx.AsyncClient(timeout=60, trust_env=False) as c:
            r = await c.request(method, f"{_BASE}{path}", params=params,
                                json=json_body, headers={"access_token": token})
        r.raise_for_status()
        body = r.json()
        if body.get("code") == 10400:
            continue  # token 失效，强刷重试
        break
    if body.get("code") != 0:
        raise RuntimeError(f"数字人服务调用失败：{body.get('msg')} (code={body.get('code')})")
    return body.get("data") or {}


async def get_balance() -> int:
    d = await _call("GET", "/user_duration")
    return int(d.get("resi_total_bean") or 0)


def estimate(text: str, model: int = MODEL, speed: float = 1.0) -> dict:
    """按实测语速估算时长与豆消耗（纯计算，不调 API）。

    speed = 生成时传给 TTS 的语速倍数：说快 1.5 倍 → 时长与豆按 1/1.5 折算。
    """
    chars = len((text or "").strip())
    speed = clamp_speed(speed)
    seconds = round(chars / (CHARS_PER_SEC * speed), 1)
    beans = max(1, int(seconds + 0.999)) * (2 if model == 1 else 1)
    return {"chars": chars, "seconds": seconds, "beans": beans,
            "yuan": round(beans * YUAN_PER_BEAN, 2)}


def split_segments(text: str, max_chars: int = SEG_MAX_CHARS) -> list[str]:
    """按句切分聚合到 ≤max_chars/段（长脚本自动分段出片再拼）；无标点超长句硬切。"""
    import re
    pieces = [p for p in re.split(r"(?<=[。！？；!?;\n])", text.strip()) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in pieces:
        while len(p) > max_chars:  # 单句超限（无标点长文）硬切
            if buf:
                out.append(buf)
                buf = ""
            out.append(p[:max_chars])
            p = p[max_chars:]
        if buf and len(buf) + len(p) > max_chars:
            out.append(buf)
            buf = p
        else:
            buf += p
    if buf:
        out.append(buf)
    return out or [text.strip()]


async def create_video_tts(person_id: str, audio_man: str, text: str,
                           model: int = MODEL, speed: float = 1.0,
                           size: tuple[int, int] | None = None) -> str:
    """配套音色原生 TTS 合成（文本直进，平台配音+对口型+字幕）。

    speed = 口播语速倍数；size = 渲染画幅（缺省按形象原生尺寸，避免上采样）。
    """
    w, h = size or (SCREEN_W, SCREEN_H)
    d = await _call("POST", "/create_video", json_body={
        "person": {"id": person_id, "width": w, "height": h, "x": 0, "y": 0},
        "source": 0,
        "audio": {"type": "tts", "volume": VOLUME,
                  "tts": {"text": [text], "audio_man": audio_man, "speed": clamp_speed(speed)}},
        "model": model,
        "resolution_rate": 0,
        "screen_width": w, "screen_height": h,
        "subtitle_config": _scaled_subtitle(w),
    })
    return str(d)


async def person_size(person_id: str) -> tuple[int, int]:
    """形象原生渲染尺寸（person_detail 的 width/height）——按它渲染避免上采样变软。"""
    try:
        d = await person_detail(person_id)
        w, h = int(d.get("width") or 0), int(d.get("height") or 0)
        if w >= 240 and h >= 240:
            return w, h
    except Exception as e:  # noqa: BLE001 拿不到就按兜底尺寸渲染，不拦出片
        print(f"[avatar] 形象尺寸探测失败 {person_id}: {type(e).__name__}: {str(e)[:80]}")
    return SCREEN_W, SCREEN_H


async def get_video(video_id: str) -> dict:
    return await _call("GET", "/video", params={"id": video_id})


async def wait_video(video_id: str, on_progress=None, timeout: float = 900,
                     poll: float = 8.0) -> dict:
    """轮询到终态：status 30=成功；4X/5X=失败（抛错）。"""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        d = await get_video(video_id)
        status, prog = d.get("status"), d.get("progress")
        line = f"status={status} progress={prog}"
        if line != last:
            last = line
            if on_progress:
                on_progress(prog or 0, d.get("msg") or "")
        if status == 30:
            return d
        if isinstance(status, int) and (40 <= status < 60):
            raise RuntimeError(f"数字人合成失败：{d.get('msg') or f'status={status}'}")
        await asyncio.sleep(poll)
    raise RuntimeError(f"数字人合成超时（{timeout:.0f}s）：video_id={video_id}")


async def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300, trust_env=False, follow_redirects=True) as c:
        async with c.stream("GET", url) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes(65536):
                    f.write(chunk)
    return dest


async def render_long(person_id: str, audio_man: str, text: str, run_dir: Path,
                      on_stage=None, model: int = MODEL, speed: float = 1.0,
                      size: tuple[int, int] | None = None) -> tuple[Path, dict]:
    """完整渲染：分段（超长才分）→ 逐段出片 → 拼接。on_stage(stage 0~100, msg)。

    检查点续跑（P4）：run_dir/state.json 记录每段的蝉镜任务 id 与产物文件。
    任务重试时——已完成段直接复用文件；已提交未收货段只轮询该任务、
    不重复提交（蝉豆不重复扣）；都没有才新提交。
    """
    import json

    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    segments = split_segments(text)
    state: dict = {"segments": [{"vid": "", "file": ""} for _ in segments]}

    def _save_state() -> None:
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False))
        tmp.replace(state_path)

    if state_path.exists():
        try:
            saved = json.loads(state_path.read_text())
            if len(saved.get("segments") or []) == len(segments):
                state = saved  # 切分一致才可复用（文本变了切分数会变）
        except Exception as e:  # noqa: BLE001 坏状态文件当无检查点（会重渲染重扣豆，必须留痕）
            print(f"[chanjing] 检查点文件损坏，将重新渲染: {type(e).__name__}: {str(e)[:100]}")

    info = {"segments": len(segments), "video_ids": [], "resumed": 0}
    files: list[Path] = []
    n = len(segments)
    for i, seg in enumerate(segments):
        seg_state = state["segments"][i]
        out = run_dir / f"seg_{i:02d}.mp4"
        base = 10 + 80 * i // n
        span = 80 / n
        if seg_state.get("file") and out.exists():
            info["resumed"] += 1
            files.append(out)
            if on_stage:
                on_stage(base + span, f"第 {i + 1}/{n} 段命中检查点，跳过")
            continue

        def _cb(prog: int, msg: str) -> None:
            if on_stage:
                on_stage(base + span * min(100, prog) / 100,
                         f"数字人合成中 第 {i + 1}/{n} 段（{prog}%）")

        if seg_state.get("vid"):
            # 已提交未收货：只轮询，不重复提交（不重复扣豆）
            info["resumed"] += 1
            if on_stage:
                on_stage(base + 5, f"第 {i + 1}/{n} 段复用已提交任务")
            d = await wait_video(seg_state["vid"], on_progress=_cb,
                                 timeout=600 + 120 * len(seg) // 300)
        else:
            if on_stage:
                on_stage(base + 2, f"提交合成 第 {i + 1}/{n} 段")
            vid = await create_video_tts(person_id, audio_man, seg, model=model, speed=speed, size=size)
            seg_state["vid"] = vid
            _save_state()  # 提交即落检查点：之后任何中断都只轮询不重扣
            d = await wait_video(vid, on_progress=_cb,
                                 timeout=600 + 120 * len(seg) // 300)
        info["video_ids"].append(seg_state.get("vid") or "")
        if on_stage:
            on_stage(base + span, f"第 {i + 1}/{n} 段完成，下载中")
        await download(d["video_url"], out)
        seg_state["file"] = out.name
        _save_state()
        files.append(out)

    if len(files) == 1:
        final = files[0]
    else:
        if on_stage:
            on_stage(92, f"拼接 {len(files)} 段")
        import subprocess
        final = run_dir / "digital_human.mp4"
        lf = run_dir / "segs.txt"
        lf.write_text("".join(f"file '{p.resolve()}'\n" for p in files))
        proc = await asyncio.to_thread(subprocess.run, [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lf),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(final)],
            capture_output=True, timeout=1800)  # 硬超时：拼接挂死不能占死任务
        if proc.returncode != 0:
            tail = (proc.stderr or b"")[-300:].decode("utf-8", "ignore").strip()
            raise RuntimeError("分段拼接失败（ffmpeg）：" + tail.splitlines()[-1])
    info["files"] = [str(p) for p in files]  # 有序分段文件：大字对齐各段字幕时间轴要用
    return final, info


# ---- 克隆管理（API 命名空间，主站克隆的形象 API 不可达）----

async def upload_video(name: str, data: bytes, wait_review: bool = True) -> str:
    """上传素材（service=customised_person），过审后返回 file_id。"""
    d = await _call("GET", "/common/create_upload_url",
                    params={"service": "customised_person", "name": name})
    sign_url, file_id = d["sign_url"], d["file_id"]
    async with httpx.AsyncClient(timeout=300, trust_env=False) as c:
        r = await c.put(sign_url, content=data,
                        headers={"Content-Type": "application/octet-stream"})
        r.raise_for_status()
    if wait_review:
        for _ in range(30):
            det = await _call("GET", "/common/file_detail", params={"id": file_id})
            if det.get("status") == 1:
                break
            await asyncio.sleep(6)
        else:
            raise RuntimeError("素材上传后审核超时（5 分钟）")
    return file_id


async def create_person(name: str, file_id: str, high_quality: bool = False) -> str:
    body: dict = {"name": name, "file_id": file_id, "resolution_rate": 0}
    if high_quality:
        body["is_high_quality"] = True
    d = await _call("POST", "/create_customised_person", json_body=body)
    return str(d)


async def person_detail(person_id: str) -> dict:
    return await _call("GET", "/customised_person", params={"id": person_id})


# ===== 左上角栏目角标（用户 2026-09-28 参照罗振宇《视频日记》定版）=====
# 形式：两行栏目名（租户品牌配置，如 跃迁／专栏）+ 暖黄细括号 + 右侧日期与期号 Day N，全程常显。
# 平台 create_video 没有文字层 → 本地叠一张透明 PNG，不重跑数字人、不再花豆。
LABEL_LINES = ("跃迁", "内容")        # 栏目名兜底（实际走租户 brand 配置；字体按租户动态子集化）
LABEL_ACCENT = (242, 193, 78)        # 暖黄（选定 B 方案；罗振宇同色系）
LABEL_BASE = 1080                    # 设计基准画幅宽：字号/坐标按它标定，实际按画幅等比缩放
LABEL_FONT = Path(__file__).resolve().parent.parent / "assets" / "LabelBold.otf"


def _label_font(size: int):
    """角标字体：优先内置 Bold 子集（Noto Sans SC Bold 子集，OFL 免费商用）；缺失回退系统中文字体。"""
    from PIL import ImageFont

    try:
        return ImageFont.truetype(str(LABEL_FONT), size)
    except Exception as e:  # noqa: BLE001
        print(f"[label] 内置角标字体不可用（{type(e).__name__}），回退系统字体")
        p = _label_font_path()
        return ImageFont.truetype(p, size) if p else None


def _label_font_path() -> str | None:
    from ..articles.charts import _FONT_CANDIDATES

    for cand in _FONT_CANDIDATES:
        if os.path.exists(cand):
            return cand
    return None


def render_label_png(path: Path, frame: tuple[int, int] | None = None,
                     day: int = 0, date_str: str = "",
                     lines: tuple[str, ...] | None = None,
                     font_path: Path | None = None,
                     accent: tuple[int, int, int] | None = None) -> bool:
    """栏目角标 → 整帧透明 PNG。frame = 成片实际画幅；lines/font_path = 租户品牌（缺省平台默认）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return False
    fw, fh = frame or (SCREEN_W, SCREEN_H)
    k = fw / LABEL_BASE
    size = max(18, int(64 * k))
    if font_path is not None:
        try:
            big = ImageFont.truetype(str(font_path), size)
        except Exception as e:  # noqa: BLE001
            print(f"[label] 租户字体不可用（{type(e).__name__}），回退内置字体")
            big = _label_font(size)
    else:
        big = _label_font(size)
    if big is None:
        print("[label] 找不到可用中文字体，跳过角标")
        return False
    small = ImageFont.truetype(str(font_path), max(12, int(size * 0.36))) if font_path else _label_font(max(12, int(size * 0.36)))
    epic = ImageFont.truetype(str(font_path), max(16, int(size * 0.92))) if font_path else _label_font(max(16, int(size * 0.92)))
    img = Image.new("RGBA", (fw, fh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad_x, pad_y = int(26 * k), int(18 * k)
    x, y = int(44 * k), int(62 * k)
    lh = int(size * 1.2)
    label_lines = tuple(lines or LABEL_LINES)
    widest = max(d.textlength(t, font=big) for t in label_lines)
    bw = int(widest + pad_x * 2)
    bh = int(lh * len(label_lines) + pad_y * 2)
    lw = max(2, int(size * 0.07))
    acc = tuple(accent or LABEL_ACCENT) + (255,)
    d.line([(x, y), (x, y + bh)], fill=acc, width=lw)                       # 左竖线
    d.line([(x, y), (x + bw, y)], fill=acc, width=lw)                       # 上臂
    d.line([(x, y + bh), (x + bw, y + bh)], fill=acc, width=lw)             # 下臂
    d.line([(x + bw, y), (x + bw, y + int(bh * 0.24))], fill=acc, width=lw)        # 右上短齿
    d.line([(x + bw, y + bh - int(bh * 0.24)), (x + bw, y + bh)], fill=acc, width=lw)  # 右下短齿
    ty = y + pad_y
    for t in label_lines:
        d.text((x + pad_x, ty), t, font=big, fill=(255, 255, 255, 255))
        ty += lh
    rx = x + bw + int(size * 0.7)
    if date_str:
        d.text((rx, y + int(size * 0.2)), date_str, font=small, fill=(255, 255, 255, 235))
    if day:
        d.text((rx, y + int(size * 0.68)), f"Day {day}", font=epic, fill=(255, 255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return True


def _probe_size(path: Path) -> tuple[int, int] | None:
    """成片实际画幅（角标按它渲染，否则 720 画幅下会贴错位置/过大）。"""
    import subprocess

    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-show_entries", "stream=width,height",
                               "-of", "csv=s=x:p=0", str(path)], capture_output=True, timeout=30)
        w, h = (proc.stdout or b"").decode().strip().split("x")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return None


def _probe_total(path: Path) -> float:
    import subprocess

    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                              capture_output=True, timeout=30)
        return float((proc.stdout or b"0").decode().strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def burn_label(src: Path, work_dir: Path, day: int = 0, date_str: str = "",
               lines: tuple[str, ...] | None = None, font_path: Path | None = None,
               accent: tuple[int, int, int] | None = None) -> Path | None:
    """把栏目角标烧进成片：一张 PNG 全程叠加。失败返回 None（调用方保留原片并写明降级）。

    lines/font_path = 租户品牌（栏目名两行 + 其字体子集），缺省用平台中性默认（跃迁/内容）。"""
    import subprocess

    png = work_dir / "label.png"
    if not render_label_png(png, _probe_size(src), day=day, date_str=date_str,
                            lines=lines, font_path=font_path, accent=accent):
        return None
    out = work_dir / "with_label.mp4"
    # -loop 1 让静态 PNG 铺满全程，shortest=1 收到视频长度；
    # 必须 map 滤镜输出 [vout]——直接 -map 0:v 会把原视频原样编码出去，角标根本不进画面（实测踩到）
    cmd = ["ffmpeg", "-y", "-i", str(src), "-loop", "1", "-i", str(png),
           "-filter_complex", "[1:v]format=rgba[l];[0:v][l]overlay=0:0:shortest=1[vout]",
           "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy",
           "-movflags", "+faststart", "-t", f"{_probe_total(src):.3f}", str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        print("[label] ffmpeg 超时（900s）")
        return None
    if proc.returncode != 0 or not out.exists():
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        print(f"[label] ffmpeg 失败 rc={proc.returncode}: {' | '.join(tail)}")
        return None
    return out
