#!/usr/bin/env python3
"""P8 全量功能契约补缺：审计标出的无脚本覆盖功能，一次补齐（R1.8/R6.6/R7.3/R11.3a/X10/R5.1）。

对应 docs/TDD.md 各契约节。纯 API 层、标准库；除海报 freetext（llm_ready 才跑）外无 LLM 依赖。
运行：python3 acceptance/run_p8.py（隔离实例 8200，流程同其他套件）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import API_BASE as BASE, check, finish, request, section, server_down, skip

if server_down():
    sys.exit(finish("P8 功能补全"))

_, health = request("GET", "/api/health")
llm_ready = (health.get("llm") or {}).get("configured") is True

section("P8 · 今日热点选题提炼（R1.4）")
if llm_ready:
    code, d = request("POST", "/api/topics/trending", body={})
    check("热点提炼受理 202", code == 202 and bool(d.get("job_id")), f"code={code}, {str(d)[:80]}")
    from runner import poll_job as _pj2
    done = _pj2(d["job_id"], timeout=420)
    check("热点提炼任务完成", (done or {}).get("status") == "succeeded",
          f"status={(done or {}).get('status')} err={(done or {}).get('error')}")
    ids = ((done or {}).get("result") or {}).get("topic_ids") or []
    check("切角落库（1~3 条 trending 选题）", 1 <= len(ids) <= 3, f"ids={ids}")
    if ids:
        code, t = request("GET", f"/api/topics/{ids[0]}", timeout=30)
        check("切角选题结构（trending + hotspot 切角证据）",
              code == 200 and t.get("source_type") == "trending"
              and (t.get("evidence") or {}).get("hotspot"),
              f"source_type={t.get('source_type')}")
else:
    code, d = request("POST", "/api/topics/trending", body={})
    check("热点提炼受理 202（无 key 只验契约，不轮询产出）",
          code == 202 and bool(d.get("job_id")), f"code={code}")

section("P8 · 话题雷达（R1.8）")
code, d = request("GET", "/api/radar")
items = d.get("items") if isinstance(d, dict) else []
check("雷达话题列表 200（种子默认空，部署者自行添加）", code == 200 and isinstance(items, list),
      f"code={code}, 话题数={len(items) if isinstance(items, list) else '?'}")
import time as _t

ch_id = f"9900{str(int(_t.time()))[-8:]}"
code, row = request("POST", "/api/radar", body={"name": "P8监控话题", "ch_id": ch_id})
check("雷达添加监控话题 201", code == 201 and row.get("ch_id") == ch_id, f"code={code}")
code, d = request("GET", "/api/radar")
items = d.get("items") if isinstance(d, dict) else []
check("雷达列表含新添加话题", code == 200 and any(t.get("ch_id") == ch_id for t in items),
      f"话题数={len(items) if isinstance(items, list) else '?'}")
code, d = request("POST", "/api/radar", body={"name": "P8监控话题", "ch_id": ch_id})
check("重复 ch_id 409", code == 409, f"code={code}, {str(d)[:60]}")
code, d = request("POST", "/api/radar", body={"name": "坏样例", "ch_id": "abc123"})
check("ch_id 非数字 400", code == 400, f"code={code}")
code, d = request("POST", "/api/radar/extract-hashtags", body={"url": "https://example.com/video/1"})
check("非抖音链接提取 400（防任意 URL 抓取）", code == 400, f"code={code}")
code, d = request("POST", "/api/radar/mine")
mine_job = d.get("job_id") if isinstance(d, dict) else None
check("雷达挖矿受理 202", code == 202 and bool(mine_job), f"code={code}")
from runner import poll_job

mine_done = poll_job(mine_job, timeout=180)
mine_res = (mine_done or {}).get("result") or {}
hashtags = mine_res.get("hashtags")
check("雷达挖矿空库契约（succeeded + hashtags 列表；真实提取属在线门控）",
      (mine_done or {}).get("status") == "succeeded" and isinstance(hashtags, list),
      f"hashtags={str(hashtags)[:60]}")

section("P8 · 同行监测周报（价值流转）")
code, d = request("GET", "/api/watch/digest")
digest_ok = (code == 200 and isinstance(d, dict)
             and isinstance(d.get("top_topics"), list)
             and isinstance(d.get("pending_authors"), list)
             and isinstance(d.get("new_videos"), int)
             and isinstance((d.get("discovered") or {}).get("top"), list))
check("同行监测周报 digest（新视频/待定夺/高分 Top3/清单外高赞同行）", digest_ok,
      f"code={code}, keys={sorted(d) if isinstance(d, dict) else '?'}")

section("P8 · 抖音采集探针（R7.3）")
code, d = request("GET", "/api/crawl/status")
check("探针状态接口（last/history/被动失败数/中文标签）",
      code == 200 and isinstance(d, dict) and "last" in d and "history" in d
      and "recent_failures" in d and bool(d.get("label")), f"code={code}, keys={sorted(d) if isinstance(d, dict) else '?'}")
code, d = request("POST", "/api/crawl/probe", body={}, timeout=90)
probe_status = ((d or {}).get("result") or {}).get("status") if isinstance(d, dict) else None
KNOWN = {"ok", "login_wall", "captcha", "proxy_down", "browser_error", "no_data"}
check("手动探测返回六态分类之一", code == 200 and probe_status in KNOWN,
      f"code={code}, status={probe_status}")

section("P8 · 调度中心（X10）")
code, d = request("GET", "/api/schedules")
items = d.get("items") if isinstance(d, dict) else []
keys = {it.get("key") for it in items} if isinstance(items, list) else set()
check("调度中心列表（内置任务+探针+每日发现）",
      code == 200 and len(items) >= 7 and "crawl_probe" in keys and "watch_discover_daily" in keys,
      f"code={code}, keys={sorted(keys)}")
target = "crawl_probe" if "crawl_probe" in keys else (sorted(keys)[0] if keys else "")
code, d = request("POST", f"/api/schedules/{target}/toggle")
check("调度开关 toggle 受理", code in (200, 202), f"code={code}")
code, d2 = request("GET", "/api/schedules")
toggled = next((it for it in (d2.get("items") or []) if it.get("key") == target), {})
code2, _ = request("POST", f"/api/schedules/{target}/toggle")
code3, d3 = request("GET", "/api/schedules")
restored = next((it for it in (d3.get("items") or []) if it.get("key") == target), {})
check("调度开关往返（关→开，状态还原）",
      toggled.get("enabled") is False and restored.get("enabled") is True,
      f"toggle后={toggled.get('enabled')}, 还原后={restored.get('enabled')}")

section("P8 · 海报提示词工场（R6.6）")
code, d = request("POST", "/api/poster/freetext", body={"content": "太短"})
check("freetext 内容太短 400", code == 400, f"code={code}")
if llm_ready:
    long_content = ("面向制造业中小企业老板的 AI 质检培训大纲：第一讲 AI 质检的边界与适用场景；"
                    "第二讲 试点产线怎么选；第三讲 验收标准怎么写进合同；第四讲 常见坑与避坑清单。")
    code, d = request("POST", "/api/poster/freetext",
                      body={"content": long_content, "poster_type": "knowledge", "count": 2}, timeout=180)
    posters = d.get("posters") if isinstance(d, dict) else None
    check("freetext 生成海报提示词（posters 带 prompt）",
          code == 200 and isinstance(posters, list) and len(posters) >= 1
          and all((p.get("prompt") or "").strip() for p in posters),
          f"code={code}, posters={len(posters) if isinstance(posters, list) else '?'}")
else:
    skip("freetext 生成", "未配置 ZHIPU_API_KEY")

section("P8 · 租户品牌与内容配置九字段（R11.3a）")
code, d = request("GET", "/api/admin/tenants/1/brand")
brand = d.get("brand") if isinstance(d, dict) else {}
NINE = ["label_line1", "label_line2", "signature", "persona", "accent", "primary",
        "asr_vocab", "cover_slogan", "audience_note"]
check("品牌 GET 返回九字段全量", code == 200 and all(k in brand for k in NINE),
      f"缺={[k for k in NINE if isinstance(brand, dict) and k not in brand]}")
code, d = request("PUT", "/api/admin/tenants/1/brand",
                  body={"cover_slogan": "P8口号测试", "audience_note": "P8受众域测试",
                        "label_line1": "P8", "label_line2": "验收"})
check("品牌 PUT 九字段覆盖受理", code == 200, f"code={code}")
code, d = request("GET", "/api/admin/tenants/1/brand")
brand = d.get("brand") if isinstance(d, dict) else {}
check("覆盖键写读回一致（口号/受众域/栏目）",
      brand.get("cover_slogan") == "P8口号测试" and brand.get("audience_note") == "P8受众域测试"
      and brand.get("label_line1") == "P8", f"{str(brand)[:100]}")
code, d = request("PUT", "/api/admin/tenants/1/brand",
                  body={"cover_slogan": "", "audience_note": "", "label_line1": "", "label_line2": ""})
check("品牌 PUT 清空受理（只存覆盖键，空值不落库）", code == 200, f"code={code}")
code, d = request("GET", "/api/admin/tenants/1/brand")
brand = d.get("brand") if isinstance(d, dict) else {}
check("空覆盖回落中性默认（栏目=跃迁/内容，口号空）",
      brand.get("label_line1") == "跃迁" and brand.get("cover_slogan") == "",
      f"label_line1={brand.get('label_line1')}, slogan={brand.get('cover_slogan')!r}")

section("P8 · 自动透镜受理与去重（R5.1）")
code, d = request("POST", "/api/topics/quick", body={"title": f"P8 自动透镜验收选题 {os.urandom(3).hex()}", "audience": "both"})
check("快速记选题入库", code == 201 and d.get("id"), f"code={code}, {str(d)[:80]}")
topic_id = d.get("id")
code, d = request("POST", "/api/articles/generate", body={"topic_id": topic_id, "length": "feed", "style": "auto"})
check("自动透镜生成受理 202", code == 202 and d.get("article_id"), f"code={code}, {str(d)[:80]}")
article_id = d.get("article_id") if isinstance(d, dict) else None
code, d = request("POST", "/api/articles/generate", body={"topic_id": topic_id, "length": "feed", "style": "auto"})
check("同源在途去重 409", code == 409, f"code={code}")
# 等自动透镜文章生成完成（后续单配图位/kg400 契约需要真实文章）
from runner import poll_job as _pj

gen_job = (d.get("job_id") if isinstance(d, dict) else None) if article_id else None
# 注意：上一步 409 的 d 覆盖了变量，重取生成 job
st, jobs = request("GET", "/api/jobs?limit=50")
gen_job = next((j["id"] for j in (jobs.get("items") or [])
                if j.get("type") == "article_generate" and j.get("status") in ("queued", "running")),
               None)
if gen_job and llm_ready:
    done = _pj(gen_job, timeout=420)
    check("自动透镜生成完成", (done or {}).get("status") == "succeeded",
          f"status={(done or {}).get('status')}")

if article_id and llm_ready:
    # 海报提示词单配图位契约（与前端同 POST 方法）
    code, d = request("POST", f"/api/articles/{article_id}/poster-prompt",
                      body={"slot_index": 0}, timeout=180)
    check("海报单配图位提示词契约（200 + prompt 非空）",
          code == 200 and bool((d or {}).get("prompt")), f"code={code}, {str(d)[:80]}")
    # kg 短正文 400：改稿为短文 → kg 拒绝 → 恢复原稿
    _, art = request("GET", f"/api/articles/{article_id}")
    origin_md = (art or {}).get("md") or ""
    request("PUT", f"/api/articles/{article_id}", body={"md": "太短"})
    code, d = request("POST", f"/api/articles/{article_id}/knowledge-graphic", body={})
    check("知识图解短正文 400", code == 400, f"code={code}")
    request("PUT", f"/api/articles/{article_id}", body={"md": origin_md})

# 非法 style 回落受理（内部回落 deep_dive，不崩溃即契约）
if article_id and not llm_ready:
    # 无 key 实例：上面提交的生成任务会失败但未必立刻离开 generating——
    # 不等就走实体级在途去重（409）会造成假阴性，先轮询到状态流转
    import time as _t
    for _ in range(45):
        _, art = request("GET", f"/api/articles/{article_id}")
        if (art or {}).get("status") != "generating":
            break
        _t.sleep(2)
code, d = request("POST", "/api/articles/generate",
                  body={"topic_id": topic_id, "length": "feed", "style": "bogus_style"})
check("非法 style 回落受理 202", code == 202, f"code={code}")


# ---------- 租户自助品牌配置（R11.3a） ----------
section("P8 · 租户自助品牌配置（R11.3a）")
code, d = request("GET", "/api/tenant/brand")
orig_brand = (d.get("brand") or {})  # 测后还原用
check("自助品牌 GET（本人租户）", code == 200 and orig_brand.get("persona"), f"code={code}")
code, d = request("PUT", "/api/tenant/brand",
                  body={"cover_slogan": "P8自助口号", "audience_note": "P8自助受众"})
check("自助品牌 PUT（租户管理员/平台管理员）", code == 200
      and (d.get("brand") or {}).get("cover_slogan") == "P8自助口号", f"code={code}, {str(d)[:80]}")
code, d = request("GET", "/api/tenant/brand")
check("自助品牌 PUT 读回", (d.get("brand") or {}).get("audience_note") == "P8自助受众", f"code={code}")
# 测后还原原配置（稀疏覆盖会替换整份覆盖键——不还原会打挂后续 E2E 品牌卡值级断言）
code, d = request("PUT", "/api/tenant/brand", body=orig_brand)
check("自助品牌测后还原", code == 200, f"code={code}")

# 成员 403：独立会话（自建 cookie jar，验证角色闸而非登录闸）
import json as _json
import sqlite3 as _sq
import urllib.request as _ur
from urllib.error import HTTPError as _HE

member_name = f"p8_member_{os.urandom(3).hex()}"
code, d = request("POST", "/api/admin/users", body={"username": member_name, "role": "member"})
one_time = d.get("one_time_password") if isinstance(d, dict) else ""
check("P8 成员账号创建（一次性密码）", code in (200, 201) and bool(one_time), f"code={code}")

import http.cookiejar as _cj

member_jar = _cj.CookieJar()
member_op = _ur.build_opener(_ur.HTTPCookieProcessor(member_jar))
req = _ur.Request(BASE + "/api/auth/login", method="POST",
                  data=_json.dumps({"username": member_name, "password": one_time}).encode(),
                  headers={"Content-Type": "application/json"})
with member_op.open(req, timeout=30) as resp:
    resp.read()
# 清首登改密旗（夹具直插，P7 同款）后重新登录拿干净会话
data_dir = os.environ.get("DATA_DIR", "data")
conn = _sq.connect(os.path.join(data_dir, "studio.db"), timeout=30)
conn.execute("UPDATE users SET must_change_password = 0 WHERE username = ?", (member_name,))
conn.commit()
conn.close()
member_op2 = _ur.build_opener(_ur.HTTPCookieProcessor(_cj.CookieJar()))
req = _ur.Request(BASE + "/api/auth/login", method="POST",
                  data=_json.dumps({"username": member_name, "password": one_time}).encode(),
                  headers={"Content-Type": "application/json"})
with member_op2.open(req, timeout=30) as resp:
    resp.read()
req = _ur.Request(BASE + "/api/tenant/brand", method="PUT",
                  data=_json.dumps({"cover_slogan": "越权写入"}).encode(),
                  headers={"Content-Type": "application/json",
                           "Cookie": "; ".join(f"{c.name}={c.value}" for c in member_jar)})
try:
    with member_op2.open(req, timeout=30) as resp:
        code_m = resp.status
except _HE as e:
    code_m = e.code
check("成员改品牌 403（角色闸）", code_m == 403, f"code={code_m}")

sys.exit(finish("P8 功能补全"))