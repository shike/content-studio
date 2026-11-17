"""ima 知识库取材（公众号写文素材源，2026-11-08）。

腾讯 ima 官方 OpenAPI（凭证只发 ima.qq.com，鉴权=三个明文头，无签名）。
配置走 app_settings：ima_kb_enabled=1 / ima_client_id / ima_api_key /
ima_kb_ids（逗号分隔，只配用户自建库——订阅的他人库不是自有经验，不作素材源）。
（历史注：检索取材 fetch_kb_material 曾服务人工报题写文链「零命中=缺料报错不硬写」，
该链 2026-11-14 拆除；本模块现只服务挖掘器/改编器/深读的目录遍历与全文拉取。）
"""
from __future__ import annotations

import asyncio

import httpx
from sqlmodel import Session

from ..models import AppSetting

BASE = "https://ima.qq.com"
CTX = "skill_version=content-studio-1.0.0"

# 挖掘器默认遍历目录（水滴跃动 AI-FDE 库核心真货区；可用 ima_topic_folders 覆盖）
DEFAULT_TOPIC_FOLDERS = "folder_7520695068157353:案例-交付实战复盘,folder_7520695084935296:案例-案例集与行业标杆,folder_7520695080739753:案例-行业场景与趋势,folder_7520695063956771:案例-方法论与工作包,folder_7520695126877635:岗位认知与转型路径,folder_7520695613417012:接单报价与个人品牌,folder_7520695097516987:市场与人才数据,folder_7520695110094246:研究报告与白皮书,folder_7520695378529762:能力模型与角色认知,folder_7520695445637163:企业落地与组织,folder_7520695386917111:行业观察与源流,folder_7520695206571358:工程规范与工具箱"


def _setting(s: Session, key: str) -> str:
    row = s.get(AppSetting, key)
    return (row.value or "").strip() if row is not None else ""


def kb_config(s: Session) -> dict | None:
    """读配置；未启用/未配齐返回 None（调用方视为缺料，挖掘/改编/深读线直接报错）。"""
    if _setting(s, "ima_kb_enabled") != "1":
        return None
    client_id = _setting(s, "ima_client_id")
    api_key = _setting(s, "ima_api_key")
    kb_ids = [x for x in _setting(s, "ima_kb_ids").split(",") if x]
    if not (client_id and api_key and kb_ids):
        return None
    return {"client_id": client_id, "api_key": api_key}


def topic_folders(s: Session) -> list[tuple[str, str]]:
    """挖掘器遍历目录：[(folder_id, 名称)]。"""
    raw = _setting(s, "ima_topic_folders") or DEFAULT_TOPIC_FOLDERS
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        fid, _, name = item.partition(":")
        if fid:
            out.append((fid, name or fid[:16]))
    return out


def topic_kb_id(s: Session) -> str:
    """选题遍历的知识库（默认取 ima_kb_ids 第一个）。"""
    ids = [x for x in _setting(s, "ima_kb_ids").split(",") if x]
    return ids[0] if ids else "ima_kb_Oqey8gOu"


async def list_folder_docs(client_id: str, api_key: str, kb_id: str,
                           folder_id: str, depth: int = 4) -> list[dict]:
    """遍历目录（递归子目录至 depth 层，2026-11-17：ima 库重组后内容进了多层子目录）。

    返回 [{title, media_id, media_type}]；99=文件夹（下钻）；9=图片（原样返回，
    由调用方剔拒）。分页/下钻间隔 0.5s（密集调用 403 教训）。"""
    docs: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as cli:
        async def _walk(fid: str, depth: int) -> None:
            cursor = ""
            while True:
                body = {"knowledge_base_id": kb_id, "cursor": cursor, "limit": 50}
                if fid:
                    body["folder_id"] = fid
                for attempt in range(4):  # 403=突发量惩罚：批内自退避，不炸整批
                    r = await cli.post(f"{BASE}/openapi/wiki/v1/get_knowledge_list", json=body, headers={
                        "ima-openapi-clientid": client_id,
                        "ima-openapi-apikey": api_key,
                        "ima-openapi-ctx": CTX,
                    })
                    if r.status_code == 403 and attempt < 3:
                        await asyncio.sleep(45)
                        continue
                    r.raise_for_status()
                    break
                data = r.json()
                if data.get("code") != 0:
                    raise RuntimeError(f"ima 列目录失败: {data.get('msg')}")
                payload = data.get("data") or {}
                for it in payload.get("knowledge_list") or []:
                    mt = it.get("media_type")
                    if mt == 99:  # 文件夹：递归下钻
                        if depth > 0:
                            await asyncio.sleep(2.0)
                            await _walk(str(it.get("media_id") or ""), depth - 1)
                        continue
                    docs.append({"title": str(it.get("title") or "")[:80],
                                 "media_id": str(it.get("media_id") or ""),
                                 "media_type": mt})
                if payload.get("is_end") or not payload.get("next_cursor"):
                    break
                cursor = payload.get("next_cursor")
                await asyncio.sleep(2.5)
        await _walk(folder_id, depth)
    return docs


async def fetch_full_doc(client_id: str, api_key: str, media_id: str, cap: int = 8000) -> str:
    """取单篇原文全文（md/txt；其他类型或失败返回空串）。"""
    try:
        info = await _post({"client_id": client_id, "api_key": api_key},
                           "openapi/wiki/v1/get_media_info", {"media_id": media_id})
        url = (info.get("url_info") or {}).get("url") or ""
        if not url:
            return ""
        async with httpx.AsyncClient(timeout=40, follow_redirects=True) as cli:
            r = await cli.get(url)
        if r.status_code != 200 or not r.content or len(r.content) > 400_000:
            return ""
        text = r.content.decode("utf-8", "ignore")
        if text[:5] == "%PDF":
            return ""
        return text[:cap]
    except Exception as e:  # noqa: BLE001
        print(f"[ima-kb] 全文获取失败（{media_id[:24]}）: {type(e).__name__}: {str(e)[:80]}")
        return ""


async def _post(cfg: dict, path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=30) as cli:
        r = await cli.post(f"{BASE}/{path}", json=body, headers={
            "ima-openapi-clientid": cfg["client_id"],
            "ima-openapi-apikey": cfg["api_key"],
            "ima-openapi-ctx": CTX,
        })
        r.raise_for_status()
        data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"ima {path} 业务错误: {data.get('msg')}")
    return data.get("data") or {}
