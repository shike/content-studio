"""生产资料包（2026-11-18 车道一）：知识库文档的深度四件套——因果链/操作清单/边界条件/数字账。

过去生成只抽金句与角度，深度原料从未进过生成（用户判「内容毫无深度」的根因）。
资料包首次生成时从正文抽取并缓存 quality.prod_kit，公众号改编与小红书笔记共用；
覆盖核检闸以它为对照——客观对照文档，非 AI 品味（延续「AI 不判爆款、数据/事实判」路线）。"""
from sqlmodel import Session

from ..db import engine
from ..llm import gateway
from ..models import Material

_EMPTY = {"chain": [], "actions": [], "boundaries": [], "numbers": []}


async def ensure_prod_kit(material_id: int, text: str | None = None) -> dict:
    """取文档的生产资料包；没有就从正文抽取并缓存（一次抽取，多线共用）。

    text 显式传入时优先用它（改编线现拉的 ima 全文比库内 body 更全），同样入缓存。"""
    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            return dict(_EMPTY)
        cached = (m.quality or {}).get("prod_kit")
        if cached:
            return cached
        title, text = m.title, text if text is not None else (m.body or "")
    if not text.strip():
        return dict(_EMPTY)
    data = await gateway.complete_json(
        [{"role": "user", "content":
          "从这篇文档提取「生产资料包」——改编成内容时保证深度不流失的四件套。\n"
          "只收文档里真实存在的东西，禁止脑补；文档没有的维度给空数组：\n"
          '{"chain": ["因果链：核心判断为什么成立，一步步，2~5 条"],'
          ' "actions": ["操作清单：读者能照做的具体动作，2~6 条"],'
          ' "boundaries": ["边界条件：何时失效/代价是什么，1~4 条"],'
          ' "numbers": [{"n": "数字", "role": "它在论证什么"}]（数字账 2~6 条）}\n'
          f"\n【标题】{title or ''}\n【正文】{text[:8000]}"}],
        max_tokens=2048, thinking="disabled")
    kit = {
        "chain": [str(x)[:200] for x in (data or {}).get("chain") or []][:5],
        "actions": [str(x)[:200] for x in (data or {}).get("actions") or []][:6],
        "boundaries": [str(x)[:200] for x in (data or {}).get("boundaries") or []][:4],
        "numbers": [{"n": str(x.get("n"))[:40], "role": str(x.get("role"))[:80]}
                    for x in (data or {}).get("numbers") or []
                    if isinstance(x, dict) and x.get("n")][:6],
    }
    if not any(kit.values()):
        return dict(_EMPTY)
    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is not None:
            q = dict(m.quality or {})
            q["prod_kit"] = kit
            m.quality = q
            s.add(m)
            s.commit()
    return kit


def kit_block(kit: dict) -> str:
    """资料包 → 注入 prompt 的文本块（空包返回空串）。"""
    lines = []
    if kit.get("chain"):
        lines.append("因果链（判断为什么成立——必须完整讲到，不许只甩结论）：")
        lines += [f"- {x}" for x in kit["chain"]]
    if kit.get("actions"):
        lines.append("操作清单（读者能照做的动作——必须落到具体步骤）：")
        lines += [f"- {x}" for x in kit["actions"]]
    if kit.get("boundaries"):
        lines.append("边界条件（何时失效/代价是什么——要讲出来）：")
        lines += [f"- {x}" for x in kit["boundaries"]]
    if kit.get("numbers"):
        lines.append("数字账（数字是机制证明的一环，不是装饰）：")
        lines += [f"- {x['n']}（{x['role']}）" for x in kit["numbers"]]
    if not lines:
        return ""
    return "## 生产资料包（深度原料——以下内容必须有机融入成稿，缺环节=深度流失）\n" + "\n".join(lines)
