"""脚本工场：3 版生成 / 批判打磨 / 终稿三产物。"""
from __future__ import annotations

import json

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import FalsifyItem, Script, ScriptTemplate, Topic, TopicCard
from ..prompts import load, render
from ..selfstyle.service import build_my_style

DEFAULT_STRUCTURE = [
    {"step": "hook", "requirement": "前3秒甩判断或喊话受众（反常识/身份定位/行动指令/提问，禁自我介绍与背景铺垫）"},
    {"step": "pain", "requirement": "把痛点讲透，让观众对号入座"},
    {"step": "solution", "requirement": "抛出你的解法或观点，先给结论"},
    {"step": "proof", "requirement": "用具体案例、数字或交付物证明（真实、可验收）"},
    {"step": "cta", "requirement": "给观众一个业务上的下一步动作或判断（不出现关注/评论/私信等转化话术）"},
]
DEFAULT_TEMPLATE_NAME = "默认口播结构"

# 六打法（2026-10-09 流量诊断定版，与 script_generate.md 同表；前端 chips 与 API 校验共用）
SIX_ANGLES = ["反常识判断", "身份定位喊话", "行动指令", "提问式", "对比打假", "数字落差"]


def seed_default_template() -> None:
    with Session(engine) as s:
        if s.exec(select(ScriptTemplate)).first() is None:
            s.add(ScriptTemplate(name=DEFAULT_TEMPLATE_NAME,
                                 structure=DEFAULT_STRUCTURE))
            s.commit()


