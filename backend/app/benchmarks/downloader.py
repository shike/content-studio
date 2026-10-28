"""抖音视频下载：Playwright 真浏览器拦截路线。

2026-09 抖音对 aweme/detail 上线 Argus 签名门槛，纯 HTTP 直连已不可行；
调研结论（16 个同类产品）：零用户 cookie 的路径 = 真浏览器环境自动生成访客身份。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from ..browser import httpx_proxy, launch, ua
from ..jobs.errors import TransientError
from ..settings import settings

_NO_WATERMARK_KEYS = ("nwm_video_url", "no_watermark_url", "nwm_video_url_h264",
                      "nwm_video_url_h265")
# 抓取 UA 统一由 browser.ua() 提供（平台感知：UA 与运行平台不一致会被抖音风控挑战）。


def _ensure_disk_space(min_gb: int = 2) -> None:
    """磁盘余量硬防御：不足时显式报错（进失败中台待处置），绝不写满系统盘。"""
    import shutil

    from ..settings import settings

    free_gb = shutil.disk_usage(settings.data_dir).free / 1e9
    if free_gb < min_gb:
        raise RuntimeError(
            f"磁盘剩余空间不足（{free_gb:.1f}GB < {min_gb}GB），已暂停下载。"
            "请清理磁盘后在任务页按指纹复活这批任务")


def download_video(url: str, out_dir: Path) -> str:
    """返回本地 mp4 路径。"""
    _ensure_disk_space()
    out_dir.mkdir(parents=True, exist_ok=True)

    path, pw_error = _try_playwright(url, out_dir)
    if path:
        return path

    hint = ""
    if settings.douyin_proxy:  # 有代理配置仍失败：提示出口可能断了（本机未开机/隧道断）
        hint = f"（经出口代理 {settings.douyin_proxy}）"
    # 代理层失败要显式区分（2026-10-27 教训：ERR_PROXY_CONNECTION_FAILED 曾被上游
    # 当成"验证码"进熔断，2 次断连把整队下载任务打成"风控窗"连环失败）
    if pw_error and any(k in pw_error for k in (
            "ERR_PROXY_CONNECTION_FAILED", "Proxy Server could not connect",
            "Host unreachable", "SOCKS5", "tunnel")):
        raise TransientError(f"出口代理不通{hint}：{str(pw_error)[:120]}（家里 Mac 关机或隧道断了，稍后自动重试）")
    raise RuntimeError(
        f"自动下载失败{hint}（浏览器路线：{pw_error or '未启用'}）。"
        "可手工下载视频后走「本地视频拆解」上传。"
    )


def extract_douyin_url(text: str) -> str:
    """从任意粘贴文本（抖音分享口令全文等）提取第一个链接；无匹配原样返回。

    用户实际会整段粘贴「5- 长按复制此条消息… https://v.douyin.com/xxx/ 2@2.com」，
    直接当 URL 用会在 httpx/正则各环节随机炸，必须先提取。
    """
    m = re.search(r"https?://[^\s'\"<>，。；！？；）)】》]+", (text or "").strip())
    if not m:
        return (text or "").strip()
    return m.group(0).rstrip(".,；;")


_DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")


def is_douyin_url(url: str) -> bool:
    """仅放行抖音域（http/https）。服务端会主动请求用户给的 URL——
    不限制则构成 SSRF：可让服务器访问内网/云元数据端点（169.254.169.254 等）。"""
    from urllib.parse import urlparse

    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return False
    if u.scheme not in ("http", "https") or not u.hostname:
        return False
    host = u.hostname.lower()
    return any(host == d or host.endswith("." + d) for d in _DOUYIN_HOSTS)


def resolve_aweme_id(url: str) -> Optional[str]:
    """分享短链/各种变体 → aweme_id。非抖音域一律 None（不发起任何请求）。"""
    if not is_douyin_url(url):
        return None
    try:
        resp = httpx.get(url, timeout=15, follow_redirects=False,
                         headers={"User-Agent": ua()}, proxy=httpx_proxy())
        loc = resp.headers.get("location", "")
        m = re.search(r"/video/(\d+)", loc or url)
        if m:
            return m.group(1)
        if resp.status_code == 200:
            m = re.search(r"/video/(\d+)", resp.url)
            if m:
                return m.group(1)
    except Exception:  # noqa: BLE001
        m = re.search(r"/video/(\d+)", url)
        return m.group(1) if m else None
    return None


def _download_mp4(video_url: str, out_dir: Path) -> Optional[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"bm_{int(time.time() * 1000)}.mp4"
    with httpx.stream("GET", video_url, timeout=300, follow_redirects=True,
                      proxy=httpx_proxy(),
                      headers={"User-Agent": ua(), "Referer": "https://www.douyin.com/"}) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_bytes(1024 * 256):
                f.write(chunk)
    return str(path) if path.stat().st_size > 0 else None


def _try_playwright(url: str, out_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """真浏览器路线：打开视频页，拦页面自身发出的 aweme/detail（自带合法签名与访客身份）。

    2026-10-27 定论：必须用**与运行平台一致**的指纹——Mac UA 跑在 Linux 上=不一致指纹，
    才是数据被扣的原因；平台一致（服务器 Linux UA）+住宅出口+匿名实测可拿 detail 与直链。
    此前两轮弯路：跨平台伪装（297f026）、登录态注入（297f026，已撤）都已被本结论替代。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright"

    rid = resolve_aweme_id(url)
    if not rid:
        return None, "无法解析视频 id"
    target = f"https://www.douyin.com/video/{rid}"
    detail: dict = {}
    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
        try:
            from ..browser import ua

            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
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
            # 轮询等 detail（固定 sleep 会在慢加载时误报"页面未吐出"）
            for _ in range(8):
                if detail:
                    break
                page.wait_for_timeout(1500)
            # 页面可能不发 detail XHR（缓存），兜底读 _ROUTER_DATA
            title_text = ""
            html = ""
            if not detail:
                title_text = page.title() or ""
                html = page.content()
                m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*</script>",
                              html, re.S)
                if m:
                    try:
                        detail = json.loads(m.group(1))
                    except json.JSONDecodeError:
                        pass
        except Exception as e:  # noqa: BLE001
            return None, f"浏览器路线失败: {str(e)[:120]}"
        finally:
            browser.close()

    video_url = _find_video_url(detail)
    if not video_url:
        # 失败原因按"可观察事实"归类（2026-10-28 二次修正：页面 HTML 的 JS 里本来就
        # 带"验证码"字样，html 关键词搜出一律误判 captcha 喂熔断——只有标题/专用 DOM
        # 命中才算真验证码；有 detail 无直链=直链被限，不说成验证码）
        if detail:
            return None, "播放直链被当前出口限流（匿名高频状态），稍后自动重试"
        if re.search(r"安全验证|验证码|captcha", title_text, re.I):
            return None, "页面吐出验证码（真验证码，低频等待可解）"
        if re.search(r"视频不见了吗|视频不存在|已被删除|审核中|暂无内容|内容暂时无法", html):
            return None, "视频已删除或不可见（毒视频，人工在 App 确认后可忽略）"
        return None, "视频数据未返回（慢加载或限流状态，稍后自动重试）"
    try:
        return _download_mp4(video_url, out_dir), None
    except Exception as e:  # noqa: BLE001
        return None, f"下载直链失败: {str(e)[:120]}"




def _find_video_url(node) -> Optional[str]:
    if isinstance(node, dict):
        # web detail 结构：video.play_addr.url_list
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
            found = _find_video_url(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_video_url(v)
            if found:
                return found
    elif isinstance(node, str):
        if node.startswith("http") and ".mp4" in node:
            return node
    return None
