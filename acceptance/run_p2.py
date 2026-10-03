#!/usr/bin/env python3
"""P2 拆解流水线验收：本地视频拆解全链 + 结构模板库 + 在线拆解（可选）。

对应《docs/技术方案.md》§13 P2。本地链无外部依赖（fixture 由脚本自动生成）；
在线拆解需下载容器健康且设置 DOUYIN_TEST_URL。
运行：python3 acceptance/run_p2.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import (check, ensure_speech_video, finish, poll_job, request,
                    section, server_down, skip)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "sample_speech.mp4")

if server_down():
    sys.exit(finish("P2 拆解流水线"))

_, health = request("GET", "/api/health")
llm_ready = (health.get("llm") or {}).get("configured") is True
test_url = os.environ.get("DOUYIN_TEST_URL", "")

section("P2 · 本地视频拆解（离线可验收）")
video = ensure_speech_video(FIXTURE)
benchmark_id = None
if video is None:
    skip("本地拆解全链", "无法生成语音 fixture（需 ffmpeg + macOS 中文 TTS）")
elif not llm_ready:
    skip("本地拆解全链", "未配置 ZHIPU_API_KEY")
else:
    with open(video, "rb") as f:
        blob = f.read()
    status, r = request("POST", "/api/analyze/local",
                        files={"file": ("sample_speech.mp4", blob, "video/mp4")},
                        timeout=300)
    if not check("本地拆解任务受理", status == 202 and r.get("job_id"),
                 f"status={status}, {str(r)[:120]}"):
        sys.exit(finish("P2 拆解流水线"))
    job = poll_job(r["job_id"], timeout=600)
    check("拆解任务完成（下载跳过→ASR→LLM）",
          job is not None and job.get("status") == "succeeded",
          (job or {}).get("error") or ("" if job else "600s 超时"))
    # 用响应里的 benchmark_id 精确取样（按"最新一条"取样会被并发的其他拆解插队）
    benchmark_id = r.get("benchmark_id")
    status, b = request("GET", f"/api/benchmarks/{benchmark_id}") if benchmark_id else (0, {})
    if check("拆解样本入库（benchmarks）", status == 200,
             f"status={status}, benchmark_id={benchmark_id}"):
        analysis = b.get("analysis") or {}
        ok = (status == 200 and len(b.get("transcript") or "") > 20
              and isinstance(analysis.get("hook"), dict)
              and isinstance(analysis.get("structure"), list)
              and isinstance(analysis.get("replicable_points"), list))
        check("拆解产物：转写非空 + hook/structure/replicable 齐备", ok, f"status={status}")
    status, listing = request("GET", "/api/topics?source=benchmark&limit=5")
    items = listing.get("items", []) if isinstance(listing, dict) else []
    check("拆解产出选题候选（source=benchmark ≥1）", status == 200 and len(items) >= 1,
          f"候选数={len(items)}")

section("P2 · 结构模板库")
status, r = request("POST", "/api/scripts/templates", body={
    "name": "验收模板",
    "structure": [
        {"step": "hook", "requirement": "前3秒点名受众+痛点"},
        {"step": "proof", "requirement": "一个可验收的案例"},
        {"step": "cta", "requirement": "明确行动指令"},
    ]})
ok = status in (200, 201) and (r.get("id") or r.get("template_id"))
check("创建结构模板", bool(ok), f"status={status}, {str(r)[:120]}")
tpl_id = r.get("id") or r.get("template_id")
status, listing = request("GET", "/api/scripts/templates")
items = listing.get("items", []) if isinstance(listing, dict) else (listing or [])
check("模板列表可查询且含新建模板", status == 200 and any(
    i.get("name") == "验收模板" for i in items if isinstance(i, dict)), f"status={status}")
if tpl_id:
    status, _ = request("DELETE", f"/api/scripts/templates/{tpl_id}")
    status2, listing = request("GET", "/api/scripts/templates")
    gone = not any((i.get("id") == tpl_id) for i in (listing.get("items") or []))
    check("模板删除（DELETE 后列表消失）", status == 200 and gone, f"status={status}, gone={gone}")
    status, _ = request("DELETE", f"/api/scripts/templates/{tpl_id}")
    check("删除不存在的模板返回 404", status == 404, f"status={status}")
if len(items) >= 1:
    # 护栏：连删到只剩一个后，最后一个必须 400（本检查只在隔离实例安全执行）
    ids = [i.get("id") for i in items if i.get("id") and i.get("id") != tpl_id]
    last = None
    for tid in ids:
        last = tid
        st, _ = request("DELETE", f"/api/scripts/templates/{tid}")
        if st != 200:
            break
    if last is not None:
        st2, _ = request("DELETE", f"/api/scripts/templates/{last}")
        check("模板护栏：最后一个模板不可删（400）", st2 == 400, f"status={st2}")

section("P2 · 拆解库分页与筛选")
status, listing = request("GET", "/api/benchmarks?limit=2")
ok = (status == 200 and isinstance(listing, dict)
      and len(listing.get("items") or []) <= 2
      and isinstance(listing.get("total"), int)
      and isinstance(listing.get("counts"), dict)
      and {"all", "todo", "done", "online", "local"} <= set(listing.get("counts") or {}))
check("拆解库分页契约（limit/total/counts）", ok, f"status={status}, total={listing.get('total')}")
first = (listing.get("items") or [{}])[0].get("id")
status, listing2 = request("GET", "/api/benchmarks?limit=2&offset=2")
second = (listing2.get("items") or [{}])[0].get("id")
check("拆解库 offset 翻页生效", status == 200 and first != second,
      f"first={first}, second={second}")
status, listing3 = request("GET", "/api/benchmarks?limit=5&analyzed=done")
done_items = listing3.get("items") or []
check("拆解库 analyzed=done 筛选", status == 200 and len(done_items) <= 5
      and all(b.get("analysis") for b in done_items), f"status={status}")
import urllib.parse as _up
_q = _up.quote("不存在作者XYZ")
status, listing4 = request("GET", f"/api/benchmarks?limit=3&author={_q}")
check("拆解库 author 筛选（无匹配返回空）",
      status == 200 and isinstance(listing4, dict) and listing4.get("items") == []
      and listing4.get("total") == 0, f"status={status}, total={listing4.get('total')}")

section("P2 · 同行监控账号")
# 临时账号用不存在的视频 ID：扫描任务会显式失败（负向契约），不触发真实拆解与 LLM 消耗
status, r = request("POST", "/api/watch/accounts", body={
    "platform": "douyin", "name": "验收-临时账号",
    "url": "https://www.douyin.com/video/9900000000000001",
    "note": "acceptance"})
acc_id = r.get("id") if isinstance(r, dict) else None
check("监控账号创建（锚点视频链接）", status in (200, 201) and bool(acc_id),
      f"status={status}, {str(r)[:120]}")
status, listing = request("GET", "/api/watch/accounts")
check("监控账号列表含新建", status == 200 and any(
    a.get("id") == acc_id for a in (listing.get("items") or [])), f"status={status}")
check("监控账号列表带统计与评分字段", status == 200 and all(
    isinstance(a, dict) and {"video_total", "video_recent_7d", "scores"} <= set(a)
    for a in (listing.get("items") or [])), f"status={status}")
if acc_id:
    status, r2 = request("PUT", f"/api/watch/accounts/{acc_id}",
                         body={"enabled": False, "name": "验收-临时账号-改"})
    check("监控账号编辑（PUT 改名+停用）",
          status == 200 and r2.get("enabled") is False and r2.get("name") == "验收-临时账号-改",
          f"status={status}, {str(r2)[:100]}")
    status, r2 = request("PUT", "/api/watch/accounts/99999999", body={"enabled": True})
    check("编辑不存在的账号返回 404", status == 404, f"status={status}")
    status, r2 = request("POST", f"/api/watch/accounts/{acc_id}/scan")
    check("单号扫描任务受理（202）", status == 202 and bool(r2.get("job_id")),
          f"status={status}, {str(r2)[:100]}")
    if r2.get("job_id"):
        job = poll_job(r2["job_id"], timeout=180)
        # 假视频 ID：锚点/分享主页路线都不出数据 → 任务必须显式 failed（不悬挂、不烧 LLM）
        check("单号扫描任务显式终结（不悬挂）",
              job is not None and job.get("status") in ("succeeded", "failed"),
              (job or {}).get("error") or ("" if job else "180s 超时"))
    status, _ = request("DELETE", f"/api/watch/accounts/{acc_id}")
    status2, listing = request("GET", "/api/watch/accounts")
    gone = not any(a.get("id") == acc_id for a in (listing.get("items") or []))
    check("监控账号删除", status in (200, 200) and gone, f"status={status}, gone={gone}")
status, r2 = request("POST", "/api/watch/accounts/99999999/scan")
check("扫描不存在的账号返回 404", status == 404, f"status={status}")
status, h = request("GET", "/api/watch/scan/history?limit=5")
check("扫描历史可查询（含单号扫描记录）", status == 200 and isinstance(h.get("items"), list)
      and any(j.get("payload", {}).get("account_id") for j in (h.get("items") or [])),
      f"status={status}, items={len((h.get('items') or []))}")
status, r2 = request("POST", "/api/watch/resolve", body={"url": ""})
check("自动识别空链接返回 400", status == 400, f"status={status}")
# 识别全链路（真实网络）：默认 SKIP，CS_WATCH_SCAN=1 启用
if os.environ.get("CS_WATCH_SCAN") == "1" and test_url:
    status, r2 = request("POST", "/api/watch/resolve", body={"url": test_url})
    if status == 202:
        job = poll_job(r2["job_id"], timeout=300)
        info = (job or {}).get("result") or {}
        check("自动识别（视频链接反查昵称+主页）",
              job is not None and job.get("status") == "succeeded"
              and bool(info.get("name")) and bool(info.get("url")),
              (job or {}).get("error") or str(info)[:100])
else:
    skip("自动识别全链路", "默认跳过（重链路）；设 CS_WATCH_SCAN=1 + DOUYIN_TEST_URL 启用")

section("P2 · 关键词发现对标账号（R3.6）")
status, r2 = request("POST", "/api/watch/discover", body={"keyword": ""})
check("发现空关键词返回 400", status == 400, f"status={status}")
status, cl = request("GET", "/api/watch/discover/candidates")
check("候选列表接口契约", status == 200 and isinstance(cl.get("items"), list),
      f"status={status}")
if os.environ.get("CS_WATCH_SCAN") == "1":
    status, r2 = request("POST", "/api/watch/discover",
                         body={"keyword": "AI 获客", "direction": "企业服务"})
    if check("发现任务受理（202）", status == 202 and bool(r2.get("job_id")),
             f"status={status}, {str(r2)[:100]}"):
        job = poll_job(r2["job_id"], timeout=420)
        check("发现任务完成（DDG 挖链接+免登录 反查作者）",
              job is not None and job.get("status") == "succeeded",
              (job or {}).get("error") or ("" if job else "420s 超时"))
        status, cl = request("GET", "/api/watch/discover/candidates")
        cands = cl.get("items") or []
        check("候选产出（粉丝数/主页/状态字段齐备）",
              status == 200 and len(cands) >= 1 and all(
                  {"name", "follower_count", "url", "status"} <= set(c) for c in cands),
              f"候选数={len(cands)}, status={status}")
        open_c = next((c for c in cands if c.get("status") == "open"), None)
        if open_c:
            status, r3 = request("POST", f"/api/watch/discover/candidates/{open_c['id']}/add")
            added_ok = (status == 200
                        and (r3.get("candidate") or {}).get("status") == "added")
            check("候选一键加入监测清单（候选转 added）", added_ok,
                  f"status={status}, {str(r3)[:100]}")
            if added_ok and r3.get("account"):
                new_acc = r3["account"]["id"]
                status, al = request("GET", "/api/watch/accounts")
                check("加入后出现在监控清单", any(
                    a.get("id") == new_acc for a in (al.get("items") or [])),
                    f"status={status}")
                request("DELETE", f"/api/watch/accounts/{new_acc}")
        else:
            check("存在待定夺候选", False, f"候选状态分布：{[c.get('status') for c in cands]}")
        status, r3 = request("POST", "/api/watch/discover/candidates/99999999/add")
        check("加入不存在的候选返回 404", status == 404, f"status={status}")
else:
    skip("发现全链路（含一键加入）", "默认跳过（重链路）；设 CS_WATCH_SCAN=1 启用")

section("P2 · 自我风格研究（R9）")
status, r = request("POST", "/api/watch/accounts", body={
    "platform": "douyin", "name": "验收-我的账号",
    "url": "https://www.douyin.com/video/9900000000000003", "kind": "self"})
self_id = r.get("id") if isinstance(r, dict) else None
check("self 账号创建（kind=self）", status == 201 and r.get("kind") == "self",
      f"status={status}, {str(r)[:100]}")
status, ov = request("GET", "/api/style/overview")
ok = (status == 200 and isinstance(ov, dict)
      and (ov.get("account") or {}).get("kind") == "self"
      and {"profile", "profile_history", "videos", "stats"} <= set(ov))
check("style overview 契约（含 self account/profile/videos/stats）", ok, f"status={status}")
status, r2 = request("POST", "/api/style/profile/update")
# 隔离库继承真实数据时可能已有已分析视频：状态自洽断言（0 条→400 拒绝；>0 条→202 受理）
_analyzed_n = ((ov or {}).get("stats") or {}).get("analyzed", 0)
check("重算画像门禁（无已分析 400 / 有已分析 202）",
      (status == 400 and _analyzed_n == 0) or (status == 202 and _analyzed_n > 0),
      f"status={status}, analyzed={_analyzed_n}")
status, r2 = request("POST", "/api/style/scan")
if check("自我扫描任务受理（202）", status == 202 and bool(r2.get("job_id")),
         f"status={status}, {str(r2)[:100]}") and r2.get("job_id"):
    job = poll_job(r2["job_id"], timeout=240)
    check("自我扫描任务显式终结（假链接不悬挂）",
          job is not None and job.get("status") in ("succeeded", "failed"),
          (job or {}).get("error") or ("" if job else "240s 超时"))
status, h = request("GET", "/api/style/profile/history")
check("画像历史接口契约", status == 200 and isinstance(h.get("items"), list),
      f"status={status}")
if self_id:
    status, _ = request("DELETE", f"/api/watch/accounts/{self_id}")
    check("self 账号清理", status == 200, f"status={status}")

# 扫描全链路（锚点路线，真实网络+LLM，重）：默认 SKIP，CS_WATCH_SCAN=1 启用
if os.environ.get("CS_WATCH_SCAN") == "1":
    status, r = request("POST", "/api/watch/scan")
    if status == 202:
        job = poll_job(r["job_id"], timeout=900)
        check("同行扫描全链路（锚点路线）",
              job is not None and job.get("status") == "succeeded",
              (job or {}).get("error") or ("" if job else "900s 超时"))
else:
    skip("同行扫描全链路", "默认跳过（重链路）；设 CS_WATCH_SCAN=1 在隔离实例启用")

section("P2 · 在线拆解（抖音链接）")
if not test_url:
    skip("在线拆解", "未设置 DOUYIN_TEST_URL")
elif not llm_ready:
    skip("在线拆解", "未配置 ZHIPU_API_KEY")
else:
    # 下载主路线为 Playwright 免登录 直连，不再要求下载容器（容器只是可选加速项）
    status, r = request("POST", "/api/analyze/video", body={"url": test_url})
    ok = status == 202 and r.get("job_id")
    check("在线拆解任务受理", bool(ok), f"status={status}, {str(r)[:120]}")
    if ok:
        job = poll_job(r["job_id"], timeout=600)
        check("在线拆解完成", job is not None and job.get("status") == "succeeded",
              (job or {}).get("error") or ("" if job else "600s 超时"))

sys.exit(finish("P2 拆解流水线"))
