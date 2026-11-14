#!/usr/bin/env python3
"""Content Studio 验收脚本公共库（仅标准库）。

供 run_p0/p1/p6/p7/p8 与 e2e_run.py 引用（P2~P5 已随退役面删除）。判定规则见 README.md：
FAIL = 契约被打破；SKIP = 外部依赖缺失。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API_BASE = os.environ.get("CS_API_BASE", "http://127.0.0.1:8100")
POLL_INTERVAL = 2.0

# 登录态：验收实例须以 CS_BOOTSTRAP_PASSWORD 启动（登录体系上线后全站 401 门禁）
_ADMIN_USER = os.environ.get("CS_ADMIN_USER", "admin")
_ADMIN_PASSWORD = os.environ.get("CS_ADMIN_PASSWORD") or os.environ.get("CS_BOOTSTRAP_PASSWORD", "")
_cookie: str | None = None
_login_attempted = False

_results: list[tuple[str, str, str]] = []


def _ensure_login() -> None:
    """首个请求前自动登录（幂等）。凭据缺失时静默跳过，用例会以 401 显式 FAIL。"""
    global _cookie, _login_attempted
    if _cookie or _login_attempted:
        return
    _login_attempted = True
    if not _ADMIN_PASSWORD:
        return
    try:
        req = urllib.request.Request(
            API_BASE + "/api/auth/login",
            data=json.dumps({"username": _ADMIN_USER, "password": _ADMIN_PASSWORD}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            set_cookie = resp.headers.get("set-cookie", "")
            if set_cookie:
                _cookie = set_cookie.split(";")[0]
    except Exception:  # noqa: BLE001 登录失败不阻断——后续用例以 401 暴露问题
        pass


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append(("PASS" if ok else "FAIL", name, detail))
    suffix = f"  {detail}" if detail else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
    return ok


def skip(name: str, reason: str) -> None:
    _results.append(("SKIP", name, reason))
    print(f"[SKIP] {name}  ({reason})")


def request(method: str, path: str, body: dict | None = None,
            timeout: float = 60.0):
    """发起 HTTP 请求，返回 (status_code, json或文本)。连接失败等异常返回 (0, 错误说明)。

    （multipart files= 组装机制随 P2 本地上传用例裁除 2026-11-14；将来补本地上传验收
    时按需重写。）"""
    url = API_BASE + path
    _ensure_login()
    data = None
    headers: dict[str, str] = {}
    if _cookie:
        headers["Cookie"] = _cookie
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # connection refused 等
        return 0, f"请求失败: {e}"


def poll_job(job_id: int, timeout: float = 300.0):
    """轮询 /api/jobs/{id} 到终态，返回最终 job dict；超时返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = request("GET", f"/api/jobs/{job_id}")
        if status == 200 and isinstance(body, dict) and body.get("status") in ("succeeded", "failed"):
            return body
        time.sleep(POLL_INTERVAL)
    return None


def finish(module: str) -> int:
    p = sum(1 for s, *_ in _results if s == "PASS")
    f = sum(1 for s, *_ in _results if s == "FAIL")
    s = sum(1 for x, *_ in _results if x == "SKIP")
    print(f"\n----- {module} 验收结果: {p} PASS / {f} FAIL / {s} SKIP -----")
    if f:
        print("结论: 未通过（存在 FAIL）")
        return 1
    print("结论: 通过" + ("（含 SKIP 项，原因见上）" if s else ""))
    return 0


def server_down() -> bool:
    """服务不可达时记 FAIL 并打印提示（服务不可达本身就是验收失败）。"""
    status, _ = request("GET", "/api/health", timeout=5)
    if status == 0:
        check("服务可达（CS_API_BASE）", False,
              "未启动：默认 http://127.0.0.1:8100，可用 CS_API_BASE 覆盖")
        return True
    return False

