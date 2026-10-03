"""免登录 话题挖矿：拉话题下视频列表，按昵称对出目标作者的 sec_uid。"""
import json
import sys
from collections import OrderedDict

from playwright.sync_api import sync_playwright

MUA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

# 已知话题 id（从视频 detail 的 text_extra 提取；真实清单属运营资产不入库，示例格式）
CH_IDS = [
    ("示例话题", 0000000000000000000),
]

TARGETS = ["待填-目标作者昵称"]


def main():
    authors: OrderedDict = OrderedDict()  # sec_uid -> {nickname, signature, videos:[(ch, desc)]}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        ctx = browser.new_context(user_agent=MUA, locale="zh-CN",
                                  viewport={"width": 420, "height": 900},
                                  is_mobile=True, has_touch=True)
        page = ctx.new_page()
        for ch_name, ch_id in CH_IDS:
            cursor = 0
            got = 0
            for _ in range(10):  # 每话题最多 10 页 × 20 条
                url = (f"https://m.douyin.com/web/api/v2/challenge/aweme/"
                       f"?ch_id={ch_id}&count=20&cursor={cursor}")
                try:
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    body = page.inner_text("body")
                    data = json.loads(body)
                except Exception as e:  # noqa: BLE001
                    print(f"[{ch_name}] 第{cursor}页失败: {str(e)[:80]}", flush=True)
                    break
                items = data.get("aweme_list") or []
                if not items:
                    break
                for it in items:
                    a = it.get("author") or {}
                    suid = a.get("sec_uid")
                    if not suid:
                        continue
                    rec = authors.setdefault(suid, {
                        "nickname": a.get("nickname", ""),
                        "signature": (a.get("signature") or "")[:90],
                        "videos": [],
                    })
                    if len(rec["videos"]) < 4:
                        rec["videos"].append(f"{ch_name}:{(it.get('desc') or '')[:36]}")
                got += len(items)
                cursor += len(items)
            print(f"[{ch_name}] 拉到 {got} 条视频", flush=True)
        browser.close()

    print(f"\n共 {len(authors)} 个作者：")
    hits = []
    for suid, rec in authors.items():
        mark = ""
        for t in TARGETS:
            if t in rec["nickname"]:
                mark = "  <== 目标"
                hits.append((suid, rec))
                break
        print(f"\n{rec['nickname']}{mark}\n  简介: {rec['signature']}")
        for v in rec["videos"]:
            print(f"   · {v}")
    with open("/tmp/challenge_authors.json", "w") as f:
        json.dump(list(authors.values()), f, ensure_ascii=False, indent=1)
    print(f"\n命中目标 {len(hits)} 个")


if __name__ == "__main__":
    sys.exit(main())
