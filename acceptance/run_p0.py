#!/usr/bin/env python3
"""P0 骨架验收：服务、健康检查、前端页面、任务契约、LLM 通道。

对应 docs/TDD.md §13 P0。
运行：python3 acceptance/run_p0.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import check, finish, request, section, server_down, skip

if server_down():
    sys.exit(finish("P0 骨架"))

section("P0 · 服务与基础设施")
status, health = request("GET", "/api/health")
if status != 200 or not isinstance(health, dict):
    check("服务启动且 /api/health 可访问", False, f"status={status}, body={health}")
    sys.exit(finish("P0 骨架"))
check("服务启动且 /api/health 可访问", health.get("status") == "ok")
check("数据库已初始化（health.db=true）", health.get("db") is True)
check("LLM 配置状态可查询", isinstance(health.get("llm"), dict) and "configured" in health["llm"],
      str(health.get("llm")))

section("P0 · 前端页面")
status, page = request("GET", "/")
ok = status == 200 and isinstance(page, str) and "跃迁内容工作室" in page
check("Web 控制台首页可访问且含应用标题", ok,
      "" if ok else f"status={status}，页面应包含标题 '跃迁内容工作室'")

section("P0 · 任务接口契约")
status, jl = request("GET", "/api/jobs?limit=3")
ok = (status == 200 and isinstance(jl, dict)
      and len(jl.get("items") or []) <= 3
      and isinstance(jl.get("total"), int)
      and isinstance(jl.get("counts"), dict)
      and {"running", "queued", "superseded", "failed", "succeeded"} <= set(jl.get("counts") or {}))
check("任务列表契约（分页+counts）", ok, f"status={status}, total={jl.get('total')}")
status, r = request("POST", "/api/jobs/clear", body={"status": "queued"})
check("clear 排队任务拒绝（400）", status == 400, f"status={status}")
status, r = request("DELETE", "/api/jobs/999999")
check("删除不存在任务 404", status == 404, f"status={status}")
status, body404 = request("GET", "/api/jobs/999999")
check("未知任务返回 404 且为 JSON", status == 404 and isinstance(body404, dict),
      f"status={status}")

section("P0 · LLM 通道")
if not (health.get("llm") or {}).get("configured"):
    skip("LLM 真实调用（最小 ping）", "未配置 ZHIPU_API_KEY")
else:
    status, r = request("POST", "/api/llm/ping",
                        body={"prompt": "只回复两个字：就绪"}, timeout=90)
    check("LLM 真实调用返回", status == 200 and bool(r.get("reply")),
          f"status={status}, {str(r)[:120]}")
    status, usage = request("GET", "/api/llm/usage")
    check("LLM 用量台账有记录", status == 200 and usage.get("calls", 0) >= 1,
          f"status={status}, {str(usage)[:120]}")

section("P0 · 设置接口")
status, conf = request("GET", "/api/settings")
ok = (status == 200 and isinstance(conf, dict)
      and conf.get("llm_model") and conf.get("search_provider")
      and isinstance(conf.get("llm_configured"), bool))
check("设置可读（模型/检索通道/配置态）", ok, f"status={status}, {str(conf)[:120]}")
if ok:
    # 非法值必须 400（不落盘）；写回当前同名值幂等（.env 内容不变）
    status, r = request("PUT", "/api/settings",
                        body={"llm_model": "no-such-model"})
    check("设置写入非法模型被拒（400）", status == 400, f"status={status}")
    status, r = request("PUT", "/api/settings",
                        body={"llm_model": conf["llm_model"],
                              "search_provider": conf["search_provider"]})
    check("设置写回当前值成功（幂等）", status == 200 and r.get("llm_model") == conf["llm_model"],
          f"status={status}")
    # 运行时配置落库（app_settings）+ 密钥只写不读
    check("密钥只写不读（仅回报已配置标记，无明文字段）",
          isinstance(conf.get("zhipu_api_key_set"), bool)
          and "zhipu_api_key" not in conf and "chanjing_secret_key" not in conf,
          f"set={conf.get('zhipu_api_key_set')}")
    import os as _os, sqlite3 as _sq
    _db = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                        _os.environ.get("DATA_DIR", "data"), "studio.db")
    try:
        _c = _sq.connect(_db, timeout=30)
        _n = _c.execute("SELECT COUNT(*) FROM app_settings").fetchone()[0]
        _c.close()
        check("运行时配置已落库 app_settings", _n >= 1, f"rows={_n}")
    except Exception as e:  # noqa: BLE001
        check("运行时配置已落库 app_settings", False, str(e)[:80])

sys.exit(finish("P0 骨架"))
