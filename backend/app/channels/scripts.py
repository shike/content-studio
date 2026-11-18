"""脚本工场：一版精品直出（2026-11-05 定版）/ 批判打磨 / 定稿三产物。"""
from __future__ import annotations

import json

from sqlmodel import Session

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import Content, Idea, Material
from ..prompts import load, render
from ..selfstyle.service import build_my_style

SIX_ANGLES = ["反常识判断", "身份定位喊话", "行动指令", "提问式", "对比打假", "数字落差"]


def _material_ctx(s: Session, material_id: int) -> dict:
    """素材消费上下文（评论/拆解/分型）——单一通道，禁止消费方直接猜 JSON。"""
    from ..materials.service import material_context

    return material_context(s, int(material_id))


_FORM_BOOK = "——；;、「」『』“”‘’《》〈〉："  # 书面标点黑名单（口播零容忍，2026-11-18 定标）


def _speech_form_check(body: str) -> dict:
    """口播形态读稿闸（2026-11-18 用户定标，代码判不经 AI 品味）：
    平均句长 ≤13 字、单句 ≤20 字、零书面标点——标尺=号主 12 万播放真实转写（21 气口均 12.2 字、最长 20、零书面标点）。"""
    import re

    # 气口=句末标点或空格：模型执行「零书面标点」后自然改用空格断句（=提词器气口）。
    # 2026-11-18 生产实测修正：空格不切会把整篇算成一句 318 字假超标、误触发定向重写。
    units = [u for u in re.split(r"[。！？\s]", (body or "").strip()) if u.strip()]
    lens = [len(re.sub(r"\s", "", u)) for u in units] or [0]
    book = sum(body.count(ch) for ch in _FORM_BOOK)
    avg = sum(lens) / len(lens)
    mx = max(lens)
    ok = avg <= 13 and mx <= 20 and book == 0
    return {"pass": ok, "avg": round(avg, 1), "max": mx, "book": book,
            "long": sum(1 for l in lens if l > 20), "sentences": len(units)}


async def _kb_ammunition(comment: str) -> str:
    """热评 → 知识库弹药：按热评主题从入选清单挑 1~3 篇，抽资料包拼弹药块（无相关返回空串）。"""
    from sqlmodel import Session, select

    from ..articles.prod_kit import ensure_prod_kit, kit_block
    from ..db import engine
    from ..models import Material

    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
            Material.status == "ready")).all()  # type: ignore[attr-defined]
    if not rows:
        return ""
    rows = sorted(rows, key=lambda d: float((d.quality or {}).get("score") or 0), reverse=True)[:200]
    index = "\n".join(f"{i + 1}. 《{(d.title or '')[:40]}》{str((d.quality or {}).get('why_good') or '')[:60]}"
                      for i, d in enumerate(rows))
    pick = await gateway.complete_json(
        [{"role": "user", "content":
          "一条高赞热评（受众心声）：\n「" + comment[:200] + "」\n"
          "从知识库清单挑 1~3 篇能给口播当「弹药」（判断/机制/数字账）的文档，没有相关就空数组。\n"
          "清单：\n" + index +
          '\n输出 JSON：{"ids": [序号数组]}'}],
        max_tokens=1024, thinking="disabled")
    by_id = {d.id: d for d in rows if d.id is not None}
    ids: list[int] = []
    for x in (pick or {}).get("ids") or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(rows) and rows[i - 1].id in by_id:
            ids.append(rows[i - 1].id)
    ammo = []
    for mid in list(dict.fromkeys(ids))[:3]:
        kit = await ensure_prod_kit(mid)
        blk = kit_block(kit)
        if blk:
            ammo.append(f"《{(by_id[mid].title or '')[:40]}》\n{blk}")
    return "\n\n".join(ammo)


async def _hotspot_for_comment(comment: str) -> str:
    """热评 → 在榜热点引信：与热评直接关联的在榜热点才挂（无=空串，不硬挂）。"""
    import asyncio as _aio

    from ..materials.collectors.hotlist import fetch_baidu_hot, fetch_tech_news

    try:
        baidu, _ = await _aio.to_thread(fetch_baidu_hot, 12)
        news, _ = await _aio.to_thread(fetch_tech_news, 6, 48)
    except Exception:  # noqa: BLE001 热点源挂=不挂，口播照出
        return ""
    items = [str(h.get("title") or "")[:40] for h in baidu[:8]]
    items += [str(n.get("title") or "")[:60] for n in news[:10]]
    if not items:
        return ""
    pick = await gateway.complete_json(
        [{"role": "user", "content":
          "热评心声：「" + comment[:200] + "」\n当前在榜热点：\n" + "\n".join(f"- {x}" for x in items) +
          '\n有直接关联的在榜热点就给它的原文标题（当爆款引信），没有给空串，宁缺勿滥。'
          '输出 JSON：{"hotspot": "标题或空串"}'}],
        max_tokens=1024, thinking="disabled")
    return str((pick or {}).get("hotspot") or "").strip()