def _script_length_spec(length: str) -> str:
    """两档字数口径（单点定义，生成与打磨两处共用）。

    2026-10-09 流量诊断定档：自己账号 40~70s 视频中位播放 441，<40s 只有 248，
    >120s 更差 → 默认档锁定黄金带 240~320 字（50~70 秒）；长版保留给视频号场景。"""
    return ("600~900 字（2~3 分钟长版）" if length == "long"
            else "240~320 字（50~70 秒黄金档）")


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
        card = s.get(TopicCard, int(payload["card_id"])) if payload.get("card_id") else None
        if payload.get("card_id") and card is None:
            raise RuntimeError(f"card {payload['card_id']} 不存在")
        falsify = (s.get(FalsifyItem, int(payload["falsify_item_id"]))
                   if payload.get("falsify_item_id") else None)
        if payload.get("falsify_item_id") and falsify is None:
            raise RuntimeError(f"falsify item {payload['falsify_item_id']} 不存在")
        if falsify is not None:
            # 实测打假稿（R1.4③）：result_note=唯一事实来源，没实测的环节一个字不许编
            topic_info = {
                "title": falsify.claim[:60],
                "angle": "",
                "audience": "both",
                "pain_points": [],
                "hooks": [],
                "claim": falsify.claim,
                "target": falsify.target,
                "evidence": (falsify.result_note or "")[:1500],
                "spice": "high",
            }
        elif card is not None:
            # 靶子卡备稿（选题工场 R1.4）：钩子/主线由卡带（备稿时可被用户改写），spice 辣度入通道
            topic_info = {
                "title": str(payload.get("hook") or card.hook)[:60],
                "angle": str(payload.get("angle") or ""),
                "audience": "both",
                "pain_points": [],
                "hooks": [str(payload.get("hook") or card.hook)[:60]],
                "spine": str(payload.get("spine") or card.spine)[:60],
                "spice": payload.get("spice") or card.spice or "mid",
            }
        else:
            topic_info = {
                "title": topic.title if topic else "",
                "angle": topic.angle if topic else "",
                "audience": topic.audience if topic else "both",
                "pain_points": (topic.evidence or {}).get("pain_points", []) if topic else [],
                "hooks": (topic.evidence or {}).get("hooks", []) if topic else [],
            }
    template_id = int(payload.get("template_id") or 0)
    template = None
    if template_id:
        with Session(engine) as s:
            template = s.get(ScriptTemplate, template_id)
        if template is None:
            print(f"[script] 结构模板 #{template_id} 不存在，按不使用处理")
    # 结构模板注入已生效（script_generate.md 有 {{STRUCTURE}} 占位符 + render() 强制校验；
    # 2026-09-27 的「静默失效待拍板」问题已在恢复注入时闭环，此注释 2026-11-13 更新）。

    length = payload.get("length", "short")
    # 打法：指定候选（≤3 个，单版直出取最贴合的一个）或自动挑（2026-11-05 一版精品定版）
    angles = [a for a in (payload.get("angles") or []) if a in SIX_ANGLES][:3]
    if angles:
        angles_spec = ("本版主打打法：从下列指定打法里选**最贴合本选题的一个**，label 直接用打法名，不得换用其他打法：\n"
                       + "、".join(angles))
    else:
        angles_spec = "从上面六个里挑一个最贴合这个选题的"
    # 2026-10-09 流量诊断定档：默认档锁定黄金带 240~320 字（见 _script_length_spec）
    length_spec = _script_length_spec(length)
    use_style = bool(payload.get("use_style", True))
    from ..tenant_brand import brand_of

    if template is not None:
        structure_text = "本版按以下段落结构推进（每步的覆盖要求）：\n" + "\n".join(
            f"- {st.get('step', '')}：{st.get('requirement', '')}"
            for st in (template.structure or []) if st.get("step"))
    else:
        structure_text = "（未选用结构模板：按上方「口播结构」硬规则组织——判断开场、单判断推进、可转述判断收尾）"
    # 两类号策略（2026-10-28）：账号模式决定内容风格——人设号禁引导，获客号要求获客引导
    # 2026-11-13 修复：原三元式在 topic 有但 evidence 无 account_type 时 str(None)="None"
    # 落库脏值，列表按 persona/leadgen 筛选两边都不中
    _raw_at = payload.get("account_type") \
        or ((topic.evidence or {}).get("account_type") if topic is not None else "")
    account_type = str(_raw_at or "") or "persona"
    account_mode = ("。本条面向【获客号】：结尾必须切获客引导——评论区扣指定词/私信关键词/主页入口，引导要具体"
                    if account_type == "leadgen"
                    else "。本条面向【人设号】：结尾判断之外允许带一句自然的关注/合集引导（软引导 2026-11-02 放开），"
                         "仍禁评论区扣字/私信领取/卖课导流")
    if falsify is not None:
        # 实测打假稿三段纪律（PRD R1.4③）：钩子=实测结果数字/中段=复盘只准用实测事实/判词落证据
        discipline_mode = "实测打假稿·三段纪律（硬规则）"
        card_discipline = (
            "- 开场（前 3 秒）：**钩子直接报实测结果数字/结论**——从「实测信息」里挑最狠的一个数字或事实"
            "一句话砸出来；观点说清+抓人，禁悬念、禁铺垫、禁背景介绍\n"
            "- 中段：**实测过程复盘**——时间线+「宣称 vs 实际」数字对比；只准使用「实测信息」里的真实事实，"
            "没实测过的环节一个字不许编\n"
            "- 判词落在实测证据上：结论必须被你的数据撑住；结尾自然收束，禁引导话术"
        )
        prompt = render("falsify_script", LENGTH=length_spec,
                        MY_STYLE=build_my_style(use_style),
                        PERSONA=brand_of(topic.tenant_id if topic else 1)["persona"],
                        DISCIPLINE_MODE=discipline_mode, CARD_DISCIPLINE=card_discipline,
                        EVIDENCE=json.dumps(topic_info, ensure_ascii=False))
    else:
        if card is not None:
            # 三段纪律（PRD R2.1 选题工场口径）：开场逐字用钩子/中段单判断论证主线且主线不出口/收尾不复述
            discipline_mode = "靶子卡备稿·三段纪律（硬规则）"
            card_discipline = (
                f"- 开场（前 3 秒）：**逐字使用钩子**「{payload.get('hook') or card.hook}」——最多 2 个字的口语化微调，"
                "禁换成话题引入/悬念式开场；钩子里的观点必须完整说出\n"
                f"- 中段：只论证主线「{payload.get('spine') or card.spine}」，单判断推进，一切案例与数字为它服务；"
                "**主线这句话本身禁止出现在正文里**（不进台词、不进字幕、不进收尾点题——它是写作纪律，不是台词）\n"
                "- 结尾：自然收束停在最有力的判断上；**禁止复述主线、禁止「总结一下」式收尾**"
            )
        else:
            discipline_mode = "常规备稿"
            card_discipline = "- 开场按上方「口播结构」硬规则（前 3 秒直接甩判断或喊话受众）；主切角以 angle 字段为准"
        prompt = render("script_generate", LENGTH=length_spec,
                        MY_STYLE=build_my_style(use_style),
                        ANGLES_SPEC=angles_spec, STRUCTURE=structure_text,
                        PERSONA=brand_of(topic.tenant_id if topic else 1)["persona"],
                        ACCOUNT_MODE=account_mode,
                        DISCIPLINE_MODE=discipline_mode, CARD_DISCIPLINE=card_discipline)
    # 2026-11-05 一版精品直出（用户定版：质量优先于选择）：单版本，预算全给打磨环节
    ctx.set_progress(20, "生成精品脚本（单版全预算）")
    data = await gateway.complete_json(
        [{"role": "system", "content": prompt},
         {"role": "user", "content": json.dumps(topic_info, ensure_ascii=False)}],
        max_tokens=20000)  # v7 prompt 变长，思考链耗尽 12000 致空正文（2026-10-10 实测）
    versions = _normalize_versions((data.get("versions") or []))[:1]
    if not versions:
        raise RuntimeError("脚本生成失败：没有任何可用版本")

    # 新契约（PRD R2.3）：生成后自动批判打磨一轮
    ctx.set_progress(60, "自动批判打磨一轮（约 3~8 分钟，慢是正常的）")
    versions = await _polish_core(versions, use_style=bool(payload.get("use_style", True)),
                                  length=length, cta_rule=_cta_rule_of(account_type))

    with Session(engine) as s:
        script = s.get(Script, script_id)
        script.versions = versions
        script.status = "generated"
        script.account_type = account_type
        s.add(script)
        if template is not None:  # usage_count 只记真实注入（此前占位符缺失静默失效也在计数——假账已修）
            tpl = s.get(ScriptTemplate, template.id)
            if tpl:
                tpl.usage_count += 1
                s.add(tpl)
        s.commit()

    ctx.set_progress(100, f"完成，产出 {len(versions)} 版")
    return {"script_id": script_id, "versions": len(versions)}


