"""公众号流水线：长文生成执行器。渲染见 renderer.py。"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import Article, Script, Topic
from ..prompts import load, render

# 长文风格库（R10）：key → 结构骨架。生成时单选，auto 交给模型按素材挑。
ARTICLE_STYLES: dict[str, str] = {
    # 八个风格 = 同一深度长文底座下的八种拆解视角（2026-09-27 重定义：全部零故事、专业书面语，
    # 差异只在「分析的透镜」）。共享硬约束见 deep_dive 的文体/语言/质量判据三行——其余风格
    # 只写视角结构，共享约束由 Prompt 底座统一注入。
    "deep_dive": (
        "【深度研究·机制拆解视角】行业深度分析：有铺垫的专业拆解，深度与可读性并重。硬性结构：\n"
        "- 开头：先用 2~4 句客观陈述行业现状或普遍认知做铺垫（陈述事实与共识，不是故事场景），随后一句话亮出核心判断\n"
        "- 现象层：点出行业普遍的理解或做法停留在哪，用公开数据/报告/常见做法佐证\n"
        "- 拆解层：把问题拆成 2~4 个层次或维度（如：现象→机制→条件→变量），逐层深入，每层有明确的机制解释与判断依据\n"
        "- 论据支撑：每层用公开数据、行业事实、逻辑推演支撑，引用具体到数字/名称/时间\n"
        "- 推演层：按这套机制推演接下来会怎么演变，给出可验证的判断\n"
        "- 边界收尾：这套理论在什么条件下不成立；最后停在最重的那个判断上\n"
        "文体：专业书面语（行业深度分析），术语准确、逻辑密度高、判断直给；禁止口语化表达、网络用语与故事叙事。\n"
        "语言：全文以中文表达为主。英文缩写术语（RAG/PoC/SaaS 类）可直接使用；禁止中英混排成句（如'demo 阶段''人在环上'式直译——写'验证演示阶段''人机协同'）；无地道译名的概念首次出现用括号标注英文原文，此后只用中文。\n"
        "质量判据：读者拿到一个别人没讲过的解释框架，读完感觉自己变聪明了。禁止：故事/场景/对话/亲历叙事、连续比喻、口语化、AI 套话（赋能/抓手/闭环/随着…的发展）。判断必须由逻辑与公开证据推出。"
    ),
    "practice": (
        "【落地实践·实施路径视角】同一深度长文底座，透镜=一套方法论在真实约束下如何走通。硬性结构：\n"
        "- 开头：2~3 句铺垫这套方法要解决的核心矛盾，随后一句话亮出总判断（可行/不可行及关键条件）\n"
        "- 适用条件：这套方法的适用边界（行业/规模/数据基础/组织条件），逐条列明，每条带依据\n"
        "- 实施路径：3~5 个关键步骤，每步 = 做什么 + 关键参数/数字 + 产出的可验收物\n"
        "- 失败条件：最常见的一到两种走不通的情形，机制层面解释为什么走不通\n"
        "- 验收收尾：落地后拿什么指标验收、看多久；最后停在最重的那个判断上\n"
        "文体与语言：与深度研究同一底座——专业书面语、中文为主、术语规范、判断直给。\n"
        "质量判据：读者拿着实施路径就能对照自己的项目列计划。禁止：故事/场景/对话/亲历叙事、口语化、无验收标准的空泛步骤。"
    ),
    "viewpoint": (
        "【立场论证·判断论证视角】同一深度长文底座，透镜=一个反共识判断的完整论证链。硬性结构：\n"
        "- 开头：2~3 句行业共识铺垫，随后亮出反共识的核心判断（一句话，不留歧义）\n"
        "- 论据链：三条以上独立成立的论据（公开数据/行业事实/机制推演），每条带出处与数字\n"
        "- 最强反驳：把反对者最硬的理由完整摆出来，正面拆解（机制层面，不许立稻草人）\n"
        "- 判断边界：这个判断在什么条件下不成立（诚实列出，增强可信度）\n"
        "- 收尾：停在最重的那条论据上\n"
        "文体与语言：与深度研究同一底座。\n"
        "质量判据：把观点遮住，懂行的人也能认出立场；反驳环节让反对者承认被正面回应过。禁止：和稀泥、骑墙、堆免责声明。"
    ),
    "framework": (
        "【框架清单·检查框架视角】同一深度长文底座，透镜=一套可复用的分析/检查框架。硬性结构：\n"
        "- 开头：2~3 句铺垫这个框架回答什么问题，随后一句话给出框架全貌\n"
        "- 框架总览：3~6 个组成维度，每个维度 = 定义 + 为什么它是必要维度（机制解释）\n"
        "- 逐维拆解：每维配一个公开行业事实佐证 + 应用条件（什么时候这条权重最高）\n"
        "- 优先级收尾：资源只够覆盖一个维度时先做哪个、为什么；最后停在最重的那个判断上\n"
        "文体与语言：与深度研究同一底座。\n"
        "质量判据：读者能拿这套框架去审自己手里的同类问题。禁止：凑数维度、各维度篇幅悬殊、无机制解释的名词罗列。"
    ),
    "comparison": (
        "【方案对比·机制对比视角】同一深度长文底座，透镜=两条以上技术/方案路线的机制级对比。硬性结构：\n"
        "- 开头：2~3 句铺垫选择背后的行业矛盾，随后一句话给出对比的结论倾向\n"
        "- 维度定义：3~4 个对比维度，先定义维度本身（为什么这个维度决定成败），再用数据/公开事实逐维展开\n"
        "- 结论矩阵：明确到『什么条件选 A、什么条件选 B、什么条件都别选』，条件可观测\n"
        "- 避坑收尾：选定之后最容易死在哪一步（机制层面）；最后停在最重的那个判断上\n"
        "文体与语言：与深度研究同一底座。\n"
        "质量判据：读者拿着结论矩阵就能做决策。禁止：『各有优劣』『看情况』式和稀泥、无数据支撑的拍脑袋打分。"
    ),
    "inquiry": (
        "【问题深挖·追因视角】同一深度长文底座，透镜=从一个具体行业问题挖到底层机制。硬性结构：\n"
        "- 开头：2~3 句铺垫这个问题的普遍性（多少人被它困扰、常见的表面答案），随后一句话亮出真正的答案方向\n"
        "- 表层答案为什么不够：列出直觉答案并逐条给出反例或数据反证\n"
        "- 机制拆解：把问题拆到底层变量（2~4 层），每层解释『为什么这一层才是关键』\n"
        "- 可验证结论：按这套机制，改变哪个变量会产生什么可观测的变化\n"
        "- 收尾：停在最关键的那个变量上\n"
        "文体与语言：与深度研究同一底座。\n"
        "质量判据：读者看完对问题的理解层次明显升级。禁止：把问题复述一遍当分析、答案绕圈、无反证的直觉断言。"
    ),
    "anatomy": (
        "【对象解剖·结构分析视角】同一深度长文底座，透镜=把一个行业事件/产品/决策拆开做结构分析。硬性结构：\n"
        "- 开头：2~3 句铺垫这个对象为什么值得解剖（普遍误解了什么），随后一句话亮出解剖后的核心发现\n"
        "- 对象界定：明确解剖的范围与维度（拆哪几块、为什么不拆别的）\n"
        "- 逐层分析：每块的结构、机制与公开数据佐证，块与块之间的因果/依赖关系\n"
        "- 可迁移规律：从解剖中提炼 2~3 条可迁移到同类对象的规律，每条带适用条件\n"
        "- 收尾：停在最反直觉的那条发现上\n"
        "文体与语言：与深度研究同一底座。\n"
        "质量判据：读者能按同一套维度去解剖同类对象。禁止：成功学叙事、只讲结果不讲结构、无适用条件的规律。"
    ),
    "outlook": (
        "【趋势推演·变量推演视角】同一深度长文底座，透镜=从当下机制推演未来演变。硬性结构：\n"
        "- 开头：2~3 句铺垫当前行业机制或普遍预期，随后一句话亮出推演的核心判断（带时间点）\n"
        "- 驱动变量：找出 2~4 个真正驱动演变的变量（成本曲线/政策条款/技术成熟度），每个带当前数值或状态\n"
        "- 推演路径：变量变化如何传导到行业格局（因果链，不罗列现象）\n"
        "- 分众影响：对两类核心受众分别意味着什么\n"
        "- 校准收尾：给未来 6~12 个月的判断 + 现在该做的准备；最后停在最可验证的那个预测上\n"
        "文体与语言：与深度研究同一底座。\n"
        "质量判据：一年后可以拿这篇文章对答案。禁止：无时间点无变量的空泛预言、科幻式畅想。"
    ),
}
BANNED_PATTERNS = [  # 与 acceptance/quality.py 保持同表（程序化合规红线）；裸「最好的」已移除——
    # 误杀「千万别挑最好的那条」（反向建议）与「最好的线，设备新」（描述性比较），语义级由 LLM 评审兜底
    "行业第一", "全网第一", "业界第一", "排名第一",
    "保证收益", "保证回本", "保证效果", "保证赚钱", "保证翻倍",
    "百分百", "稳赚", "包赚", "躺赚", "轻松月入", "闭眼入",
]
DEFAULT_STYLE = "deep_dive"  # Q13：用户常用"深度长文"，默认固化+选择器弱化

# 双档位（2026-10-02 用户拍板：双档位·默认公众号版）。feed=公众号版（推荐流完读率优先：
# 1200~1800 字、开头即答案、判断式小节标题、金句收尾）；deep=深度版（原 3000~5000 长文规格，
# 铺垫式开头、机制拆解/方法框架/边界条件三件套）。规格文本分别注入三个 prompt 的占位符。
ARTICLE_LENGTHS: dict[str, dict[str, str]] = {
    "feed": {
        "words": "300~450",
        "title": (
            "- 微信公众号标题，≤32 字（展示上限）\n"
            "- 本篇发公众号推荐流：标题要在信息流里 0.5 秒内让目标读者（中小企业老板/业务负责人）"
            "判断「这说的就是我」\n"
            "- 公式（至少占两条）：人群定位（老板/中小企业/传统行业）+ 具体数字或金额 + "
            "冲突/反常识 + 可带走的判断\n"
            "- 用目标读者的痛感词：他被服务商忽悠过、怕项目烂尾、想找人把关——"
            "「项目烂尾」「被忽悠」「钱花了没效果」「先别急着上」这类词可用；"
            "「验收」「归因」「机制」「链路」「边界」这类项目管理词不许出现在标题里\n"
            "- 主标题 1 个 + 备选 2 个（备选换角度，不改事实；其中 1 个可以是更克制专业式的陈述句）\n"
            "- 禁止（出现即返工）：论文标题腔——「X 的 Y：从 A 到 B 的 Z 拆解」「XX 的机制/归因/边界」"
            "式对仗主副题；悬念钩子腔——「只问一句」「终于有人讲清了」「你以为…其实」"
            "（平台判定标题党会限流）；口语情绪词——「准不准」「说白了」"
        ),
        "outline": (
            "- 本篇是「公众号版」（默认档）：总字数 1200~1800 字，3~4 个小节，每节 300~450 字\n"
            "- 开头即答案：第一节第一句=全文最重的结论或最反直觉的数字（从素材里挑最狠的），"
            "再用 2~3 句交代背景与冲突；禁止铺垫式开头（「随着……」「近年来……」「在……的大背景下」"
            "出现即报废）\n"
            "- 小节标题=判断句或利益句（例：「80% 的 AI 项目死在验收标准没写进合同」），"
            "禁止名词术语式标题（例：「XX 的机制拆解」）\n"
            "- 3~4 节里至少 1 节是完整机制/方法拆解（步骤+反例，读者能直接套用）；"
            "边界条件并进对应小节，不单开一节\n"
            "- 每节至少带 1 个素材里的具体数字\n"
            "- 结尾节先用一句可转发的金句收束（替目标读者说出他想说而不敢说的话），"
            "再给读者业务上的下一步动作（例：「签约前把验收标准写进合同」）；"
            "严禁任何读者与作者互动的转化话术（评论区/私信/关注/扣字/领资料——出现即报废）"
        ),
        "section": (
            "篇幅 300~450 字，Markdown，关键数字与结论加粗\n"
            "- 段落 1~3 行一段，给足呼吸感（读者在手机上扫读）\n"
            "- 每节至少一个具体数字；若本节是全文第一节，第一句直接给全文最重的判断或数字，禁止任何铺垫"
        ),
    },
    "deep": {
        "words": "700~1100",
        "title": (
            "- 微信公众号标题，≤32 字（展示上限）\n"
            "- 专业严谨的书面对象文体：主标题直接陈述文章的核心判断、机制或方法，"
            "像行业研究报告的章节题，不像口播钩子\n"
            "- 推荐句式：「X 的真实形态/机制/归因：基于 Y 的拆解」「为什么 X 决定了 Y：机制与边界」"
            "「X 验收/落地中的 Z：链路拆解与判断」\n"
            "- 具体压倒抽象：带数字、行业词、机制词，让目标读者一眼看出分析对象与深度\n"
            "- 主标题 1 个 + 备选 2 个（备选换角度，不改事实）\n"
            "- 禁止（出现即返工）：悬念钩子腔「只问一句」「终于有人讲清了」「你以为…其实」；"
            "口语与情绪词「准不准」「说白了」「哈哈」"
        ),
        "outline": (
            "- 本篇是「深度版」：总字数 3000~5000 字，5~7 个小节，每节 700~1100 字\n"
            "- 第一节先用 2~4 句客观陈述行业现状或普遍认知做铺垫，随后亮出核心判断或关键问题；"
            "铺垫控制在首段内，不许整节都是铺垫\n"
            "- 至少一节是完整机制拆解：把问题的成因链条一层层剥开，每层有明确证据\n"
            "- 至少一节是可复用的方法框架：条件+步骤+反例，专业读者能直接套用\n"
            "- 至少一处写边界条件：这套理论在什么条件下不成立\n"
            "- 结尾节停在最重的判断上"
        ),
        "section": (
            "篇幅 700~1100 字，Markdown，关键数字与结论加粗\n"
            "- 若本节是全文第一节，按大纲要求先做 2~4 句行业现状铺垫再给判断"
        ),
    },
}
DEFAULT_LENGTH = "feed"

# 刻意转化话术（自然收尾红线；与 acceptance/quality.py 的 CTA_PATTERNS 同表，改一处必须同步另一处）
_CTA_PATTERNS = [
    r"评论区[打回留发扣]", r"评论区?扣[「『]?[验关资1-9]", r"私信[我领发回复要]", r"关注我", r"扣[个1-9]", r"回复关键?词",
    r"加[我微].{0,2}信", r"领.{0,4}资料", r"收藏.{0,6}转发",
]


def _banned_hits(text: str) -> list[str]:
    """程序化违禁词命中（否定语境放行：'千万别挑最好的'是反向建议不是绝对化承诺——
    2026-10-02 P1 实测误杀后加窗口）。与 acceptance/quality.py 的 banned_words 同表同逻辑，
    改一处必须同步另一处。"""
    hits = []
    for p in BANNED_PATTERNS:
        start = 0
        while True:
            i = text.find(p, start)
            if i < 0:
                break
            window = text[max(0, i - 4):i]
            if not any(ch in window for ch in "别不没非莫勿忌免避离"):
                hits.append(p)
                break
            start = i + len(p)
    return hits


def _article_generate_fail(payload: dict, _exc: Exception) -> None:
    with Session(engine) as s:  # 失败回退：文章不得永远卡在"生成中"
        art = s.get(Article, int(payload.get("article_id") or 0))
        if art is not None and art.status == "generating":
            art.status = "failed"
            s.add(art)
            s.commit()


@register_executor("article_generate", on_fail=_article_generate_fail)
async def article_generate(ctx: JobContext, payload: dict) -> dict:
    article_id = payload["article_id"]
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise RuntimeError(f"article {article_id} 不存在")
        tenant_id = article.tenant_id
        from ..tenant_brand import brand_of

        persona_text = brand_of(tenant_id)["persona"]
        topic = s.get(Topic, article.topic_id) if article.topic_id else None
        script = s.get(Script, article.script_id) if article.script_id else None
        from ..models import ResearchReport
        report = ""
        if topic:
            rep = s.exec(
                select(ResearchReport).where(ResearchReport.topic_id == topic.id)
                .order_by(ResearchReport.id.desc())  # type: ignore[attr-defined]
            ).first()
            report = rep.content if rep is not None else ""
        material = {
            "选题": {
                "标题": topic.title if topic else "",
                "角度": topic.angle if topic else "",
                "受众": topic.audience if topic else "both",
                "痛点": (topic.evidence or {}).get("pain_points", []) if topic else [],
                "钩子": (topic.evidence or {}).get("hooks", []) if topic else [],
            },
            "深研报告（深度素材，长文的主要原料）": report,
            "口播脚本终稿（可改写扩写，不要照抄）": script.final_text if script else "",
        }

    style = payload.get("style") or DEFAULT_STYLE
    length = payload.get("length") or DEFAULT_LENGTH
    length_spec = ARTICLE_LENGTHS.get(length) or ARTICLE_LENGTHS[DEFAULT_LENGTH]

    # 自动透镜（style=auto）：读选题素材挑最合适的分析透镜，无效回落默认
    if style == "auto":
        ctx.set_progress(10, "自动挑选分析透镜")
        from ..tenant_brand import brand_of as _bo

        pick = await gateway.complete_json(
            [{"role": "user", "content": render("style_pick", PERSONA=_bo(tenant_id)["persona"])
              + "\n\n" + json.dumps(material, ensure_ascii=False)}],
            purpose="style_pick", max_tokens=1024, thinking="disabled")
        picked = str(pick.get("style") or "").strip()
        curated = ("deep_dive", "practice", "inquiry", "anatomy", "comparison")
        if picked not in curated and picked in ARTICLE_STYLES:
            print(f"[article] 透镜 {picked} 不在精选五，回落 deep_dive")
            picked = "deep_dive"
        if picked in ARTICLE_STYLES:
            style = picked
            ctx.set_progress(12, f"透镜已挑：{picked}（{(pick.get('reason') or '')[:40]}）")
        else:
            print(f"[article] 透镜挑选无效（{picked[:20]}），回落 {DEFAULT_STYLE}")
            style = DEFAULT_STYLE
    style_guide = ARTICLE_STYLES.get(style) or ARTICLE_STYLES[DEFAULT_STYLE]

    # 第一段：标题组 + 配图规划（小 JSON，稳）
    ctx.set_progress(15, "起标题、规划配图位")
    title_data = await gateway.complete_json(
        [{"role": "user", "content": render("article_title", PERSONA=persona_text,
                                            TITLE_SPEC=length_spec["title"])
          + f"【风格框架】{style_guide}\n\n"
          + json.dumps(material, ensure_ascii=False)}],
        purpose="article_title", max_tokens=8192, thinking="disabled")
    title = str(title_data.get("title") or "").strip()
    title_alts = [t for t in (title_data.get("title_alts") or []) if t][:2]
    # 第二段：大纲 → 分节写作 → 组装（分段小调用，绕开长输出偶发空正文）
    ctx.set_progress(30, "规划长文大纲")
    outline = await gateway.complete_json(
        [{"role": "user", "content": render("article_outline", STYLE_GUIDE=style_guide,
                                            PERSONA=persona_text, LENGTH_SPEC=length_spec["outline"])
          + "\n\n选题与素材（JSON）：\n" + json.dumps(material, ensure_ascii=False)}],
        purpose="article_outline", max_tokens=8192, thinking="disabled")
    sections_plan = [x for x in (outline.get("sections") or []) if isinstance(x, dict) and x.get("heading")]
    # 节数依从性守卫：模型偶尔无视档位节数要求只出 1~2 节（#11 实测：feed 档大纲只给 1 节，
    # 成文 475 字就"完成"了）——带明确节数重试一次，仍不达标才放行（质量评审/长度窗兜底拦截）
    min_sections = 3 if length == "feed" else 5
    if sections_plan and len(sections_plan) < min_sections:
        print(f"[article] 大纲只出 {len(sections_plan)} 节（要求 {min_sections}+），重试一次")
        retry_prompt = (render("article_outline", STYLE_GUIDE=style_guide,
                               PERSONA=persona_text, LENGTH_SPEC=length_spec["outline"])
                        + f"\n\n注意：你上一稿只规划了 {len(sections_plan)} 个小节，不满足本篇形态的节数要求。"
                        + f"重新输出完整大纲，必须规划 {min_sections}~{min_sections + 1} 个小节。\n\n"
                        + "选题与素材（JSON）：\n" + json.dumps(material, ensure_ascii=False))
        outline2 = await gateway.complete_json(
            [{"role": "user", "content": retry_prompt}],
            purpose="article_outline", max_tokens=8192, thinking="disabled")
        sections_plan2 = [x for x in (outline2.get("sections") or [])
                          if isinstance(x, dict) and x.get("heading")]
        if len(sections_plan2) > len(sections_plan):
            sections_plan = sections_plan2
    if not sections_plan:
        raise RuntimeError("大纲为空，长文生成失败")

    parts: list[str] = []
    n = len(sections_plan)
    for i, sec in enumerate(sections_plan):
        ctx.set_progress(40 + 50 * i // n, f"写作第 {i + 1}/{n} 节：{sec.get('heading', '')}")
        # 「长文风格」框架暂不进分节 prompt：article_section.md 里没有 {{STYLE_GUIDE}} 占位符
        # （此处原有注入一直是静默失效的，风格只经大纲影响正文）——2026-09-27 待用户拍板：
        # 补占位符让风格贯穿正文，还是维持"风格只定大纲"。定了再改，别再留无声 replace。
        sec_prompt = (render("article_section", PERSONA=persona_text,
                             HEADING=str(sec.get("heading") or ""),
                             POINTS=json.dumps(sec.get("points") or [], ensure_ascii=False),
                             SECTION_SPEC=length_spec["section"])
                      + "\n\n选题与素材（JSON）：\n" + json.dumps(material, ensure_ascii=False))
        part = await gateway.complete(
            [{"role": "system", "content": sec_prompt}],
            purpose="article_section", max_tokens=12000,
            thinking="disabled")
        # 截断守卫：结尾无句读 = 疑似被截断，带上下文重写一次
        if part.strip() and not part.rstrip().endswith(("。", "！", "？", "”", "」", "…")):
            part = await gateway.complete(
                [{"role": "system", "content": sec_prompt
                  + f"\n\n注意：你上一稿在小节中间被截断了。请完整重写本节，"
                    f"篇幅 {length_spec['words']} 字，并以完整句子收尾。"}],
                purpose="article_section", max_tokens=12000,
                thinking="disabled")
        # 合规守卫：违禁词/刻意转化话术命中即定向重写一次（与 acceptance/quality.py 同表）
        _hits = _banned_hits(part)
        _cta = next((m.group(0) for p in _CTA_PATTERNS if (m := re.search(p, part))), None)
        if _hits or _cta:
            _why = f"违规用语（{'、'.join(_hits)}）" if _hits else f"刻意转化话术（{_cta}）"
            part = await gateway.complete(
                [{"role": "system", "content": sec_prompt
                  + f"\n\n注意：你上一稿出现了{_why}。请完整重写本节："
                    "所有判断改为概率化、可验证的表述，禁止任何'最/第一/保证/百分百'类绝对化措辞，"
                    "禁止评论区/私信/关注/扣字/领资料等与读者互动的引导——"
                    f"「下一步」只写读者业务上的动作，篇幅 {length_spec['words']} 字，以完整句子收尾。"}],
                purpose="article_section", max_tokens=12000,
                thinking="disabled")
        parts.append(f"## {sec.get('heading', '')}\n\n{part}")

    md_text = "\n\n".join(parts)

    # 老编辑守卫：全文过一遍机翻腔/AI 味（Q：专业度 OK 但机翻痕迹浓）。
    # 铁律=数字与图片占位标记一个不能少，否则保留原稿；失败同样回落原稿。
    if md_text.strip():
        import re as _re

        def _sig(t: str) -> tuple:
            return (sorted(_re.findall(r"\d+(?:\.\d+)?", t)), t.count("!["))

        try:
            edited = await gateway.complete(
                [{"role": "system", "content": load("article_editor")},
                 {"role": "user", "content": md_text}],
                purpose="article_editor", max_tokens=20000,
                thinking="disabled")
            # 元指令防线：编辑模型偶尔把「全文已符合修订标准…原样输出」这类流水线
            # 自述写进正文（2026-10-02 P1 实测 voice=5.0）——命中即弃用编辑稿保留原稿
            _META = ("修订标准", "原样输出", "直译腔", "修改说明", "修订说明", "已按上述要求")
            if edited and edited.strip() and _sig(edited) == _sig(md_text):
                if any(m in edited for m in _META):
                    print("[article] 编辑稿含元指令文本，弃用保留原稿")
                else:
                    md_text = edited
            else:
                print("[article] 编辑守卫未采用（数字/占位标记变化），保留原稿")
        except Exception as e:  # noqa: BLE001 润色失败不影响成文
            print(f"[article] 编辑守卫失败，保留原稿: {type(e).__name__}: {str(e)[:100]}")

    # 数据图表（专职步骤）：从成文提取数据对比点 → matplotlib 渲染 → 按节插入。
    # 不再依赖分节写作顺带输出（模型依从性不稳定）；提取不到就不加，绝不编造。
    from ..articles.charts import render_chart, validate_spec
    if md_text.strip():
        try:
            spec_resp = await gateway.complete_json(
                [{"role": "system", "content": load("article_charts")},
                 {"role": "user", "content": md_text[:8000]}],
                purpose="article_charts", max_tokens=4000,
                thinking="disabled")
            specs = [x for x in (spec_resp.get("charts") or []) if isinstance(x, dict)][:3]
        except Exception as e:  # noqa: BLE001
            specs = []
            print(f"[article] 图表提取失败（跳过）: {type(e).__name__}: {str(e)[:80]}")
        rendered = []
        for k, sp in enumerate(specs):
            try:
                sp = validate_spec(json.dumps(sp))
                sp.setdefault("after_section", k + 1)
                from ..tenant_brand import primary_of

                path = await asyncio.to_thread(render_chart, sp, primary_of(tenant_id))
                rendered.append((min(int(sp.get("after_section") or k + 1), 6), sp, Path(path).name))
            except Exception as e:  # noqa: BLE001
                print(f"[article] 图表 {k} 渲染失败跳过: {type(e).__name__}: {str(e)[:60]}")
        for sec_no, sp, name in sorted(rendered, key=lambda x: -x[0]):
            t = str(sp.get("title") or "图表").replace('"', '')
            md_text = _insert_chart_after_section(md_text, sec_no, t, name)

    # 自动配图（Q：文章要有一堆配图且自动出现）：剩余图片占位逐张 CogView 生成，
    # 单篇成本约 0.06×张数；失败跳过保留占位标记（可手动在占位卡再生成）
    from ..articles.images import gen_image

    placeholders = re.findall(r"!\[([^\]]*)\]\(search:([^)]*)\)", md_text)
    for cap, kw in placeholders:
        try:
            r = await asyncio.to_thread(gen_image, cap, kw)
            fname = Path(r["file"]).name
            md_text = md_text.replace(f"![{cap}](search:{kw})", f"![]({fname})", 1)
            ctx.set_progress(97, f"AI 配图完成：{(cap or kw)[:28]}")
        except Exception as e:  # noqa: BLE001 单张失败不阻塞成文
            print(f"[article] 自动配图失败（保留占位）: {type(e).__name__}: {str(e)[:80]}")



    # 头图（深靛渐变+白字标题+账号名）：渲染并插入 md 顶部
    try:
        from ..articles.charts import render_cover
        from ..tenant_brand import brand_of

        signature = brand_of(tenant_id)["signature"]  # 租户品牌署名（头图右下）
        from ..tenant_brand import cover_slogan_of, primary_of

        cover_path = await asyncio.to_thread(
            render_cover, title or "深度分析", signature, primary_of(tenant_id),
            cover_slogan_of(tenant_id))
        cover_name = Path(cover_path).name
        md_text = f"![头图](cover:{cover_name})\n\n" + md_text
    except Exception as e:  # noqa: BLE001 头图失败不影响正文
        print(f"[article] 头图渲染失败: {type(e).__name__}: {str(e)[:80]}")

    # 一页纸知识图解（文末总结图）：LLM 抽取 → 排版 → PNG；失败降级不阻塞成文
    try:
        from ..articles.kg import generate_for_article

        ctx.set_progress(98, "生成一页纸知识图解")
        kg = await generate_for_article(article_id, tenant_id, md_text)
        md_text = md_text.rstrip() + f"\n\n![知识图解]({kg['file']})\n"
        ctx.set_progress(99, "知识图解完成")
    except Exception as e:  # noqa: BLE001 降级不阻塞
        print(f"[article] 知识图解失败（不影响成文）: {type(e).__name__}: {str(e)[:80]}")

    with Session(engine) as s:
        article = s.get(Article, article_id)
        article.title = title or article.title
        article.title_alts = title_alts
        article.md = md_text
        article.status = "draft"
        s.add(article)
        s.commit()

        ctx.set_progress(100, "完成")
        return {"article_id": article_id, "title": title, "style": style, "length": length}


def _insert_chart_after_section(md: str, sec_no: int, title: str, filename: str) -> str:
    """把图表图片标记插入第 sec_no 个小节（1 基）的末尾；越界则追加到文末。"""
    lines = md.split("\n")
    out: list[str] = []
    cnt = 0
    inserted = False
    i = 0
    while i < len(lines):
        out.append(lines[i])
        if lines[i].startswith("## "):
            cnt += 1
            if cnt == sec_no:
                j = i + 1
                while j < len(lines) and not lines[j].startswith("## "):
                    out.append(lines[j])
                    j += 1
                out.append(f"\n![图表：{title}](chart:{filename})\n")
                inserted = True
                i = j
                continue
        i += 1
    if not inserted:
        out.append(f"\n![图表：{title}](chart:{filename})")
    return "\n".join(out)
