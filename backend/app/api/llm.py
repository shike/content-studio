"""LLM 通道与用量接口。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..llm import gateway

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
