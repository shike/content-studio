"""国内热点榜抓取（今日热点选题 R1.4 的原料源之一）。

百度热搜页是服务端渲染 + 内嵌 JSON（<!--s-data=…-->），免登录可抓；
机房 IP 实测直连 200（2026-10-08 服务器验证）。抓取失败一律降级返回
meta，由调用方如实标注"未生效"，绝不静默吞。
"""
from __future__ import annotations

import json
import re

import httpx

_BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def fetch_baidu_hot(limit: int = 15, timeout: float = 12.0) -> tuple[list[dict], dict]:
    """抓百度热搜实时榜。返回 (条目[{word, desc?}], meta{status, count, note})。"""
    meta: dict = {"source": "baidu_hot", "status": "ok", "count": 0, "note": ""}
    try:
        resp = httpx.get(_BAIDU_HOT_URL, headers={"User-Agent": _UA},
                         timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001 降级：原料缺一路不阻塞，调用方如实标注
        meta["status"] = f"error: {type(e).__name__}: {str(e)[:80]}"
        return [], meta

    html = resp.text
    items: list[dict] = []
    # 首选：内嵌 s-data JSON（cards[].content[].word/desc）
    m = re.search(r"<!--s-data=(.*?)-->", html, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
            for card in (data.get("cards") or []):
                for c in (card.get("content") or []):
                    w = str(c.get("word") or "").strip()
                    if w:
                        items.append({"word": w, "desc": str(c.get("desc") or "").strip()[:80]})
        except Exception:  # noqa: BLE001 JSON 坏了走正则兜底
            items = []
    if not items:  # 兜底：正则直提词
        items = [{"word": w, "desc": ""}
                 for w in re.findall(r'"word":"([^"]{2,40})"', html)]
    # 去重保序截断
    seen: set[str] = set()
    uniq: list[dict] = []
    for it in items:
        if it["word"] not in seen:
            seen.add(it["word"])
            uniq.append(it)
    uniq = uniq[:limit]
    meta["count"] = len(uniq)
    if not uniq:
        meta["status"] = "error: 页面解析为空（结构可能变了）"
    return uniq, meta
