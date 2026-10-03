"""渲染每个监控账号的分享主页，取简介+近期作品标题（只读，用于精准度判据）。"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def main():
    accounts = json.load(sys.stdin)
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=UA, locale="zh-CN",
                                  viewport={"width": 420, "height": 900})
        for acc in accounts:
            page = ctx.new_page()
            info = {"name": acc["name"], "url": acc["url"], "signature": "", "videos": []}
            try:
                page.goto(acc["url"], timeout=40000, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                # 分享页标题即昵称；meta description 常含简介
                info["page_title"] = page.title()[:50]
                desc = page.locator('meta[name="description"]').get_attribute("content") or ""
                info["signature"] = desc[:120]
                # 近期作品标题：分享页 DOM 里的 li/p 描述文本
                texts = page.eval_on_selector_all(
                    "[class*='video'] span, [class*='desc'], p", "els => els.map(e => e.textContent.trim())")
                vids = [t for t in texts if 8 < len(t) < 60 and not t.isdigit()]
                info["videos"] = list(dict.fromkeys(vids))[:8]
            except Exception as e:  # noqa: BLE001
                info["error"] = str(e)[:80]
            print(f"== {info['name']} | {info.get('page_title','')} | {info['signature']}", flush=True)
            for v in info["videos"][:6]:
                print(f"   · {v}", flush=True)
            out.append(info)
            page.close()
        browser.close()
    with open("/tmp/account_audit.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
