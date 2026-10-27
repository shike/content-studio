#!/usr/bin/env python3
"""P7 认证与单管理员契约验收（2026-10-27 起：多租户/积分体系已拆除，用户裁定 1A+2B）。

覆盖：未登录 401 / 错误口令 401+限流 429 / 登录会话 / 首登强制改密（服务端 403 口径）/
改密全端重登 / 登出即失效 / 已拆除面（管理后台/积分/切换/台账）路由 404 / 业务数据可见。
运行：DATA_DIR=data-acceptance CS_API_BASE=http://127.0.0.1:8200 \
     CS_BOOTSTRAP_PASSWORD=... python3 acceptance/run_p7.py
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner import check, finish, section  # noqa: E402

API_BASE = os.environ.get("CS_API_BASE", "http://127.0.0.1:8100")
ADMIN_PASSWORD = os.environ.get("CS_ADMIN_PASSWORD") or os.environ.get("CS_BOOTSTRAP_PASSWORD", "")
_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data-acceptance", "studio.db")


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


def main() -> int:
    if not ADMIN_PASSWORD:
        print("需要 CS_BOOTSTRAP_PASSWORD / CS_ADMIN_PASSWORD（隔离实例管理员口令）")
        return 1

    section("P7 · 登录与会话")
    st, _d, _sc = call("GET", "/api/topics")
    check("未登录业务接口 401", st == 401, f"status={st}")
    st, d, _ = call("POST", "/api/auth/login", {"username": "admin", "password": "wrong-password"})
    check("错误口令 401", st == 401, f"status={st}")
    st, d, sc = call("POST", "/api/auth/login", {"username": "admin", "password": ADMIN_PASSWORD})
    check("管理员登录 200 且角色 platform_admin", st == 200 and d.get("role") == "platform_admin",
          str(d)[:120])
    admin = sc.split(";")[0]
    st, d, _sc = call("GET", "/api/auth/me", cookie=admin)
    check("me 返回身份与工作室名", st == 200 and d.get("username") == "admin"
          and bool(d.get("tenant_name")) and "tenants" not in d, str(d)[:140])

    section("P7 · 首登强制改密（服务端口径）")
    if os.path.exists(_DB):
        conn = sqlite3.connect(_DB)
        conn.execute("DELETE FROM users WHERE username='otp-user'")
        conn.execute(
            "INSERT INTO users (created_at, updated_at, tenant_id, username, password_hash,"
            " role, display_name, status, must_change_password)"
            " VALUES (datetime('now'), datetime('now'), 1, 'otp-user', '', 'platform_admin',"
            " 'OTP 用户', 'active', 1)")
        conn.execute(
            "UPDATE users SET password_hash=(SELECT password_hash FROM users WHERE username='admin')"
            " WHERE username='otp-user'")
        conn.commit()
        conn.close()
    else:
        print("隔离库不存在，跳过首登改密夹具")
    st, d, sc_otp = call("POST", "/api/auth/login", {"username": "otp-user", "password": ADMIN_PASSWORD})
    check("一次性密码登录 200 且标记 must_change_password",
          st == 200 and d.get("must_change_password") is True, str(d)[:120])
    otp_cookie = sc_otp.split(";")[0] if sc_otp else ""
    st, d, _sc = call("GET", "/api/topics", cookie=otp_cookie)
    check("未改密前业务接口 403（防绕过前端）", st == 403, f"status={st}")
    st, d, sc_new = call("POST", "/api/auth/change-password",
                         {"old_password": ADMIN_PASSWORD, "new_password": "P7-Ready-2026!"},
                         cookie=otp_cookie)
    check("改密成功", st == 200, str(d)[:100])
    st, d, _sc = call("GET", "/api/topics", cookie=sc_new.split(";")[0])
    check("改密后新会话可用", st == 200, f"status={st}")
    st, d, _sc = call("GET", "/api/topics", cookie=otp_cookie)
    check("改密后旧会话作废（全端重登）", st == 401, f"status={st}")
    st, d, _sc = call("POST", "/api/auth/change-password",
                 {"old_password": "wrong-old", "new_password": "whatever-123"},
                 cookie=sc_new.split(";")[0])
    check("原密码错误 400", st == 400)
    st, d, _sc = call("POST", "/api/auth/change-password",
                 {"old_password": "P7-Ready-2026!", "new_password": "short"},
                 cookie=sc_new.split(";")[0])
    check("新密码过短 400", st == 400)

    section("P7 · 登出与限流")
    st, d, sc_l = call("POST", "/api/auth/login", {"username": "admin", "password": ADMIN_PASSWORD})
    logout_cookie = sc_l.split(";")[0]
    st, _d2, _sc2 = call("POST", "/api/auth/logout", cookie=logout_cookie)
    check("登出 200", st == 200)
    st, _d3, _sc3 = call("GET", "/api/topics", cookie=logout_cookie)
    check("登出后服务端会话作废（401）", st == 401, f"status={st}")
    st, d, _ = call("POST", "/api/auth/login", {"username": "admin", "password": "totally-wrong"})
    check("错口令 401（限流窗口内持续拒绝）", st == 401, f"status={st}")

    section("P7 · 已拆除面（多租户/积分/台账）路由 404")
    for path, method, label in (
        ("/api/auth/switch-tenant", "POST", "租户切换已拆除"),
        ("/api/auth/tenants", "GET", "我的租户列表已拆除"),
        ("/api/credits", "GET", "积分接口已拆除"),
        ("/api/admin/tenants", "GET", "管理后台租户管理已拆除"),
        ("/api/admin/users", "POST", "管理后台成员管理已拆除"),
        ("/api/llm/usage", "GET", "LLM 用量台账已拆除"),
        ("/api/llm/calls", "GET", "LLM 调用流水已拆除"),
        ("/api/avatar/balance", "GET", "蝉豆余额端点已拆除"),
        ("/api/avatar/usage", "GET", "蝉豆消耗台账已拆除"),
    ):
        st, _d, _sc = call(method, path, body={} if method == "POST" else None, cookie=admin)
        check(f"{label}（{path} → 404）", st == 404, f"status={st}")

    section("P7 · 业务数据可见（单租户）")
    st, d, _sc = call("GET", "/api/topics?limit=5", cookie=admin)
    check("选题列表可见且带计数", st == 200 and isinstance(d.get("items"), list))
    st, d, _sc = call("GET", "/api/style/overview", cookie=admin)
    check("我的风格 overview 200", st == 200)
    st, d, _sc = call("GET", "/api/metrics/account", cookie=admin)
    check("账号日级指标端点 200", st == 200)
    st, d, _sc = call("GET", "/api/settings", cookie=admin)
    check("设置页契约 200（管理员依赖保持）", st == 200)
    st, _d5, _sc5 = call("GET", "/api/settings")
    check("设置页未登录 401", st == 401)

    return finish("P7")


if __name__ == "__main__":
    sys.exit(main())
