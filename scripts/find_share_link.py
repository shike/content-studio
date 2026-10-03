#!/usr/bin/env python3
"""从竞对官网/落地页挖抖音分享短链（v.douyin.com）或 sec_uid。
用法：python3 scripts/find_share_link.py <官网URL> [更多URL...]
输出：找到的分享链 / sec_uid / 视频 id，可直接用于同行监控添加。
"""
import re
import sys

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

for url in sys.argv[1:]:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=30, follow_redirects=True)
    except Exception as e:
        print(f"{url}: 抓取失败 {type(e).__name__}")
        continue
    text = r.text
    shares = re.findall(r"https?://v\.douyin\.com/[A-Za-z0-9]+", text)
    users = re.findall(r"share/user/([A-Za-z0-9_-]{20,})", text)
    vids = re.findall(r"douyin\.com/video/(\d{15,})", text)
    print(f"== {url}")
    if shares:
        print("  分享短链:", list(dict.fromkeys(shares)))
    if users:
        print("  sec_uid:", list(dict.fromkeys(users)))
    if vids:
        print("  视频 id:", list(dict.fromkeys(vids))[:3])
    if not (shares or users or vids):
        print("  未找到抖音线索")
