"""同城 HL 线（组局/沙龙发现）：预算化搜索矩阵 + 账号分类取证。

2026-11-16 C 阶段从 service.py 拆出（只挪不改行为）。执行器 hl_search_matrix
经本模块导入注册（watch/service.py facade 再导出，注册链不断）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from urllib.parse import quote

from sqlmodel import Session, select

from ...browser import launch
from ...db import engine
from ...jobs.runner import register_executor
from ...llm import gateway
from ...models import Source


def _harvest_budget(key: str, default: int) -> int:
    """战役模式预算覆盖：app_settings.harvest_hl_search / harvest_hl_verify 可临时调大
    （挖号冲刺期用），不设或非法回落默认小预算（夜间常态挖不动太快会撞限速）。"""
    from ...models import AppSetting
    k = f"harvest_{key}"
    with Session(engine) as s:
        row = s.get(AppSetting, k)
    try:
        v = int(str(row.value)) if row is not None else default
    except (TypeError, ValueError):
        v = default
    return max(1, min(v, 200))


_HL_CITIES = ["苏州", "上海", "杭州", "南京", "无锡", "宁波", "温州", "常州", "南通",
              "嘉兴", "绍兴", "湖州", "金华", "台州", "昆山", "吴江"]


_HL_KEYWORDS = ["线下沙龙", "组局", "AI沙龙", "同城聚会", "主理人", "AI活动", "创业沙龙",
                "读书会", "女性创业", "破圈", "私董会", "资源对接", "AI互助", "同城创业",
                "AI圈子", "饭局"]


_HL_SEARCH_BUDGET = 12      # 每晚搜索数（16城×16词=256 组合 ≈21 晚轮完，账号会长新）


_HL_VERIFY_BUDGET = 25      # 每晚取证作者数


_HL_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1")


_HL_ACT = ["沙龙", "组局", "聚会", "线下", "圈子", "社群", "主理人", "读书会", "共创",
           "同城", "活动", "交流"]


_HL_SIGNUP = ["报名", "➕", "☎", "电话", "微信", "加V", "wx", "私信", "加我", "领取", "留电", "VX"]


_HL_TOPIC = ["AI", "Ai", "ai", "人工智能", "创业", "副业", "成长", "IP", "赚钱", "生意",
             "财商", "认知", "链接", "资源", "搞钱", "自由职业", "出海"]


_HL_NEG = ["金价", "美业", "护肤", "探店", "好物", "穿搭", "美食", "带货", "橱窗",
           "餐饮", "婚纱", "装修", "驾校", "海鲜", "黄金"]


def _hl_setting(key: str, default: list[str]) -> list[str]:
    from ...models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, key)
    try:
        v = json.loads(row.value) if row is not None and row.value else default
        return v if isinstance(v, list) and v else default
    except Exception:  # noqa: BLE001
        return default


def _hl_get_cursor() -> list[str]:
    from ...models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, "hl_search_cursor")
    try:
        v = json.loads(row.value) if row is not None and row.value else []
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def _hl_save_cursor(done: list[str]) -> None:
    from ...models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, "hl_search_cursor")
        val = json.dumps(done, ensure_ascii=False)
        if row is None:
            s.add(AppSetting(key="hl_search_cursor", value=val))
        else:
            row.value = val
            s.add(row)
        s.commit()


def _hl_classify(nick: str, sig: str, descs: list[str]):
    text_all = f"{nick} {sig} {' '.join(descs)}"
    if any(w in text_all for w in _HL_NEG):
        return None
    act = [w for w in _HL_ACT if w in text_all]
    signup = [w for w in _HL_SIGNUP if w in sig]
    topic = [w for w in _HL_TOPIC if w in text_all]
    if act and signup and topic:
        return "A"
    if len(act) >= 2 and topic:
        return "B"
    if act and topic:
        return "B"
    return None


def _hl_crawl_sync(ctx, done_queries: list[str], cities: list[str], keywords: list[str],
                   n_search: int, n_verify: int) -> dict:
    """同步爬取体（executor 在线程池里调用）：搜索矩阵 → 逐号取证 → 分类。"""
    from playwright.sync_api import sync_playwright

    matrix = [f"{c} {k}" for c in cities for k in keywords]
    todo = [m for m in matrix if m not in done_queries]
    if not todo:  # 一轮走完：重置游标，账号会长新
        done_queries = []
        todo = matrix[:]
    batch, todo = todo[:n_search], todo[n_search:]

    authors: dict[str, dict] = {}
    searched = []
    with sync_playwright() as p:
        browser = launch(p)
        ctxm = browser.new_context(user_agent=_HL_MOBILE_UA, locale="zh-CN",
                                   viewport={"width": 390, "height": 844}, is_mobile=True)
        page = ctxm.new_page()

        def walk(o, bag):
            if isinstance(o, dict):
                sec = o.get("sec_uid")
                if sec:
                    prev = bag.get(sec, {})
                    bag[sec] = {"nick": (o.get("nickname") or prev.get("nick") or "")[:24],
                                "signature": (o.get("signature") or prev.get("signature") or "")[:100]}
                for v in o.values():
                    walk(v, bag)
            elif isinstance(o, list):
                for v in o:
                    walk(v, bag)

        def on_resp(r):
            u = r.url
            if ("search" in u and "aweme" in u) or "general/search" in u \
                    or "search/item" in u or "search/user" in u:
                try:
                    body = r.text()
                    if body.strip().startswith("{"):
                        walk(json.loads(body), authors)
                except Exception:
                    pass

        page.on("response", on_resp)

        def _routerdata_fallback(pg) -> None:
            """页面没发 search XHR 时读 HTML 内嵌 _ROUTER_DATA 兜底（downloader 同款）。"""
            try:
                m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>",
                              pg.content(), re.S)
                if m:
                    walk(json.loads(m.group(1)), authors)
            except Exception:  # noqa: BLE001
                pass

        for m in batch:
            try:
                page.goto(f"https://www.douyin.com/search/{quote(m)}", timeout=30000,
                          wait_until="domcontentloaded")
                # 2026-11-13 取证升级：本路线并非登录墙（双出口实跑：标题正常、无验证码、
                # 无登录标记，homeproxy 出口截到 sec_uid），真正的病是固定 6s 窗——数据
                # XHR 到达慢且抖（同跑 direct 0 个 / 隧道 5 个），是「12s 窗」同类事故。
                # 升级为 60s 轮询（本查询对作者池有增量即走）+ _ROUTER_DATA 兜底
                q_mark = len(authors)
                end = time.time() + 60
                while time.time() < end and len(authors) == q_mark:
                    page.wait_for_timeout(2000)
                try:
                    tab = page.locator("text=用户").first
                    if tab.count() > 0:
                        tab.click(timeout=2000)
                        page.wait_for_timeout(3500)
                except Exception:
                    pass
                for _ in range(2):
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                if len(authors) == q_mark:
                    _routerdata_fallback(page)
                searched.append(m)
                ctx.set_progress(int(50 * len(searched) / max(len(batch), 1)),
                                 f"搜索 {len(searched)}/{len(batch)}：{m}（作者池 {len(authors)}）")
            except Exception as e:  # noqa: BLE001 单查询失败不断轮
                ctx.set_progress(10, f"搜索失败 {m}：{type(e).__name__}")
            time.sleep(random.uniform(3.5, 5.0))
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass

        # ---- 逐号取证 ----
        verified: list[dict] = []

        def on_w(r, rec):
            if "aweme/post" in r.url and "/web/api/v2/" in r.url:
                try:
                    d = r.json()
                    for a in (d.get("aweme_list") or [])[:15]:
                        dsc = (a.get("desc") or "").strip()[:60]
                        if dsc:
                            rec["works"].append(dsc)
                except Exception:
                    pass

        for i, sec in enumerate(list(authors.keys())[:n_verify]):
            rec = {"sec": sec, "nick": authors[sec].get("nick", ""), "signature": "", "works": []}

            def on_w2(r, rec=rec):
                on_w(r, rec)

            page.on("response", on_w2)
            try:
                page.goto(f"https://www.douyin.com/share/user/{sec}", timeout=30000,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                head = page.evaluate("() => document.body.innerText.slice(0, 320)")
                m2 = re.search(r"([\u4e00-\u9fa5A-Za-z0-9_·]{2,24})\n抖音号\s*([^\n]+)\n([^\n]+)", head)
                if m2:
                    rec["nick"] = rec["nick"] or m2.group(1)
                    rec["signature"] = m2.group(3)[:100]
                for _ in range(2):
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1800)
                verified.append(rec)
            except Exception:
                pass
            finally:
                try:
                    page.remove_listener("response", on_w2)
                except Exception:
                    pass
            ctx.set_progress(50 + int(45 * (i + 1) / max(n_verify, 1)),
                             f"取证 {i + 1}/{min(n_verify, len(authors))}（候选 {len(verified)}）")
            time.sleep(random.uniform(2.2, 3.5))
        browser.close()
    return {"searched": searched, "todo_left": todo, "done_queries": done_queries,
            "authors": authors, "verified": verified}


@register_executor("material_hl_search")
async def material_hl_search(ctx, payload: dict) -> dict:
    """同城组局号搜索矩阵挖掘（每晚调度）：城市×关键词搜索 → 作者池 → 取证 →
    四要素筛 → LLM 分级 → 判 A/B 入候选表（keyword=搜索矩阵，人工定夺）。"""
    cities = _hl_setting("hl_search_cities", _HL_CITIES)
    keywords = _hl_setting("hl_search_keywords", _HL_KEYWORDS)
    n_search = int(_harvest_budget("hl_search", _HL_SEARCH_BUDGET))
    n_verify = min(_harvest_budget("hl_verify", _HL_VERIFY_BUDGET), 60)

    with Session(engine) as s:
        known = {c.ext_id for c in s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "peer_account")).all()  # type: ignore[attr-defined]
            if c.ext_id}

    done_queries = _hl_get_cursor()
    result = await asyncio.to_thread(_hl_crawl_sync, ctx, done_queries, cities,
                                     keywords, n_search, n_verify)
    searched = result["searched"]
    _hl_save_cursor(result["done_queries"] + searched)

    notes: list[str] = []
    cands = []
    for rec in result["verified"]:
        if rec["sec"] in known:
            continue
        tier = _hl_classify(rec["nick"], rec["signature"], rec["works"])
        if tier:
            cands.append({**rec, "tier": tier})
    if not cands:
        ctx.set_progress(100, f"搜索 {len(searched)}，作者池 {len(result['authors'])}，"
                              f"取证 {len(result['verified'])}，无新候选")
        return {"searches": len(searched), "secs": len(result["authors"]),
                "verified": len(result["verified"]), "inserted": 0, "notes": notes}

    # ---- LLM 分级（暖暖标准）----
    batch = [{"id": i + 1, "昵称": c["nick"], "签名": c["signature"],
              "作品": " / ".join(c["works"][:3])} for i, c in enumerate(cands)]
    sys_prompt = (
        "你是「同城组局获客号」鉴定员。目标物种（标尺=「暖暖AI赋能」）：在抖音组织/推广线下同城活动"
        "（AI/创业/成长/生意主题的沙龙、组局、社群）获客的个人主理人号——同城标签、活动词、报名或加微入口，"
        "小号也无妨。判「强」：江浙沪城市词+活动词+主题命中+获客意图；判「中」：江浙沪+活动词+主题但证据较弱；"
        "判「否」：实体商家卖货、机构公司号、纯生活号、外地号、搭子喝酒等无关号。"
        '输出 JSON：{"ranks":[{"id":1,"tier":"强|中|否","why":"≤14字"}]}')
    data = await gateway.complete_json(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
        max_tokens=4000, thinking="disabled")
    jmap = {r.get("id"): r for r in (data.get("ranks") or []) if isinstance(r, dict)}

    inserted = 0
    strong = mid = rejected = 0
    with Session(engine) as s:
        known = {c.ext_id for c in s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "peer_account")).all()  # type: ignore[attr-defined]
            if c.ext_id}
        for i, c in enumerate(cands):
            j = jmap.get(i + 1)
            if not isinstance(j, dict):
                continue
            tier = str(j.get("tier") or "")
            why = str(j.get("why") or "")[:40]
            if tier not in ("强", "中") or c["sec"] in known:
                rejected += 1
                continue
            s.add(Source(
                tenant_id=1, kind="peer_account", platform="douyin",
                name=c["nick"][:60], ext_id=c["sec"], status="candidate",
                url=f"https://www.iesdouyin.com/share/user/{c['sec']}",
                handle=c["nick"][:60],
                config={"keyword": "搜索矩阵", "direction": "同城组局",
                        "signature": c["signature"][:100],
                        "account_type": "leadgen",
                        "pure_reason": f"搜索矩阵·LLM判{tier} · {why}"[:90],
                        "stats": {"works": len(c.get("works", []))}}))
            inserted += 1
            if tier == "强":
                strong += 1
            else:
                mid += 1
            notes.append(f"LLM判{tier}：{c['nick']} · {why}")
        s.commit()
    ctx.set_progress(100, f"搜索 {len(searched)}，取证 {len(result['verified'])}，"
                          f"LLM 入表 {inserted}（强{strong} 中{mid} 否{rejected}）")
    return {"searches": len(searched), "secs": len(result["authors"]),
            "verified": len(result["verified"]), "inserted": inserted,
            "strong": strong, "mid": mid, "rejected": rejected, "notes": notes}