@register_executor("script_hotcomment")
async def script_hotcomment(ctx: JobContext, payload: dict) -> dict:
    """热评→口播稿（2026-11-18 车道二·用户定版）：高赞热评开场=受众认领，知识库弹药垫深度，
    形态读稿闸收口（代码机械判：平均≤13字/单句≤20字/零书面标点，不过定向重写一次）。"""
    import json as _json

    from sqlmodel import Session, select

    from ..channels.engine import new_script_content
    from ..db import engine
    from ..models import Material, MaterialAnalysis
    from ..tenant_brand import brand_of

    material_id = int(payload.get("material_id") or 0)
    cidx = int(payload.get("comment_idx") or 0)
    comment = likes = src_title = ""
    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise RuntimeError(f"素材 #{material_id} 不存在")
        src_title = m.title or ""
        for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                MaterialAnalysis.material_id == material_id)).all():  # type: ignore[attr-defined]
            if a.kind == "comments":
                items = (a.payload or {}).get("items") or []
                if cidx < len(items):
                    comment = str((items[cidx] or {}).get("t") or "")
                    likes = str((items[cidx] or {}).get("d") or "0")
                break
    if not comment:
        raise RuntimeError("热评不存在（该素材没有此条评论）")

    with Session(engine) as s:
        script = new_script_content(
            s, tenant_id=1, channel_key="persona",
            title_hint=f"热评口播：{comment[:30]}",
            payload={"script_state": "generating", "account_type": "persona",
                     "hot_comment": {"material_id": material_id, "idx": cidx}})
        s.commit()
        script_id = script.id

    # 失败必须把脚本行标 failed（2026-11-18）：否则每次重试新建的行会永远停在「生成中」
    try:
        ctx.set_progress(20, "知识库弹药匹配")
        ammo = await _kb_ammunition(comment)
        ctx.set_progress(30, "在榜热点挂靠检查")
        hotspot = await _hotspot_for_comment(comment)
        ctx.set_progress(45, "口播稿生成")
        prompt = render("script_hotcomment", PERSONA=brand_of(1)["persona"],
                        COMMENT=comment[:300], LIKES=likes or "?",
                        KB_AMMO=ammo or "（知识库无直接相关弹药——按人设与常识把这条心声讲透，禁编造数字）",
                        VIRAL_BLOCK=_viral_block(hotspot))
        data = await gateway.complete_json([{"role": "user", "content": prompt}], max_tokens=20000)
        versions = _normalize_versions((data.get("versions") or []))[:1]
        if not versions:
            raise RuntimeError("口播稿生成空响应")
        body = str(versions[0].get("body") or "")

        chk = _speech_form_check(body)
        if not chk["pass"]:
            ctx.set_progress(70, f"形态未过（均 {chk['avg']} 字/句·最长 {chk['max']}·书面标点 {chk['book']}），定向重写")
            data = await gateway.complete_json(
                [{"role": "user", "content": prompt +
                  f"\n\n【返工·形态读稿闸】上一稿形态不达标：平均句长 {chk['avg']} 字/句（目标 ≤13）、"
                  f"最长句 {chk['max']} 字（目标 ≤20）、书面标点 {chk['book']} 处（目标 0）。"
                  "全稿逐句拆短重写：一句一口气，书面标点全清，用口语挂钩词（对不对/为什么呢/说白了）替代书面连接。其余规格照旧。"}],
                max_tokens=20000)
            versions = _normalize_versions((data.get("versions") or []))[:1] or versions
            chk = _speech_form_check(str(versions[0].get("body") or ""))

        with Session(engine) as s:
            script = s.get(Content, script_id)
            p = dict(script.payload or {})
            p["script_state"] = "generated"
            p["account_type"] = "persona"
            p["form_check"] = chk
            script.payload = p
            script.versions = versions
            script.status = "review"
            script.note = (f"热评口播｜来源《{src_title[:24]}》｜形态体检：均 {chk['avg']} 字、最长 {chk['max']}、书面标点 {chk['book']}")
            s.add(script)
            s.commit()
        ctx.set_progress(100, f"完成（形态体检：均 {chk['avg']} 字/最长 {chk['max']}/书面标点 {chk['book']}）")
        return {"script_id": script_id, "form": chk}
    except Exception as e:  # noqa: BLE001
        with Session(engine) as s:
            sc = s.get(Content, script_id)
            if sc is not None:
                p = dict(sc.payload or {})
                p["script_state"] = "failed"
                p["error"] = f"{type(e).__name__}: {str(e)[:180]}"
                sc.payload = p
                sc.status = "failed"
                s.add(sc)
                s.commit()
        raise


