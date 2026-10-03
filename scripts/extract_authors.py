"""从种子视频链接反查真实作者（只读，不改库）。免登录：拦截页面自身的 aweme detail 接口。"""
import json
import sys

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SEEDS = [
    # 种子=对标作者任一视频的分享链接（贴真实链接即可反查作者与相似作者；
    # 真实种子属运营资产不入库，此处仅示例格式）
    ("示例-对标视频", "https://www.douyin.com/video/0000000000000000000"),
]


def find_author(o, out):
    if isinstance(o, dict):
        a = o.get("author")
        if isinstance(a, dict) and a.get("sec_uid") and not out:
            out.append({
                "nickname": a.get("nickname", ""),
                "sec_uid": a.get("sec_uid", ""),
                "signature": (a.get("signature") or "")[:80],
            })
        for v in o.values():
            find_author(v, out)
    elif isinstance(o, list):
        for v in o:
            find_author(v, out)


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 1280, "height": 900})
        for label, url in SEEDS:
            detail = {}
            page = ctx.new_page()

            def on_response(resp, detail=detail):
                try:
                    if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                        detail.update(resp.json())
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            try:
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
            except Exception as e:  # noqa: BLE001
                print(f"{label} | 失败: {str(e)[:80]}", flush=True)
                page.close()
                continue
            found: list = []
            find_author(detail, found)
            if found:
                a = found[0]
                line = f"{label} | {a['nickname']} | {a['sec_uid'][:40]} | {a['signature']}"
                results.append(a | {"label": label})
            else:
                line = f"{label} | 未取到作者"
            print(line, flush=True)
            page.close()
        browser.close()
    with open("/tmp/seed_authors.json", "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
