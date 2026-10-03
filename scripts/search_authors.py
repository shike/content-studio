"""抖音搜索流捞作者：按关键词 harvest 视频搜索结果里的作者（只读）。"""
import json
import sys
from collections import OrderedDict

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

KEYWORDS = ["FDE", "企业AI落地", "FDE 培训", "AI落地顾问", "AI交付工程师"]


def walk_authors(o, out):
    if isinstance(o, dict):
        a = o.get("author")
        if isinstance(a, dict) and a.get("sec_uid"):
            out[a["sec_uid"]] = {
                "nickname": a.get("nickname", ""),
                "sec_uid": a["sec_uid"],
                "signature": (a.get("signature") or "")[:100],
            }
        for v in o.values():
            walk_authors(v, out)
    elif isinstance(o, list):
        for v in o:
            walk_authors(v, out)


def main():
    authors: OrderedDict = OrderedDict()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1280, "height": 900})
        for kw in KEYWORDS:
            found: dict = {}
            page = ctx.new_page()

            def on_response(resp):
                try:
                    if "search/item" in resp.url or "general/search" in resp.url:
                        walk_authors(resp.json(), found)
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            try:
                page.goto(f"https://www.douyin.com/search/{kw}?type=video",
                          timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                for _ in range(3):
                    page.mouse.wheel(0, 3200)
                    page.wait_for_timeout(2000)
            except Exception as e:  # noqa: BLE001
                print(f"[{kw}] 失败: {str(e)[:80]}", flush=True)
            print(f"[{kw}] 捞到 {len(found)} 个作者", flush=True)
            for k, v in found.items():
                authors.setdefault(k, v)
            page.close()
        browser.close()
    for a in authors.values():
        print(f"{a['nickname']} | {a['sec_uid']} | {a['signature']}", flush=True)
    with open("/tmp/search_authors.json", "w") as f:
        json.dump(list(authors.values()), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
