#!/usr/bin/env python3
"""Content Studio 验收脚本公共库（仅标准库）。

供 run_p0.py ~ run_p4.py 引用。判定规则见 README.md：
FAIL = 契约被打破；SKIP = 外部依赖缺失。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

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
            files: dict | None = None, timeout: float = 60.0):
    """发起 HTTP 请求，返回 (status_code, json或文本)。

    files: {字段名: (文件名, bytes, content_type)}，自动组 multipart。
    连接失败等异常返回 (0, 错误说明)。
    """
    url = API_BASE + path
    _ensure_login()
    data = None
    headers: dict[str, str] = {}
    if _cookie:
        headers["Cookie"] = _cookie
    if files is not None:
        boundary = "----cs" + uuid.uuid4().hex
        buf = bytearray()
        for k, v in (body or {}).items():
            buf += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
        for k, (fn, blob, ctype) in files.items():
            buf += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                    f'Content-Type: {ctype}\r\n\r\n').encode()
            buf += blob + b"\r\n"
        buf += f"--{boundary}--\r\n".encode()
        data = bytes(buf)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
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


def ensure_speech_video(path: str) -> str | None:
    """生成带中文语音的测试视频，返回路径；依赖缺失返回 None。"""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "make_video.py")
    ret = subprocess.run(
        [sys.executable, script, "--out", path, "--speech",
         "大家好，今天聊一聊传统工厂如何用人工智能做质量检测。"
         "我们先从一条试点产线讲起，两周时间就能看到可验收的结果，"
         "先有交付物，再谈规模化，这是这套做法的基本方式。"],
        capture_output=True, text=True)
    if ret.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    print(f"    fixture 生成失败: {ret.stderr.strip()[:200]}")
    return None
