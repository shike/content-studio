"""配图文件签名与合法性（公众号粘贴链路的公开签名图基座）。

（AI 生图 gen_image 已随 search: 占位图链路退役 2026-11-14：提示词不再产出
search: 标记，自动配图循环与手动生图端点双双无输入；charts.py 的图表/头图
渲染仍在产 gen_ 之外前缀的本地图，签名/校验/白名单照旧服务。）"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets


# 配图文件名前缀白名单（防目录穿越；公开签名图路由共用）
IMAGE_PREFIXES = ("gen_", "chart_", "cover_", "flow_", "kg_")


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
