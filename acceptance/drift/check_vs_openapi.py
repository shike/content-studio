"""check × openapi 差集脚本：套件里引用的 /api 路径 vs 后端静态路由表。

用法：python3 acceptance/drift/check_vs_openapi.py [--md] [--strict]
  --md      以 Markdown 表输出（汇总 + 每套件死端点）
  --strict  存在「不在线」端点时以退出码 1 结束（给 CI 用）

openapi 来源：backend/app/api/*.py 的 APIRouter(prefix=…) 与 @router.<method>(path) 静态合成。
不依赖起服务：api/ 共 21 个 .py，其中 19 个注册了 APIRouter，main.py:108-127 把这 19 个全部 include_router（__init__ 与 _listing 非路由），静态合成与线上 openapi 条目一一对应。模块名集合断言见 code_routes()。
check 来源：acceptance/run_p*.py + e2e_run.py 里的 "/api/..." 字面量。
路径参数统一归一为 {}；拼接前缀（如 /api/articles/images/ + filename）与刻意负断言在 IGNORE 里排除。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "backend" / "app" / "api"
CHECK_FILES = ["run_p0.py", "run_p1.py", "run_p6.py", "run_p7.py", "run_p8.py", "e2e_run.py"]

# 静态扫描误报：拼接前缀（真实路由带文件名参数，在线）
IGNORE_PREFIX = ("/api/articles/images/", "/api/public/images/")# 负断言：刻意打「已下线/不存在」的路由，绿=删干净了。每条带出处，不计入死端点、
# 也不算进 A 阶段工作量（2026-11-16 对抗讨论定版：P7 九条是契约护栏，P1 飞瓜/P8 trending 同理）。
NEGATIVE_EXACT = {
    "/api/jobs/999999": "run_p0 —— 刻意打不存在的任务，验 404 契约",
    "/api/topics/import/feigua": "run_p1 —— 飞瓜导入已移除，打 404/405 即绿（commit 0cccf40 全删 topics.py）",
    "/api/topics/trending": "run_p8 —— 旧热点提炼下线后的存量兼容探针（注：/api/topics/* 现已全 404，本条不再有区分度，DSH 建议下轮删）",
    "/api/admin/tenants": "run_p7:119-132 负断言（多租户拆除 a78e200，2026-10-27）",
    "/api/admin/users": "run_p7:119-132 负断言（同上）",
    "/api/auth/switch-tenant": "run_p7:119-132 负断言（切换租户拆除，a78e200）",
    "/api/auth/tenants": "run_p7:119-132 负断言（同上）",
    "/api/avatar/balance": "run_p7:119-132 负断言（数字人退役 4a9ba6a，2026-10-29）",
    "/api/avatar/usage": "run_p7:119-132 负断言（同上）",
    "/api/credits": "run_p7:119-132 负断言（积分体系拆除，a78e200）",
    "/api/llm/calls": "run_p7:119-132 负断言（用量台账不在系统范围，PRD R8.3/X4）",
    "/api/llm/usage": "run_p7:119-132 负断言（同上）",
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
    router_modules: set[str] = set()
    for f in API_DIR.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        m = re.search(r'APIRouter\(prefix="([^"]*)"', src)
        prefix = m.group(1) if m else ""
        hits = re.findall(r'@router\.(?:get|post|put|patch|delete)\(\s*"([^"]*)"', src)
        if hits:
            router_modules.add(f.stem)
        for path in hits:
            routes.add(norm(prefix + path))
    return routes, router_modules


def main_module_names() -> set[str]:
    """main.py 里 include_router 的模块名（别名还原成 api/ 下的文件名：auth_routes→auth_api）。"""
    src = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    block = re.search(r"from \.api import \(([^)]*)\)", src, re.S)
    alias2mod: dict[str, str] = {}
    for part in (block.group(1) if block else "").split(","):
        part = part.strip()
        if not part:
            continue
        mod, _, alias = part.partition(" as ")
        alias2mod[(alias or mod).strip()] = mod.strip()
    return {alias2mod.get(a, a) for a in re.findall(r"app\.include_router\((\w+)\.router", src)}


def check_routes(path: Path) -> tuple[set[str], int]:
    """返回 (入库端点集合, raw 匹配条数)。raw 是正则匹配条数，不是行数（跨行 f-string 时两者不同）。"""
    src = path.read_text(encoding="utf-8")
    # 宽捕获 [^"']：f-string 内插（如 /api/jobs/failures/{urllib.parse.quote(x)}/resolve）
    # 也能整条抓到；norm() 剥 query 并把 {...} 归一为 {}（括号随花括号内容一起消掉）；
    # 最后严格字符校验拦 f-string 碎片（/api/scripts/{d.get( 这类残片过不了关）。
    # 2026-11-16 两轮修正：仅加 ? 会漏带括号的 1 条活端点；仅宽捕获会放进代码碎片。
    found = [m.group(1) for m in re.finditer(r'''["'](/api/[^"']*)["']''', src)]
    keep: set[str] = set()
    for r in found:
        if r.endswith((".png", ".jpg", ".json")):
            continue
        n = norm(r)
        if re.fullmatch(r"/api/[\w\-{}/\.]*", n):
            keep.add(n)
    return keep, len(found)


def head_line() -> str:
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    sha = git("rev-parse", "--short", "HEAD") or "?"
    state = "dirty" if git("status", "--porcelain") else "clean"
    return f"HEAD {sha} / {state}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    live, router_modules = code_routes()
    included = main_module_names()
    if router_modules != included:
        print(f"main.py include_router 模块集合与 api/ 有 APIRouter 的文件集合不一致："
              f"缺 {sorted(router_modules - included)}，多 {sorted(included - router_modules)}",
              file=sys.stderr)
        return 2
    ignore_norm = tuple(norm(p) for p in IGNORE_PREFIX)
    neg_norm = {norm(k): v for k, v in NEGATIVE_EXACT.items()}

    def ignored(e: str) -> bool:
        """前缀豁免按已 rstrip 的 norm 值做 startswith（将来出现 /api/articles/images/x.png 也不会漏）。"""
        return any(e == p or e.startswith(p + "/") for p in ignore_norm)

    rows = []
    all_eps: set[str] = set()
    raw_all = 0
    raw_in_eps = 0
    raw_uniq: set[str] = set()
    for name in CHECK_FILES:
        eps, raw_n = check_routes(ROOT / "acceptance" / name)
        raw_all += raw_n
        raw_in_eps += len(eps)
        raw_uniq |= eps
        normed = {norm(e) for e in eps}
        all_eps |= normed
        # 三类按 norm 后分别数：在线 / 负断言 / 前缀豁免；剩下才是死端点。
        in_neg = sorted(e for e in normed if e in neg_norm)
        in_pre = sorted(e for e in normed if e not in neg_norm and ignored(e))
        up = sorted(e for e in normed
                    if e in live and e not in neg_norm and not ignored(e))
        dead = sorted(e for e in normed
                      if e not in live and e not in neg_norm and not ignored(e))
        rows.append((name, len(normed), up, in_neg, in_pre, dead))

    neg_all = sorted(e for e in all_eps if e in neg_norm)
    pre_all = sorted(e for e in all_eps if e not in neg_norm and ignored(e))
    live_all = sorted(e for e in all_eps
                      if e in live and e not in neg_norm and not ignored(e))
    dead_all = sorted({e for e in all_eps if e not in live
                       and e not in neg_norm and not ignored(e)})
    total = len(all_eps)
    assert total == len(live_all) + len(neg_all) + len(pre_all) + len(dead_all)
    pct = round(len(dead_all) / total * 100, 1) if total else 0.0
    summary = (f"HEAD {head_line()[5:]}\n"
               f"raw {raw_all} 条（正则匹配，非行数；按文件去重后 {raw_in_eps}） / "
               f"raw 唯一 {len(raw_uniq)}\n"
               f"norm 唯一 {total}\n"
               f"后端路由 {len(live)}")

    if args.md:
        print(f"# check × openapi 差集（`python3 acceptance/drift/check_vs_openapi.py --md`）\n")
        print("```\n" + summary + "\n```\n")
        print("| 套件 | 端点数 | 在线 | 负断言 | 前缀豁免 | 不在线 |")
        print("| --- | --- | --- | --- | --- | --- |")
        for name, n, up, in_neg, in_pre, dead in rows:
            print(f"| {name} | {n} | {len(up)} | {len(in_neg)} | {len(in_pre)} | {len(dead)} |")
        print(f"| **合计（去重）** | **{total}** | **{len(live_all)}** | **{len(neg_all)}** | "
              f"**{len(pre_all)}** | **{len(dead_all)}（{pct}%）** |")
        print("\n（合计三列相加 = 端点数，死端点单列；负断言出处见末节）\n")
        print("## 不在线端点（待处理）\n")
        for d in dead_all:
            owners = [name for name, _, _, _, _, dd in rows if d in dd]
            print(f"- `{d}` — {', '.join(owners)}")
        print("\n## 负断言 / 已下线路由（有出处，不计入死端点）\n")
        for k in neg_all:
            print(f"- `{k}` — {NEGATIVE_EXACT[k]}")
        if pre_all:
            print("\n## 前缀豁免（拼接前缀，真实路由在）\n")
            for k in pre_all:
                print(f"- `{k}` — 命中 IGNORE_PREFIX")
    else:
        print(summary)
        for name, n, up, in_neg, in_pre, dead in rows:
            print(f"{name}: {n} endpoints, {len(up)} live + {len(in_neg)} neg + "
                  f"{len(in_pre)} prefix + {len(dead)} dead")
            for d in dead:
                print(f"  DEAD {d}")
        print(f"negative assertions (documented): {len(neg_all)}")
        for k in neg_all:
            print(f"  NEG  {k}  ({neg_norm[k]})")
        print(f"total unique: {total} = {len(live_all)} live + {len(neg_all)} neg + "
              f"{len(pre_all)} prefix + {len(dead_all)} dead ({pct}%)")

    return 1 if (args.strict and dead_all) else 0


if __name__ == "__main__":
    sys.exit(main())
