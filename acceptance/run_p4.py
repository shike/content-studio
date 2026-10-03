#!/usr/bin/env python3
"""P4 发布台验收（新契约）：只做物料包；台账/指标回流/sau 已按 PRD R6 移除，
以负向契约检查（必须 404/400）防止死灰复燃。

对应《docs/TDD.md》§13 P4 与《docs/PRD.md》R6。
运行：python3 acceptance/run_p4.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import check, finish, request, section, server_down, skip

if server_down():
    sys.exit(finish("P4 发布台"))

section("P4 · 发布物料包（唯一保留功能）")
script_id = os.environ.get("CS_SCRIPT_ID")
if not script_id:
    status, listing = request("GET", "/api/scripts?status=final&limit=1")
    items = listing.get("items", []) if isinstance(listing, dict) else (listing or [])
    if not items:  # 兼容后端不筛 status 的返回
        status, listing = request("GET", "/api/scripts?limit=20")
        items = [x for x in (listing.get("items") or []) if x.get("status") == "final"]
    if items:
        script_id = items[0]["id"]
if not script_id:
    skip("物料包", "库中无定稿脚本（先跑 run_p1.py 或设 CS_SCRIPT_ID）")
else:
    print(f"    使用脚本 id={script_id}")
    status, pkg = request("POST", "/api/publishing/packages",
                          body={"asset_type": "script", "asset_id": int(script_id)})
    ok = (status in (200, 201) and pkg.get("douyin_title")
          and isinstance(pkg.get("douyin_tags"), list) and len(pkg.get("douyin_tags", [])) >= 2
          and pkg.get("cover_text"))
    check("物料包字段完整（抖音标题/话题≥2/封面文案）", ok,
          f"status={status}, {str(pkg)[:150]}")

section("P4 · 已移除功能的负向契约（防死灰复燃）")
status, _ = request("POST", "/api/publishing/records",
                    body={"asset_type": "script", "asset_id": 1,
                          "platform": "douyin", "published_url": ""})
check("发布台账已移除（POST /records → 404）", status == 404, f"status={status}")
status, _ = request("GET", "/api/publishing/records")
check("发布台账查询已移除（GET /records → 404）", status == 404, f"status={status}")
status, _ = request("POST", "/api/publishing/metrics",
                    body={"record_id": 1, "metrics": {"plays": 1}})
check("指标回流已移除（POST /metrics → 404）", status == 404, f"status={status}")
status, _ = request("GET", "/api/publishing/summary")
check("发布汇总已移除（GET /summary → 404）", status == 404, f"status={status}")
status, _ = request("GET", "/api/publishing/upload-tools")
check("sau 检测接口已移除（→ 404）", status == 404, f"status={status}")
status, _ = request("POST", "/api/publishing/upload",
                    body={"platform": "douyin", "account": "x", "video_path": "/tmp/x.mp4",
                          "asset_type": "script", "asset_id": 1})
check("sau 上传已移除（POST /upload → 404）", status == 404, f"status={status}")
status, _ = request("POST", "/api/publishing/dismissals",
                    body={"scope": "publish", "asset_id": 1})
check("dismissals 仅剩 article scope（publish → 400）", status == 400, f"status={status}")
status, _ = request("POST", "/api/publishing/dismissals",
                    body={"scope": "metrics", "asset_id": 1})
check("dismissals 仅剩 article scope（metrics → 400）", status == 400, f"status={status}")

section("P4 · 「不做长文」出口（保留功能）")
status, r = request("POST", "/api/publishing/dismissals",
                    body={"scope": "article", "asset_id": 999998})
check("忽略登记（article scope）", status in (200, 201), f"status={status}")
status, listing = request("GET", "/api/publishing/dismissals")
has_it = any(d.get("scope") == "article" and d.get("asset_id") == 999998
             for d in (listing.get("items") or []))
check("忽略列表可查", status == 200 and has_it, f"status={status}")
status, _ = request("DELETE", "/api/publishing/dismissals/article")
check("恢复忽略（DELETE article 全部）", status == 200, f"status={status}")

sys.exit(finish("P4 发布台"))
