"""脚本工场：3 版生成 / 批判打磨 / 终稿三产物。"""
from __future__ import annotations

import json

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import Script, ScriptTemplate, Topic
from ..prompts import load, render
from ..selfstyle.service import build_my_style

DEFAULT_STRUCTURE = [
    {"step": "hook", "requirement": "前3秒点名受众+痛点，制造看下去的理由"},
    {"step": "pain", "requirement": "把痛点讲透，让观众对号入座"},
    {"step": "solution", "requirement": "抛出你的解法或观点，先给结论"},
    {"step": "proof", "requirement": "用具体案例、数字或交付物证明（真实、可验收）"},
    {"step": "cta", "requirement": "明确行动指令：关注 / 评论关键词 / 私信"},
]
DEFAULT_TEMPLATE_NAME = "默认口播结构"


def seed_default_template() -> None:
    with Session(engine) as s:
        if s.exec(select(ScriptTemplate)).first() is None:
            s.add(ScriptTemplate(name=DEFAULT_TEMPLATE_NAME,
                                 structure=DEFAULT_STRUCTURE))
            s.commit()


def _pick_template() -> ScriptTemplate | None:
    with Session(engine) as s:
        return s.exec(
            select(ScriptTemplate)
            .order_by(ScriptTemplate.usage_count.desc(), ScriptTemplate.id)
        ).first()


def _normalize_versions(raw: list) -> list[dict]:
    versions = []
    for v in raw:
        if isinstance(v, dict) and v.get("hook") and v.get("body"):
            versions.append({
                "label": v.get("label", f"版本{len(versions) + 1}"),
                "hook": v["hook"],
                "body": v["body"],
                "notes": v.get("notes", ""),
            })
    return versions[:3]


def _script_generate_fail(payload: dict, _exc: Exception) -> None:
    with Session(engine) as s:  # 失败回退：脚本不得永远卡在"生成中"
        sc = s.get(Script, int(payload.get("script_id") or 0))
        if sc is not None and sc.status in ("generating", "polishing"):
            sc.status = "failed"
            s.add(sc)
            s.commit()


@register_executor("script_generate", on_fail=_script_generate_fail)
async def script_generate(ctx: JobContext, payload: dict) -> dict:
    return await _script_generate(ctx, payload)


async def _script_generate(ctx: JobContext, payload: dict) -> dict:
    script_id = payload["script_id"]
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise RuntimeError(f"script {script_id} 不存在")
        topic = s.get(Topic, script.topic_id) if script.topic_id else None
        topic_info = {
            "title": topic.title if topic else "",
            "angle": topic.angle if topic else "",
            "audience": topic.audience if topic else "both",
            "pain_points": (topic.evidence or {}).get("pain_points", []) if topic else [],
            "hooks": (topic.evidence or {}).get("hooks", []) if topic else [],
        }
    template = _pick_template()
    # 结构模板：prompt 里没有 {{STRUCTURE}} 占位符，注入一直是静默失效（usage_count 照加、
    # 模型收不到结构）——2026-09-27 待用户拍板：恢复注入（模板收尾步要按「不刻意 CTA」改写）
    # 还是下线该功能；定了再补占位符 + 让 render() 强制校验。

    length = payload.get("length", "short")
    length_spec = "600~900 字（2~3 分钟长版）" if length == "long" else "300~500 字（60~90 秒短版）"
    use_style = bool(payload.get("use_style", True))
    from ..tenant_brand import brand_of

    prompt = render("script_generate", LENGTH=length_spec,
                    MY_STYLE=build_my_style(use_style),
                    PERSONA=brand_of(topic.tenant_id if topic else 1)["persona"])
    versions: list[dict] = []
    for attempt in range(2):  # 不足 3 版时补一轮
        ctx.set_progress(10 + attempt * 20, f"生成 3 版脚本（第 {attempt + 1} 次）")
        data = await gateway.complete_json(
            [{"role": "system", "content": prompt},
             {"role": "user", "content": json.dumps(topic_info, ensure_ascii=False)}],
            purpose="script_generate", max_tokens=12000)
        merged = _normalize_versions((data.get("versions") or []))
        seen = {v["hook"] for v in versions}
        versions += [v for v in merged if v["hook"] not in seen]
        if len(versions) >= 3:
            break
    versions = versions[:3]
    if not versions:
        raise RuntimeError("脚本生成失败：没有任何可用版本")

    # 新契约（PRD R2.3）：生成后自动批判打磨一轮
    ctx.set_progress(60, "自动批判打磨一轮（约 3~8 分钟，慢是正常的）")
    versions = await _polish_core(versions, use_style=bool(payload.get("use_style", True)))

    with Session(engine) as s:
        script = s.get(Script, script_id)
        script.versions = versions
        script.status = "generated"
        s.add(script)
        if template is not None:
            tpl = s.get(ScriptTemplate, template.id)
            if tpl:
                tpl.usage_count += 1
                s.add(tpl)
        s.commit()

    ctx.set_progress(100, f"完成，产出 {len(versions)} 版")
    return {"script_id": script_id, "versions": len(versions)}


