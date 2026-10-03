"""公开媒体路由（无登录态）：签名配图，供公众号编辑器跨域抓取后转存素材库。

为什么必须公开：公众号编辑器从 mp.weixin.qq.com 抓 <img> 外链，带不了本站 Cookie，
登录态图片地址一律取不到（粘贴过去是裂图）。故对文件名做 HMAC 签名，持签名即可取——
能力型 URL（同图床做法），不带签名的请求仍需登录（见 articles.article_image）。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..articles.images import valid_image_name, verify_image
from ..settings import settings

router = APIRouter(prefix="/api/public")


@router.get("/images/{filename}")
def public_image(filename: str, t: str = ""):
    """签名公开配图。签名不匹配一律 404（不泄露文件是否存在）。"""
    if not valid_image_name(filename) or not verify_image(filename, t):
        raise HTTPException(status_code=404, detail="image not found")
    for cand in sorted((settings.data_dir / "article_images").glob(f"*/{filename}")):
        return FileResponse(cand, media_type="image/png")
    legacy = settings.data_dir / "article_images" / filename  # 迁移前平铺的老文件
    if legacy.exists():
        return FileResponse(legacy, media_type="image/png")
    raise HTTPException(status_code=404, detail="image not found")
