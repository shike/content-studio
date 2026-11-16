"""check × openapi 差集脚本：套件里引用的 /api 路径 vs 后端静态路由表。

用法：python3 acceptance/drift/check_vs_openapi.py [--md] [--strict]
  --md      以 Markdown 表输出（汇总 + 每套件死端点）
  --strict  存在「不在线」端点时以退出码 1 结束（给 CI 用）

openapi 来源：backend/app/api/*.py 的 APIRouter(prefix=…) 与 @router.<method>(path) 静态合成。
不依赖起服务：main.py:108-127 已 include_router 全部 21 个模块，静态合成与线上 openapi 条目一一对应。
check 来源：acceptance/run_p*.py + e2e_run.py 里的 "/api/..." 字面量。
路径参数统一归一为 {}；拼接前缀（如 /api/articles/images/ + filename）与刻意负断言在 IGNORE 里排除。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "backend" / "app" / "api"
CHECK_FILES = ["run_p0.py", "run_p1.py", "run_p6.py", "run_p7.py", "run_p8.py", "e2e_run.py"]

# 静态扫描误报：拼接前缀（真实路由带文件名参数，在线）
IGNORE_PREFIX = ("/api/articles/images/", "/api/public/images/")
# 负断言：刻意打「已下线/不存在」的路由，绿=删干净了。每条带出处，不计入死端点、
# 也不算进 A 阶段工作量（2026-11-16 对抗讨论定版：P7 九条是契约护栏，P1 飞瓜/P8 trending 同理）。
NEGATIVE_EXACT = {
    "/api/jobs/999999": "run_p0 —— 刻意打不存在的任务，验 404 契约",
    "/api/topics/import/feigua": "run_p1 —— 飞瓜导入已移除，打 404/405 即绿（commit 0cccf40 全删 topics.py）",
    "/api/topics/trending": "run_p8 —— 旧热点提炼端点已删，打 404 即绿（选题工场替代，PRD R1.4）",
    "/api/admin/tenants": "run_p7:119-131 负断言（多租户拆除 a78e200，2026-10-27）",
    "/api/admin/users": "run_p7:119-131 负断言（同上）",
    "/api/auth/switch-tenant": "run_p7:119-131 负断言（切换租户拆除，a78e200）",
    "/api/auth/tenants": "run_p7:119-131 负断言（同上）",
    "/api/avatar/balance": "run_p7:119-131 负断言（数字人退役 4a9ba6a，2026-10-29）",
    "/api/avatar/usage": "run_p7:119-131 负断言（同上）",
    "/api/credits": "run_p7:119-131 负断言（积分体系拆除，a78e200）",
    "/api/llm/calls": "run_p7:119-131 负断言（用量台账不在系统范围，PRD R8.3/X4）",
    "/api/llm/usage": "run_p7:119-131 负断言（同上）",
    "/api/radar": "run_p8:186 负断言——独立雷达端点已退役，打 404 即绿（选题工场替代，PRD R1.8）",
    "/api/radar/mine": "run_p8:188 负断言——雷达挖矿端点已退役，打 404 即绿",
    "/api/articles/kb-topics": "run_p8:348-351 负断言——kb 选题池整池下线（2026-11-13 用户裁决）：GET 非 int 参数断 422、POST 断 404",
}


def norm(p: str) -> str:
    p = p.split("?")[0]
    p = re.sub(r"\{[^}]+\}", "{}", p)
    return (p.rstrip("/") or "/")


def code_routes() -> set[str]:
    routes: set[str] = set()
    for f in API_DIR.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        m = re.search(r'APIRouter\(prefix="([^"]*)"', src)
        prefix = m.group(1) if m else ""
        for mm in re.finditer(r'@router\.(?:get|post|put|patch|delete)\(\s*"([^"]*)"', src):
            routes.add(norm(prefix + mm.group(1)))
    return routes


def check_routes(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8")
    found = {m.group(1) for m in re.finditer(r'''["'](/api/[A-Za-z0-9_\-{}/\.]*)["']''', src)}
    return {r for r in found if not r.endswith((".png", ".jpg", ".json"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    live = code_routes()
    rows = []
    all_eps: set[str] = set()
    for name in CHECK_FILES:
        eps = check_routes(ROOT / "acceptance" / name)
        all_eps |= eps
        dead = sorted(e for e in eps if norm(e) not in live
                      and e not in NEGATIVE_EXACT and not e.startswith(IGNORE_PREFIX))
        rows.append((name, len(eps), sorted(e for e in eps if norm(e) in live), dead))

    neg_all = sorted(e for e in all_eps if e in NEGATIVE_EXACT)
    dead_all = sorted({e for e in all_eps if norm(e) not in live
                       and e not in NEGATIVE_EXACT and not e.startswith(IGNORE_PREFIX)})
    total = len(all_eps)
    pct = round(len(dead_all) / total * 100, 1) if total else 0.0

    if args.md:
        print("# check × openapi 差集（`python3 acceptance/drift/check_vs_openapi.py --md`）\n")
        print("| 套件 | 端点数 | 在线 | 不在线 |")
        print("| --- | --- | --- | --- |")
        for name, n, live_n, dead in rows:
            print(f"| {name} | {n} | {len(live_n)} | {len(dead)} |")
        print(f"| **合计（去重）** | **{total}** | **{total - len(dead_all)}** | **{len(dead_all)}（{pct}%）** |")
        print(f"\n（另有 {len(neg_all)} 条负断言/已下线路由有出处豁免，见下节，不计入死端点）\n")
        print("## 不在线端点（待处理）\n")
        for d in dead_all:
            owners = [name for name, _, _, dd in rows if d in dd]
            print(f"- `{d}` — {', '.join(owners)}")
        print("\n## 负断言 / 已下线路由（有出处，不计入死端点）\n")
        for k in neg_all:
            print(f"- `{k}` — {NEGATIVE_EXACT[k]}")
    else:
        print(f"code routes: {len(live)}")
        for name, n, live_n, dead in rows:
            print(f"{name}: {n} endpoints, {len(dead)} dead")
            for d in dead:
                print(f"  DEAD {d}")
        print(f"negative assertions (documented): {len(neg_all)}")
        for k in neg_all:
            print(f"  NEG  {k}  ({NEGATIVE_EXACT[k]})")
        print(f"total unique: {total}, dead: {len(dead_all)} ({pct}%)")

    return 1 if (args.strict and dead_all) else 0


if __name__ == "__main__":
    sys.exit(main())
