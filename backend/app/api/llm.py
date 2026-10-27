"""LLM 通道与用量接口。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import require_user
from ..llm import gateway
from ..models import User
from ..settings import settings

router = APIRouter(prefix="/api/llm")


class PingIn(BaseModel):
    prompt: str = "只回复两个字：就绪"


@router.post("/ping")
async def ping(body: PingIn) -> dict:
    if not gateway.is_configured():
        raise HTTPException(status_code=503, detail="LLM 未配置密钥（.env 设置 ZHIPU_API_KEY）")
    # 思考型模型的推理也会消耗 max_tokens，预算给足才能产出正文
    reply = await gateway.complete(
        [{"role": "user", "content": body.prompt}], purpose="ping", max_tokens=1024
    )
    return {"reply": reply}




class SearchProbeIn(BaseModel):
    query: str = "智谱 GLM 最新发布 模型"




@router.post("/search-probe")
async def search_probe(body: SearchProbeIn) -> dict:
    """检索通道 A/B 实测：GLM 与 DDG 对同一查询的原始证据并排返回（人工查验用）。

    两路探测都是同步阻塞调用（各可达 90s），必须 to_thread——直接写在 async 端点里
    会卡死整个事件循环（队列消费/调度/全部请求一起停）。"""
    import asyncio

    import httpx

    from ..search import _ddg_search

    q = body.query.strip() or "智谱 GLM 最新发布 模型"

    def _glm_probe() -> dict:
        try:
            r = httpx.post(
                f"{settings.zhipu_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.zhipu_api_key}"},
                timeout=90,
                json={"model": settings.zhipu_search_model,
                      "messages": [{"role": "user",
                                    "content": f"请联网搜索并给出资料：{q}"}],
                      "tools": [{"type": "web_search", "web_search": {
                          "enable": "True", "search_engine": "search_std",
                          "search_result": "True", "count": "5",
                          "content_size": "high", "search_query": q}}]})
            msg = (r.json().get("choices") or [{}])[0].get("message", {}) if r.status_code == 200 else {}
            return {"http": r.status_code,
                    "usage": r.json().get("usage") if r.status_code == 200 else None,
                    "web_search_field": msg.get("web_search"),
                    "tool_calls": msg.get("tool_calls"),
                    "content_head": (msg.get("content") or "")[:500]}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {str(e)[:120]}"}

    glm, (ddg_results, ddg_status) = await asyncio.gather(
        asyncio.to_thread(_glm_probe),
        asyncio.to_thread(_ddg_search, q, 4))
    return {
        "query": q,
        "glm": glm,
        "glm_verdict": ("有搜索来源" if isinstance(glm.get("web_search_field"), list)
                        and glm["web_search_field"] else "无搜索来源（模型自答）"),
        "ddg": {"status": ddg_status,
                "results": [{"title": i["title"], "url": i["url"]} for i in ddg_results]},
        "ddg_verdict": (f"返回 {len(ddg_results)} 条" if ddg_results
                        else f"未返回（{ddg_status}）"),
    }
