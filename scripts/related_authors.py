"""从精准种草账号的视频页捞推荐流里的其他作者（只读）。

合集/推荐流会混入算法判定的相似作者——对齐"讲FDE、企业AI落地"的同类号。
"""
import json
import sys
from collections import OrderedDict

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SEEDS = [
    # 种子=对标作者任一视频链接（真实种子属运营资产不入库，此处仅示例格式）
    ("示例-对标作者", "https://www.douyin.com/video/0000000000000000000"),
]

KNOWN = {
    # 已核对过的 sec_uid → 昵称（真实映射属运营资产不入库，此处仅示例格式）
    "MS4wLjABAAAAxxxxxxxxxxxxxxxxxxxxxxxxxxx": "示例-对标作者",



def walk(o, out):
    if isinstance(o, dict):
        a = o.get("author")
        if isinstance(a, dict) and a.get("sec_uid"):
            out[a["sec_uid"]] = {
                "nickname": a.get("nickname", ""),
                "sec_uid": a["sec_uid"],
                "signature": (a.get("signature") or "")[:100],
            }
        for v in o.values():
            walk(v, out)
    elif isinstance(o, list):
        for v in o:
            walk(v, out)


def main():
    authors: OrderedDict = OrderedDict()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1280, "height": 900})
        for label, url in SEEDS:
            found: dict = {}
            page = ctx.new_page()

            def on_response(resp):
                try:
                    u = resp.url
                    if "mix/aweme" in u or "aweme/detail" in u or "relation/related" in u:
                        walk(resp.json(), found)
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                for _ in range(4):
                    page.mouse.wheel(0, 3200)
                    page.wait_for_timeout(2200)
            except Exception as e:  # noqa: BLE001
                print(f"[{label}] 失败: {str(e)[:80]}", flush=True)
            new = {k: v for k, v in found.items() if k not in KNOWN}
            print(f"[{label}] 流内作者 {len(found)} 个，新面孔 {len(new)} 个", flush=True)
            for k, v in new.items():
                authors.setdefault(k, v)
            page.close()
        browser.close()
    for a in authors.values():
        print(f"{a['nickname']} | {a['sec_uid']} | {a['signature']}", flush=True)
    with open("/tmp/related_authors.json", "w") as f:
        json.dump(list(authors.values()), f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
