"""配图生成（智谱 CogView）：文章自动配图与占位卡手动生图共用。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

import httpx

from ..settings import settings

# 配图文件名前缀白名单（防目录穿越；公开签名图路由共用）
IMAGE_PREFIXES = ("gen_", "chart_", "cover_", "flow_", "kg_")


def _tenant_image_dir():
    """配图按租户分目录（媒体租户边界）；归属=当前 ACTOR，系统上下文回落租户 1。"""
    from ..auth import ACTOR
    tenant_id, _u, _r = ACTOR.get()
    d = settings.data_dir / "article_images" / str(tenant_id or 1)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ===== 配图公开签名（公众号粘贴链路）=====
# 公众号编辑器从 mp.weixin.qq.com 跨域抓取外链图片并转存素材库——它带不了我们的登录 Cookie，
# 所以粘贴载荷里的配图必须是"无需登录但不可猜测"的地址：对文件名做 HMAC 签名（能力型 URL）。
# 签名对文件名确定性计算（不设过期），文章 html 里的地址长期可重复粘贴。
_SIG_KEY_NAME = "internal_image_sig_key"  # 存 AppSetting（不在 MANAGED 清单，不进设置页）
_SIG_KEY_CACHE: bytes | None = None


def _sig_key() -> bytes:
    """签名密钥：首次使用生成并落库（不入设置页语义），保证重启/多进程一致。"""
    global _SIG_KEY_CACHE
    if _SIG_KEY_CACHE is not None:
        return _SIG_KEY_CACHE
    from sqlmodel import Session

    from ..db import engine
    from ..models import AppSetting

    with Session(engine) as s:
        row = s.get(AppSetting, _SIG_KEY_NAME)
        if row is None:
            key = secrets.token_urlsafe(32)
            s.add(AppSetting(key=_SIG_KEY_NAME, value=json.dumps(key)))
            s.commit()
        else:
            key = json.loads(row.value)
    _SIG_KEY_CACHE = key.encode()
    return _SIG_KEY_CACHE


def sign_image(name: str) -> str:
    """配图签名（22 字符 base64url，约 132 bit）。"""
    mac = hmac.new(_sig_key(), name.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:22]


def verify_image(name: str, sig: str) -> bool:
    return bool(sig) and hmac.compare_digest(sign_image(name), sig)


def valid_image_name(name: str) -> bool:
    """文件名合法性：白名单前缀 + 无路径分隔（公开路由与登录路由共用同一判据）。"""
    return ("/" not in name and ".." not in name and name.startswith(IMAGE_PREFIXES))


_COGVIEW = "cogview-3-flash"  # ≈0.06 元/张；质量不满意可换 cogview-4（≈0.25 元/张）


def gen_image(caption: str, keywords: str = "") -> dict:
    """生成 1344x768 插画并下载落盘。返回 {image_url, file}。"""
    if not settings.zhipu_api_key:
        raise RuntimeError("未配置 ZHIPU_API_KEY")
    theme = (caption or keywords or "").strip()[:80]
    prompt = (f"为一篇行业深度分析文章生成配图。配图主题：{theme}。"
              "现代简洁的商务科技插画风格，冷色调蓝灰为主，画面干净专业，构图疏朗，"
              "画面中不要出现任何文字，横版构图")
    resp = httpx.post(
        "https://open.bigmodel.cn/api/paas/v4/images/generations",
        headers={"Authorization": f"Bearer {settings.zhipu_api_key}",
                 "Content-Type": "application/json"},
        json={"model": _COGVIEW, "prompt": prompt, "size": "1344x768"},
        timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"CogView http {resp.status_code}: {resp.text[:120]}")
    url = ((resp.json().get("data") or [{}])[0].get("url") or "")
    if not url:
        raise RuntimeError("CogView 未返回图片")
    import shutil

    free_gb = shutil.disk_usage(settings.data_dir).free / 1e9
    if free_gb < 1:
        raise RuntimeError(f"磁盘剩余 {free_gb:.1f}GB 不足，跳过生图（清理后可在占位卡重试）")
    out_dir = _tenant_image_dir()
    dest = out_dir / f"gen_{int(time.time() * 1000)}.png"
    img = httpx.get(url, timeout=60)
    img.raise_for_status()
    dest.write_bytes(img.content)
    return {"image_url": f"/api/articles/images/{dest.name}", "file": str(dest)}
