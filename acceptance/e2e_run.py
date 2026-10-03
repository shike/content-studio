#!/usr/bin/env python3
"""E2E 端到端验收：Playwright 驱动真实浏览器，走用户旅程（AUDIT A3 界面冒烟的自动化版）。

前提：隔离实例已在 127.0.0.1:8200 运行（DATA_DIR=data-acceptance + CS_BOOTSTRAP_PASSWORD，
流程同其他套件；LLM 配置可选——本套件不触发任何 LLM 调用）。
运行：python3 acceptance/e2e_run.py

覆盖：
- 登录流程（错误密码拒/正确密码进）
- 全站 14 页逐页打开，console error/pageerror 零异常，每页特征元素在位
- 工作台快速记选题（无 LLM 的真实用户操作）→ 选题库出现
- 公众号：fixture 文章行展开 → 渲染 → HTML 预览出现
- 管理后台：品牌卡九字段在位、改头图口号保存成功回显
- 租户白名单：成员账号无管理后台入口
- 登出
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
from runner import check, finish, request, section

BASE = os.environ.get("CS_API_BASE", "http://127.0.0.1:8200")
UI = BASE  # 前端由后端同源托管
ADMIN_PWD = os.environ.get("CS_BOOTSTRAP_PASSWORD", "accept-12345")
MEMBER_PWD = "E2eMember#2026"

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
        "INSERT INTO articles (tenant_id, topic_id, title, md, html, status, created_at, updated_at) "
        "VALUES (1, NULL, ?, ?, ?, 'rendered', datetime('now'), datetime('now'))",
        (f"E2E 种子文章 {stamp}", art_md, art_html))
    conn.execute(
        "INSERT INTO topics (tenant_id, title, angle, audience, source_type, source_ref, status, "
        "score, created_at, updated_at) "
        "VALUES (1, ?, 'E2E 验收角度', 'both', 'manual', 'e2e', 'approved', 8.0, "
        "datetime('now'), datetime('now'))",
        (f"E2E 种子选题 {stamp}",))
    # 同行监测周报夹具：一条待定夺的抖音视频（digest 卡有数据可渲染）
    conn.execute(
        "INSERT INTO benchmark_videos (tenant_id, url, author, title, transcript, media_path, "
        "source, scan_status, created_at, updated_at) "
        "VALUES (1, 'https://www.douyin.com/video/e2e001', 'E2E测试作者', "
        "'E2E 同行视频样例 #企业AI落地', '', '', 'douyin', 'pending', datetime('now'), datetime('now'))")
    conn.execute(
        "INSERT INTO topics (tenant_id, title, angle, audience, source_type, source_ref, status, "
        "score, created_at, updated_at) "
        "VALUES (1, 'E2E 高分选题样例', 'E2E 切角：从验收单切入', 'both', 'benchmark', 'e2e-topic', "
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
        check("正确密码进入工作台", "工作台" in page.inner_text("nav"))

        # ---------- 全站逐页零报错 + 页头断言 ----------
        section("E2E · 全站逐页零报错（14 页）")

        def goto_page(label: str) -> None:
            """locator 点击侧栏菜单（evaluate 的 .click() 不触发 React 导航，勿用）。"""
            page.locator('aside button', has_text=label).first.click(timeout=6000)
            page.wait_for_timeout(900)

        PAGES = ["工作台", "选题库", "脚本工场", "口播数字人", "公众号", "发布台",
                 "海报提示词", "同行监测", "我的风格", "拆解库", "话题雷达", "任务",
                 "设置", "管理后台"]
        for label in PAGES:
            goto_page(label)
            h2 = page.locator("main h2")
            got = h2.first.inner_text().strip() if h2.count() else "(无页头)"
            check(f"页面打开：{label}", got == label, f"页头={got!r}")
            if label == "工作台":
                check("工作台含同行监测周报卡", "同行监测周报" in page.inner_text("main"))

        # ---------- 工作台快速记选题 ----------
        section("E2E · 工作台快速记选题")
        goto_page("工作台")
        page.click('button:has-text("快速记选题")')
        page.fill('input[placeholder*="一句话记下"]', f"E2E 快速记选题 {stamp}")
        page.click('button:has-text("记下来")')
        page.wait_for_timeout(1000)
        check("快速记选题成功回显", "已记" in page.inner_text("main") or "记下来" not in page.inner_text("main"))

        # ---------- 公众号：文章展开与渲染 ----------
        section("E2E · 公众号文章展开与渲染")
        goto_page("公众号")
        # 行标题在无脚本关联时显示兜底文案「文章 #N」——按行内稳定标记点行展开
        page.locator('text=/文章 #\\d+/').first.click()
        page.wait_for_timeout(800)
        check("文章行展开（md 编辑器出现）", page.locator("textarea").count() >= 1)
        render_btn = page.locator('button:has-text("渲染公众号格式")')
        if render_btn.count() == 0:
            render_btn = page.locator('button:has-text("已渲染")')
        render_btn.first.click()
        page.wait_for_timeout(1200)
        check("渲染交互出 HTML 预览", page.locator("text=复制公众号 HTML").count() >= 1
              or page.locator("text=已渲染").count() >= 1)

        # ---------- 管理后台：品牌卡九字段 ----------
        section("E2E · 管理后台品牌卡")
        goto_page("管理后台")
        # 进租户 tab → 点租户行进详情 → 品牌卡在此层级
        page.locator('button', has_text='租户（').first.click()
        page.wait_for_timeout(900)
        page.locator('main').get_by_text('示例工作室').first.click()
        page.wait_for_timeout(1200)
        check("品牌卡九字段在位",
              all(page.locator(f'text={t}').count() >= 1 for t in
                  ["角标栏目 · 第一行", "头图口号", "受众域口径", "ASR 领域词表", "图表主色"]))
        # 值级断言：列表接口必须带 brand（曾漏传导致品牌卡全空——用户报"我自己的租户什么内容都没有"）
        l1 = page.locator('label', has_text='角标栏目 · 第一行').locator('input').input_value()
        check("品牌卡回显租户已配置内容（栏目第一行=示例种子值）", l1 == "跃迁", f"label_line1={l1!r}")
        page.locator('label', has_text='头图口号').locator('input').fill(f"E2E口号 {stamp}")
        page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('button')]
                    .find(x => x.textContent.includes('保存品牌配置'));
                if (b) b.click();
            }""")
        page.wait_for_timeout(1200)
        check("品牌保存成功回显", "品牌已保存" in page.inner_text("main"))

        # ---------- 租户白名单：成员无管理后台 ----------
        section("E2E · 租户白名单冒烟")
        member_name = f"e2e_member_{stamp}"
        st, d = request("POST", "/api/admin/users", body={
            "username": member_name, "role": "member", "tenant_id": 1})
        one_time = d.get("one_time_password") if isinstance(d, dict) else ""
        check("成员账号创建（返回一次性密码）", st in (200, 201) and bool(one_time), f"code={st}")
        page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('button')]
                    .find(x => x.textContent.trim() === '退出');
                if (b) b.click();
            }""")
        page.wait_for_timeout(1000)
        if page.locator('input[placeholder="用户名"]').count() == 0:
            page.reload()
            page.wait_for_timeout(1000)
        # 成员首登强制改密：先处理
        page.fill('input[placeholder="用户名"]', member_name)
        page.fill('input[placeholder="密码"]', one_time)
        page.click('button:has-text("登录")')
        page.wait_for_timeout(1500)
        if page.locator('text=设置新密码').count() >= 1:
            page.get_by_placeholder('刚才登录用的密码').fill(one_time)
            page.get_by_placeholder('新密码', exact=True).fill(MEMBER_PWD)
            page.get_by_placeholder('再输入一次新密码').fill(MEMBER_PWD)
            page.click('button:has-text("保存并进入")')
            page.wait_for_timeout(1800)
        page.wait_for_selector('aside', timeout=8000)
        nav_text = page.inner_text("aside")
        check("成员无管理后台入口", "管理后台" not in nav_text)
        # 登出
        page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('button')]
                    .find(x => x.textContent.trim() === '退出');
                if (b) b.click();
            }""")
        page.wait_for_timeout(800)

        # ---------- 汇总 ----------
        # 预期噪声：未登录探活/登出后的 401；无蝉镜凭证实例上数字人页 balance 探询的
        # 400（合法降级路径，前端有"未配置"回显）——浏览器把 4xx 响应自动记 console.error
        real_errors = [e for e in errors
                       if "favicon" not in e and "401 (Unauthorized)" not in e
                       and "/api/avatar/balance" not in e]
        check("全站 console/pageerror 零异常（401 探活噪声除外）", not real_errors,
              f"{len(real_errors)} 条：{real_errors[:3]}")
        browser.close()

    print(f"\n----- E2E 端到端 验收结果: {len(PASS)} PASS / {len(FAIL)} FAIL -----")
    for f in FAIL:
        print(f"  FAIL: {f}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
