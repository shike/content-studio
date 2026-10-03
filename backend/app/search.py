"""联网检索：双通道。

- GLM web_search（智谱 API 工具，稳定，需 ZHIPU_API_KEY；模型用免费 flash 档，
  只付搜索费）——auto 模式下有 key 即优先。
- DuckDuckGo HTML（免 key 备胎）：限流狠（连续请求返回 202 挑战页），
  已做缓存+退避；Bing 投毒、百度验证码，均实测不可用。
执行结果（provider/status/refs）如实返回调用方展示，绝不静默吞。
"""
from __future__ import annotations

import json
from typing import Optional
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .settings import settings

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
_CACHE_TTL = 24 * 3600


def search_enabled() -> bool:
    return getattr(settings, "search_enabled", True)


def _cache_path() -> Path:
    p = settings.data_dir / "cache" / "search.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _cache_all() -> dict:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 缓存坏了当空，但留痕
        print(f"[search] 检索缓存读取失败，忽略: {type(e).__name__}: {str(e)[:80]}")
        return {}


def web_search(query: str, max_results: int = 4) -> tuple[list[dict], str, str, str]:
    """返回 (results, status, provider, note)。

    note 非空时表示首选通道失败后回退（例如 GLM 余额不足→DDG），如实透出。
    """
    cache = _cache_all()
    key = query.strip().lower()
    hit = cache.get(key)
    if hit and time.time() - hit["ts"] < _CACHE_TTL:
        return hit["results"], "ok(cache)", hit.get("provider", "cache"), ""

    provider = settings.search_provider
    use_glm = provider == "glm" or (provider == "auto" and settings.zhipu_api_key)
    # GLM 搜索不吃 site:/inurl: 这类引擎限定符（实测恒空/返回无关页），带限定符的查询直接走 DDG
    if use_glm and re.search(r"\b(site:|inurl:|intitle:)", query, re.I):
        use_glm = False
    if use_glm:
        results, status = _glm_search(query, max_results)
        if results:
            _cache_put(cache, key, results, "glm")
            return results, status, "glm", ""
        if provider == "glm":  # 强制 glm 时不回退，如实报错
            return [], status or "error", "glm", ""

    note = f"GLM 通道失败（{status}），已回退 DuckDuckGo" if use_glm else ""
    results, ddg_status = _ddg_search(query, max_results)
    if results:
        _cache_put(cache, key, results, "ddg")
        return results, ddg_status, "ddg", note
    # 境内服务器直连 DDG 不可达（Errno 101）。带 site: 的查询（账号发现等）回退 Bing——
    # 2026-09-28 实测：Bing 可达且返回真实 douyin.com/video 结果（此前"投毒"记载已过期）
    if re.search(r"\b(site:|inurl:|intitle:)", query, re.I):
        bing_results, bing_status = _bing_search(query, max_results)
        if bing_results:
            _cache_put(cache, key, bing_results, "bing")
            note2 = f"DDG 不可达（{ddg_status}），已回退 Bing"
            return bing_results, bing_status, "bing", note2
    return [], ddg_status, "ddg", note


def _cache_put(cache: dict, key: str, results: list, provider: str) -> None:
    cache[key] = {"ts": int(time.time()), "results": results, "provider": provider}
    try:
        _cache_path().write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 写缓存失败不影响结果，留痕
        print(f"[search] 检索缓存写入失败: {type(e).__name__}: {str(e)[:80]}")