def _viral_block(hotspot: str) -> str:
    """爆款基因块（2026-11-18 用户裁决「AI 不判爆款、数据判」：范本=自有账号过闸真实爆款）。

    挂靠热点才注入（无热点=干货稿，不硬装情绪——实证公式：热点×立场×一线视角缺一不爆）。
    范本库 app_settings.viral_gene_samples 由回流循环自动更新（self_scan 收尾收割播放 ≥3 万样本）。"""
    hotspot = (hotspot or "").strip()
    if not hotspot:
        return ""
    from sqlmodel import Session

    from ..db import engine
    from ..models import AppSetting

    with Session(engine) as s:
        row = s.get(AppSetting, "viral_gene_samples")
        samples = (row.value or "").strip() if row else ""
    if not samples:
        samples = "（范本库暂空——等自有账号回流积累，先按公式写）"
    return (
        f"## 爆款基因模式（本条挂靠热点：{hotspot}）——**本块优先级最高，与下方其他规范冲突时以本块为准**\n"
        "本账号数据实证公式：**爆款 = 热点对象 × 强情绪立场（骂/质问/站队）× 一线身份视角**，"
        "热点窗口期 48h 内发布；无热点的纯干货在本账号只有几千播放天花板。本条为爆款稿，硬性动作：\n"
        f"1. **前 3 秒内必须直接对「{hotspot}」甩出立场**（骂/质问/站队选一），热点词必须出现在第一句——"
        "像《祝Anthropic早日倒闭》那样标题即立场，像《Workbuddy那么复杂！FDE怎么办》那样开口即质问\n"
        "2. 全文以**被热点折腾的一线落地者**身份说话，禁旁观评测腔、禁客观分析腔\n"
        "3. 血肉用一线落地细节作证（在现场看到什么、用户真实反应、踩过的坑）\n"
        "4. 结尾留站队钩子（让观众必须选边评论），不做圆满总结\n"
        "以下是自己账号的真实爆款范本（播放量=受众判决；ASR 转写含错别字——"
        "**只学立场、姿态、节奏与结构，严禁复用其主题与措辞**）：\n\n"
        + samples
    )


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
        sc = s.get(Content, int(payload.get("script_id") or 0))
        if sc is not None and sc.status == "drafting":
            sc.status = "failed"
            p = dict(sc.payload or {})
            p["script_state"] = "failed"
            sc.payload = p
            s.add(sc)
            s.commit()


@register_executor("script_generate", on_fail=_script_generate_fail)
async def script_generate(ctx: JobContext, payload: dict) -> dict:
    return await _script_generate(ctx, payload)


def _scaffold(*, discipline_mode: str, card_discipline: str, my_style: str,
              length_spec: str, version_label: str, version_note: str,
              compliance_extra: str = "") -> str:
    """共享脚手架注入（2026-11-16 收敛：falsify_script/script_generate 两模板的
    主线/说话方式/合规/反模板/格式五段逐字同构，改规则曾需同步两处且已在漂移）。"""
    from ..prompts import load as _load
    out = _load("_script_scaffold")
    for k, v in (("DISCIPLINE_MODE", discipline_mode), ("CARD_DISCIPLINE", card_discipline),
                 ("MY_STYLE", my_style), ("COMPLIANCE_EXTRA", compliance_extra),
                 ("LENGTH", length_spec), ("VERSION_LABEL", version_label),
                 ("VERSION_NOTE", version_note)):
        out = out.replace("{{%s}}" % k, v)
    return out


