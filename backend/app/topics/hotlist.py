"""国内热点榜与科技/AI 资讯抓取（今日热点靶子线的原料源，2026-11-12 扩供应）。

五个榜/讯源，全部免登录（2026-11-12 生产服务器逐一实测）：
- 百度热搜：页面服务端渲染 + 内嵌 JSON（<!--s-data=…-->），直连 200。
- 抖音热点榜：iesdouyin 403，www.douyin.com hot/search/list + Referer 匿名 200，
  49 条带真实 hot_value（他发布平台自己的榜=第一优先）。
- 知乎热榜：api.zhihu.com/topstory/hot-list 匿名 200（web 端 v3 接口 401 别用）。
  微博 hotSearch 匿名 403、GitHub trending 服务器双次超时、36kr RSS 空——别回收。
- 科技资讯 RSS：爱范儿/IT之家/少数派/雷峰网（虎嗅 rss 已死、量子位/机器之心 RSS
  废弃——别再回收）。抓取失败一律降级返回 meta，由调用方如实标注"未生效"，绝不静默吞。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

_BAIDU_HOT_URL = "https://top.baidu.com/board?tab=realtime"
_DOUYIN_HOT_URL = ("https://www.douyin.com/aweme/v1/web/hot/search/list/"
                   "?device_platform=webapp&aid=6383&channel=channel_pc_web"
                   "&version_code=170400&pc_client_type=1")
_ZHIHU_HOT_URL = "https://api.zhihu.com/topstory/hot-list?limit=50"
# 国内科技/AI 资讯（RSS 实测可用清单；虎嗅 rss 已死、量子位/机器之心 RSS 废弃——别再回收）
_TECH_FEEDS: list[tuple[str, str]] = [
    ("爱范儿", "https://www.ifanr.com/feed"),
    ("IT之家", "https://www.ithome.com/rss/"),
    ("少数派", "https://sspai.com/feed"),
    ("雷峰网", "https://www.leiphone.com/feed"),
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


def fetch_douyin_hot(limit: int = 50, timeout: float = 12.0) -> tuple[list[dict], dict]:
    """抓抖音热点榜（他发布平台自己的榜=第一优先；2026-11-12 服务器匿名实测可用）。

    返回 ([{word, desc?, hot_value}], meta)。必须有 Referer，否则 403。"""
    meta: dict = {"source": "douyin_hot", "status": "ok", "count": 0, "note": ""}
    try:
        resp = httpx.get(_DOUYIN_HOT_URL,
                         headers={"User-Agent": _UA, "Referer": "https://www.douyin.com/"},
                         timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 降级：原料缺一路不阻塞，调用方如实标注
        meta["status"] = f"error: {type(e).__name__}: {str(e)[:80]}"
        return [], meta
    items: list[dict] = []
    for x in ((data.get("data") or {}).get("word_list")) or []:
        w = str(x.get("word") or "").strip()
        if not w:
            continue
        tag = x.get("sentence_tag")
        items.append({"word": w,
                      "desc": (tag if isinstance(tag, str) else "").strip()[:60],
                      "hot_value": x.get("hot_value")})
        if len(items) >= limit:
            break
    meta["count"] = len(items)
    if not items:
        meta["status"] = "error: word_list 为空（结构可能变了）"
    return items, meta


def fetch_zhihu_hot(limit: int = 30, timeout: float = 12.0) -> tuple[list[dict], dict]:
    """抓知乎热榜（api.zhihu.com 匿名实测可用；web 端 v3 接口 401 别换过去）。

    返回 ([{word, desc?}], meta)，word=问题全句（知乎的判决往往长在问句里）。"""
    meta: dict = {"source": "zhihu_hot", "status": "ok", "count": 0, "note": ""}
    try:
        resp = httpx.get(_ZHIHU_HOT_URL, headers={"User-Agent": _UA},
                         timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001 降级：原料缺一路不阻塞，调用方如实标注
        meta["status"] = f"error: {type(e).__name__}: {str(e)[:80]}"
        return [], meta
    items: list[dict] = []
    for x in (data.get("data") or []):
        w = str(((x.get("target") or {}).get("title")) or "").strip()
        if not w:
            continue
        items.append({"word": w[:50], "desc": ""})
        if len(items) >= limit:
            break
    meta["count"] = len(items)
    if not items:
        meta["status"] = "error: data 为空（结构可能变了）"
    return items, meta


def fetch_tech_news(per_source: int = 8, fresh_hours: int = 48,
                    timeout: float = 12.0) -> tuple[list[dict], dict]:
    """抓国内科技/AI 资讯（爱范儿/IT之家/少数派/雷峰网 RSS），只留近 fresh_hours 小时的条目。

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
        meta["status"] = "error: 四个源都没有可用条目"
    return items, meta
