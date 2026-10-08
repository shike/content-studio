"""国内热点榜与科技/AI 资讯抓取（今日热点选题 R1.4 的原料源）。

百度热搜页是服务端渲染 + 内嵌 JSON（<!--s-data=…-->），免登录可抓；
机房 IP 实测直连 200（2026-10-08 服务器验证）。科技资讯走爱范儿/IT之家
的标准 RSS（同日验证本机+服务器均 200）。抓取失败一律降级返回 meta，
由调用方如实标注"未生效"，绝不静默吞。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

_BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"
# 国内科技/AI 资讯（RSS 实测可用清单；虎嗅 rss 已死、量子位/机器之心 RSS 废弃——别再回收）
_TECH_FEEDS: list[tuple[str, str]] = [
    ("爱范儿", "https://www.ifanr.com/feed"),
    ("IT之家", "https://www.ithome.com/rss/"),
]
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


def fetch_tech_news(per_source: int = 8, fresh_hours: int = 48,
                    timeout: float = 12.0) -> tuple[list[dict], dict]:
    """抓国内科技/AI 资讯（爱范儿+IT之家 RSS），只留近 fresh_hours 小时的条目。

    返回 ([{title, source, date}], meta)。这是"领域内今日热点"的主力原料——
    泛热搜榜上领域条目稀少，科技媒体的日更能保证切角有料可绑。"""
    meta: dict = {"source": "tech_news", "status": "ok", "count": 0, "note": ""}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=fresh_hours)
    items: list[dict] = []
    src_stats: list[str] = []
    for name, url in _TECH_FEEDS:
        got = 0
        try:
            resp = httpx.get(url, headers={"User-Agent": _UA}, timeout=timeout,
                             follow_redirects=True)
            resp.raise_for_status()
            for raw in re.findall(r"<item>(.*?)</item>", resp.text, re.S):
                tm = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S)
                title = re.sub(r"\s+", " ", tm.group(1)).strip() if tm else ""
                if not title:
                    continue
                dm = re.search(r"<pubDate>([^<]+)</pubDate>", raw)
                when = None
                if dm:
                    try:
                        when = parsedate_to_datetime(dm.group(1).strip())
                    except Exception:  # noqa: BLE001 日期坏条目按无日期处理
                        when = None
                if when is not None and when < cutoff:
                    continue  # 只留新鲜条目（旧闻不许当"今日"）
                items.append({"title": title, "source": name,
                              "date": when.astimezone(timezone.utc).strftime("%m-%d") if when else ""})
                got += 1
                if got >= per_source:
                    break
        except Exception as e:  # noqa: BLE001 单源失败不拖垮整路
            src_stats.append(f"{name}: error {type(e).__name__}")
            continue
        src_stats.append(f"{name}: {got} 条")
    meta["count"] = len(items)
    meta["note"] = "；".join(src_stats)
    if not items:
        meta["status"] = "error: 两个源都没有可用条目"
    return items, meta