async def _script_generate(ctx: JobContext, payload: dict) -> dict:
    script_id = payload["script_id"]
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise RuntimeError(f"script {script_id} 不存在")
        card = s.get(Idea, int(payload["card_id"])) if payload.get("card_id") else None
        if payload.get("card_id") and card is None:
            raise RuntimeError(f"card {payload['card_id']} 不存在")
        falsify = (s.get(Material, int(payload["falsify_item_id"]))
                   if payload.get("falsify_item_id") else None)
        if payload.get("falsify_item_id") and (falsify is None
                                               or falsify.origin_type != "test_record"):
            raise RuntimeError(f"实测素材 {payload['falsify_item_id']} 不存在")
        ore = (s.get(Material, int(payload["ore_id"]))
               if payload.get("ore_id") else None)
        if payload.get("ore_id") and (ore is None
                                      or (ore.quality or {}).get("comment_kind") != "leadgen_ore"):
            raise RuntimeError(f"获客矿素材 {payload['ore_id']} 不存在")
        ore_ctx = _material_ctx(s, ore.id) if ore is not None else {}
        kb = (s.get(Material, int(payload["kb_id"]))
              if payload.get("kb_id") else None)
        if payload.get("kb_id") and (kb is None or kb.origin_type != "kb_doc"):
            raise RuntimeError(f"知识库文档 {payload['kb_id']} 不存在")
        if falsify is not None:
            # 实测打假稿（R1.4③）：body=实测结论（唯一事实来源），没实测的环节一个字不许编
            fq = falsify.quality or {}
            topic_info = {
                "title": (falsify.title or "")[:60],
                "angle": "",
                "audience": "both",
                "pain_points": [],
                "hooks": [],
                "claim": falsify.title,
                "target": fq.get("target") or "",
                "evidence": (falsify.body or "")[:1500],
                "spice": "high",
            }
        elif ore is not None:
            # 获客矿建稿（分型路由消费面 2026-11-14）：求学评论区=需求清单，稿子必须接住需求+完成获客引导
            demands = [str(c.get("t") or "").strip()[:60]
                       for c in (ore_ctx.get("comments") or [])
                       if isinstance(c, dict) and str(c.get("t") or "").strip()][:10]
            share = int(round(float(ore_ctx.get("leadgen_share") or 0) * 100))
            topic_info = {
                "title": f"获客需求：{(ore.title or '')[:50]}",
                "angle": "",
                "audience": "both",
                "pain_points": demands,
                "hooks": [],
                "demand_note": (f"这条视频的评论区 {share}% 在求资料/学习（上面 pain_points=赞数最高的求学评论原文）。"
                                "稿子要面向这些需求做获客内容：接住需求、给出具体可上手的方案，结尾按获客引导完成转化动作；"
                                f"视频背景供参考：{(ore_ctx.get('analysis') or {}).get('summary', '')[:160]}"),
            }
        elif kb is not None:
            # 知识库文档建稿（2026-11-17）：挖掘器的改编角度=切角、金句=钩子证据、原文=唯一事实来源
            kq = kb.quality or {}
            quotes = [str(q).strip() for q in (kq.get("best_quotes") or []) if str(q).strip()]
            topic_info = {
                "title": (kb.title or "")[:60],
                "angle": str(kq.get("adapt_angle") or "")[:200],
                "audience": "both",
                "pain_points": [],
                "hooks": quotes[:2],
                "claim": str(kq.get("why_good") or "")[:200],
                "evidence": (("\n".join(f"「{q}」" for q in quotes[:3]) + "\n" if quotes else "")
                             + (kb.body or "")[:1200]),
                "spice": "mid",
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

    length = payload.get("length", "short")
    hotspot = str(payload.get("hotspot") or "").strip()  # 2026-11-18 爆款基因模式（挂靠热点才注入）
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

    # 两类号策略（2026-10-28）：账号模式决定内容风格——人设号禁引导，获客号要求获客引导
    # 2026-11-13 修复：account_type 必须显式落值，不能 str(None)="None" 污染筛选
    _raw_at = payload.get("account_type") \
        or ("leadgen" if ore is not None else "")
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
                        PERSONA=brand_of(1)["persona"],
                        SCAFFOLD=_scaffold(
                            discipline_mode=discipline_mode, card_discipline=card_discipline,
                            my_style=build_my_style(use_style), length_spec=length_spec,
                            version_label="实测打假", version_note="一句话",
                            compliance_extra="- 对被测对象的指控必须与实测事实一一对应——不夸大、不株连、不给没测过的环节编细节"),
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
                        ANGLES_SPEC=angles_spec,
                        PERSONA=brand_of(1)["persona"],
                        ACCOUNT_MODE=account_mode,
                        VIRAL_BLOCK=_viral_block(hotspot),
                        SCAFFOLD=_scaffold(
                            discipline_mode=discipline_mode, card_discipline=card_discipline,
                            my_style=build_my_style(use_style), length_spec=length_spec,
                            version_label="打法名", version_note="打法一句话"))
    # 2026-11-05 一版精品直出（用户定版：质量优先于选择）：单版本，预算全给打磨环节
    ctx.set_progress(20, "生成精品脚本（单版全预算）——大模型整篇写作中，本步约 2~4 分钟")
    data = await gateway.complete_json(
        [{"role": "system", "content": prompt},
         {"role": "user", "content": json.dumps(topic_info, ensure_ascii=False)}],
        max_tokens=20000)  # v7 prompt 变长，思考链耗尽 12000 致空正文（2026-10-10 实测）
    versions = _normalize_versions((data.get("versions") or []))[:1]
    if not versions:
        raise RuntimeError("脚本生成失败：没有任何可用版本")

    # 爆款基因闸（2026-11-18 两轮冒烟教训：模型会绕开热点直接写文档内容）——
    # 挂了热点但稿子没入题，机械检查后定向重写一次，不靠模型自觉
    if hotspot:
        hot_core = hotspot.split()[0]
        if hot_core.lower() not in json.dumps(versions[0], ensure_ascii=False).lower():
            ctx.set_progress(50, f"稿子未入题热点「{hot_core}」，定向重写")
            data = await gateway.complete_json(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": json.dumps(topic_info, ensure_ascii=False)
                  + f"\n\n【返工要求】上一稿完全没入题热点「{hotspot}」，作废。重写："
                    f"第一句必须对「{hotspot}」甩出立场（骂/质问/站队选一），"
                    "文档里的判断和细节只作为论据为热点立场服务——不是写文档的读后感。其余规格照旧。"}],
                max_tokens=20000)
            versions = _normalize_versions((data.get("versions") or []))[:1] or versions

    # 新契约（PRD R2.3）：生成后自动批判打磨一轮
    ctx.set_progress(60, "自动批判打磨一轮（约 3~8 分钟，慢是正常的）")
    versions = await _polish_core(versions, use_style=bool(payload.get("use_style", True)),
                                  length=length, cta_rule=_cta_rule_of(account_type), ctx=ctx)

    with Session(engine) as s:
        script = s.get(Content, script_id)
        script.versions = versions
        p = dict(script.payload or {})
        p["script_state"] = "generated"
        p["account_type"] = account_type
        if hotspot:
            p["hotspot"] = hotspot  # 迭代重写（script_revise）要知道爆款基因模式在挂谁
        script.payload = p
        script.status = "review"
        s.add(script)
        s.commit()

    ctx.set_progress(100, f"完成，产出 {len(versions)} 版")
    return {"script_id": script_id, "versions": len(versions)}


