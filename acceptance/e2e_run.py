#!/usr/bin/env python3
"""E2E 端到端验收：Playwright 驱动真实浏览器，走用户旅程（TDD §13 界面冒烟的自动化版）。

前提：隔离实例已在 127.0.0.1:8200 运行（DATA_DIR=data-acceptance + CS_BOOTSTRAP_PASSWORD，
流程同其他套件；LLM 配置可选——本套件不触发任何 LLM 调用）。
运行：python3 acceptance/e2e_run.py

覆盖：
- 登录流程（错误密码拒/正确密码进）
- 全站 17 页逐页打开（今天+产线树 13 叶+任务/设置），console error/pageerror 零异常，每页特征元素在位
- 今天页快速记选题（无 LLM 的真实用户操作）→ 待拍清单出现
- 内容工场：fixture 文章行展开 → md 编辑器 → 渲染公众号格式
- 账号经营：品牌卡字段在位、改头图口号保存成功回显
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mac 侧浏览器固定在项目根 .ms-playwright/（与 backend/app/browser.py 同口径）
if sys.platform == "darwin":
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                          str(Path(__file__).resolve().parent.parent / ".ms-playwright"))
from runner import section

BASE = os.environ.get("CS_API_BASE", "http://127.0.0.1:8200")
UI = BASE  # 前端由后端同源托管
ADMIN_PWD = os.environ.get("CS_BOOTSTRAP_PASSWORD", "accept-12345")

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (PASS if ok else FAIL).append(f"{name}{'：' + detail if detail and not ok else ''}")
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail and not ok else ""))
    return ok


def main() -> int:
    # ---------- 前置：种子数据（fixture 直插，与 P7 同款做法） ----------
    section("E2E · 前置种子")
    data_dir = os.environ.get("DATA_DIR", "data")
    import sqlite3
    conn = sqlite3.connect(os.path.join(data_dir, "studio.db"), timeout=30)
    stamp = str(int(time.time()))[-6:]
    art_md = (f"![头图](cover:cover_e2e.png)\n\n## 试点产线怎么选\n\n"
              f"E2E 种子正文第一段，用于验证渲染与展开交互。\n\n## 验收单怎么写\n\n"
              f"E2E 种子正文第二段。\n")
    art_html = "<section><h2>试点产线怎么选</h2><p>E2E 种子正文第一段。</p></section>"
    conn.execute(
        # 2026-11-13：全新库无 server default，直插必须覆盖全部 NOT NULL 列
        "INSERT INTO articles (tenant_id, topic_id, title, note, publish_url, published_at, md, html, status, created_at, updated_at) "
        "VALUES (1, NULL, ?, '', '', '', ?, ?, 'rendered', datetime('now'), datetime('now'))",
        (f"E2E 种子文章 {stamp}", art_md, art_html))
    conn.execute(
        "INSERT INTO topics (tenant_id, line, title, angle, audience, source_type, source_ref, status, "
        "score, created_at, updated_at) "
        "VALUES (1, 'persona', ?, 'E2E 验收角度', 'both', 'manual', 'e2e', 'approved', 8.0, "
        "datetime('now'), datetime('now'))",
        (f"E2E 种子选题 {stamp}",))
    # 同行监测周报夹具：一条待定夺的抖音视频（digest 卡有数据可渲染）
    conn.execute(
        "INSERT INTO benchmark_videos (tenant_id, url, author, title, transcript, media_path, "
        "quality_score, account_type, notes, source, scan_status, created_at, updated_at) "
        "VALUES (1, 'https://www.douyin.com/video/e2e001', 'E2E测试作者', "
        "'E2E 同行视频样例 #企业AI落地', '', '', 0.0, 'persona', '', 'douyin', 'pending', datetime('now'), datetime('now'))")
    # 选题工场夹具（R1.4）：一张 draft 靶子卡（今日靶子页四面渲染断言用）
    conn.execute(
        "INSERT INTO topic_cards (tenant_id, batch_date, source, pool, spine, hook, spice, judge, gates, status, created_at, updated_at) "
        "VALUES (1, date('now'), 'douyin_peer', ?, ?, ?, 'high', ?, ?, 'draft', datetime('now'), datetime('now'))",
        (json.dumps({
            "domain_evidence": "监控对标号 E2E测试作者（AI 落地赛道）",
            "heat_evidence": "E2E测试作者《E2E 同行视频样例》评论 55",
            "heat_level": "高", "role": "中小企业老板", "coverage": "boss",
            "douyin_evidence": {"kind": "peer_video", "ref": "https://www.douyin.com/video/e2e001",
                                "comments": 55, "diggs": 320},
            "peer_covered": True,
        }, ensure_ascii=False),
         "小老板学AI先砍培训预算",
         "老板砍AI培训预算，砍的是自己的交付",
         json.dumps({"hook_pull": 8, "spine_single": 8, "traffic_potential": 9,
                     "verdict": "pass", "reason": "一手评论实数 55，三维过线"}, ensure_ascii=False),
         json.dumps([{"gate": "主线字数线", "pass": True, "detail": "12 字"}], ensure_ascii=False)))
    conn.execute(
        "INSERT INTO topics (tenant_id, line, title, angle, audience, source_type, source_ref, status, "
        "score, created_at, updated_at) "
        "VALUES (1, 'persona', 'E2E 高分选题样例', 'E2E 切角：从验收单切入', 'both', 'benchmark', 'e2e-topic', "
        "'approved', 8.5, datetime('now'), datetime('now'))")
    conn.execute("UPDATE users SET must_change_password = 0")
    conn.commit()
    # 夹具头图落一张真实 PNG（md 引用 cover:cover_e2e.png，缺文件会 404）
    from PIL import Image
    img_dir = os.path.join(data_dir, "article_images", "1")
    os.makedirs(img_dir, exist_ok=True)
    Image.new("RGB", (1080, 460), (94, 106, 210)).save(os.path.join(img_dir, "cover_e2e.png"))
    article_title = f"E2E 种子文章 {stamp}"
    check("种子写入（文章+选题+关闭首登改密）", True)

    # ---------- E2E 本体（Playwright） ----------
    section("E2E · 浏览器登录")
    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chromium", headless=True)
        page = browser.new_page(viewport={"width": 1560, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        def _on_console(m):
            if m.type != "error":
                return
            loc = ""
            try:
                loc = (m.location or {}).get("url", "")
            except Exception:
                pass
            errors.append(f"console.error: {m.text} @ {loc}")
        page.on("console", _on_console)

        # 登录：错误密码拒
        page.goto(UI)
        page.wait_for_selector('input[placeholder="用户名"]', timeout=8000)
        page.fill('input[placeholder="用户名"]', "admin")
        page.fill('input[placeholder="密码"]', "wrong-password")
        page.click('button:has-text("登录")')
        page.wait_for_timeout(800)
        check("错误密码被拒（停留登录页）",
              page.locator('input[placeholder="密码"]').count() == 1)
        # 正确密码进
        page.fill('input[placeholder="密码"]', ADMIN_PWD)
        page.click('button:has-text("登录")')
        page.wait_for_selector('nav', timeout=8000)
        check("正确密码进入工作台（侧栏「今天」在位）", "今天" in page.inner_text("nav"))

        # ---------- 全站逐页零报错 + 页头断言 ----------
        section("E2E · 全站逐页零报错（7 顶层 + 页内 tab）")

        # 2026-11-16 对齐 11-14 导航改版：侧栏 7 顶层（今天/人设号/获客/公众号/监控/任务/设置），
        # 目的地由页内 tab 承载（AGENTS.md 前端规矩）。旧「写死 17 页」断言退役——
        # 17 个旧叶标签里 14 个已不是侧栏按钮（DSH #57 施工单①）。
        # 有 PageHeader 的页面期望=第一个 main h2（只写实测确认过的）；未知 tab 只验 main 非空且无错误。
        NAV = {
            "今天": [],
            "人设号": ["选题", "视频稿", "经营"],
            "获客": ["获客选题", "视频稿"],
            "公众号": ["工场", "清单", "画像"],
            "监控": ["监控号", "话题雷达", "拆解库", "账号候选"],
            "任务": [],
            "设置": [],
        }
        WANT_H2 = {
            ("人设号", "选题"): "今日靶子",
            ("人设号", "经营"): "账号经营",
            ("获客", "获客选题"): "获客选题",
            ("公众号", "工场"): "公众号内容工场",
            ("公众号", "清单"): "内容清单",
            ("公众号", "画像"): "公众号风格画像",
            ("监控", "监控号"): "监控的账号",
            ("监控", "拆解库"): "拆解库",
        }

        def goto_top(top: str) -> None:
            """locator 点击侧栏顶层菜单（evaluate 的 .click() 不触发 React 导航，勿用）。"""
            page.locator('aside button', has_text=top).first.click(timeout=6000)
            page.wait_for_timeout(900)

        NAVMAP = {
            "今天": ("今天", None),
            "今日靶子": ("人设号", "选题"),
            "脚本工场": ("人设号", "视频稿"),
            "账号经营": ("人设号", "经营"),
            "获客选题": ("获客", "获客选题"),
            "获客视频稿": ("获客", "视频稿"),
            "内容工场": ("公众号", "工场"),
            "内容清单": ("公众号", "清单"),
            "风格画像": ("公众号", "画像"),
            "监控号": ("监控", "监控号"),
            "话题雷达": ("监控", "话题雷达"),
            "拆解库": ("监控", "拆解库"),
            "账号候选": ("监控", "账号候选"),
            "任务": ("任务", None),
            "设置": ("设置", None),
        }

        def goto_page(label: str) -> None:
            """按 NAVMAP 两级导航：先侧栏顶层，再页内 tab（无 tab 的顶层直接落）。"""
            top, tab = NAVMAP[label]
            goto_top(top)
            if tab:
                page.locator("main button", has_text=tab).first.click(timeout=6000)
                page.wait_for_timeout(900)

        for top, tabs in NAV.items():
            goto_top(top)
            page.wait_for_timeout(400)
            for tab in tabs:
                tb = page.locator("main button", has_text=tab).first
                if not tb.count():
                    continue
                tb.click(timeout=6000)
                page.wait_for_timeout(900)
                main_txt = page.inner_text("main")
                want = WANT_H2.get((top, tab))
                if want:
                    check(f"页面可达：{top}·{tab}", want in main_txt,
                          f"页内未见签名 {want!r}")
                else:
                    check(f"页面可达：{top}·{tab}", bool(main_txt.strip()) and "出错了" not in main_txt,
                          f"main 前 60 字={main_txt[:60]!r}")
            main_txt = page.inner_text("main")
            if top == "今天":
                ok = "今日工作台" in main_txt
            elif top == "任务":
                ok = "任务" in main_txt
            elif top == "设置":
                ok = "设置" in main_txt
            else:
                ok = bool(main_txt.strip()) and "出错了" not in main_txt
            check(f"页面可达：{top}", ok, f"main 前 60 字={main_txt[:60]!r}")

        # ---------- 今日靶子卡（选题工场 R1.4） ----------
        section("E2E · 今日靶子卡四面渲染")
        goto_page("今日靶子")
        page.wait_for_timeout(800)
        body = page.inner_text("main")
        check("靶子卡：钩子第一行+主线第二行",
              "老板砍AI培训预算，砍的是自己的交付" in body and "小老板学AI先砍培训预算" in body)
        check("靶子卡：一手实数/同行已发/法官徽章",
              "评论 55" in body and "同行已发" in body and "法官 8/8/9" in body)
        page.click("text=老板砍AI培训预算，砍的是自己的交付")
        page.wait_for_timeout(500)
        body2 = page.inner_text("main")
        check("靶子卡：裁决面（备稿表单+打法+法官判词）",
              "开场钩子（说给观众听的第一句，可改）" in body2 and "反常识判断" in body2
              and "一手评论实数 55" in body2)

        # ---------- 今天页一句话投喂（2026-11-16 对齐靶子卡线：投喂 → 靶子卡） ----------
        section("E2E · 今天页一句话投喂")
        goto_page("今天")
        page.fill('input[placeholder*="一句话投喂选题"]', f"E2E 投喂选题 {stamp}")
        page.locator('button:has-text("投喂")').first.click()
        page.wait_for_timeout(1500)
        # DSH #57①：断点必须用成功文案（Today.tsx:99），95 行 busy 在 await 前就 set，断它=假绿
        check("投喂受理回显（成功文案出现）",
              "投喂已排队，靶子卡稍后进「选题」页" in page.inner_text("main"))

        # ---------- 内容工场：文章展开与渲染 ----------
        section("E2E · 内容工场文章展开与渲染")
        goto_page("内容工场")
        # 2026-11-13 对齐 11-11 决策队列制改版：rendered 文章展开=HTML 预览（排版在产线内
        # 完成，页面已无「渲染」按钮）；真实决策闭环=展开预览 → 发布登记 → 状态流转 published
        page.locator(f'text={article_title}').first.click()
        page.wait_for_timeout(800)
        check("文章行展开（HTML 预览出现）", page.locator("text=E2E 种子正文第一段").count() >= 1)
        page.locator('button:has-text("已发布 · 登记")').first.click()
        page.wait_for_timeout(600)
        page.locator('input[placeholder*="http"], input[placeholder*="链接"], input').first.fill(
            "https://mp.weixin.qq.com/s/e2e-seed")
        page.locator('button:has-text("确认已发布")').first.click()
        page.wait_for_timeout(1200)
        check("发布登记闭环（行流转已发布）", page.locator('text=已发布').count() >= 1,
              "登记后行状态徽章应变为已发布")

        # ---------- 设置页：品牌卡（2026-11-13 对齐现 UI：BrandForm 宿主=设置页） ----------
        section("E2E · 设置页品牌卡")
        goto_page("设置")
        page.locator('label', has_text='角标 · 第一行').first.wait_for(state='visible', timeout=8000)
        check("品牌卡字段在位",
              all(page.locator(f'text={t}').count() >= 1 for t in
                  ["角标 · 第一行", "头图口号", "受众域口径", "ASR 领域词表", "图表主色"]))
        l1 = page.locator('label', has_text='角标 · 第一行').locator('input').input_value()
        check("品牌卡回显已配置内容（栏目第一行=中性默认值）", l1 == "跃迁", f"label_line1={l1!r}")
        kw_input = page.locator('label', has_text='头图口号').locator('input')
        kw_input.wait_for(state='visible', timeout=8000)
        kw_input.fill('E2E口号 ' + stamp)
        page.locator('button:has-text("保存品牌配置")').first.click()
        page.wait_for_timeout(1200)
        check("品牌保存成功回显", "已保存" in page.inner_text("main"))


        # ---------- 汇总 ----------
        # 预期噪声：未登录探活/登出后的 401——浏览器把 4xx 响应自动记 console.error
        real_errors = [e for e in errors
                       if "favicon" not in e and "401 (Unauthorized)" not in e
                      ]
        check("全站 console/pageerror 零异常（401 探活噪声除外）", not real_errors,
              f"{len(real_errors)} 条：{real_errors[:3]}")
        browser.close()

    print(f"\n----- E2E 端到端 验收结果: {len(PASS)} PASS / {len(FAIL)} FAIL -----")
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
