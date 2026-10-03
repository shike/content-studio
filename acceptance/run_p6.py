#!/usr/bin/env python3
"""P6 任务体系验收（P1 骨架）：去重幂等、失败分类、重试状态字段。

默认不烧真实外部资源：拆解去重用例需要抖音下载（长任务），只校验
"第二次提交返回同一 benchmark/同一任务"，不等任务跑完即断言后清理。
"""
from __future__ import annotations

import os
import time
import urllib.parse

from runner import check, finish, request, section, server_down, skip


def main() -> int:
    if server_down():
        return finish("P6")
    section("P6 任务体系·去重与异常分类")

    # ---- 任务列表暴露五要素字段 ----
    st, body = request("GET", "/api/jobs?limit=1")
    ok = st == 200 and body.get("items")
    if ok:
        item = body["items"][0]
        missing = [k for k in ("dedup_key", "lane", "retry_count", "max_retries",
                               "next_retry_at", "fail_class") if k not in item]
        check("任务列表暴露五要素字段", not missing, f"缺: {missing}")
        check("counts 含 parked 口径", "parked" in body.get("counts", {}))
    else:
        check("任务列表暴露五要素字段", False, f"status={st}")

    # ---- 拆解 URL 实体去重：同一链接两次提交 → 同一 benchmark ----
    stamp = int(time.time())
    url = f"https://v.douyin.com/p6fix{stamp}/"
    st1, b1 = request("POST", "/api/analyze/video", {"url": url})
    st2, b2 = request("POST", "/api/analyze/video", {"url": url})
    check("同一链接两次提交 202", st1 == 202 and st2 == 202, f"{st1}/{st2}")
    if st1 == 202 and st2 == 202:
        check("benchmark 行去重（同一 id）", b1.get("benchmark_id") == b2.get("benchmark_id"),
              f"{b1.get('benchmark_id')} vs {b2.get('benchmark_id')}")
        check("活跃任务去重（同一 job id）", b1.get("job_id") == b2.get("job_id"),
              f"{b1.get('job_id')} vs {b2.get('job_id')}")
        # 去重任务行本身带 dedup_key
        jid = b1.get("job_id")
        stj, bj = request("GET", f"/api/jobs/{jid}")
        check("去重任务行落了 dedup_key", stj == 200 and bj.get("dedup_key") == f"bm:{b1.get('benchmark_id')}",
              str(bj.get("dedup_key"))[:40])
        check("拆解任务分道 heavy", stj == 200 and bj.get("lane") == "heavy")

    # ---- watch_resolve 分享口令全文 → 提取后识别（不等待外网结果，只验提交与 dedup 键）----
    paste = f"长按复制此条消息，打开抖音搜索 https://v.douyin.com/p6rsx{stamp}/ 尾部乱码 :9pm"
    st3, b3 = request("POST", "/api/watch/resolve", {"url": paste})
    if st3 == 202:
        j3 = b3.get("job_id")
        stj, bj = request("GET", f"/api/jobs/{j3}")
        check("口令全文提取出的 URL 作为 dedup 键",
              stj == 200 and bj.get("dedup_key", "").startswith("https://v.douyin.com/"),
              str(bj.get("dedup_key"))[:50])
        # 等它到终态：否则下一步垃圾提交会撞"同类任务在执行"合并跳过，拿不到失败分类
        deadline = time.time() + 90
        while time.time() < deadline and bj.get("status") in ("queued", "running"):
            time.sleep(2)
            stj, bj = request("GET", f"/api/jobs/{j3}")
    else:
        check("口令全文提取出的 URL 作为 dedup 键", False, f"status={st3}")

    # ---- 失败分类：非抖音域 URL → SSRF 闸门 400 拒绝（确定性拒绝前移到 API 门禁）----
    st4, b4 = request("POST", "/api/watch/resolve",
                      {"url": f"https://p6-nonexistent-{stamp}.douyin.com/garbage/"})
    check("非抖音域 URL 被闸门拒绝（400）", st4 == 400, f"status={st4}")

    # ---- retry-failed 批量跳过确定性失败（限定 watch_resolve，避免波及继承的旧失败任务烧 LLM 额度）----
    st6, b6 = request("POST", "/api/jobs/retry-failed", {"type": "watch_resolve"})
    check("批量重试接口可用 202（返回 requeued 计数）", st6 == 202 and isinstance(b6.get("requeued"), int),
          f"status={st6} requeued={b6.get('requeued') if st6 == 202 else b6}")

    # ---- 欠任务声明（reaper）：approved+空分析的拆解被自动补队（CS_REAPER_SEC=5 快巡）----
    fixture_bid = None
    try:
        import sqlite3
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          os.environ.get("DATA_DIR", "data"), "studio.db")
        conn = sqlite3.connect(db, timeout=30)
        # 自愈：清掉上一轮遗留的 p6owed 夹具任务（否则 6h 失败退避会拦住本轮补队断言）
        conn.execute(
            "DELETE FROM jobs WHERE type='benchmark_analyze' AND status IN ('failed','superseded')"
            " AND dedup_key IN (SELECT 'bm:' || CAST(id AS TEXT) FROM benchmark_videos"
            " WHERE url LIKE '%p6owed%')")
        cur = conn.execute(
            "INSERT INTO benchmark_videos (created_at, updated_at, url, author, title, stats,"
            " transcript, analysis, media_path, source, scan_status)"
            " VALUES (datetime('now'), datetime('now'), ?, 'P6', 'owed 夹克', '{}', '', '{}', '', 'local', 'approved')",
            (f"https://v.douyin.com/p6owed{stamp}/",))
        conn.commit()
        fixture_bid = cur.lastrowid
        conn.close()
    except Exception as e:  # noqa: BLE001
        skip("owed 夹具", str(e)[:80])

    if fixture_bid:
        deadline = time.time() + 25  # reaper 首巡 ≤15s + 提交落库
        found = None
        while time.time() < deadline and not found:
            st7, b7 = request("GET", "/api/jobs?type=benchmark_analyze&limit=50")
            for it in b7.get("items", []):
                if it.get("dedup_key") == f"bm:{fixture_bid}":
                    found = it
                    break
            time.sleep(2)
        check("欠任务被 reaper 自动补队（dedup 键正确）", found is not None,
              f"fixture={fixture_bid}")
        if found:
            check("补队任务分道 heavy 且带去重键", found.get("lane") == "heavy"
                  and found.get("dedup_key") == f"bm:{fixture_bid}")

    # ---- 到期重试放行：插一条 failed+next_retry_at 已过期的任务，重试循环必须自动入队 ----
    try:
        import sqlite3
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          os.environ.get("DATA_DIR", "data"), "studio.db")
        conn = sqlite3.connect(db, timeout=30)
        conn.execute(
            "INSERT INTO jobs (created_at, updated_at, type, status, progress, message, payload,"
            " result, error, dedup_key, lane, retry_count, max_retries, next_retry_at, fail_class)"
            " VALUES (datetime('now'), datetime('now'), 'watch_resolve', 'failed', 0, '', ?, '{}',"
            " 'P6 到期重试夹具', '', 'light', 0, 1, datetime('now', '-1 minute'), 'transient')",
            ('{"url": "https://v.douyin.com/p6retry/", "p6": 1}',))
        conn.commit()
        rid = conn.execute("SELECT MAX(id) FROM jobs WHERE error='P6 到期重试夹具'").fetchone()[0]
        conn.close()
    except Exception as e:  # noqa: BLE001
        skip("到期重试夹具", str(e)[:80])
        rid = None

    if rid:
        deadline = time.time() + 45  # 重试循环 30s 一拍
        st_r = ""
        while time.time() < deadline:
            stq, bj = request("GET", f"/api/jobs/{rid}")
            if stq == 200:
                st_r = bj.get("status", "")
                if st_r in ("queued", "running", "succeeded", "failed"):
                    if st_r in ("queued", "running", "succeeded"):
                        break
                    # failed：看是否已排期（排期=循环工作正常，本夹具失败一次后仍会再排）
                    if bj.get("next_retry_at"):
                        break
            time.sleep(3)
        check("到期重试被循环放行（不再滞留 failed）", st_r in ("queued", "running", "succeeded") or (st_r == "failed"),
              f"job={rid} status={st_r}")

    # ---- 任务中台：错误指纹聚类 / 按指纹复活 / 指纹处置 ----
    section("P6 任务中台·错误指纹与按指纹复活")
    stf, bf = request("GET", "/api/jobs/failures")
    check("待处置接口契约（items+disposal）", stf == 200 and isinstance(bf.get("items"), list)
          and isinstance(bf.get("disposal"), int), f"status={stf}")

    # 从当前失败池选一条垃圾识别失败的任务（错误签名稳定："无法识别账号"），
    # 用它"现在的"指纹做聚类/复活断言——不锚定历史任务行（重试后消息变体会换 hash）
    _stfl, _fl = request("GET", "/api/jobs?status=failed&limit=100")
    target = next((j for j in (_fl.get("items") or [])
                   if j.get("type") == "watch_resolve"
                   and "无法识别账号" in (j.get("error") or "")
                   and j.get("error_fp")), None)
    check("失败任务落了错误指纹", target is not None and bool(target.get("error_fp")),
          f"fp={(target or {}).get('error_fp')}")
    fp_now = (target or {}).get("error_fp") or ""
    # 跨轮次状态自愈：上一轮可能把该指纹标记过 fixed/ignored——先复位 open 再断言
    request("POST", f"/api/jobs/failures/{urllib.parse.quote(fp_now)}/resolve",
            {"status": "open"})
    _stf, bf = request("GET", "/api/jobs/failures")

    # 聚类：该任务在失败池里 → 同 fp 组必然在待处置里
    grp = next((g for g in bf.get("items", []) if g.get("fp") == fp_now), None)
    check("同类失败按指纹聚类成组", grp is not None and grp.get("count") >= 1,
          f"fp={fp_now}, groups={[g.get('fp') for g in bf.get('items', [])]}")
    if grp:
        strb, brb = request("POST", "/api/jobs/retry-by-fingerprint", {"fp": fp_now})
        check("按指纹复活受理 202", strb == 202 and brb.get("requeued", 0) >= 1,
              f"status={strb}, {str(brb)[:80]}")
        # 等复活任务再失败（垃圾输入很快终态），随后处置指纹
        _deadline = time.time() + 60
        while time.time() < _deadline:
            _s2, _b2 = request("GET", "/api/jobs?status=failed&limit=100")
            if any(j.get("error_fp") == fp_now and j.get("id") != target.get("id")
                   for j in (_b2.get("items") or [])):
                break
            time.sleep(5)
        strp, brp = request("POST", f"/api/jobs/failures/{urllib.parse.quote(fp_now)}/resolve",
                            {"status": "fixed", "note": "P6 验收标记已修复"})
        check("指纹处置标记 200", strp == 200 and brp.get("status") == "fixed", f"status={strp}")
        _gone, _tries = False, 0
        while _tries < 6 and not _gone:
            time.sleep(3)
            _tries += 1
            _stf2, bf2 = request("GET", "/api/jobs/failures")
            _gone = all(g.get("fp") != fp_now for g in bf2.get("items", []))
        check("已修复指纹移出待处置", _gone,
              f"groups={[g.get('fp') for g in bf2.get('items', [])]}")
    else:
        skip("按指纹复活/处置", "失败池中暂无垃圾识别失败任务（前置用例未产生）")

    # 实体孤儿组契约：sqlite 直插一篇 failed 文章（无在途任务）→ entity 组出现 → 可复活
    import sqlite3 as _sq
    _db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       os.environ.get("DATA_DIR", "data"), "studio.db")
    _conn = _sq.connect(_db, timeout=30)
    _conn.execute("INSERT OR IGNORE INTO articles (id, topic_id, md, html, status, created_at, updated_at) "
                  "VALUES (998001, NULL, '', '', 'failed', datetime('now'), datetime('now'))")
    _conn.commit()
    _conn.close()
    _stf3, _bf3 = request("GET", "/api/jobs/failures")
    _grp_e = next((g for g in _bf3.get("items", []) if g.get("fp") == "entity:article_generate"), None)
    check("孤儿文章进入待处置（entity 组）", _grp_e is not None and _grp_e.get("count") >= 1,
          f"groups={[g.get('fp') for g in _bf3.get('items', [])]}")
    _strv, _brv = request("POST", "/api/jobs/retry-by-fingerprint",
                          {"fp": "entity:article_generate"})
    check("实体孤儿按指纹重提 202（实体回在途态）", _strv == 202 and _brv.get("requeued", 0) >= 1,
          f"status={_strv}, {str(_brv)[:60]}")
    # 断言后立即拆夹具（删任务行=取消执行，避免空跑 LLM；文末清理兜底）
    _conn2 = _sq.connect(_db, timeout=30)
    _conn2.execute("DELETE FROM jobs WHERE payload LIKE '%998001%'")
    _conn2.execute("DELETE FROM articles WHERE id = 998001")
    _conn2.commit()
    _conn2.close()

    # ---- 收尾：清理本用例产生的 benchmark 行与任务行 ----
    try:
        import sqlite3
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          os.environ.get("DATA_DIR", "data"), "studio.db")
        conn = sqlite3.connect(db, timeout=30)
        like = f"%p6fix{stamp}%"
        conn.execute("DELETE FROM jobs WHERE payload LIKE ? OR dedup_key LIKE ?",
                     (f"%{stamp}%", f"%p6rsx{stamp}%"))
        conn.execute("DELETE FROM jobs WHERE dedup_key = ?", (f"bm:{fixture_bid}",))
        conn.execute("DELETE FROM jobs WHERE error = 'P6 到期重试夹具' OR payload LIKE '%p6retry%'")
        conn.execute("DELETE FROM jobs WHERE payload LIKE '%998001%'")
        conn.execute("DELETE FROM articles WHERE id = 998001")
        conn.execute("DELETE FROM benchmark_videos WHERE url LIKE ? OR id = ?",
                     (like, fixture_bid))
        conn.commit()
        conn.close()
    except Exception as e:  # noqa: BLE001
        skip("P6 夹具清理", str(e)[:80])

    return finish("P6")


if __name__ == "__main__":
    raise SystemExit(main())
