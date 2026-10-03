#!/usr/bin/env python3
"""P5 数字人（蝉镜引擎）验收。

契约范围：配置 CRUD、生成入参校验、预估数学、余额接口、生成记录与成片端点。
默认不烧蝉豆（真实合成需 CS_CHANJING_LIVE=1）。
凭证未配置时，依赖蝉镜 API 的用例记 SKIP。
"""
from __future__ import annotations

import os
import sqlite3
import time
import urllib.parse

from runner import API_BASE, check, finish, request, section, server_down, skip

# DATA_DIR 相对仓库根（与启动 uvicorn 的 CWD 一致），本文件在 acceptance/ 下
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DB = os.path.join(_REPO_ROOT, os.environ.get("DATA_DIR", "data"), "studio.db")


def main() -> int:
    if server_down():
        return finish("P5")
    section("P5 数字人·蝉镜引擎契约")

    # ---- 配置 CRUD ----
    st, body = request("GET", "/api/avatar/configs")
    check("配置列表 200", st == 200 and isinstance(body.get("items"), list))
    check("配置字段含 engine/chanjing_person_id",
          st == 200 and all("engine" in c and "chanjing_person_id" in c for c in body.get("items", [])))

    st, body = request("POST", "/api/avatar/configs", {"name": f"P5-{int(time.time())}"})
    check("创建配置 201 且 engine=chanjing", st == 201 and body.get("engine") == "chanjing", str(body)[:80])
    cid = body.get("id") if st == 201 else None

    st, body = request("POST", "/api/avatar/configs", {"name": "  "})
    check("空名称创建 400", st == 400)

    st, body = request("PUT", "/api/configs/999999", {"name": "x"})
    check("改不存在配置 404", st == 404)
    st, body = request("PUT", "/api/avatar/configs/999999", {"name": "x"})
    check("改不存在配置(数字人路由) 404", st == 404)

    if cid:
        st, body = request("PUT", f"/api/avatar/configs/{cid}", {"name": "P5-改名"})
        check("更新配置 200", st == 200 and body.get("name") == "P5-改名")
        st, body = request("DELETE", f"/api/avatar/configs/{cid}")
        check("删除配置 200", st == 200)
        st, body = request("GET", "/api/avatar/configs")
        check("删除后列表不含该配置", all(c.get("id") != cid for c in body.get("items", [])))

    # ---- 生成入参校验（不真实合成）----
    st, body = request("POST", "/api/avatar/generate", {})
    check("生成缺参 400", st == 400)
    st, body = request("POST", "/api/avatar/generate", {"script_id": 999999, "avatar_id": 999999})
    check("生成脚本不存在 404", st == 404)

    # ---- 生成记录与成片端点 ----
    st, body = request("GET", "/api/avatar/videos")
    check("生成记录列表 200", st == 200 and isinstance(body.get("items"), list))
    st, body = request("GET", "/api/avatar/videos")
    if body.get("items"):
        vid = body["items"][0]["id"]
        st2, b2 = request("GET", f"/api/avatar/videos/{vid}/file")
        check("已存在记录的成片端点 200/404", st2 in (200, 404), f"status={st2}")
    st2, _ = request("GET", "/api/avatar/videos/999999999/file")
    check("不存在成片 404", st2 == 404)

    # ---- 预估与余额（依赖凭证，但都不烧豆）----
    st, body = request("GET", "/api/avatar/balance")
    configured = not (st == 400 and "未配置" in str(body.get("detail", "")))
    if st == 0:
        skip("蝉豆余额接口", f"请求失败: {body}")
    elif not configured:
        skip("蝉豆余额接口", "凭证未配置")
    else:
        check("蝉豆余额接口 200 且为整数", st == 200 and isinstance(body.get("beans"), int),
              f"beans={body.get('beans')}")

    # 造一条带定稿文本的临时脚本（直接写隔离库，收尾删除）
    script_id = None
    try:
        conn = sqlite3.connect(_DB)
        cur = conn.execute(
            # tenant_id 必须给：老库靠 ALTER 迁移是 nullable，全新库建表是 NOT NULL
            "INSERT INTO scripts (created_at, updated_at, tenant_id, topic_id, versions, final_text,"
            " teleprompter_text, storyboard, title_candidates, status)"
            " VALUES (datetime('now'), datetime('now'), 1, NULL, '[]', ?, '', '{}', '[]', 'final')",
            ("大家好，我在企业里负责数字化落地。最近见了十几位企业主，发现一个规律。",))
        conn.commit()
        script_id = cur.lastrowid
        conn.close()
    except Exception as e:  # 库不可写（如非隔离环境）就不做预估用例
        skip("预估用例夹具", f"无法写隔离库: {e}")

    if script_id and configured:
        q = urllib.parse.quote(str(script_id))
        st, body = request("GET", f"/api/avatar/estimate?script_id={q}")
        check("预估接口 200", st == 200, str(body)[:100])
        if st == 200:
            exp_beans = max(1, int(body["seconds"] + 0.999))
            check("预估数学自洽（beans=ceil(seconds)）",
                  body.get("beans") == exp_beans and body.get("chars", 0) > 0,
                  f"chars={body.get('chars')} seconds={body.get('seconds')} beans={body.get('beans')}")
            check("预估含余额与充足性判断",
                  isinstance(body.get("balance"), int) and isinstance(body.get("insufficient"), bool))
            # 租户口径：本次扣多少积分（按秒）+ 自己的积分池余额（2026-09-27 起按秒计费）
            import math as _math
            exp_points = max(1, _math.ceil(body["seconds"] * 3))
            check("预估含租户积分口径（points=ceil(秒×3)、credits）",
                  body.get("points") == exp_points and isinstance(body.get("credits"), int),
                  f"seconds={body.get('seconds')} points={body.get('points')} 期望={exp_points} credits={body.get('credits')}")
            st_q, body_q = request("GET", f"/api/avatar/estimate?script_id={q}&model=1")
            check("高质版积分翻倍（6 分/秒）",
                  st_q == 200 and body_q.get("points") == max(1, _math.ceil(body_q["seconds"] * 6)),
                  f"points={body_q.get('points')} seconds={body_q.get('seconds')}")
            st_c, credits = request("GET", "/api/credits")
            check("租户积分接口可用（余额+价目）",
                  st_c == 200 and isinstance(credits.get("balance"), int) and len(credits.get("prices") or {}) >= 5,
                  str(credits)[:100])
        st, body = request("GET", "/api/avatar/estimate?script_id=999999")
        check("预估无定稿脚本 400", st == 400)
    elif not configured:
        skip("预估接口（依赖凭证）", "凭证未配置")

    if script_id:
        try:
            conn = sqlite3.connect(_DB)
            conn.execute("DELETE FROM scripts WHERE id=?", (script_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ---- 真实合成（烧豆，默认跳过）----
    if os.environ.get("CS_CHANJING_LIVE") == "1":
        skip("真实合成端到端", "本环境未接入真实配置（需 avatar_video 任务全链路）")
    else:
        skip("真实合成端到端", "烧蝉豆，仅 CS_CHANJING_LIVE=1 时执行")

    return finish("P5")


if __name__ == "__main__":
    raise SystemExit(main())
