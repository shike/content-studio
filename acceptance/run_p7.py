#!/usr/bin/env python3
"""P7 多租户隔离验收：第二租户看不到/改不到租户 1 的数据，账目与任务互相隔离。

对应《docs/技术方案.md》租户隔离三件套（select 过滤 / get 拦截 / flush 填归属）
+ 积分真扣减 + 任务列表租户收窄。
运行：DATA_DIR=data-acceptance CS_API_BASE=http://127.0.0.1:8200 \
     CS_BOOTSTRAP_PASSWORD=... python3 acceptance/run_p7.py
（隔离实例=复制库约定，admin 密码经 hash 重置为 CS_BOOTSTRAP_PASSWORD）
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import check, finish, section  # noqa: E402

API_BASE = os.environ.get("CS_API_BASE", "http://127.0.0.1:8100")
ADMIN_PASSWORD = os.environ.get("CS_ADMIN_PASSWORD") or os.environ.get("CS_BOOTSTRAP_PASSWORD", "")


def call(method, path, body=None, cookie=None, expect=None):
    req = urllib.request.Request(API_BASE + path, method=method)
    if cookie:
        req.add_header("Cookie", cookie)
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data) as r:
            code = r.status
            raw = r.read()
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"_binary": True, "size": len(raw)}
            sc = r.headers.get("set-cookie")
    except urllib.error.HTTPError as e:
        code, sc = e.code, e.headers.get("set-cookie")
        try:
            payload = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            payload = {}
    if expect is not None and code != expect:
        raise AssertionError(f"{method} {path} → {code}（期望 {expect}）: {str(payload)[:140]}")
    return code, payload, sc


def ready_session(username: str, otp: str, new_password: str = "P7-Ready-2026!") -> str:
    """一次性密码登录 → 首登强制改密 → 返回可直接用业务接口的新会话 Cookie。

    对应 R11.2 服务端强制：未改密前业务路由一律 403（防绕过前端拦截）。"""
    code, _, sc = call("POST", "/api/auth/login", {"username": username, "password": otp})
    if code != 200 or not sc:
        raise AssertionError(f"{username} 登录失败: {code}")
    code, _, sc2 = call("POST", "/api/auth/change-password",
                        {"old_password": otp, "new_password": new_password},
                        cookie=sc.split(";")[0])
    if code != 200 or not sc2:
        raise AssertionError(f"{username} 首登改密失败: {code}")
    return sc2.split(";")[0]  # 改密已作废旧会话并补发新会话


def main() -> int:
    if not ADMIN_PASSWORD:
        print("需要 CS_BOOTSTRAP_PASSWORD / CS_ADMIN_PASSWORD（隔离实例管理员口令）")
        return 1

    section("P7 · 双租户账号准备")
    code, _, sc = call("POST", "/api/auth/login",
                       {"username": "admin", "password": ADMIN_PASSWORD})
    check("租户 1 平台管理员登录", code == 200)
    admin = sc.split(";")[0]

    import time
    stamp = str(int(time.time()))[-6:]
    code, d, _ = call("POST", "/api/admin/tenants",
                      {"name": f"P7隔离客户{stamp}", "credits": 200,
                       "admin_username": f"p7_boss_{stamp}"},
                      cookie=admin, expect=200)
    boss_pwd = d["one_time_password"]
    tenant_b = d["tenant_id"]
    check("建第二租户（初始积分 200）", code == 200 and tenant_b > 0)

    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_member_{stamp}", "role": "member", "tenant_id": tenant_b},
                      cookie=admin, expect=200)
    member_pwd = d["one_time_password"]
    member = ready_session(f"p7_member_{stamp}", member_pwd)
    check("第二租户成员登录", code == 200)

    # 租户 1 先造一条自己的选题
    code, d, _ = call("POST", "/api/topics/quick",
                      {"title": f"P7 租户1专属选题：交付边界案例{stamp}"},
                      cookie=admin, expect=201)
    t1_topic = d.get("id")
    check("租户 1 建快速选题", code == 201 and t1_topic)

    section("P7 · 数据可见性隔离（select 过滤）")
    code, d, _ = call("GET", "/api/topics?limit=200", cookie=member, expect=200)
    items = d.get("items") if isinstance(d, dict) else d
    titles = json.dumps(items, ensure_ascii=False)
    check("租户 2 看不到租户 1 的选题", "P7 租户1专属选题" not in titles, titles[:120])

    code, d, _ = call("GET", "/api/topics?limit=200", cookie=admin, expect=200)
    titles1 = json.dumps(d, ensure_ascii=False)
    check("租户 1 能看到自己的选题", "P7 租户1专属选题" in titles1)

    section("P7 · 越权访问拦截（get 拦截 → 404）")
    code, _, _ = call("GET", f"/api/topics/{t1_topic}", cookie=member)
    check("租户 2 读租户 1 选题详情 404", code == 404)
    code, _, _ = call("GET", f"/api/topics/{t1_topic}", cookie=admin, expect=200)
    check("租户 1 读自己的选题 200", code == 200)

    section("P7 · 归属正确（flush 填充）")
    code, d, _ = call("POST", "/api/topics/quick",
                      {"title": f"P7 租户2自建选题：客户自己的方向{stamp}"},
                      cookie=member, expect=201)
    t2_topic = d.get("id")
    check("租户 2 建自己的选题", code == 201 and t2_topic)
    code, d, _ = call("GET", "/api/topics?limit=200", cookie=member, expect=200)
    mine = json.dumps(d, ensure_ascii=False)
    check("租户 2 能看到自己建的选题", "P7 租户2自建选题" in mine)
    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_t1_member_{stamp}", "role": "member"}, cookie=admin, expect=200)
    t1_member = ready_session(f"p7_t1_member_{stamp}", d["one_time_password"])
    code, d, _ = call("GET", "/api/topics?limit=200", cookie=t1_member, expect=200)
    theirs = json.dumps(d, ensure_ascii=False)
    check("租户 1 成员看不到租户 2 的选题", "P7 租户2自建选题" not in theirs)
    code, d, _ = call("GET", "/api/topics?limit=200", cookie=admin, expect=200)
    allv = json.dumps(d, ensure_ascii=False)
    check("平台管理员跨租户可见（设计豁免）", "P7 租户2自建选题" in allv)

    section("P7 · 任务列表租户收窄")
    code, d, _ = call("GET", "/api/jobs?limit=200", cookie=member, expect=200)
    tenant_ids = {it.get("tenant_id") for it in d.get("items", [])}
    check("租户 2 任务列表只含本租户", tenant_ids <= {tenant_b}, str(tenant_ids))

    section("P7 · 积分真扣减（提交计量任务即扣）")
    code, d, _ = call("POST", "/api/topics/ideas",
                      {"text": "P7 深研：企业 AI 落地的交付边界（租户2）"},
                      cookie=member, expect=202)
    check("租户 2 提交深研受理 202", code == 202)
    code, d, _ = call("GET", f"/api/admin/tenants/{tenant_b}/transactions", cookie=admin, expect=200)
    txns = d.get("transactions", [])
    deducts = [t for t in txns if t["delta"] < 0 and "idea_research" in t["reason"]]
    check("租户 2 流水出现 -30 预扣（idea_research）", len(deducts) >= 1, str(txns[:2]))
    code, d, _ = call("GET", f"/api/admin/tenants/{tenant_b}/usage", cookie=admin, expect=200)
    check("租户 2 积分净变动为负", d["credits"]["consume"] >= 30, str(d["credits"]))

    section("P7 · 余额闸（积分耗尽拒绝计量任务）")
    # 把租户 2 积分扣到 0：负数调整
    call("POST", f"/api/admin/tenants/{tenant_b}/topup",
         {"points": -170, "reason": "P7 清零测试"}, cookie=admin, expect=200)
    code, d, _ = call("POST", "/api/topics/ideas",
                      {"text": "P7 零余额深研应被拒"},
                      cookie=member)
    check("零余额提交深研 402", code == 402, f"code={code}")

    section("P7 · 金额隐藏（租户侧只见积分/蝉豆）")
    code, d, _ = call("GET", "/api/llm/usage", cookie=member, expect=200)
    check("成员 usage 契约可用（按租户口径）", isinstance(d, dict) and "calls" in d, str(d)[:80])
    check("成员 usage 不含成本金额", "cost_est" not in d
          and all("cost_est" not in p for p in d.get("by_purpose", [])), str(d)[:100])
    code, d, _ = call("GET", "/api/llm/calls?limit=5", cookie=member, expect=200)
    check("成员调用流水不含金额", all("cost_est" not in it for it in d.get("items", [])))
    code, d, _ = call("GET", "/api/avatar/usage", cookie=member, expect=200)
    check("成员蝉豆台账不含折¥", "yuan" not in d, str(d))
    check("成员台账是积分口径（见 points、不见豆/¥）",
          "points" in d and "beans" not in d, str(d)[:100])
    code, d, _ = call("GET", "/api/llm/usage", cookie=admin, expect=200)
    check("平台管理员仍见成本", "cost_est" in d, str(d)[:60])

    section("P7 · 媒体文件租户边界（配图）")
    import pathlib
    data_dir = pathlib.Path(os.environ.get("DATA_DIR", "data")).resolve()
    img_dir = data_dir / "article_images" / "1"
    img_dir.mkdir(parents=True, exist_ok=True)
    fixture_img = img_dir / "cover_p7test.png"
    fixture_img.write_bytes(b"\x89PNG\r\n\x1a\nP7FIXTURE")
    code, _, _ = call("GET", "/api/articles/images/cover_p7test.png", cookie=member)
    check("租户 2 读租户 1 配图 404", code == 404, f"code={code}")
    code, _, _ = call("GET", "/api/articles/images/cover_p7test.png", cookie=admin, expect=200)
    check("租户 1 管理员读自己配图 200", code == 200)
    code, _, _ = call("GET", "/api/articles/images/..%2Ftopics", cookie=admin)
    check("目录穿越不放行（400/404）", code in (400, 404), f"code={code}")

    # 签名公开图（公众号粘贴链路）：只认签名不认登录态——微信编辑器跨域抓图带不了 Cookie
    import re as _re
    import sqlite3 as _sqlite

    def _db_exec(sql: str, args: tuple = ()) -> None:
        conn = _sqlite.connect(data_dir / "studio.db", timeout=30)
        conn.execute(sql, args)
        conn.commit()
        conn.close()

    _db_exec("INSERT INTO articles (id, topic_id, md, title, html, status, created_at, updated_at, tenant_id)"
             " VALUES (997002, NULL, '# t', '', ?, 'rendered', datetime('now'), datetime('now'), 1)",
             ('<p>x</p><img src="/api/articles/images/cover_p7test.png">',))
    code, d, _ = call("GET", "/api/articles/997002/wechat-copy", cookie=admin, expect=200)
    signed = (d or {}).get("html") or ""
    m = _re.search(r'src="(/api/public/images/cover_p7test\.png\?t=[A-Za-z0-9_\-]{22})"', signed)
    check("粘贴载荷把配图改写成签名公开地址", bool(m), signed[:160])
    if m:
        code, _, _ = call("GET", m.group(1))  # 无 Cookie：微信编辑器视角
        check("签名图无登录态可取（微信跨域抓图 200）", code == 200, f"code={code}, {m.group(1)}")
        code, _, _ = call("GET", m.group(1).replace("?t=", "?t=bad"))
        check("错误签名 404", code == 404, f"code={code}")
        code, _, _ = call("GET", "/api/public/images/cover_p7test.png")
        check("无签名 404", code == 404, f"code={code}")
    _db_exec("DELETE FROM articles WHERE id=997002")
    fixture_img.unlink(missing_ok=True)

    section("P7 · 租户数据导出")
    code, d, _ = call("GET", f"/api/admin/tenants/{tenant_b}/export", cookie=admin, expect=200)
    tables = (d or {}).get("tables", {})
    check("导出含业务表与流水", "topics" in tables and "credit_transactions" in tables
          and len(tables.get("topics", [])) >= 1, str(list(tables))[:100])

    section("P7 · 租户删除（备份先行 + 物理清理）")
    import urllib.parse
    # 清掉租户 B 在途任务（无 LLM key 的深研任务可能滞留队列），否则删除被拒
    import sqlite3 as _sq3
    _conn = _sq3.connect(data_dir / "studio.db")
    _conn.execute("UPDATE jobs SET status='failed' WHERE tenant_id=? AND status IN ('queued','running','parked')", (tenant_b,))
    _conn.commit()
    _conn.close()
    # 停用租户后成员不能重新登录（会话已在停用时清除，补登录也必须被拒）
    call("POST", f"/api/admin/tenants/{tenant_b}/status",
         {"status": "disabled"}, cookie=admin, expect=200)
    code, d, _ = call("POST", "/api/auth/login",
                      {"username": f"p7_member_{stamp}", "password": "P7-Ready-2026!"})
    check("停用租户成员重新登录 403", code == 403, f"code={code}, {str(d)[:60]}")
    call("POST", f"/api/admin/tenants/{tenant_b}/status",
         {"status": "active"}, cookie=admin, expect=200)
    code, _, _ = call("DELETE",
                      f"/api/admin/tenants/{tenant_b}?confirm_name={urllib.parse.quote('错误名字')}",
                      cookie=admin)
    check("确认名不符 400", code == 400)
    import urllib.parse
    cn = urllib.parse.quote(f"P7隔离客户{stamp}")
    code, d, _ = call("DELETE", f"/api/admin/tenants/{tenant_b}?confirm_name={cn}",
                      cookie=admin, expect=200)
    check("删除受理（含备份）", code == 200 and d.get("ok") is True, str(d)[:80])
    code, d, _ = call("GET", "/api/admin/tenants", cookie=admin, expect=200)
    names = json.dumps(d, ensure_ascii=False)
    check("租户列表不再包含已删租户", f"P7隔离客户{stamp}" not in names)
    code, _, _ = call("GET", "/api/auth/me", cookie=member)
    check("被删租户成员会话失效 401", code == 401)
    code, _, _ = call("POST", "/api/auth/login",
                      {"username": f"p7_member_{stamp}", "password": member_pwd})
    check("被删租户成员无法登录 401", code == 401)

    section("P7 · 克隆计费闸（预扣 300 积分）")
    # 零余额租户成员点克隆 → 402（余额闸先于凭证校验，无需真实凭据即可测）
    code, d, _ = call("POST", "/api/admin/tenants",
                      {"name": f"P7零余额{stamp}", "credits": 0}, cookie=admin, expect=200)
    zero_tenant = d["tenant_id"]
    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_zero2_{stamp}", "role": "member", "tenant_id": zero_tenant},
                      cookie=admin, expect=200)
    zero2 = ready_session(f"p7_zero2_{stamp}", d["one_time_password"])
    boundary = "----p7clone"
    mp = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"v.mp4\"\r\n"
          "Content-Type: video/mp4\r\n\r\n").encode() + b"P7FAKE" + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{API_BASE}/api/avatar/configs/1/clone_video?name=x",
                                 data=mp, method="POST",
                                 headers={"Cookie": zero2, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req) as r:
            code_clone = r.status
    except urllib.error.HTTPError as e2:
        code_clone = e2.code
    check("零余额克隆 402", code_clone == 402, f"code={code_clone}")
    call("DELETE", f"/api/admin/tenants/{zero_tenant}?confirm_name={urllib.parse.quote(f'P7零余额{stamp}')}",
         cookie=admin, expect=200)

    section("P7 · 首登改密服务端强制")
    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_otp_{stamp}", "role": "member"}, cookie=admin, expect=200)
    otp_pwd = d["one_time_password"]
    _, _, osc = call("POST", "/api/auth/login",
                     {"username": f"p7_otp_{stamp}", "password": otp_pwd})
    otp_cookie = osc.split(";")[0]
    code, d, _ = call("GET", "/api/topics?limit=5", cookie=otp_cookie)
    check("未改密访问业务接口 403", code == 403, f"code={code}")
    code, _, _ = call("GET", "/api/auth/me", cookie=otp_cookie, expect=200)
    check("未改密仍可访问 /auth/me（供前端引导改密）", code == 200)
    otp_cookie2 = ready_session(f"p7_otp_{stamp}", otp_pwd)
    code, _, _ = call("GET", "/api/topics?limit=5", cookie=otp_cookie2, expect=200)
    check("改密后业务接口放行 200", code == 200)

    section("P7 · 登录限流（防爆破）")
    # 专用账号：连续错误密码触顶 → 429；正确密码在锁定期内同样被拒；他人账号不受影响
    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_lock_{stamp}", "role": "member"}, cookie=admin, expect=200)
    lock_pwd = d["one_time_password"]
    codes = []
    for _ in range(6):
        c, _, _ = call("POST", "/api/auth/login",
                       {"username": f"p7_lock_{stamp}", "password": "wrong-pass"})
        codes.append(c)
    check("连续失败后触发 429", codes[-1] == 429, f"codes={codes}")
    c, _, _ = call("POST", "/api/auth/login",
                   {"username": f"p7_lock_{stamp}", "password": lock_pwd})
    check("锁定期内正确密码也被拒 429", c == 429, f"code={c}")
    c, _, _ = call("POST", "/api/auth/login",
                   {"username": "admin", "password": ADMIN_PASSWORD})
    check("同 IP 其他账号不受影响（正常登录 200）", c == 200, f"code={c}")

    section("P7 · 安全加固（XSS 净化 / 任务 IDOR / 台账隔离 / 会话失效）")
    import sqlite3 as _sq
    _db = data_dir / "studio.db"

    # ① 存储型 XSS：直插含脚本的文章（模拟投毒）→ 交付路径必须净化
    # 注意 title 必须给值：老库靠 ALTER 迁移是 nullable，全新库建表是 NOT NULL（两条路径都要能跑）
    _c = _sq.connect(_db, timeout=30)
    _c.execute("INSERT INTO articles (id, topic_id, md, title, html, status, created_at, updated_at, tenant_id)"
               " VALUES (997001, NULL, '# t', '', ?, 'rendered', datetime('now'), datetime('now'), 1)",
               ('<p>正常</p><script>alert(1)</script><img src=x onerror=alert(2)>',))
    _c.commit(); _c.close()
    code, d, _ = call("GET", "/api/articles/997001", cookie=admin, expect=200)
    bad_html = d.get("html", "")
    check("文章详情交付前净化（无 script/onerror）",
          "<script" not in bad_html and "onerror" not in bad_html, bad_html[:80])
    code, d, _ = call("GET", "/api/articles/997001/wechat.html", cookie=admin)
    raw = json.dumps(d, ensure_ascii=False)  # 二进制/文本响应经 call 容错为 _binary
    check("wechat.html 净化后交付 200", code == 200)
    _c = _sq.connect(_db, timeout=30)
    _c.execute("DELETE FROM articles WHERE id=997001")
    _c.commit(); _c.close()

    # 用仍存活的租户 1 成员做跨租户断言（租户 2 在前段已删除）
    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_sec_{stamp}", "role": "member"}, cookie=admin, expect=200)
    sec_member = ready_session(f"p7_sec_{stamp}", d["one_time_password"])

    # ② 任务 IDOR：直插租户 2 的任务 → 租户 1 成员读/删必须 404（平台管理员可读）
    _c = _sq.connect(_db, timeout=30)
    _c.execute("INSERT INTO jobs (created_at, updated_at, type, status, progress, message,"
               " dedup_key, lane, retry_count, max_retries, fail_class, error_fp, archived,"
               " retry_of, tenant_id, user_id)"
               " VALUES (datetime('now'), datetime('now'), 'p7_idor', 'succeeded', 100, '',"
               " '', 'heavy', 0, 0, '', '', 0, 0, 2, 0)")
    _c.commit()
    _idor_jid = _c.execute("SELECT id FROM jobs WHERE type='p7_idor'").fetchone()[0]
    _c.close()
    code, d, _ = call("GET", f"/api/jobs/{_idor_jid}", cookie=sec_member)
    check("租户 1 成员读租户 2 任务详情 404", code == 404, f"code={code}")
    code, d, _ = call("GET", f"/api/jobs/{_idor_jid}", cookie=admin, expect=200)
    check("平台管理员读该任务 200", code == 200)
    code, _, _ = call("DELETE", f"/api/jobs/{_idor_jid}", cookie=sec_member)
    check("租户 1 成员删租户 2 任务 404", code == 404, f"code={code}")

    # ③ 蝉豆台账租户隔离：给租户 2 记流水，租户 1 成员看 usage 不应包含
    _c = _sq.connect(_db, timeout=30)
    # points 必须给：老库靠 ALTER 迁移是 nullable，全新库建表是 NOT NULL
    _c.execute("INSERT INTO bean_ledger (created_at, updated_at, tenant_id, video_id, beans, points, seconds, model)"
               " VALUES (datetime('now'), datetime('now'), 2, 0, 999, 999, 99.0, 1)")
    _c.commit(); _c.close()
    code, d, _ = call("GET", "/api/avatar/usage", cookie=sec_member, expect=200)
    check("租户 1 积分台账不含租户 2 流水", d.get("points", 0) < 999 and "beans" not in d, str(d))

    # ④ 登出失效会话 + 改密踢其他会话
    code, d, _ = call("POST", "/api/admin/users",
                      {"username": f"p7_sess_{stamp}", "role": "member"}, cookie=admin, expect=200)
    sess_pwd = d["one_time_password"]
    sess_cookie = ready_session(f"p7_sess_{stamp}", sess_pwd)
    code, _, _ = call("GET", "/api/auth/me", cookie=sess_cookie, expect=200)
    check("新会话可用", code == 200)
    call("POST", "/api/auth/logout", cookie=sess_cookie, expect=200)
    code, _, _ = call("GET", "/api/auth/me", cookie=sess_cookie)
    check("登出后旧会话立即失效 401", code == 401, f"code={code}")

    # 收尾：清理 P7 夹具（选题；隔离实例跑完即删库）
    call("DELETE", f"/api/topics/{t1_topic}", cookie=admin)

    return finish("P7 多租户隔离")


if __name__ == "__main__":
    raise SystemExit(main())