@register_executor("script_revise")
async def script_revise(ctx: JobContext, payload: dict) -> dict:
    """对话框内迭代（2026-11-18 用户裁决：实时生成、不行就在框里说改）：按反馈重写当前版本。

    追加新版本留痕（保留最近 5 版）；爆款基因模式随 payload 热点延续。"""
    script_id = payload["script_id"]
    feedback = str(payload.get("feedback") or "").strip()
    if not feedback:
        raise RuntimeError("修改反馈为空")
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise RuntimeError(f"脚本 #{script_id} 不存在")
        p = dict(script.payload or {})
        p["script_state"] = "revising"
        script.payload = p
        s.add(script)
        s.commit()
        last = (script.versions or [{}])[-1]
        draft = str(last.get("body") or "")
    if not draft.strip():
        raise RuntimeError("原稿为空，无法按反馈修改")
    hotspot = str(p.get("hotspot") or "").strip()
    from ..tenant_brand import brand_of
    ctx.set_progress(30, "按反馈重写整稿")
    data = await gateway.complete_json(
        [{"role": "user", "content": render("script_revise", PERSONA=brand_of(1)["persona"],
                                            VIRAL_BLOCK=_viral_block(hotspot),
                                            FEEDBACK=feedback[:300],
                                            DRAFT=draft)}],
        max_tokens=20000, thinking="disabled")
    new_versions = _normalize_versions((data.get("versions") or []))[:1]
    if not new_versions or not str(new_versions[0].get("body") or "").strip():
        raise RuntimeError("按反馈修改空响应，稍后自动重试")
    v = new_versions[0]
    v["notes"] = ((last.get("notes") or "") + f"｜改：{feedback[:40]}").strip("｜")[:200]
    with Session(engine) as s:
        script = s.get(Content, script_id)
        script.versions = ((script.versions or []) + [v])[-5:]  # 追加留痕，保最近 5 版
        p2 = dict(script.payload or {})
        p2["script_state"] = "generated"
        script.payload = p2
        script.status = "review"
        s.add(script)
        s.commit()
    ctx.set_progress(100, "修改完成")
    return {"script_id": script_id}


