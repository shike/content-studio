#!/usr/bin/env python3
"""P1 内容核心验收：靶子卡备稿 → 脚本工场（一版精品+打磨+定稿）→ 公众号流水线。

对应《docs/TDD.md》§13 P1。需 ZHIPU_API_KEY。
2026-11-16 重写：选题池线（R1.1/R1.1b/R1.1c，/api/topics/*）已按用户裁决退役，
本套件改走 R1.4 靶子卡线——直插 approved 夹具卡 → /api/scripts/generate(card_id)
→ /api/articles/generate(script_id)。备稿受理契约不依赖 LLM，无 key 实例也验。
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

section("P1 · 靶子卡夹具（直插 approved 卡，白盒不动 LLM）")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.db import engine as _eng  # noqa: E402
from app.models import TopicCard as _TC  # noqa: E402
from sqlmodel import Session as _S  # noqa: E402
with _S(_eng) as _s:
    _card = _TC(tenant_id=1, batch_date="2000-01-01", source="manual", pool={},
                spine="P1 夹具主线：把交付标准写进合同，验收合格再扩展",
                hook="P1 夹具钩子：培训费打水漂之前，先看这篇",
                spice="mid", takeaways=[],
                judge={"verdict": "pass", "reason": "P1 夹具"},
                status="approved")
    _s.add(_card)
    _s.commit()
    card_id = _card.id
check("夹具卡入库（approved，可备稿）", bool(card_id), f"card_id={card_id}")

section("P1 · 靶子卡备稿受理（card_id → 202，不触发 LLM）")
script_id = None
_job_id = None
status, r = request("POST", "/api/scripts/generate",
                    body={"card_id": card_id, "angles": ["反常识判断"]})
check("脚本生成任务受理（202 + script_id/job_id）",
      status == 202 and r.get("script_id") and r.get("job_id"),
      f"status={status}, {str(r)[:120]}")
if status == 202:
    script_id = r.get("script_id")
    _job_id = r.get("job_id")
else:
    sys.exit(finish("P1 内容核心"))
# 备稿回填：卡转 approved 且 script_id 双向锚（白盒核）
with _S(_eng) as _s:
    _row = _s.get(_TC, card_id)
    check("备稿后卡状态 approved 且回填 script_id",
          _row is not None and _row.status == "approved" and _row.script_id == script_id,
          f"status={getattr(_row, 'status', None)}, script_id={getattr(_row, 'script_id', None)}")

section("P1 · 脚本工场（一版精品直出+自动打磨+定稿）")
if not llm_ready or not script_id:
    skip("脚本生成/打磨/终稿", "未配置 ZHIPU_API_KEY" if not llm_ready else "无受理到的 script_id")
else:
    # 生成=一版精品直出+自动打磨一轮（两次 LLM 调用，思考型模型实测可达 ~10 分钟）
    job = poll_job(_job_id, timeout=900)
    check("一版精品脚本生成完成", job is not None and job.get("status") == "succeeded",
          (job or {}).get("error") or ("" if job else "900s 超时"))
    status, s = request("GET", f"/api/scripts/{script_id}")
    versions = s.get("versions") or []
    ok = (status == 200 and len(versions) == 1
          and all(v.get("hook") and v.get("body") for v in versions))
    check("脚本为一版精品且带钩子+正文", ok, f"版本数={len(versions)}")
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
    # 一版精品 LLM 质量评审（X11：钩子/口语化/证明/自然收尾/合规/人设回扣）
    for i, v in enumerate(versions):
        ok_q, note = quality.judge(v.get("body") or "")
        check(f"精品质量评审·第{i + 1}版（单项≥6 总评≥7）", ok_q, note)
    status, fr = request("POST", f"/api/scripts/{script_id}/finalize", timeout=240)
    check("定稿任务受理 202", status == 202 and fr.get("job_id"), f"status={status}")
    job = poll_job(fr.get("job_id"), timeout=600)
    check("定稿任务完成", job is not None and job.get("status") == "succeeded",
          f"status={job.get('status') if job else '超时'}")
    status, s = request("GET", f"/api/scripts/{script_id}")
    ok = (status == 200 and s.get("teleprompter_text")
          and isinstance(s.get("storyboard"), list) and len(s.get("storyboard", [])) >= 1
          and len(s.get("title_candidates") or []) >= 3)
    check("终稿三产物：提词器文本/分镜/标题候选≥3", ok, f"status={status}")

section("P1 · 已移除的飞瓜导入端点（负断言）")
check("已移除的飞瓜导入端点不再存在（405/404）",
      request("POST", "/api/topics/import/feigua", body={"csv": "x"})[0] in (404, 405))

section("P1 · 公众号流水线（script_id → 深度长文）")
if not llm_ready or not script_id:
    skip("长文生成/改稿/渲染", "未配置 ZHIPU_API_KEY" if not llm_ready else "无受理到的 script_id")
else:
    status, r = request("POST", "/api/articles/generate",
                        body={"script_id": script_id, "style": "auto"})
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
    ok_q, note = quality.judge(a_md, kind="article")  # 深度版唯一档位（2026-11-02 定版 feed 退役）：用深度长文尺
    check("长文质量评审（单项≥6 总评≥7）", ok_q, note)
    import re as _re
    _plain = _re.sub(r'\[CHART[^\]]*\]', '', a_md, flags=_re.S)
    _han = len(_re.findall(r'[\u4e00-\u9fff]', _plain))
    check("深度版长度档（3000~5000 字，剥图表/标记按汉字计，容差 ±25%）",
          status == 200 and 2250 <= _han <= 6250,
          f"汉字数={_han}")
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
    status, payload = request("GET", f"/api/articles/{article_id}/wechat-copy")
    payload_html = (payload or {}).get("html") if isinstance(payload, dict) else ""
    check("粘贴载荷把配图换成签名公开地址（微信跨域抓图）",
          status == 200 and "/api/public/images/" in (payload_html or ""),
          f"status={status}, 签名图数={(payload_html or '').count('/api/public/images/')}")


sys.exit(finish("P1 内容核心"))