def _cta_rule_of(account_type: str) -> str:
    """CTA 政策单点定义（生成链与手动打磨共用）：人设号软引导（2026-11-02 放开），获客号强化引导。"""
    return ("保留并强化获客引导（这是获客号内容，引导是核心交付；"
            "但不得承诺收益/包就业等合规红线）"
            if account_type == "leadgen"
            else "允许结尾带一句自然的关注/合集引导（人设号软引导 2026-11-02 放开）；"
                 "仍禁评论区扣字/私信领取/卖课导流")


async def _polish_core(versions: list[dict], use_style: bool = True,
                       length: str = "short",
                       cta_rule: str = _cta_rule_of("persona")) -> list[dict]:
    """批判打磨核心：评审→重写→逐版合并（critique 以「｜改进：」并入 notes）。"""
    length_spec = _script_length_spec(length)
    data = await gateway.complete_json(
        [{"role": "system", "content": render("script_polish", MY_STYLE=build_my_style(use_style),
                                              LENGTH=length_spec, CTA_RULE=cta_rule)},
         {"role": "user", "content": json.dumps(versions, ensure_ascii=False)}],
        max_tokens=20000)  # 同上，v5 动刀清单变长
    polished = _normalize_versions(data.get("versions") or [])
    critiques = data.get("critiques") or []
    merged = list(versions)
    for i, v in enumerate(polished):
        if i < len(merged):
            note = v.get("notes", "")
            if i < len(critiques):
                note = f"{critiques[i]}｜改进：{note}" if note else critiques[i]
            elif note:  # 模型偶发漏 critiques：notes 有内容也保留改进标记（P1 契约不因抽签翻车）
                note = f"｜改进：{note}"
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
    merged = await _polish_core(original, use_style=bool(payload.get("use_style", True)),
                                length=payload.get("length", "short"),
                                cta_rule=_cta_rule_of(script.account_type or "persona"))

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
        max_tokens=8000)

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
