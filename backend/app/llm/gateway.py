"""LLM 网关：GLM 为主、DeepSeek 备选，调用重试 + 用量成本台账（docs/技术方案.md §8）。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

import httpx
from sqlmodel import Session

from ..db import engine
from ..jobs.errors import QuotaExhaustedError, TransientError
from ..models import LLMCall
from ..settings import settings

# 元 / 百万 tokens: (输入, 输出)。缺省按 glm-5 计。
# glm-5.3-flash 为估算值（官方未公布牌价，实际走 Coding Plan 套餐额度）
_PRICES = {
    "glm-5": (4, 18),
    "glm-5-turbo": (5, 22),
    "glm-4.7": (2, 8),
    "glm-5.3": (8, 28),
    "glm-5.3-flash": (2, 8),
    "deepseek-chat": (2, 8),
    "deepseek-reasoner": (4, 16),
}


class LLMError(RuntimeError):
    pass


def active() -> dict:
    provider = settings.llm_provider.lower()
    if provider == "deepseek":
        return {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com",
            "key": settings.deepseek_api_key,
            "model": settings.llm_model if settings.llm_model.startswith("deepseek") else "deepseek-chat",
        }
    return {
        "provider": "glm",
        "base_url": settings.zhipu_base_url.rstrip("/"),
        "key": settings.zhipu_api_key,
        "model": settings.llm_model,
    }


def is_configured() -> bool:
    return bool(active()["key"])


def _record(purpose: str, model: str, tokens_in: int, tokens_out: int,
            latency_ms: int, ok: bool) -> None:
    price = _PRICES.get(model, _PRICES["glm-5"])
    cost = (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000
    from ..auth import ACTOR
    tenant_id, _uid, _role = ACTOR.get()
    with Session(engine) as s:
        s.add(LLMCall(purpose=purpose, model=model, tokens_in=tokens_in,
                      tokens_out=tokens_out, cost_est=round(cost, 6),
                      latency_ms=latency_ms, ok=ok, tenant_id=tenant_id))
        s.commit()


# 全局 LLM 并发闸（2026-10-07）：直调端点（知识图解/海报/物料包标签/ping）不入任务队列，
# 也没有任何并发限制——信号量统一收编全部 LLM 调用（队列任务同样过闸，heavy 本就串行无感）
_LLM_SEM: Optional[asyncio.Semaphore] = None
_LLM_CONCURRENCY = 2


def _llm_sem() -> asyncio.Semaphore:
    global _LLM_SEM
    if _LLM_SEM is None:
        _LLM_SEM = asyncio.Semaphore(_LLM_CONCURRENCY)
    return _LLM_SEM


async def complete(messages: list[dict], *, purpose: str = "chat",
                   temperature: float = 0.7, max_tokens: int = 4096,
                   thinking: Optional[str] = None) -> str:
    """thinking="disabled" 时关闭思考链（长文场景：预算全给正文，防推理耗尽空正文）。"""
    cfg = active()
    if not cfg["key"]:
        raise LLMError("LLM 未配置密钥（在 .env 设置 ZHIPU_API_KEY）")
    messages = list(messages)
    # Coding Plan 端点拒收 system 单独成消息（1214）：自动转 user
    if len(messages) == 1 and messages[0].get("role") == "system":
        messages[0] = {**messages[0], "role": "user"}
    body = {"model": cfg["model"], "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if thinking == "disabled":
        body["thinking"] = {"type": "disabled"}
    started = time.perf_counter()
    tokens_in = tokens_out = 0
    ok = False
    last_err: Optional[Exception] = None
    content: Optional[str] = None
    async with _llm_sem():
        return await _complete_locked(
            messages, body=body, cfg=cfg, purpose=purpose,
            started=started, tokens_in=tokens_in, tokens_out=tokens_out,
            ok=ok, last_err=last_err, content=content)


async def _complete_locked(messages: list[dict], *, body: dict, cfg: dict, purpose: str,
                           started: float, tokens_in: int, tokens_out: int,
                           ok: bool, last_err: Optional[Exception],
                           content: Optional[str]) -> str:
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{cfg['base_url']}/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {cfg['key']}"},
                )
                if resp.status_code >= 400:
                    # 带上端点返回的具体错误，只看状态码会误诊
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
            raw = data["choices"][0]["message"]["content"] or ""
            if not raw.strip():
                # 思考型模型 max_tokens 不足时正文为空（推理耗尽预算）
                raise LLMError("模型返回空正文（max_tokens 可能被推理耗尽）")
            content = raw
            usage = data.get("usage") or {}
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            ok = True
            break
        except Exception as e:  # noqa: BLE001 统一重试
            last_err = e
            content = None  # 失败的尝试不得留下半成品正文
            await asyncio.sleep(2 ** attempt)
    _record(purpose, cfg["model"], tokens_in, tokens_out,
            int((time.perf_counter() - started) * 1000), ok)
    if not ok or not content:
        detail = str(last_err) or "未知错误"
        # 任务体系异常分类：额度窗口挂起、通道抖动跨任务重试（P1 骨架）
        if any(k in detail for k in ("1113", "余额不足", "quota")):
            raise QuotaExhaustedError(f"LLM 额度不可用: {detail}")
        if any(k in detail for k in ("Server disconnected", "timed out", "Timeout",
                                     "Connection", "HTTP 5")):
            raise TransientError(f"LLM 调用失败（重试3次）: {detail}")
        raise LLMError(f"LLM 调用失败（重试3次）: {detail}")
    return content


async def complete_json(messages: list[dict], *, purpose: str = "json",
                        temperature: float = 0.3, max_tokens: int = 4096,
                        thinking: Optional[str] = None) -> dict:
    """要求模型输出严格 JSON；解析失败带错误回炉一次。"""
    attempt_msgs = list(messages)
    last_err = "未执行"
    for _ in range(2):
        reply = await complete(attempt_msgs, purpose=purpose,
                               temperature=temperature, max_tokens=max_tokens,
                               thinking=thinking)
        try:
            return _extract_json(reply)
        except ValueError as e:
            last_err = str(e)
            attempt_msgs = list(messages) + [
                {"role": "assistant", "content": reply},
                {"role": "user",
                 "content": f"上面的输出不是合法 JSON（错误：{last_err}）。"
                            f"请只输出一个合法 JSON 对象，不要任何解释或代码块标记。"},
            ]
    raise LLMError(f"结构化输出解析失败: {last_err}")


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("输出中未找到 JSON 对象")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 顶层不是对象")
    return data


def usage_summary(tenant_id: int | None = None) -> dict:
    with Session(engine) as s:
        q = s.query(LLMCall)
        if tenant_id is not None:
            q = q.filter(LLMCall.tenant_id == tenant_id)
        rows = q.all()
    by_purpose: dict[str, dict] = {}
    for r in rows:
        b = by_purpose.setdefault(r.purpose, {"purpose": r.purpose, "calls": 0,
                                              "tokens_in": 0, "tokens_out": 0, "cost_est": 0.0})
        b["calls"] += 1
        b["tokens_in"] += r.tokens_in
        b["tokens_out"] += r.tokens_out
        b["cost_est"] = round(b["cost_est"] + r.cost_est, 4)
    return {
        "calls": len(rows),
        "tokens_in": sum(r.tokens_in for r in rows),
        "tokens_out": sum(r.tokens_out for r in rows),
        "cost_est": round(sum(r.cost_est for r in rows), 4),
        "by_purpose": sorted(by_purpose.values(),
                             key=lambda x: -x["cost_est"]),
    }