def _glm_search(query: str, max_results: int) -> tuple[list[dict], str]:
    """智谱独立搜索 API（/web_search，按次计费，中文质量优于 DDG）。

    走 zhipu_search_base_url（按量端点）；LLM 生成走 coding 端点，互不干扰。"""
    if not settings.zhipu_api_key:
        return [], "no_key"
    base = settings.zhipu_search_base_url.rstrip("/")
    try:
        resp = httpx.post(
            f"{base}/web_search",
            headers={"Authorization": f"Bearer {settings.zhipu_api_key}",
                     "Content-Type": "application/json"},
            json={"search_engine": "search_std",
                  "search_query": query[:400], "count": max(max_results, 3)},
            timeout=30,
        )
        if resp.status_code != 200:
            try:
                err = (resp.json().get("error") or {})
                code, message = err.get("code"), err.get("message", "")
            except Exception:  # noqa: BLE001
                code, message = None, resp.text[:60]
            if code == "1113" or resp.status_code == 429:
                return [], "智谱余额不足（请到 open.bigmodel.cn 充值）"
            if resp.status_code == 401:
                return [], "智谱 key 无效（检查 ZHIPU_API_KEY）"
            return [], f"glm_error {code or resp.status_code}: {message[:60]}"
        results = []
        for it in (resp.json().get("search_result") or [])[:max_results]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            link = str(it.get("link") or it.get("url") or "").strip()
            if title or link:
                results.append({"title": title or link,
                                "snippet": str(it.get("content") or "")[:150],
                                "url": link})
        # 成本台账：web_search 按次计费（search_std 官方价约 ¥0.01/次，估算口径）
        if results:
            try:
                from sqlmodel import Session

                from ..db import engine
                from ..models import LLMCall

                from ..auth import ACTOR

                _actor_tenant, _uid, _role = ACTOR.get()
                with Session(engine) as s:
                    s.add(LLMCall(tenant_id=_actor_tenant, purpose="web_search", model="search_std",
                                  tokens_in=0, tokens_out=0, cost_est=0.01))
                    s.commit()
            except Exception:  # noqa: BLE001 台账失败不影响搜索
                pass
        if not results:
            return [], "glm_empty（未返回搜索来源）"
        return results, "ok"
    except Exception as e:  # noqa: BLE001
        return [], f"glm_error: {str(e)[:80]}"


# 全局 DDG 闸门：跨消费者（选题深研/账号发现等）统一限速。
# 限流是 IP 级惩罚窗且无区分度——与其被动挨罚（全检索瘫痪），
# 不如主动节流：最小间隔 + 每小时真实请求配额（缓存命中不计）。
_DDG_GATE = {"last": 0.0, "window": []}
_DDG_GATE_LOCK = None  # 惰性初始化（线程锁）
_DDG_MIN_INTERVAL = 12.0  # 两次真实请求最小间隔（秒）
_DDG_HOURLY_CAP = 15  # 每小时真实请求上限


def _ddg_gate() -> Optional[str]:
    """None=放行（内部会自动等最小间隔）；str=配额耗尽原因。"""
    import threading
    import time

    global _DDG_GATE_LOCK
    if _DDG_GATE_LOCK is None:
        _DDG_GATE_LOCK = threading.Lock()
    with _DDG_GATE_LOCK:
        now = time.time()
        st = _DDG_GATE
        st["window"] = [t for t in st["window"] if now - t < 3600]
        if len(st["window"]) >= _DDG_HOURLY_CAP:
            return f"本小时 DDG 配额已用尽（{len(st['window'])}/{_DDG_HOURLY_CAP}），下一小时自动恢复"
        wait = st["last"] + _DDG_MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)  # 调用方均在 to_thread 线程里，阻塞无碍事件循环
        st["last"] = time.time()
        st["window"].append(st["last"])
    return None