def _cta_rule_of(account_type: str) -> str:
    """CTA 政策单点定义（生成链与手动打磨共用）：人设号软引导（2026-11-02 放开），获客号强化引导。"""
    return ("保留并强化获客引导（这是获客号内容，引导是核心交付；"
            "但不得承诺收益/包就业等合规红线）"
            if account_type == "leadgen"
            else "允许结尾带一句自然的关注/合集引导（人设号软引导 2026-11-02 放开）；"
                 "仍禁评论区扣字/私信领取/卖课导流")


async def _polish_core(versions: list[dict], cta_rule: str,
                       use_style: bool = True, length: str = "short",
                       ctx: JobContext | None = None) -> list[dict]:
    """批判打磨核心：评审→重写→逐版合并（critique 以「｜改进：」并入 notes）。

    ctx 传入时逐阶段回报进度（2026-11-18：单次长调用期间页面无输出被用户判「卡死」）。"""
    length_spec = _script_length_spec(length)
    ctx_set = (lambda p, m: ctx.set_progress(p, m)) if ctx is not None else (lambda p, m: None)
    ctx_set(65, "批判评审中（整稿挑毛病）")
    data = await gateway.complete_json(
        [{"role": "system", "content": render("script_polish", MY_STYLE=build_my_style(use_style),
                                              LENGTH=length_spec, CTA_RULE=cta_rule)},
         {"role": "user", "content": json.dumps(versions, ensure_ascii=False)}],
        max_tokens=20000)  # 同上，v5 动刀清单变长
    polished = _normalize_versions(data.get("versions") or [])
    critiques = data.get("critiques") or []
    ctx_set(85, "按评审重写")
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
        sc = s.get(Content, int(payload.get("script_id") or 0))
        if sc is not None and sc.status == "drafting":
            p = dict(sc.payload or {})
            p["script_state"] = "generated"
            sc.payload = p
            sc.status = "review"
            s.add(sc)
            s.commit()


@register_executor("script_polish", on_fail=_script_polish_fail)
async def script_polish(ctx: JobContext, payload: dict) -> dict:
    return await _script_polish(ctx, payload)


async def _script_polish(ctx: JobContext, payload: dict) -> dict:
    script_id = payload["script_id"]
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise RuntimeError(f"script {script_id} 不存在")
        if not script.versions:
            raise RuntimeError("脚本还没有版本，先生成")
        original = list(script.versions)

    ctx.set_progress(30, "批判打磨中")
    merged = await _polish_core(original, use_style=bool(payload.get("use_style", True)),
                                length=payload.get("length", "short"),
                                cta_rule=_cta_rule_of((script.payload or {}).get("account_type") or "persona"))

    with Session(engine) as s:
        script = s.get(Content, script_id)
        script.versions = merged
        p = dict(script.payload or {})
        p["script_state"] = "generated"
        script.payload = p
        script.status = "review"
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
    titles = (script.payload or {}).get("title_candidates") or []
    first_title = titles[0] if titles else ""
    return {"script_id": script_id, "status": script.status, "title": first_title}


async def finalize_script(script_id: int, version_index: int = 0) -> Content:
    """定稿：一次 LLM 调用，产出提词器/分镜/标题三件套（由 script_finalize 任务驱动）。"""
    with Session(engine) as s:
        script = s.get(Content, script_id)
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
        script = s.get(Content, script_id)
        script.body = chosen.get("body", "")
        p = dict(script.payload or {})
        p["teleprompter_text"] = data.get("teleprompter_text", "")
        p["storyboard"] = data.get("storyboard") or []
        p["title_candidates"] = data.get("title_candidates") or []
        p["script_state"] = "final"
        script.payload = p
        script.title = (script.payload.get("title_hint") or script.title or "")[:60]
        script.status = "ready"
        s.add(script)
        s.commit()
        s.refresh(script)
        # 选题 consumed：稿件定稿即闭环（四渠道统一状态机，PRD R0.8）
        if script.idea_id:
            idea = s.get(Idea, script.idea_id)
            if idea is not None and idea.status == "approved":
                idea.status = "consumed"
                s.add(idea)
                s.commit()
    return script
