#!/usr/bin/env python3
"""P1 内容核心验收：选题中心（idea深研/定审与否决）+ 脚本工场 + 公众号流水线 + LLM 台账。

对应《docs/TDD.md》§13 P1。需 ZHIPU_API_KEY。
运行：python3 acceptance/run_p1.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import quality
from runner import check, finish, poll_job, request, section, server_down, skip

if server_down():
    sys.exit(finish("P1 内容核心"))

_, health = request("GET", "/api/health")
llm_ready = (health.get("llm") or {}).get("configured") is True

section("P1 · idea 深度研究")
if not llm_ready:
    skip("idea→深研→选题入库", "未配置 ZHIPU_API_KEY")
    topic_id = None
else:
    status, r = request("POST", "/api/topics/ideas",
                        body={"text": "给制造业企业主讲AI质检怎么落地，先做试点产线再谈推广"})
    if not check("深研任务受理（202 + topic_id/job_id）",
                 status == 202 and r.get("topic_id") and r.get("job_id"),
                 f"status={status}, {str(r)[:120]}"):
        sys.exit(finish("P1 内容核心"))
    topic_id = r["topic_id"]
    job = poll_job(r["job_id"], timeout=600)
    check("深研任务完成", job is not None and job.get("status") == "succeeded",
          (job or {}).get("error") or ("" if job else "360s 超时"))
    status, t = request("GET", f"/api/topics/{topic_id}")
    ok = (status == 200 and t.get("title") and t.get("angle")
          and t.get("audience") in ("boss", "fde", "both")
          and len(str(t.get("research_report") or "")) > 200)
    check("选题入库：标题/角度/受众/研究报告齐备", ok, f"status={status}")
    score = t.get("score")
    check("评分为 0-10 数字", isinstance(score, (int, float)) and 0 <= score <= 10,
          f"score={score}")
    status, _ = request("POST", f"/api/topics/{topic_id}/approve")
    check("选题定审（approve）", status == 200, f"status={status}")

section("P1 · 快速记选题（R1.1b，不触发 LLM）")
status, r = request("POST", "/api/topics/quick",
                    body={"title": "验收-快速选题", "audience": "boss"})
check("快速选题入库（201，source=manual）",
      status == 201 and (r.get("id") or r.get("topic_id")),
      f"status={status}, {str(r)[:100]}")
quick1 = r.get("id") or r.get("topic_id")
status, r2 = request("POST", "/api/topics/quick",
                     body={"title": "验收-快速选题（重复）", "audience": "boss"})
quick2 = r2.get("id") or r2.get("topic_id")
similar = (r2.get("evidence") or {}).get("similar_to") or []
check("相似选题自动标记（R1.1c：第二次入库带 similar_to）",
      status == 201 and quick2 and quick1 in similar,
      f"status={status}, similar_to={similar}")

section("P1 · 快速选题与选题否决")
csv_line = "验收专用-快速选题测试题"
status, r = request("POST", "/api/topics/quick", body={"title": csv_line})
check("快速选题入库 201", status == 201, f"status={status}, {str(r)[:120]}")
quick_id = r.get("id") if status == 201 else None
if quick_id:
    status, _ = request("POST", f"/api/topics/{quick_id}/reject")
    status2, t = request("GET", f"/api/topics/{quick_id}")
    check("选题否决（reject）", status == 200 and t.get("status") == "rejected",
          f"status={status}, status2={status2}")
    check("已移除的飞瓜导入端点不再存在（405/404）",
          request("POST", "/api/topics/import/feigua", body={"csv": "x"})[0] in (404, 405))

section("P1 · 脚本工场")
script_id = None
if not llm_ready:
    skip("脚本生成/打磨/终稿", "未配置 ZHIPU_API_KEY")
else:
    status, r = request("POST", "/api/scripts/generate", body={"topic_id": topic_id})
    if not check("脚本生成任务受理", status == 202 and r.get("script_id"),
                 f"status={status}, {str(r)[:120]}"):
        sys.exit(finish("P1 内容核心"))
    script_id = r["script_id"]
    # 生成=三版生成+自动打磨一轮（两次 LLM 调用，思考型模型实测可达 ~10 分钟）
    job = poll_job(r.get("job_id"), timeout=900)
    check("3 版脚本生成完成", job is not None and job.get("status") == "succeeded")
    status, s = request("GET", f"/api/scripts/{script_id}")
    versions = s.get("versions") or []
    ok = (status == 200 and len(versions) == 3
          and all(v.get("hook") and v.get("body") for v in versions))
    check("脚本含 3 个版本且各带钩子+正文", ok, f"版本数={len(versions)}")
    # 新契约：生成后自动打磨一轮（polish 会把批判意见以「｜改进：」并入 notes）
    check("生成后自动打磨一轮（notes 含改进标记）",
          any("｜改进：" in (v.get("notes") or "") for v in versions),
          "versions.notes 无「｜改进：」标记")
    # 违禁词硬检查（程序化，零成本）
    banned = [w for v in versions for w in quality.banned_words(v.get("body") or "")]
    check("脚本违禁词检查（合规红线）", not banned, f"命中：{banned}" if banned else "干净")
    status, r = request("POST", f"/api/scripts/{script_id}/polish")
    # 思考型模型打磨实测最长 ~350s，等待预算给足
    job = poll_job(r.get("job_id"), timeout=600) if status == 202 else None
    check("批判打磨任务完成", job is not None and job.get("status") == "succeeded",
          f"status={status}")
    # 定稿是同步 LLM 调用（思考型模型实测 50~90s），超时给足
    # 三版逐版 LLM 质量评审（X11：钩子/口语化/证明/自然收尾/合规/人设回扣）
    for i, v in enumerate(versions):
        ok_q, note = quality.judge(v.get("body") or "")
        check(f"三版质量评审·第{i + 1}版（单项≥6 总评≥7）", ok_q, note)
    status, fr = request("POST", f"/api/scripts/{script_id}/finalize", timeout=240)
    check("定稿任务受理 202", status == 202 and fr.get("job_id"), f"status={status}")
    job = poll_job(fr.get("job_id"), timeout=360)
    check("定稿任务完成", job is not None and job.get("status") == "succeeded",
          f"status={job.get('status') if job else '超时'}")
    status, s = request("GET", f"/api/scripts/{script_id}")
    ok = (status == 200 and s.get("teleprompter_text")
          and isinstance(s.get("storyboard"), list) and len(s.get("storyboard", [])) >= 1
          and len(s.get("title_candidates") or []) >= 3)
    check("终稿三产物：提词器文本/分镜/标题候选≥3", ok, f"status={status}")

section("P1 · 公众号流水线")
if not llm_ready:
    skip("长文生成/改稿/渲染", "未配置 ZHIPU_API_KEY")
else:
    # 双档位（2026-10-02）默认档=feed（公众号版）：推荐流完读率优先的 1200~1800 字规格。
    # P1 端到端跑的就是用户拿到的默认产物；deep 档走同一代码路径，仅规格文本不同。
    status, r = request("POST", "/api/articles/generate",
                        body={"topic_id": topic_id, "length": "feed", "style": "auto"})
    if not check("长文生成任务受理", status == 202 and r.get("article_id"),
                 f"status={status}, {str(r)[:120]}"):
        sys.exit(finish("P1 内容核心"))
    article_id = r["article_id"]
    job = poll_job(r.get("job_id"), timeout=1200)
    check("长文生成完成", job is not None and job.get("status") == "succeeded")
    status, a = request("GET", f"/api/articles/{article_id}")
    a_md = a.get("md") or ""
    banned_a = quality.banned_words(a_md)
    check("长文违禁词检查（合规红线）", not banned_a, f"命中：{banned_a}" if banned_a else "干净")
    ok_q, note = quality.judge(a_md, kind="feed")  # 公众号版用推荐流尺（开头即答案/可扫读/人味）
    check("长文质量评审（单项≥6 总评≥7）", ok_q, note)
    check("公众号版长度档（800~3600 字，1200~1800 正文+图表图解标记）",
          status == 200 and 800 <= len(a_md) <= 3600,
          f"md长度={len(a_md)}")
    status, _ = request("PUT", f"/api/articles/{article_id}",
                        body={"md": (a.get("md") or "") + "\n\n（验收追加段落）"})
    check("人工改稿保存（PUT）", status == 200, f"status={status}")
    status, a2 = request("POST", f"/api/articles/{article_id}/render",
                         body={"md": (a.get("md") or "") + "\n\n（渲染契约探针）"})
    html = a2.get("html") or "" if isinstance(a2, dict) else ""
    # 契约：必须回完整文章（id/status/md）——前端按 id 匹配更新列表，只回 {html} 会静默无反馈
    contract_ok = (isinstance(a2, dict) and a2.get("id") == article_id
                   and a2.get("status") == "rendered"
                   and "渲染契约探针" in (a2.get("md") or ""))
    check("渲染为公众号内联样式 HTML", status == 200 and "style=" in html and "<section" in html,
          f"status={status}, html长度={len(html)}")
    check("渲染回完整文章且渲染即保存改稿（前端回显契约）", contract_ok,
          f"字段={sorted(a2)[:8] if isinstance(a2, dict) else a2}, status={status}")
    # 配图 src 回归守卫：md 用 cover:/chart: scheme 写图，scheme 不在 bleach 协议白名单里，
    # 渲染器不剥前缀就会被净化整条丢掉 → 公众号粘贴后图片是裂图（2026-09-27 修）
    check("渲染产物含配图 src（scheme 前缀不再丢图）",
          "/api/articles/images/" in html, f"image src 数={html.count('/api/articles/images/')}")
    # CHART 剥离守卫：分节写作的 [CHART] 规格块是中间产物，渲染器不剥则原始 JSON 透进
    # 公众号 HTML 读者可见（2026-10-02 修，27c9c5f）
    check("渲染产物无 [CHART] 规格块残留（JSON 不漏给读者）",
          "[CHART]" not in html and "[/CHART]" not in html,
          f"html 含 CHART 标记={html.count('[CHART]')}")
    # 知识图解端点契约：手动重生成（成文时已自动追加过一次，这里验证端点本身）
    status, kg = request("POST", f"/api/articles/{article_id}/knowledge-graphic", body={}, timeout=180)
    kg_ok = (status == 200 and isinstance(kg, dict)
             and str(kg.get("file") or "").startswith("gen_kg_") and bool(kg.get("title")))
    check("知识图解端点契约（手动重生成 200 + gen_kg_ 文件 + 标题）", kg_ok,
          f"status={status}, {str(kg)[:100]}")
    check("知识图解随文追加（md 含 gen_kg_ 图解标记）",
          "gen_kg_" in a_md, f"md 含图解标记数={a_md.count('gen_kg_')}")
    # 海报提示词包契约：POST-only 端点，前端曾漏 method 打成 GET 落 404 兜底（92423fe 修）——
    # 此断言用与前端相同的 POST 方法防回归
    status, pack = request("POST", f"/api/articles/{article_id}/poster-pack", body={}, timeout=180)
    pack_ok = (status == 200 and isinstance(pack, dict)
               and pack.get("mode") in ("llm", "llm+template", "template")
               and isinstance(pack.get("items"), list) and len(pack["items"]) >= 1
               and all(str(it.get("prompt") or "").strip() for it in pack["items"]))
    check("海报提示词包契约（POST 200 + items 带 prompt + mode 标记）", pack_ok,
          f"status={status}, mode={(pack or {}).get('mode') if isinstance(pack, dict) else pack}, "
          f"items={len((pack or {}).get('items') or []) if isinstance(pack, dict) else '?'}")
    status, payload = request("GET", f"/api/articles/{article_id}/wechat-copy")
    payload_html = (payload or {}).get("html") if isinstance(payload, dict) else ""
    check("粘贴载荷把配图换成签名公开地址（微信跨域抓图）",
          status == 200 and "/api/public/images/" in (payload_html or ""),
          f"status={status}, 签名图数={(payload_html or '').count('/api/public/images/')}")

section("P1 · LLM 用量台账")
if not llm_ready:
    skip("用量统计", "未配置 ZHIPU_API_KEY")
else:
    status, usage = request("GET", "/api/llm/usage")
    check("本次验收产生用量记录（calls≥4）",
          status == 200 and usage.get("calls", 0) >= 4, f"{str(usage)[:120]}")

sys.exit(finish("P1 内容核心"))
