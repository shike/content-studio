"""微信公众平台发布适配器（PRD R4.5，人工触发）：建草稿 / 群发。

边界（写死）：
- 只由人工点按触发（API 端点直调），无定时/无人值守调用路径；
- `wechat_publish_enabled` 总开关（app_settings）关闭时端点显式报错回退人工；
- 凭证=认证号的 AppID/AppSecret（设置页只写不读）+ 公众号后台 IP 白名单（服务器出口 IP）；
- 失败按微信 errcode 分类（可重试/确定性），失败可回退人工复制 HTML。
"""
from __future__ import annotations

import time

import httpx
from sqlmodel import Session

from ..db import engine
from ..models import AppSetting

_API = "https://api.weixin.qq.com/cgi-bin"
_TOKEN_SKEW = 300  # 提前 5 分钟刷新

_token_cache: dict = {"token": "", "expires_at": 0.0}


class WechatPublishError(RuntimeError):
    """发布失败（凭证/权限/微信侧拒绝）——调用方转 4xx/任务失败并回退人工。"""


def _settings_value(s: Session, key: str) -> str:
    row = s.get(AppSetting, key)
    return (row.value or "").strip() if row is not None else ""


def _config(s: Session) -> dict:
    app_id = _settings_value(s, "wechat_app_id")
    # 密钥只写不读：库里存的是写入时的明文（密钥回显口径=是否已配置）
    secret = _settings_value(s, "wechat_app_secret")
    enabled = _settings_value(s, "wechat_publish_enabled").lower() in ("1", "true", "on")
    return {"app_id": app_id, "secret": secret, "enabled": enabled}


def publish_configured() -> bool:
    """API 发布是否可用（开关+双凭证齐备）。"""
    with Session(engine) as s:
        cfg = _config(s)
    return bool(cfg["enabled"] and cfg["app_id"] and cfg["secret"])


def _access_token() -> str:
    with Session(engine) as s:
        cfg = _config(s)
    if not cfg["enabled"]:
        raise WechatPublishError("公众号 API 发布未启用（设置页 wechat_publish_enabled）")
    if not cfg["app_id"] or not cfg["secret"]:
        raise WechatPublishError("缺少 AppID/AppSecret（设置页公众号发布卡配置，密钥只写不读）")
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    resp = httpx.get(f"{_API}/token", params={
        "grant_type": "client_credential", "appid": cfg["app_id"],
        "secret": cfg["secret"]}, timeout=15)
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise WechatPublishError(
            f"取 access_token 失败：errcode={data.get('errcode')} {str(data.get('errmsg'))[:120]}"
            "（核对 AppID/Secret 与公众号后台 IP 白名单）")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in") or 7200) - _TOKEN_SKEW
    return token


def _wechat_post(path: str, body: dict) -> dict:
    token = _access_token()
    resp = httpx.post(f"{_API}/{path}", params={"access_token": token},
                      json=body, timeout=30)
    data = resp.json()
    err = data.get("errcode", data.get("base_resp", {}).get("errcode", 0))
    if err not in (0, None):
        raise WechatPublishError(
            f"{path} 失败：errcode={err} {str(data.get('errmsg') or data.get('base_resp', {}).get('err_msg'))[:160]}")
    return data


def add_draft(*, title: str, html: str, digest: str = "", author: str = "",
              thumb_media_id: str = "") -> str:
    """建公众号草稿（draft/add），返回 draft media_id。内容为已渲染净化的 HTML。"""
    data = _wechat_post("draft/add", {
        "articles": [{"title": title[:64], "author": author[:16],
                      "digest": digest[:120], "content": html,
                      "need_open_comment": 0, "only_fans_can_comment": 0,
                      **({"thumb_media_id": thumb_media_id} if thumb_media_id else {})}]})
    return str(data.get("media_id") or "")


def submit_publish(media_id: str) -> dict:
    """群发已草稿内容（freepublish/submit）。返回发布任务 id（公众号侧异步生效）。"""
    data = _wechat_post("freepublish/submit", {"media_id": media_id})
    return {"publish_id": str(data.get("publish_id") or ""),
            "data": {k: v for k, v in data.items() if k != "errcode"}}


def upload_permanent_image(png_bytes: bytes) -> str:
    """封面图上传（material/add_material，type=image）→ media_id（草稿 thumb 必填）。"""
    token = _access_token()
    resp = httpx.post(f"{_API}/material/add_material",
                      params={"access_token": token},
                      files={"media": ("cover.png", png_bytes, "image/png")}, timeout=60)
    data = resp.json()
    if not data.get("media_id"):
        raise WechatPublishError(
            f"封面上传失败：errcode={data.get('errcode')} {str(data.get('errmsg'))[:120]}")
    return str(data["media_id"])