async def _polish_core(versions: list[dict], use_style: bool = True) -> list[dict]:
    """批判打磨核心：评审→重写→逐版合并（critique 以「｜改进：」并入 notes）。"""
    data = await gateway.complete_json(
        [{"role": "system", "content": render("script_polish", MY_STYLE=build_my_style(use_style))},
         {"role": "user", "content": json.dumps(versions, ensure_ascii=False)}],
        purpose="script_polish", max_tokens=12000)
    polished = _normalize_versions(data.get("versions") or [])
    critiques = data.get("critiques") or []
    merged = list(versions)
    for i, v in enumerate(polished):
        if i < len(merged):
            note = v.get("notes", "")
            if i < len(critiques):
                note = f"{critiques[i]}｜改进：{note}" if note else critiques[i]
            merged[i] = {**merged[i], "hook": v["hook"], "body": v["body"],
                         "notes": note}
    return merged


def _script_polish_fail(payload: dict, _exc: Exception) -> None:
    with Session(engine) as s:  # 打磨失败回退：版本仍在，退回待定稿
        sc = s.get(Script, int(payload.get("script_id") or 0))
        if sc is not None and sc.status == "polishing":
            sc.status = "generated"
            s.add(sc)
            s.commit()


@register_executor("script_polish", on_fail=_script_polish_fail)
async def script_polish(ctx: JobContext, payload: dict) -> dict:
    return await _script_polish(ctx, payload)


async def _script_polish(ctx: JobContext, payload: dict) -> dict:
    script_id = payload["script_id"]
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise RuntimeError(f"script {script_id} 不存在")
        if not script.versions:
            raise RuntimeError("脚本还没有版本，先生成")
        original = list(script.versions)

    ctx.set_progress(30, "批判打磨中")
    merged = await _polish_core(original, use_style=bool(payload.get("use_style", True)))

    with Session(engine) as s:
        script = s.get(Script, script_id)
        script.versions = merged
        script.status = "generated"
        s.add(script)
        s.commit()

    ctx.set_progress(100, "打磨完成")
    return {"script_id": script_id, "polished": len(merged)}


@register_executor("script_finalize")
async def script_finalize(ctx: JobContext, payload: dict) -> dict:
    """定稿任务化（一切待处理工作进任务体系）：产出三件套，重试幂等（覆盖式定稿）。"""
    script_id = payload["script_id"]
    ctx.set_progress(20, "定稿三件套生成中（提词器/分镜/标题）")
    script = await finalize_script(script_id, int(payload.get("version_index") or 0))
    ctx.set_progress(100, "定稿完成")
    first_title = (script.title_candidates or [""])[0] if script.title_candidates else ""
    return {"script_id": script_id, "status": script.status, "title": first_title}


async def finalize_script(script_id: int, version_index: int = 0) -> Script:
    """定稿：一次 LLM 调用，产出提词器/分镜/标题三件套（由 script_finalize 任务驱动）。"""
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise ValueError(f"script {script_id} 不存在")
        versions = script.versions or []
        if not versions:
            raise ValueError("脚本还没有版本，先生成")
        chosen = versions[min(max(version_index, 0), len(versions) - 1)]

    data = await gateway.complete_json(
        [{"role": "system", "content": load("script_finalize")},
         {"role": "user", "content": json.dumps(chosen, ensure_ascii=False)}],
        purpose="script_finalize", max_tokens=8000)

    with Session(engine) as s:
        script = s.get(Script, script_id)
        script.final_text = chosen.get("body", "")
        script.teleprompter_text = data.get("teleprompter_text", "")
        script.storyboard = data.get("storyboard") or []
        script.title_candidates = data.get("title_candidates") or []
        script.status = "final"
        if script.topic_id:
            topic = s.get(Topic, script.topic_id)
            if topic is not None:
                topic.status = "produced"
                s.add(topic)
        s.add(script)
        s.commit()
        s.refresh(script)
    return script