def _bing_search(query: str, max_results: int) -> tuple[list[dict], str]:
    """Bing 网页搜索（境内可达）。解析 <li class="b_algo"> 里的标题与真实链接。

    2026-09-28 实测：site:douyin.com/video 查询返回真实抖音视频结果（此前"投毒"记载已过期）；
    用途=DDG 不可达（境内服务器 Errno 101）时，带引擎限定符的查询的回退通道。
    """
    try:
        resp = httpx.get("https://www.bing.com/search",
                         params={"q": query, "setlang": "zh-hans"},
                         headers={"User-Agent": _UA,
                                  "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                         timeout=15, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        return [], f"error({type(e).__name__})"
    if resp.status_code != 200:
        return [], f"http_{resp.status_code}"
    results = []
    for m in re.finditer(r'<li class="b_algo".*?<h2><a href="([^"]+)"[^>]*>(.*?)</a></h2>', resp.text, re.S):
        url = m.group(1)
        title = _clean(m.group(2))
        if title and url.startswith("http") and "bing.com" not in urlparse(url).netloc:
            results.append({"title": title, "snippet": "", "url": url})
        if len(results) >= max_results:
            break
    return results, ("ok" if results else "empty")


def _ddg_search(query: str, max_results: int) -> tuple[list[dict], str]:
    """html 端点 1 发 → 202 就换 lite 端点 1 发 → 都封立刻返回。

    限流是 IP 级分钟惩罚窗且 *.duckduckgo.com 共享：短间隔重试只会加深惩罚，
    省着打（全局闸门限速 + 每小时配额）+ 把恢复交给调用方的长周期重试。
    """
    gate = _ddg_gate()
    if gate is not None:
        return [], gate
    # DDG 境内直连不可达（Errno 101，2026-09-28 实测）→ 经住宅出口代理（home_proxy 对
    # duckduckgo.com 走 Mac 的 7890 出海；douyin 流量仍直连家宽，互不影响）
    proxy = getattr(settings, "douyin_proxy", "") or None
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": _UA,
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=15, follow_redirects=True, proxy=proxy,
        )
        if resp.status_code == 200 and "result__a" in resp.text:
            results = _parse_ddg(resp.text)[:max_results]
            if results:
                return results, "ok"
        elif resp.status_code not in (202,):
            time.sleep(2)  # 非 202 的偶发错误才值得原地重试一发
            resp = httpx.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": _UA,
                         "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
                timeout=15, follow_redirects=True, proxy=proxy,
            )
            if resp.status_code == 200 and "result__a" in resp.text:
                results = _parse_ddg(resp.text)[:max_results]
                if results:
                    return results, "ok"
    except Exception:  # noqa: BLE001
        pass
    # html 限流/坏掉 → lite 端点兜底（不同前端不同限流池，2026-09-25 实测可用）
    try:
        resp = httpx.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={"User-Agent": _UA,
                     "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
            timeout=15, follow_redirects=True,
        )
        if resp.status_code == 200:
            results = _parse_ddg_lite(resp.text)[:max_results]
            if results:
                return results, "ok(lite)"
        return [], "rate_limited" if resp.status_code == 202 else "error"
    except Exception:  # noqa: BLE001
        return [], "error"


def _parse_ddg_lite(html: str) -> list[dict]:
    """lite 版结果页：结果链接全部经 //duckduckgo.com/l/?uddg= 跳转，_real_url 负责还原。"""
    results = []
    for m in re.finditer(r'<a rel="nofollow" href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        title = _clean(m.group(2))
        url = _real_url(m.group(1))
        if title and url.startswith("http") \
                and "duckduckgo.com" not in urlparse(url).netloc:
            results.append({"title": title, "snippet": "", "url": url})
    return results


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def _real_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.netloc or ""):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return href


def _parse_ddg(html: str) -> list[dict]:
    results = []
    for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        results.append({"title": _clean(m.group(2)),
                        "snippet": "", "url": _real_url(m.group(1))})
    # 带摘要的完整解析（有块结构时优先）
    blocks = re.findall(
        r'<div class="result results_links.*?(?=<div class="result results_links|<div class="nav-link)',
        html, re.S)
    if blocks:
        results = []
        for block in blocks:
            t = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not t:
                continue
            s = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
            results.append({
                "title": _clean(t.group(2)),
                "snippet": _clean(s.group(1)) if s else "",
                "url": _real_url(t.group(1)),
            })
    return [r for r in results if r["title"]]


def batch_search(queries: list[str], per_query: int = 4) -> tuple[str, dict]:
    """单查询为主（限流纪律）。返回 (参考文本, 执行元数据)。"""
    q = queries[0] if queries else ""
    if not q:
        return "", {"query": "", "refs": 0, "status": "error", "provider": "-"}
    results, status, provider, note = web_search(q, per_query)
    lines = [f"- {r['title']}：{r['snippet'][:120]}（{r['url'][:80]}）"
             for r in results if r["title"]]
    meta = {"query": q, "refs": len(results), "status": status,
            "provider": provider, "note": note}
    return ("\n".join(lines[:8]) if lines else ""), meta
