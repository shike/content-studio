#!/usr/bin/env python3
"""E2E 端到端验收：Playwright 驱动真实浏览器，走用户旅程（AUDIT A3 界面冒烟的自动化版）。

前提：隔离实例已在 127.0.0.1:8200 运行（DATA_DIR=data-acceptance + CS_BOOTSTRAP_PASSWORD，
流程同其他套件；LLM 配置可选——本套件不触发任何 LLM 调用）。
运行：python3 acceptance/e2e_run.py

覆盖：
- 登录流程（错误密码拒/正确密码进）
- 全站 16 页逐页打开（今天+产线树 13 叶+任务/设置），console error/pageerror 零异常，每页特征元素在位
- 今天页快速记选题（无 LLM 的真实用户操作）→ 待拍清单出现
- 内容工场：fixture 文章行展开 → md 编辑器 → 渲染公众号格式
- 账号经营：品牌卡字段在位、改头图口号保存成功回显
"""
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
    topic_title = f"E2E 种子选题 {stamp}"
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
        section("E2E · 全站逐页零报错（17 页）")

        def goto_page(label: str) -> None:
            """locator 点击侧栏菜单（evaluate 的 .click() 不触发 React 导航，勿用）。"""
            page.locator('aside button', has_text=label).first.click(timeout=6000)
            page.wait_for_timeout(900)

        # （侧栏叶按钮, 页头期望）：现行侧栏=今天 + 产线树三组（App.tsx TREE，默认全展开）+ 任务/设置。
        # 2026-11-13 对齐 11-11/11-12 改版：选题库→今日靶子（人设号第一叶）、新增内容清单；
        # 有 PageHeader 的页面期望=第一个 main h2（title 常与侧栏叶不同名，如 内容看板→脚本工场）；
        # 候选两页（DiscoveredAccounts）无页级标题，改验页内签名文本。
        PAGES = [
            ("今天", "今天要拍"),
            ("今日靶子", "今日靶子"),
            ("内容看板", "脚本工场"),
            ("账号经营", "账号经营"),
            ("对标拆解", "拆解库"),
            ("候选定夺", "开始搜索"),
            ("监控清单", "监控的账号"),
            ("获客选题", "选题库"),
            ("脚本工场", "脚本工场"),
            ("纯获客候选", "开始搜索"),
            ("获客监控", "监控的账号"),
            ("打法卡库", "拆解库"),
            ("内容清单", "内容清单"),
            ("内容工场", "公众号内容工场"),
            ("风格画像", "公众号风格画像"),
            ("任务", "任务"),
            ("设置", "设置"),
        ]
        for label, want in PAGES:
            goto_page(label)
            h2 = page.locator("main h2")
            if h2.count():
                got = h2.first.inner_text().strip()
                check(f"页面打开：{label}", got == want, f"页头={got!r} 期望={want!r}")
            else:
                check(f"页面打开：{label}", want in page.inner_text("main"),
                      f"页内未见签名 {want!r}（该页无页级标题）")

        # ---------- 今天页快速记选题 ----------
        section("E2E · 今天页快速记选题")
        goto_page("今天")
        page.fill('input[placeholder*="快速记选题"]', f"E2E 快速记选题 {stamp}")
        page.click('button:has-text("入库")')
        page.wait_for_timeout(1500)
        check("快速记选题成功回显（待拍清单出现）",
              f"E2E 快速记选题 {stamp}" in page.inner_text("main"))

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
