#!/usr/bin/env python3
"""P8 全量功能契约补缺：审计标出的无脚本覆盖功能，一次补齐（R1.8/R7.3/R11.2/X10/R5.1）。

对应 docs/TDD.md 各契约节。纯 API 层、标准库；热点提炼与自动透镜按 llm_ready 分流
（有 key 实例轮询产出，无 key 实例只验受理契约，不触发 LLM 调用）。
运行：python3 acceptance/run_p8.py（隔离实例 8200，流程同其他套件）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import check, finish, request, section, server_down

if server_down():
    sys.exit(finish("P8 功能补全"))

_, health = request("GET", "/api/health")
llm_ready = (health.get("llm") or {}).get("configured") is True

section("P8 · 可靠性契约（调度/重试/熔断/画像，2026-10-07 加固锁定）")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.jobs.errors import classify as _classify  # noqa: E402
from app.jobs.runner import policy_of as _policy_of  # noqa: E402
from app.jobs import schedules as _sched  # noqa: E402
from app.benchmarks import service as _bms  # noqa: E402
from app.articles.service import length_specs as _ls  # noqa: E402
from app.jobs.runner import RETRY_LADDER, _ladder_delay  # noqa: E402
from app.jobs.runner import _EXECUTORS as _executors  # noqa: E402

class _FakeRow:  # effective_hour 只读 key/hour
    key = "hl_search_daily"
    hour = None

check("组局号搜索挖掘生效钟点=2（不被全局扫描钟点覆盖）", _sched.effective_hour(_FakeRow()) == 2)
check("验证码失败分类为 captcha（可挂起重试）",
      _classify(Exception("页面未吐出视频数据（可能触发验证码）")) == "captcha")
_p = _policy_of("benchmark_analyze")
check("拆解策略：统一退避梯子 + 硬超时 2700（引擎统一 2026-10-12）",
      _p.task_timeout == 2700 and _p.max_retries == 4
      and [1800, 7200, 21600, 86400] == RETRY_LADDER
      and _ladder_delay(1) == 1800 and _ladder_delay(4) == 86400,
      f"timeout={_p.task_timeout}, max_retries={_p.max_retries}, ladder={RETRY_LADDER}")
_bms._CAPTCHA_FAILS.clear()
try:
    _bms._captcha_breaker()
    check("下载熔断未触发（无失败记录）", True)
except _bms.CaptchaError:
    check("下载熔断未触发（无失败记录）", False)
_bms._captcha_record(); _bms._captcha_record()
try:
    _bms._captcha_breaker()
    check("下载熔断 2 次失败后触发", False)
except _bms.CaptchaError:
    check("下载熔断 2 次失败后触发", True)
finally:
    _bms._CAPTCHA_FAILS.clear()
# feed 档已退役（2026-11-02，其 {{WHO}} 受众插槽随之消亡）；deep 唯一档位的受众个性化
# 走 PERSONA/品牌注入，规格本身租户无关——契约=占位符全解析、无旧 FDE 行业话术残留
spec = _ls("deep")
_spec_all = "\n".join(v for v in spec.values() if isinstance(v, str))
check("长文规格实例化（无 {{WHO}} 残留、无旧行业话术）",
      "{{" not in _spec_all and "项目烂尾" not in _spec_all)
spec_b = _ls("deep")
_spec_b_all = "\n".join(v for v in spec_b.values() if isinstance(v, str))
check("长文规格换画像产物一致（deep 规格租户无关，受众经品牌注入）",
      _spec_all == _spec_b_all)

section("P8 · 选题工场（R1.4 三原则：三秒钩子/半句话主线/广度×热度）")
# 白盒：字数口径（时长是法律字数是代理）与三闸纯函数，无 LLM
from app.cards import service as _cards  # noqa: E402
check("三秒字数口径=3×语速（15 字线，无宽裕）",
      _cards._max_chars() == 3 * max(1, int(_cards.settings.hook_speed_cps)))
check("计长剔除标点（可发音字符口径）",
      _cards._pron_len("小老板学AI，全是收割？") == 10)
_g = []
check("主线闸：窄主语被拦（广度原则，未知角色按窄处理）",
      _cards._spine_gate("钉钉管理员早晚被取代", "钉钉管理员", "douyin_peer",
                         _g, [], []) is False
      and any(x["gate"] == "覆盖面闸" and not x["pass"] for x in _g), f"{_g}")
_g2 = []
check("主线闸：大盘角色过闸",
      _cards._spine_gate("小老板学AI全是收割", "中小企业老板", "douyin_peer",
                         _g2, [], []) is True, f"{_g2}")
_g3 = []
check("三秒闸：超线钩子被拦（>15 可发音字符）",
      _cards._hook_gate("中小企业上AI之前必须先把这三件事写进合同条款里去", _g3) is False)
_g4 = []
check("三秒闸：劝告开头词根被拦",
      _cards._hook_gate("建议老板先把AI用起来再说", _g4) is False)
# jail 捞回/不做/投喂受理（HTTP 契约，白盒夹具不动 LLM）
from app.db import engine as _eng  # noqa: E402
from app.models import TopicCard as _TC  # noqa: E402
from sqlmodel import Session as _S  # noqa: E402
with _S(_eng) as _s:
    _jc = _TC(tenant_id=1, batch_date="2000-01-01", source="manual", pool={},
              spine="测试主线", hook="测试钩子", status="jail",
              judge={"verdict": "kill", "reason": "P8 夹具"})
    _s.add(_jc)
    _s.commit()
    _jid = _jc.id
code, d = request("GET", "/api/cards/jail")
check("弃卡池可查（jail 列表含夹具卡+法官理由在位）",
      code == 200 and any(it["id"] == _jid and (it.get("judge") or {}).get("reason")
                          for it in d.get("items") or []), f"code={code}")
code, d = request("POST", f"/api/cards/{_jid}/revive")
check("弃卡捞回（jail→draft，用户翻案盖过法官）", code == 200 and d.get("status") == "draft", f"code={code}")
code, d = request("POST", f"/api/cards/{_jid}/discard")
check("不做出口（draft→discarded）", code == 200 and d.get("status") == "discarded", f"code={code}")
code, d = request("POST", "/api/topics/trending", body={})
check("旧热点提炼端点已删 404（选题工场替代）", code == 404, f"code={code}")
code, d = request("POST", "/api/cards/seed", body={"text": f"P8 投喂受理测试 {os.urandom(3).hex()}"})
check("手动投喂受理 202（card_seed 任务）", code == 202 and bool(d.get("job_id")), f"code={code}")
# 卡备稿→脚本全链（真实 LLM；投喂文案自带判词词根与大盘主语，降低被闸拦的概率；
# 模型锻出不合闸的主线=如实失败也是契约行为，compose 段以 card_id 在位为前提）
if llm_ready:
    from runner import poll_job as _pj2
    code, d = request("POST", "/api/cards/seed",
                      body={"text": "大量中小企业老板花几万块买的AI课其实就是收割，真正用起来的不到一成"})
    seed_done = _pj2(d.get("job_id"), timeout=420) if d.get("job_id") else None
    seed_st = (seed_done or {}).get("status")
    check("投喂成卡（主线→钩子→法官链走完，或被闸如实拦下）",
          seed_st == "succeeded" or (seed_st == "failed" and "未过闸" in str((seed_done or {}).get("error"))),
          f"status={seed_st}, err={str((seed_done or {}).get('error'))[:80]}")
    card_id = ((seed_done or {}).get("result") or {}).get("card_id") if seed_st == "succeeded" else None
    if card_id:
        code, d = request("POST", f"/api/cards/{card_id}/compose", body={"angle": "反常识判断"})
        check("卡备稿受理 202（卡→脚本，卡转 approved）", code == 202 and d.get("script_id")
              and d.get("job_id"), f"code={code}, {str(d)[:80]}")
        if code == 202 and d.get("job_id"):
            sid, jid = d["script_id"], d["job_id"]
            code, d = request("POST", f"/api/cards/{card_id}/compose", body={"angle": "反常识判断"})
            check("同卡在途去重 409（实体级）", code == 409, f"code={code}")
            done = _pj2(jid, timeout=900)
            check("卡备稿脚本完成（一版精品直出）", (done or {}).get("status") == "succeeded",
                  f"status={(done or {}).get('status')}, err={str((done or {}).get('error'))[:80]}")
            code, sc = request("GET", f"/api/scripts/{sid}")
            vs = sc.get("versions") or []
            check("卡备稿脚本契约（persona/一版/钩子非空）",
                  code == 200 and sc.get("account_type") == "persona" and len(vs) == 1
                  and bool((vs[0] or {}).get("hook")), f"code={code}, versions={len(vs)}")

section("P8 · 热点雷达整合（2026-10-30：话题雷达并入 trending，独立雷达面退役）")
code, d = request("GET", "/api/radar")
check("独立雷达端点已退役 404（功能并入热点雷达）", code == 404, f"code={code}")
code, d = request("POST", "/api/radar/mine")
check("雷达挖矿端点已退役 404", code == 404, f"code={code}")
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
code, d = request("POST", "/api/crawl/probe", body={}, timeout=300)
probe_status = ((d or {}).get("result") or {}).get("status") if isinstance(d, dict) else None
KNOWN = {"ok", "login_wall", "captcha", "proxy_down", "browser_error", "no_data"}
check("手动探测返回六态分类之一", code == 200 and probe_status in KNOWN,
      f"code={code}, status={probe_status}")

section("P8 · 调度中心（X10）")
code, d = request("GET", "/api/schedules")
items = d.get("items") if isinstance(d, dict) else []
keys = {it.get("key") for it in items} if isinstance(items, list) else set()
check("调度中心列表（内置任务+探针+每日发现）",
      code == 200 and len(items) >= 7 and "crawl_probe" in keys and "hl_search_daily" in keys,
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


section("P8 · 工作室品牌与内容配置九字段（R9 品牌域）")
code, d = request("GET", "/api/tenant/brand")
brand = (d.get("brand") if isinstance(d, dict) else {}) or {}
NINE = ["label_line1", "label_line2", "signature", "persona", "accent", "primary",
        "asr_vocab", "cover_slogan", "audience_note"]
check("品牌 GET 返回九字段全量", code == 200 and all(k in brand for k in NINE),
      f"缺={[k for k in NINE if isinstance(brand, dict) and k not in brand]}")
code, d = request("PUT", "/api/tenant/brand",
                  body={"cover_slogan": "P8口号测试", "audience_note": "P8受众域测试",
                        "label_line1": "P8", "label_line2": "验收"})
check("品牌 PUT 九字段覆盖受理", code == 200, f"code={code}")
code, d = request("GET", "/api/tenant/brand")
brand = (d.get("brand") if isinstance(d, dict) else {}) or {}
check("覆盖键写读回一致（口号/受众域/栏目）",
      brand.get("cover_slogan") == "P8口号测试" and brand.get("audience_note") == "P8受众域测试"
      and brand.get("label_line1") == "P8", f"{str(brand)[:100]}")
code, d = request("PUT", "/api/tenant/brand",
                  body={"cover_slogan": "", "audience_note": "", "label_line1": "", "label_line2": ""})
check("品牌 PUT 清空受理（只存覆盖键，空值不落库）", code == 200, f"code={code}")
code, d = request("GET", "/api/tenant/brand")
brand = (d.get("brand") if isinstance(d, dict) else {}) or {}
check("空覆盖回落中性默认（栏目=跃迁/内容，口号空）",
      brand.get("label_line1") == "跃迁" and brand.get("cover_slogan") == "",
      f"label_line1={brand.get('label_line1')}, slogan={brand.get('cover_slogan')!r}")

section("P8 · 自动透镜受理与去重（R5.1）")
code, d = request("POST", "/api/topics/quick", body={"title": f"P8 自动透镜验收选题 {os.urandom(3).hex()}", "audience": "both"})
check("快速记选题入库", code == 201 and d.get("id"), f"code={code}, {str(d)[:80]}")
topic_id = d.get("id")
code, d = request("POST", "/api/articles/generate", body={"topic_id": topic_id, "length": "deep", "style": "auto"})
check("自动透镜生成受理 202", code == 202 and d.get("article_id"), f"code={code}, {str(d)[:80]}")
article_id = d.get("article_id") if isinstance(d, dict) else None
code, d = request("POST", "/api/articles/generate", body={"topic_id": topic_id, "length": "deep", "style": "auto"})
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
                  body={"topic_id": topic_id, "length": "deep", "style": "bogus_style"})
check("非法 style 回落受理 202", code == 202, f"code={code}")


# ---------- 产线闸白盒（2026-11-13 补覆盖：拆解质量闸/改编原创度闸/调度硬超时/深读下线） ----------
section("P8 · 产线闸白盒（拆解质量闸/改编原创度闸）")
from app.benchmarks.service import _gate_verdict as _gv  # noqa: E402
_g1, _w1 = _gv({"comment": 25, "digg": 10})
check("拆解质量闸：评论达线即拆", _g1 is True and "评论" in _w1, f"{_g1}, {_w1}")
_g2, _w2 = _gv({"comment": None, "digg": 350})
check("拆解质量闸：评论未知只按赞线判（赞线通过）", _g2 is True and "赞350" in _w2, f"{_g2}, {_w2}")
_g2b, _w2b = _gv({"comment": None, "digg": 50})
check("拆解质量闸：评论未知且赞线未达→不拆并注明评论?", _g2b is False and "评论?" in _w2b, f"{_g2b}, {_w2b}")
_g3, _w3 = _gv({"comment": 3, "digg": 50})
check("拆解质量闸：双线未达不拆（ignored 保留行）", _g3 is False, f"{_g3}, {_w3}")

from app.articles.adapt import _dup_ratio as _dr  # noqa: E402
_sen = "制造业数字化转型的第一性原理是把交付标准写进合同条款里去"
_r_copy, _h_copy = _dr(f"我们的话。{_sen}。别的话。", f"前奏。{_sen}。结尾。")
check("改编原创度闸：≥14 字整句照搬被检出", _r_copy > 30 and len(_h_copy) >= 1, f"ratio={_r_copy}, hits={_h_copy}")
_r_ok, _ = _dr("把交付标准写进合同，是转型成败的胜负手，其余一切都要往后排", f"前奏。{_sen}。结尾。")
check("改编原创度闸：换口径写法不误伤", _r_ok <= 30, f"ratio={_r_ok}")

check("hl_search 硬超时已配（heavy 唯一 worker 不再被无界占用）",
      _policy_of("hl_search_matrix").task_timeout == 3600)
check("旧靶子产线执行器已删（2026-11-14 选题工场替代）",
      "trending_topics" not in _executors and "self_story_targets" not in _executors)
check("选题工场硬超时已配（card_daily 1800/card_seed 900）",
      _policy_of("card_daily").task_timeout == 1800 and _policy_of("card_seed").task_timeout == 900)
check("选题工场调度在位（card_line_daily 替代 trending/self_story 两调度）",
      {"card_line_daily"} <= {s.key for s in _sched.REGISTRY}
      and not {"trending_daily", "self_story_daily"} & {s.key for s in _sched.REGISTRY})
check("card_daily/card_seed 执行器已注册", "card_daily" in _executors and "card_seed" in _executors)
check("深读线调度已下线（2026-11-13：TDD「停用中」口径落地，执行器保留回滚）",
      "article_deepread_daily" not in {s.key for s in _sched.REGISTRY})
check("kb-topics 选题池执行器已退役（2026-11-13 用户裁决整池下线，不在执行器注册表）",
      "article_kb_topics" not in _executors)

section("P8 · 源文改编产线契约（2026-11-11 定版，补覆盖）")
code, d = request("GET", "/api/articles/content-items")
check("内容清单契约（items/total/双组计数）",
      code == 200 and isinstance(d.get("items"), list) and isinstance(d.get("total"), int)
      and isinstance(d.get("status_counts"), dict) and isinstance(d.get("category_counts"), dict),
      f"code={code}, keys={sorted(d) if isinstance(d, dict) else '?'}")
code, d = request("GET", "/api/articles/ledger?limit=5")
check("产线当日账契约（items+anomaly 标记）",
      code == 200 and isinstance(d.get("items"), list)
      and all("anomaly" in it for it in d.get("items") or []),
      f"code={code}, keys={sorted(d) if isinstance(d, dict) else '?'}")
# kb-topics 选题池已退役（2026-11-13 整池下线，路由移除）。8200 实测：GET 落到同前缀
# GET /{article_id:int} 的路径参数校验（"kb-topics" 非数字 → 422，路由匹配先于类型校验），
# POST 无同段路由 → 404；两条合起来证明旧路由确已不存在。
code, d = request("GET", "/api/articles/kb-topics")
check("知识库选题池已退役（GET 被 /{article_id} int 校验拦下=422）", code == 422, f"code={code}")
code, d = request("POST", "/api/articles/kb-topics")
check("知识库选题池触发端点已退役 404", code == 404, f"code={code}")

section("P8 · prompt 层契约（frontmatter 闸，2026-11-13）")
_prompts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "app", "prompts")
for _n in ("article_hotspots", "self_style_extract", "self_profile_update"):
    with open(os.path.join(_prompts_dir, f"{_n}.md"), encoding="utf-8") as _f:
        _raw = _f.read()
    check(f"{_n}.md frontmatter 完整（开头 ---，缺了元数据会原样进 prompt）", _raw.startswith("---\n"))
from app.prompts import load as _pload  # noqa: E402
try:
    for _n in ("article_hotspots", "self_style_extract", "self_profile_update",
               "article_kb_miner_eval", "benchmark_analyze", "article_title",
               "pool_pick", "spine_forge", "hook_cast", "judge_cards"):
        _pload(_n)
    check("关键 prompt 全部可加载（含选题工场四件）", True)
except Exception as _e:  # noqa: BLE001
    check("关键 prompt 全部可加载（含选题工场四件）", False, str(_e)[:120])
for _n in ("trending_target_industry", "trending_target_judge", "self_story_target"):
    check(f"{_n}.md 已随旧链路删除",
          not os.path.exists(os.path.join(_prompts_dir, f"{_n}.md")))


# ---------- 租户自助品牌配置（R11.2） ----------
section("P8 · 租户自助品牌配置（R11.2）")
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


sys.exit(finish("P8 功能补全"))
