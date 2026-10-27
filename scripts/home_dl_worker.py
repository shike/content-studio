"""拆解下载本机 worker（自包含，脱离仓库运行）。

装在 ~/Library/Application Support/content-studio/（launchd 禁读 Desktop 仓库——TCC，
见 watchdog 同款教训）。流程：SSH 到服务器列 data/home_dl/*.req 排队件 → 本机真实
Mac 浏览器匿名下载（服务器侧 Linux 指纹被抖音扣数据，同 IP 本机实测可过；登录态
授权已否决，本 worker 永不携带任何 cookie）→ 回传服务器 → 删排队件。

循环由 launchd StartInterval 驱动（每次唤醒跑一轮就退出），Mac 睡眠/关机自然停摆，
服务器侧任务超时进退避梯子并报「等待本机下载」。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
SSH_KEY = HOME / ".ssh" / "content_studio_deploy"
SSH_BASE = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-i", str(SSH_KEY), "ubuntu@cs.dropleap.cn"]
REMOTE_ROOT = "/srv/content-studio"
POLL_SEC = int(os.environ.get("HOME_DL_POLL", "120"))
MIN_GAP = float(os.environ.get("HOME_DL_GAP", "20"))  # 两次下载最小间隔（家宽 IP 纪律同服务器）
UA = os.environ.get(
    "DOUYIN_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_NO_WATERMARK_KEYS = ("nwm_video_url", "no_watermark_url", "nwm_video_url_h264",
                      "nwm_video_url_h265", "download_addr", "play_url")


def log(msg: str) -> None:
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)


def sh(*args: str, input_text: str | None = None) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True,
                       input=input_text, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} 失败: {(r.stderr or r.stdout).strip()[:150]}")
    return r.stdout


def ssh(cmd: str, input_text: str | None = None) -> str:
    return sh(*SSH_BASE, cmd, input_text=input_text)


def resolve_aweme_id(url: str) -> str | None:
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    try:  # 分享短链：跟随一次 302
        import httpx
        r = httpx.get(url, timeout=15, follow_redirects=False,
                      headers={"User-Agent": UA})
        loc = r.headers.get("location", "") or str(r.url)
        m = re.search(r"/video/(\d+)", loc)
        return m.group(1) if m else None
    except Exception:  # noqa: BLE001
        return None


def find_video_url(node) -> str | None:
    if isinstance(node, dict):
        pa = node.get("play_addr") or node.get("playUrl")
        if isinstance(pa, dict):
            for u in pa.get("url_list") or []:
                if isinstance(u, str) and u.startswith("http"):
                    return u
        for key in _NO_WATERMARK_KEYS:
            v = node.get(key)
            if isinstance(v, str) and v.startswith("http"):
                return v
        for v in node.values():
            found = find_video_url(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = find_video_url(v)
            if found:
                return found
    elif isinstance(node, str):
        if node.startswith("http") and ".mp4" in node:
            return node
    return None


def download_video(url: str, out_dir: Path) -> Path:
    """本机匿名下载（拦页面自身 aweme/detail，自带合法签名；完整版 Chromium 内核）。"""
    import httpx
    from playwright.sync_api import sync_playwright

    free_gb = shutil.disk_usage(HOME).free / 1e9
    if free_gb < 2:
        raise RuntimeError(f"本机磁盘不足（剩 {free_gb:.1f}GB）")
    rid = resolve_aweme_id(url)
    if not rid:
        raise RuntimeError("无法解析视频 id")
    target = f"https://www.douyin.com/video/{rid}"
    detail: dict = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, channel="chromium",
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        try:
            ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                      viewport={"width": 1280, "height": 800})
            page = ctx.new_page()

            def on_response(resp):
                if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                    try:
                        detail.update(resp.json())
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)
            page.goto(target, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            ctx.close()
        finally:
            browser.close()
    video_url = find_video_url(detail.get("aweme_detail") or detail)
    if not video_url:
        raise RuntimeError("页面未吐出视频数据（可能触发验证码，可重试）")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bm_{rid}.mp4"
    with httpx.stream("GET", video_url, timeout=300, follow_redirects=True,
                      headers={"User-Agent": UA, "Referer": "https://www.douyin.com/"}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(1024 * 256):
                f.write(chunk)
    if path.stat().st_size == 0:
        raise RuntimeError("直链下载空文件")
    return path


def deliver(req_path: str, payload: dict) -> None:
    bid, url, dest = payload["benchmark_id"], payload["url"], payload["dest"]
    if ssh(f"test -f {dest} && echo y || true").strip() == "y":
        ssh(f"rm -f {req_path}")  # 重试幂等：文件已回传过，只清排队件
        log(f"#{bid} 服务器已有成品，跳过下载")
        return
    tmp = Path(tempfile.mkdtemp(prefix="cs_home_dl_"))
    try:
        t0 = time.time()
        local = download_video(url, tmp)
        sh("scp", "-o", "BatchMode=yes", "-i", str(SSH_KEY),
           str(local), f"ubuntu@cs.dropleap.cn:{dest}.part")
        ssh(f"mv {dest}.part {dest} && rm -f {req_path}")
        log(f"#{bid} 完成 {local.stat().st_size // 1024}KB {time.time() - t0:.0f}s")
    except Exception as e:  # noqa: BLE001 单件失败不拖垮整轮，报错回传给服务器侧
        msg = str(e)[:180].replace("\n", " ")
        ssh(f"cat > {dest}.err", input_text=msg)
        ssh(f"rm -f {req_path}")
        log(f"#{bid} 失败: {msg}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def one_pass() -> None:
    listing = ssh(f"ls -1 {REMOTE_ROOT}/data/home_dl/*.req 2>/dev/null || true").strip()
    if not listing:
        return
    reqs = [line.strip() for line in listing.splitlines() if line.strip()]
    log(f"取到 {len(reqs)} 件排队")
    for i, req_path in enumerate(reqs):
        if i:
            time.sleep(MIN_GAP)
        try:
            payload = json.loads(ssh(f"cat {req_path}"))
            deliver(req_path, payload)
        except Exception as e:  # noqa: BLE001 读排队件失败（网络抖动）本轮跳过
            log(f"排队件 {req_path} 读取失败: {str(e)[:120]}")


if __name__ == "__main__":
    if "--once" in sys.argv:  # 手动单轮（调试用）
        one_pass()
    else:  # launchd 每次唤醒跑一轮（StartInterval 驱动）
        try:
            one_pass()
        except Exception as e:  # noqa: BLE001 服务器不可达等：安静退出等下轮
            log(f"本轮跳过: {str(e)[:150]}")
