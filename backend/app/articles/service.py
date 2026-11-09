"""公众号流水线：长文生成执行器。渲染见 renderer.py。"""
from __future__ import annotations

import asyncio
import json
import time
import re
from pathlib import Path
from datetime import datetime, timezone

from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import AppSetting, Article, KBDocument, KBTopic, Script, Topic
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
    "包就业", "包接单", "保过", "百分百就业", "学完月入",
]
DEFAULT_STYLE = "deep_dive"  # Q13：用户常用"深度长文"，默认固化+选择器弱化

# 双档位（2026-10-02 用户拍板：双档位·默认公众号版）。feed=公众号版（推荐流完读率优先：
# 1200~1800 字、开头即答案、判断式小节标题、金句收尾）；deep=深度版（原 3000~5000 长文规格，
# 铺垫式开头、机制拆解/方法框架/边界条件三件套）。规格文本分别注入三个 prompt 的占位符。
# deep 档 2026-10-08 按「数字生命卡兹克」爆款拆解吸收四条：判决句标题（废论文腔）、
# 可转述原创框架、术语必跟类比、数字体感锚+人物年代叙事锚（一句话事实，不破去故事化裁定）。
ARTICLE_LENGTHS: dict[str, dict[str, str]] = {
    "feed": {
        "words": "300~450",
        "title": (
            "- 微信公众号标题，≤32 字（展示上限）\n"
            "- 本篇发公众号推荐流：标题要在信息流里 0.5 秒内让目标读者（{{WHO}}）"
            "判断「这说的就是我」\n"
            "- 公式（至少占两条）：人群定位（点明 {{WHO}} 里的具体人群）+ 具体数字或金额 + "
            "冲突/反常识 + 可带走的判断\n"
            "- 用目标读者的痛感词（从受众域口径提炼：踩过什么坑、怕什么损失、想要什么保障）——"
            "「被坑过」「怕没效果」「钱花了没效果」「先别急着上」这类具体痛感词可用；"
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
            "- 小节标题=判断句或利益句（句式示范：「80% 的行业投入，死在标准没写进约定里」，内容按受众域替换），"
            "禁止名词术语式标题（例：「XX 的机制拆解」）\n"
            "- 3~4 节里至少 1 节是完整机制/方法拆解（步骤+反例，读者能直接套用）；"
            "边界条件并进对应小节，不单开一节\n"
            "- 每节至少带 1 个素材里的具体数字\n"
            "- 结尾节先用一句可转发的金句收束（替目标读者说出他想说而不敢说的话），"
            "再给读者业务上的下一步动作（句式示范：「动手之前，先把验收标准写进约定」，按受众域替换）；"
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
            "- 首选判决句式：一个完整的结论宣告（示范：「AI 让数学再也回不去那个旧世界了」——"
            "陈述一个反共识或不可逆的判断，主语具体、零术语堆砌，让目标读者一眼看到立场）\n"
            "- 备选可用克制专业式的陈述句：直接陈述核心判断/机制/方法，像行业研究报告的章节题；"
            "具体压倒抽象，带数字、行业词、机制词\n"
            "- 主标题 1 个 + 备选 2 个（备选换角度，不改事实；判决句式与克制陈述式至少各占其一）\n"
            "- 禁止（出现即返工）：论文标题腔——「X 的 Y：从 A 到 B 的 Z 拆解」「XX 的机制/归因/边界」"
            "式对仗主副题；悬念钩子腔——「只问一句」「终于有人讲清了」「你以为…其实」；"
            "口语与情绪词——「准不准」「说白了」「哈哈」"
        ),
        "outline": (
            "- 本篇是「深度版」：总字数 3000~5000 字，5~7 个小节，每节 700~1100 字\n"
            "- 第一节先用 2~4 句客观陈述行业现状或普遍认知做铺垫，随后亮出核心判断或关键问题；"
            "铺垫控制在首段内，不许整节都是铺垫\n"
            "- 原创框架（硬规则）：全文必须贡献至少一个可转述的原创判断框架——读者看完能一句话"
            "复述给同行的洞见或分析框架（用素材推出来，不是复述素材里现成的结论）；"
            "在大纲里明确它落在哪一节亮出（建议倒数第二节，让全文的铺垫都为它服务）\n"
            "- 叙事锚：关键知识点/结论优先挂「人物+年代+冲突」的一句话事实锚"
            "（谁、哪一年、做了什么、卡在哪或翻在哪），分配进对应小节当论据；"
            "只写一句话事实，不展开情节，素材里没有人物锚就不硬造\n"
            "- 至少一节是完整机制拆解：把问题的成因链条一层层剥开，每层有明确证据\n"
            "- 至少一节是可复用的方法框架：条件+步骤+反例，专业读者能直接套用\n"
            "- 至少一处写边界条件：这套理论在什么条件下不成立\n"
            "- 结尾节停在最重的判断上"
        ),
        "section": (
            "篇幅 700~1100 字，Markdown，关键数字与结论加粗\n"
            "- 若本节是全文第一节，按大纲要求先做 2~4 句行业现状铺垫再给判断\n"
            "- 类比纪律：术语与机制首次出现时，紧跟一个生活化类比或最小可验证例子"
            "（单个即可，禁止连续堆比喻）；类比服务于理解，不是修辞\n"
            "- 数字体感：关键数字后面跟一个体感锚点（这个数≈多少年/多少钱/多少倍日常量级），"
            "换算必须有公开事实作依据（如限速、均价、公开基准），素材没有依据就保留原数字，不许硬换算\n"
            "- 大纲分配的「人物+年代+冲突」事实锚按一句话事实写出，不展开成情节或场景"
        ),
    },
}
DEFAULT_LENGTH = "deep"  # 深度版唯一档位（2026-11-02 用户定版：公众号做深度内容，feed 退役）


def _audience_short(audience_note: str) -> str:
    """受众域口径 → 标题/大纲里的"目标读者"短语（去提示前缀，取核心人群）。"""
    t = (audience_note or "").strip()
    for pre in ("目标读者：", "目标读者", "面向", "受众：", "受众"):
        if t.startswith(pre):
            t = t[len(pre):]
    return t[:40] or "目标读者"


def length_specs(length: str, audience_note: str) -> dict[str, str]:
    """按租户受众域实例化档位规格：行业锚点随画像注入，模板只留中性框架与句式示范。

    此前"中小企业老板/项目烂尾"等 FDE 行业话术写死在所有租户的默认规格里
    （换行业部署即错默认）。"""
    base = ARTICLE_LENGTHS.get(length) or ARTICLE_LENGTHS[DEFAULT_LENGTH]
    who = _audience_short(audience_note)
    return {k: v.replace("{{WHO}}", who) if isinstance(v, str) else v for k, v in base.items()}

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
        from ..tenant_brand import audience_note_of, brand_of

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
        # 公众号独立报题线（2026-11-02）：无选题无脚本的草稿，报题标题+备注就是原料
        if topic is None and script is None:
            material = {
                "报题": {"标题": article.title, "备注/参考链接": article.note or ""},
                "深研报告（深度素材，长文的主要原料）": "",
            }
        # 公众号独立风格画像（从已发长文提取，app_settings）：有就注入所有 prompt 的素材块
        style_row = s.get(AppSetting, "article_style_note")
        if style_row is not None and (style_row.value or "").strip():
            material["公众号风格画像（从已发长文提取的表达习惯，行文必须贴合）"] = style_row.value[:1200]

    # ima 知识库取材（2026-11-08/09）：两条路径。
    # A. kb_doc_ids（知识库选题线）：把选题绑定的依据文档全文抽出来注入——素材在报题时就已确定。
    # B. kb_grounding（人工报题写文链）：按报题语义检索 ima，命中注入、零命中=缺料报错不硬写。
    # 每晚自动直出链两者都不带——热点判决走公开事实，不受知识库闸约束。
    kb_sources: list | None = None
    if payload.get("kb_grounding"):
        from ..articles.ima_kb import fetch_kb_material

        ctx.set_progress(6, "检索 ima 知识库素材")
        kb_sources, kb_text = await fetch_kb_material(article.title, article.note or "")
        if kb_sources:
            material["知识库素材（唯一事实来源）"] = kb_text
            material["知识库取材纪律"] = (
                "案例/数字/流程/验收标准等事实一律只用上面素材里出现的；"
                "素材里没有的不许编造；公开行业事件可引用但须注明是公开信息；"
                "每个小节至少呼应一处素材内容")
        else:
            raise RuntimeError(
                f"知识库缺料：ima 检索无相关素材（报题：{(article.title or '')[:40]}）。"
                "先往 ima 知识库补料，或改报题重写")

    style = payload.get("style") or DEFAULT_STYLE
    length = "deep"  # 深度版唯一档位（2026-11-02 用户定版，feed 退役；payload.length 忽略）
    length_spec = length_specs(length, audience_note_of(tenant_id))

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
        # 全文预算折算进每节（2026-11-07 修超长）：节数规格内部矛盾（5~7 节 × 每节 700~1100，
        # 上限算术可到 7700 > 总上限 5000）——模型逐节守规格但加总超规（实证两轮 6272/5100 字）。
        # 按实际节数把 3000~5000 总预算折算成本节篇幅，替换档位里的静态节长。
        sec_spec = length_spec["section"]
        if length == "deep":
            per_lo, per_hi = max(400, 3000 // n), max(450, 5000 // n)
            replaced = sec_spec.replace("篇幅 700~1100 字",
                                        f"篇幅 {per_lo}~{per_hi} 字（全文共 {n} 节、总预算 3000~5000 字）", 1)
            assert replaced != sec_spec, "deep 档 section 规格缺「篇幅 700~1100 字」锚点，替换静默失效"
            sec_spec = replaced
        sec_prompt = (render("article_section", PERSONA=persona_text,
                             HEADING=str(sec.get("heading") or ""),
                             POINTS=json.dumps(sec.get("points") or [], ensure_ascii=False),
                             SECTION_SPEC=sec_spec)
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
        # 标题行防线：分节模型偶尔无视「不要写标题行」把「## 标题」写进正文开头（2026-10-08
        # deep 档实测一篇重复 2 处）——组装前剥掉正文里的 2 级标题行（只有组装处该出 ##；### 子标题保留）
        part = "\n".join(l for l in part.splitlines() if not l.lstrip().startswith("## "))
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
        article.kb_sources = kb_sources or []
        article.md = md_text
        article.status = "draft"
        s.add(article)
        s.commit()

        ctx.set_progress(100, "完成")
        return {"article_id": article_id, "title": title, "style": style, "length": length}


_KB_BAN_CONNECTORS = ("综上所述", "由此可见", "说到底", "总而言之")


def _kb_style_violations(md: str, anchor_texts: list[str]) -> list[str]:
    """成文文风禁令机检：返回违规描述清单（空=干净）。提示词里的禁令靠模型自觉
    不可靠（#8 实测「不是X而是Y」×5）——代码正则兜底，命中走定向重写。"""
    out: list[str] = []
    bs = re.findall(r"不是[^。！？\n]{1,24}[，,][^。！？\n]{0,6}?(而是|是)", md)
    if len(bs) >= 2:
        out.append(f"「不是X，而是/是Y」式对仗出现 {len(bs)} 次（全文至多 1 次），保留最关键的一处，其余改写为直陈句")
    paras = [p.strip() for p in md.split("\n\n")]
    singles = [p for p in paras[1:-1]
               if 8 <= len(p) <= 30 and not p.startswith(("#", "|", "!", "-", "*"))
               and p.count("。") <= 1 and "：" not in p]
    if len(singles) >= 2:
        out.append(f"单句成段的戏剧化排版出现 {len(singles)} 处（如「{singles[0][:20]}」），并回上下文段落")
    if re.search(r"\*\*[^*\n]{1,24}\*\*[：:]", md):
        out.append("存在「加粗论点+冒号」的要点句式，改为正常陈述句")
    hits = [c for c in _KB_BAN_CONNECTORS if c in md]
    if hits:
        out.append(f"出现报告腔连接词 {'、'.join(hits)}，删除或改为直陈")
    if re.search(r"最[^。，\n]{1,8}的一种", md):
        out.append("出现「最X的一种」式标签，改为直陈")
    heads = re.findall(r"^##+\s*\d", md, flags=re.M)
    all_heads = re.findall(r"^##+ ", md, flags=re.M)
    if len(heads) >= 3 and len(heads) == len(all_heads):
        out.append("全部小节都是数字编号（01/02/03 模板骨架），改为直陈内容的小节标题或取消编号")
    import difflib

    for at in anchor_texts:
        m = difflib.SequenceMatcher(None, md, at).get_matching_blocks()
        longest = max(m, key=lambda b: b.size) if m else None
        if longest is not None and longest.size >= 40:
            out.append(f"正文与范本存在 {longest.size} 字连续重复（「{md[longest.a:longest.a + 30]}…」），必须改为原创表达")
    return out


@register_executor("article_kb_write", on_fail=_article_generate_fail)
async def article_kb_write(ctx: JobContext, payload: dict) -> dict:
    """知识库选题成文：绑定依据文档全文，单遍写出书面语 1900~2200 字规格。

    与 article_generate 的分节流水线不同——素材在选用时已锁定（kb_doc_ids），
    配方的关键是单遍成文让全分辨率细节不被大纲切碎（配方见 prompts/article_kb_write.md，
    由两篇认可稿反推固化）。失败回退复用 _article_generate_fail（文章不得卡"生成中"）。
    """
    article_id = payload["article_id"]
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise RuntimeError(f"article {article_id} 不存在")
        tenant_id = article.tenant_id
        article_title0 = article.title or ""
        article_note = article.note or ""
        docs = [d for d in (article.kb_doc_ids or []) if isinstance(d, dict) and d.get("media_id")]
        topic = s.exec(select(KBTopic).where(KBTopic.article_id == article_id)).first()  # type: ignore[attr-defined]
        judgment = (f"论点：{topic.judgment}\n读者：{topic.audience}\n为什么值得写：{topic.why}"
                    if topic is not None else (article_note or article_title0))
        from ..tenant_brand import audience_note_of, brand_of

        persona_text = brand_of(tenant_id)["persona"]
        length_spec = length_specs("deep", audience_note_of(tenant_id))
        from ..articles.ima_kb import kb_config

        cfg = kb_config(s)
    if not docs:
        raise RuntimeError("知识库成文缺少依据文档（kb_doc_ids 为空），无法按素材成文")
    if cfg is None:
        raise RuntimeError("ima 知识库未启用或凭证不全，无法取依据文档全文")

    # 依据文档全文（ima COS 直链逐篇拉取）；全空=多半是 403 限流窗，走退避梯子
    from ..articles.ima_kb import fetch_full_doc

    ctx.set_progress(8, f"取依据文档全文（{len(docs)} 篇）")
    parts: list[str] = []
    got_any = False
    for i, d in enumerate(docs, 1):
        text = await fetch_full_doc(cfg["client_id"], cfg["api_key"], str(d["media_id"]))
        got_any = got_any or bool(text.strip())
        parts.append(f"【文档{i}】{str(d.get('title') or '').strip()}\n{text}")
    material = "\n\n".join(parts)
    if not got_any:
        raise TransientError("依据文档全文全部获取失败（ima 限流或文档不可读），稍后自动重试")

    # 作者案头：先把"要说的一句新话"想出来（增量判断+主证据+砍料+带走动作）——
    # 没有这道，成稿必然沦为对单篇依据文档的改写（#8 教训：判断=文档标题复述）
    ctx.set_progress(18, "作者案头：提炼增量判断与主证据")
    brief = None
    try:
        brief = await gateway.complete_json(
            [{"role": "user", "content": render("article_kb_brief", MATERIAL=material)}],
            purpose="article_kb_brief", max_tokens=4000, thinking="disabled")
    except Exception as e:  # noqa: BLE001 简报失败降级回选题判断，不阻塞成文
        print(f"[kb-write] 简报失败，降级用选题判断: {type(e).__name__}: {str(e)[:80]}")
    b_judgment = str((brief or {}).get("judgment") or "").strip()
    if not b_judgment:
        b_judgment = judgment
        print("[kb-write] 增量判断为空，降级用选题判断")
    brief_text = json.dumps(brief, ensure_ascii=False) if brief else json.dumps(
        {"judgment": b_judgment, "evidence": [], "cut": [], "takeaway": ""}, ensure_ascii=False)

    # 标题组（与主线同一 prompt：标题+备选）
    ctx.set_progress(30, "起标题")
    title_data = await gateway.complete_json(
        [{"role": "user", "content": render("article_title", PERSONA=persona_text,
                                            TITLE_SPEC=length_spec["title"])
          + "\n\n选题素材：\n" + material[:9000]}],
        purpose="article_title", max_tokens=8192, thinking="disabled")
    title_from_call = str(title_data.get("title") or "").strip()
    title_alts = [t for t in (title_data.get("title_alts") or []) if t][:2]

    # 带范本成文：两篇认可稿全文进提示词——模型先见过"好长什么样"，再谈规则
    ctx.set_progress(45, "成文（范本对齐+硬红线）")
    anchors_text = load("article_kb_anchors")
    body = await gateway.complete(
        [{"role": "user", "content": render("article_kb_write", ANCHORS=anchors_text,
                                            BRIEF=brief_text, MATERIAL=material)}],
        purpose="article_kb_write", max_tokens=8192, thinking="disabled")
    body = (body or "").strip()
    if body.startswith("```"):  # 剥掉模型偶发的代码围栏
        body = re.sub(r"^```[a-z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    lines = body.split("\n")
    if not lines or not lines[0].lstrip().startswith("# "):
        raise TransientError("成文输出缺「# 标题」首行（疑似空正文或截断），稍后自动重试")
    title = lines[0].lstrip()[2:].strip() or title_from_call or article_title0
    md_text = "\n".join(lines[1:]).strip()
    n_chars = len(re.sub(r"\s", "", md_text))
    if n_chars < 1500:
        raise TransientError(f"成文仅 {n_chars} 字（规格 1900~2200、下限 1500），疑似截断，稍后自动重试")

    # 禁令机检→定向重写（只动违规处；数字/图片标记签名守卫，变了弃用）
    anchor_texts = [x for x in anchors_text.split("## 范本")[1:] if len(x) > 200]
    violations = _kb_style_violations(md_text, anchor_texts)
    if violations:
        ctx.set_progress(75, f"机检命中 {len(violations)} 项，定向重写")
        try:
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_kb_polish",
                                                    VIOLATIONS="\n".join(f"- {v}" for v in violations),
                                                    DRAFT=md_text)}],
                purpose="article_kb_polish", max_tokens=8192, thinking="disabled")

            def _sig(t: str) -> tuple:
                return (sorted(re.findall(r"\d+(?:\.\d+)?", t)), t.count("!["))

            polished = (polished or "").strip()
            if polished and _sig(polished) == _sig(md_text):
                remain = _kb_style_violations(polished, anchor_texts)
                print(f"[kb-write] 定向重写完成，剩余违规 {len(remain)} 项"
                      + (f"：{remain[0][:40]}" if remain else ""))
                md_text = polished
            else:
                print("[kb-write] 重写稿签名不符（数字/图片变化），保留原稿")
        except Exception as e:  # noqa: BLE001 重写失败不阻塞成文
            print(f"[kb-write] 定向重写失败，保留原稿: {type(e).__name__}: {str(e)[:80]}")

    # 数据图表：与主线同一套后处理；提取不到就不加，绝不编造
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
            print(f"[kb-write] 图表提取失败（跳过）: {type(e).__name__}: {str(e)[:80]}")
        rendered = []
        for k, sp in enumerate(specs):
            try:
                sp = validate_spec(json.dumps(sp))
                sp.setdefault("after_section", k + 1)
                from ..tenant_brand import primary_of

                path = await asyncio.to_thread(render_chart, sp, primary_of(tenant_id))
                rendered.append((min(int(sp.get("after_section") or k + 1), 6), sp, Path(path).name))
            except Exception as e:  # noqa: BLE001
                print(f"[kb-write] 图表 {k} 渲染失败跳过: {type(e).__name__}: {str(e)[:60]}")
        for sec_no, sp, name in sorted(rendered, key=lambda x: -x[0]):
            t = str(sp.get("title") or "图表").replace('"', '')
            md_text = _insert_chart_after_section(md_text, sec_no, t, name)

    # 头图（渲染失败不影响正文）
    ctx.set_progress(92, "渲染头图")
    try:
        from ..articles.charts import render_cover
        from ..tenant_brand import cover_slogan_of, primary_of

        signature = brand_of(tenant_id)["signature"]
        cover_path = await asyncio.to_thread(
            render_cover, title or "深度分析", signature, primary_of(tenant_id),
            cover_slogan_of(tenant_id))
        md_text = f"![头图](cover:{Path(cover_path).name})\n\n" + md_text
    except Exception as e:  # noqa: BLE001
        print(f"[kb-write] 头图渲染失败: {type(e).__name__}: {str(e)[:80]}")

    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise RuntimeError(f"article {article_id} 不存在（落稿时已被删除）")
        article.title = title or article.title
        article.title_alts = title_alts
        article.kb_sources = docs
        article.md = md_text
        article.status = "draft"
        s.add(article)
        topic = s.exec(select(KBTopic).where(KBTopic.article_id == article_id)).first()  # type: ignore[attr-defined]
        if topic is not None and topic.status == "selected":
            topic.status = "written"
            s.add(topic)
        s.commit()
        ctx.set_progress(100, "完成")
        return {"article_id": article_id, "title": title, "style": "kb_write"}


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


# ---- 公众号独立报题线（2026-11-02 三线定版）：独立热点报题 + 独立风格画像 ----

_ARTICLE_STYLE_KEY = "article_style_note"


def _article_pending_exists(title: str) -> bool:
    with Session(engine) as s:
        row = s.exec(select(Article).where(
            Article.title == title[:60],  # type: ignore[attr-defined]
            Article.status.in_(["pending", "generating", "draft", "edited", "rendered"]),  # type: ignore[attr-defined]
        )).first()
        return row is not None


@register_executor("article_hotspots")
async def article_hotspots(ctx: JobContext, payload: dict) -> dict:
    """公众号独立热点报题（2026-11-05 改每日一篇直出）：站外热点×画像 → 1 篇待写草稿，
    建稿后自动链发 article_generate 成文——早晨草稿箱里是一篇完整长文，改完即发。

    与抖音选题提炼（trending_topics）完全独立——公众号线不进选题库，报题即草稿。"""
    from ..tenant_brand import brand_of
    from ..topics.hotlist import fetch_baidu_hot, fetch_tech_news

    ctx.set_progress(10, "收集站外热点")
    baidu, baidu_meta = await asyncio.to_thread(fetch_baidu_hot, 12)
    news, news_meta = await asyncio.to_thread(fetch_tech_news, 6, 48)
    items = []
    for h in baidu[:8]:
        items.append({"来源": "百度热搜", "标题": str(h.get("title") or "")[:40]})
    for n in news[:10]:
        items.append({"来源": str(n.get("source") or "科技媒体"), "标题": str(n.get("title") or "")[:60]})
    if not items:
        raise TransientError("站外热点源全部无数据（百度/科技媒体都空），稍后自动重试")

    # 受众锚定预筛（2026-11-05 垃圾选题教训：科技媒体清单里挑出「Anthropic 中转站」
    # 这种纯技术行业新闻——读者的买家是不懂技术的实体老板，先硬性剔除与生意无关的题）
    ctx.set_progress(25, "按读者受众预筛热点")
    screen = await gateway.complete_json(
        [{"role": "user", "content":
          "本号的生意：卖一门 1999 元的线下课，教不懂技术的普通人做 AI 落地（FDE）。"
          "读者=这类人：实体老板、想转行的普通人，关心生意成本、用人、赚钱、职业机会。\n"
          "候选热点必须过**双锚**，缺一即剔除：\n"
          "① 受众锚：他们（不懂技术）看得懂、用得上；需要写代码/调 API/懂模型圈的话题剔除\n"
          "② 主题锚：话题必须长在 AI 上——AI 对生意的影响、AI 落地的坑、AI 带来的职业机会、"
          "学 AI 落地的价值。跟 AI 无关的话题（哪怕受众完全对，如汽车/餐饮/房产本身）一律剔除\n"
          "（真实教训：《甲醇混动车成本账》受众对但主题是汽车，与目标零关系）\n"
          '\n清单：\n' + json.dumps(items, ensure_ascii=False) +
          '\n输出 JSON：{"relevant": [双锚都命中的序号数组]}。一条都没有就给空数组。'}],
        purpose="discover_keywords", max_tokens=1024, thinking="disabled")
    keep = {int(x) for x in (screen.get("relevant") or []) if str(x).strip().isdigit()}
    items = [x for i, x in enumerate(items) if (i + 1) in keep] if keep else []
    if not items:
        raise TransientError("今日热点里没有读者业务相关的题（受众锚定筛空），宁缺勿滥，稍后重试")
    ctx.set_progress(35, f"预筛后剩 {len(items)} 条相关热点，提炼切角")

    persona_text = brand_of(1)["persona"]
    data = await gateway.complete_json(
        [{"role": "user", "content": render("article_hotspots", PERSONA=persona_text)
          + "\n\n热点清单（JSON）：\n" + json.dumps(items, ensure_ascii=False)}],
        purpose="article_hotspots", max_tokens=4000, thinking="disabled")
    picks = [x for x in (data.get("topics") or []) if isinstance(x, dict) and (x.get("title") or "").strip()][:1]
    if not picks:
        raise TransientError("热点提炼未产出切角（LLM 空响应），稍后自动重试")

    created = []
    with Session(engine) as s:
        for x in picks:
            title = str(x.get("title") or "").strip()[:60]
            if not title or _article_pending_exists(title):
                continue
            note = (f"热点：{str(x.get('hotspot') or '')[:80]}｜"
                    f"为什么现在写：{str(x.get('why_now') or '')[:80]}｜"
                    f"切角：{str(x.get('angle') or '')[:80]}")
            s.add(Article(title=title, note=note, status="pending"))
            created.append(title)
        s.commit()
    ctx.set_progress(90, f"报题 {len(created)} 篇，链发成文任务")
    # 直出链：报题即自动生成长文（每晚一篇，早晨可发）
    chained = 0
    if created:
        from ..jobs.runner import runner
        from ..db import engine as _engine
        from sqlmodel import Session as _S
        with _S(_engine) as s2:
            rows = s2.exec(select(Article).where(
                Article.title.in_(created),  # type: ignore[attr-defined]
                Article.status == "pending")).all()  # type: ignore[attr-defined]
            ids = [r.id for r in rows]
        for aid in ids:
            await runner.submit("article_generate",
                                {"article_id": aid, "style": "auto", "length": "deep"})
            with _S(_engine) as s3:
                art = s3.get(Article, aid)
                if art is not None:
                    art.status = "generating"
                    s3.add(art)
                    s3.commit()
            chained += 1
    ctx.set_progress(100, f"报题 {len(created)} 篇，直出链发 {chained} 篇")
    return {"drafts": created, "chained": chained}


@register_executor("article_style_extract")
async def article_style_extract(ctx: JobContext, payload: dict) -> dict:
    """公众号风格画像：从已成文长文提取表达习惯，存 app_settings，长文生成默认注入。"""
    ctx.set_progress(15, "收集已成文长文")
    with Session(engine) as s:
        rows = s.exec(select(Article).where(
            Article.status.in_(["draft", "edited", "rendered"]),  # type: ignore[attr-defined]
        ).order_by(Article.id.desc())  # type: ignore[attr-defined]
        ).all()
        samples = []
        for a in rows:
            if a.md and len(a.md.strip()) > 300:
                samples.append(a.md[:3000])
            if len(samples) >= 4:
                break
    if not samples:
        raise RuntimeError("还没有已成文的长文可提取（先在公众号线写出第一篇）")

    ctx.set_progress(45, "提炼风格特征")
    data = await gateway.complete_json(
        [{"role": "user", "content":
            "以下是同一作者的多篇公众号深度长文节选。请提炼这位作者稳定的表达习惯，输出 JSON："
            "{\"note\": \"200 字以内的风格画像（句式习惯/开头收尾方式/论证风格/用词偏好/忌讳），"
            "写成给写作模型的指令，不要罗列例子\"}\n\n"
            + "\n\n———\n\n".join(samples)}],
        purpose="article_style_extract", max_tokens=2000, thinking="disabled")
    note = str(data.get("note") or "").strip()
    if not note:
        raise TransientError("风格提炼空响应，稍后自动重试")

    with Session(engine) as s:
        row = s.get(AppSetting, _ARTICLE_STYLE_KEY)
        if row is None:
            s.add(AppSetting(key=_ARTICLE_STYLE_KEY, value=note))
        else:
            row.value = note
            s.add(row)
        s.commit()
    ctx.set_progress(100, "公众号风格画像已更新")
    return {"note": note[:120]}


# ---- 知识库选题线（2026-11-09）：体系化遍历 ima 目录 → 带判断选题 → 报题即草稿链写 ----

_KB_TOPIC_BATCH = 12       # 每轮用于选题的文档数
_KB_TOPIC_FULLTEXT = 3     # 其中取全文摘录的篇数


def _kb_upsert_docs(s: Session, kb_id: str, folder_id: str, folder_name: str,
                    docs: list[dict]) -> int:
    """目录清单并入 kb_documents 台账，返回新入库（status=new）数量。"""
    new_cnt = 0
    for d in docs:
        mid = d.get("media_id") or ""
        if not mid:
            continue
        exists = s.exec(select(KBDocument).where(
            KBDocument.media_id == mid)).first()  # type: ignore[attr-defined]
        if exists is not None:
            continue
        s.add(KBDocument(kb_id=kb_id, folder_id=folder_id, folder_name=folder_name,
                         media_id=mid, title=d.get("title") or "", status="new"))
        new_cnt += 1
    s.commit()
    return new_cnt


@register_executor("article_kb_topics")
async def article_kb_topics(ctx: JobContext, payload: dict) -> dict:
    """体系化全量遍历 ima 核心目录 → 按批提炼选题 → 全部入选题池（KBTopic，待选）。

    与抽卡式不同：按目录顺序全库遍历，单次运行带时间预算（可断点续跑——kb_documents
    的 mined 标记记住进度，下次运行从断点继续），跑完全库后选题池即全量选题目录。
    素材纪律：选题必须绑定具体库内文档；文档不足以支撑合格选题时跳过，不产泛主题；
    与已有选题/文章近义去重；用户「不做」过的题不复活。"""
    from ..articles.ima_kb import fetch_full_doc, kb_config, list_folder_docs, topic_folders, topic_kb_id
    from ..models import KBDocument, KBTopic

    budget_sec = int(payload.get("budget_sec") or 480)
    batch_size = int(payload.get("batch_size") or 12)

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("知识库选题线未启用：缺少 ima_kb_enabled / 凭证配置")
        client_id, api_key = cfg["client_id"], cfg["api_key"]
        kb_id = topic_kb_id(s)
        folders = topic_folders(s)
        recent = [r.title for r in s.exec(select(Article).where(
            Article.status.in_(["pending", "generating", "draft", "edited", "rendered"]))  # type: ignore[attr-defined]
            .order_by(Article.id.desc()).limit(30)).all()  # type: ignore[attr-defined]
            if r.title]
    if not folders:
        raise RuntimeError("选题遍历目录为空：请配置 ima_topic_folders")

    def _norm(t: str) -> str:
        return re.sub(r"[\s_\-——（）()\[\]「」《》,，。.·！!?？：:]", "", (t or "")).lower()

    def _match_doc(dt: str, docs: list[dict]) -> dict | None:
        nq = _norm(dt)
        if len(nq) < 6:
            return None
        for d in docs:
            nd = _norm(d["title"])
            if nq == nd or (len(nq) >= 8 and (nq in nd or nd in nq)):
                return d
        return None

    started = time.monotonic()
    added_total = 0
    processed_docs = 0
    batch_no = 0
    exhausted = False

    for fid, fname in folders:
        if time.monotonic() - started > budget_sec:
            break
        try:
            docs = await list_folder_docs(client_id, api_key, kb_id, fid)
        except Exception as e:  # noqa: BLE001
            print(f"[kb-topics] 目录列举失败（{fname}）: {type(e).__name__}: {str(e)[:80]}")
            continue
        with Session(engine) as s:
            new_cnt = 0
            for d in docs:
                mid = d.get("media_id") or ""
                if not mid:
                    continue
                exists = s.exec(select(KBDocument).where(
                    KBDocument.media_id == mid)).first()  # type: ignore[attr-defined]
                if exists is None:
                    s.add(KBDocument(kb_id=kb_id, folder_id=fid, folder_name=fname,
                                     media_id=mid, title=d.get("title") or "", status="new"))
                    new_cnt += 1
            s.commit()
            fresh = s.exec(select(KBDocument).where(
                KBDocument.folder_id == fid,  # type: ignore[attr-defined]
                KBDocument.status == "new")).all()  # type: ignore[attr-defined]
            fresh = [{"title": x.title, "media_id": x.media_id, "id": x.id} for x in fresh]
        processed_docs += len(fresh)
        if not fresh:
            await asyncio.sleep(0.5)
            continue

        # 本目录按批提炼
        for i in range(0, len(fresh), batch_size):
            if time.monotonic() - started > budget_sec:
                break
            chunk = fresh[i:i + batch_size]
            batch_no += 1
            excerpts = []
            for d in chunk[:_KB_TOPIC_FULLTEXT]:
                txt = await fetch_full_doc(client_id, api_key, d["media_id"], cap=3000)
                if txt:
                    excerpts.append(f"【{d['title']}】\n{txt}")
                await asyncio.sleep(0.6)
            titles_block = "\n".join(f"- {d['title']}" for d in chunk)
            excerpts_block = "\n\n".join(excerpts) or "（本轮无全文摘录，仅按标题判断）"
            recent_block = "\n".join(f"- {t[:40]}" for t in recent) or "（无）"
            try:
                pick = await gateway.complete_json(
                    [{"role": "user", "content": render("article_kb_topic", MAX_TOPICS="2")
                      + f"\n\n目录：{fname}\n\n文档标题清单：\n{titles_block}"
                      + f"\n\n部分正文摘录：\n{excerpts_block}"
                      + f"\n\n近期已报题标题（避免意思相近）：\n{recent_block}"}],
                    purpose="article_kb_topic", max_tokens=4000, thinking="disabled")
            except Exception as e:  # noqa: BLE001 单批 LLM 失败跳过该批
                print(f"[kb-topics] 批 {batch_no} 提炼失败跳过: {type(e).__name__}: {str(e)[:80]}")
                pick = {}
            topics = [t for t in (pick.get("topics") or [])
                      if isinstance(t, dict) and (t.get("title") or "").strip()][:2]

            title_to_doc = {d["title"]: d for d in chunk}
            added = 0
            dropped: list[str] = []
            with Session(engine) as s:
                for t in topics:
                    title = str(t.get("title") or "").strip()[:60]
                    if not title:
                        continue
                    ntitle = _norm(title)
                    dup_topic = any(_norm(x.title) == ntitle for x in s.exec(select(KBTopic)).all())  # type: ignore[attr-defined]
                    dup_article = any(_norm(a.title) == ntitle for a in s.exec(select(Article).where(
                        Article.status.in_(["pending", "generating", "draft", "edited", "rendered"]))).all())  # type: ignore[attr-defined]
                    if dup_topic or dup_article:
                        dropped.append(f"近义重复：{title[:24]}")
                        continue
                    doc_ids = []
                    for dt in [str(x) for x in (t.get("docs") or [])]:
                        hit = _match_doc(dt, chunk)
                        if hit:
                            doc_ids.append({"media_id": hit["media_id"], "title": hit["title"]})
                    if not doc_ids:
                        dropped.append(f"依据对不上：{dt[:24]}")
                        continue
                    s.add(KBTopic(title=title,
                                  judgment=str(t.get("judgment") or "")[:200],
                                  audience=str(t.get("audience") or "")[:12],
                                  why=str(t.get("why") or "")[:120],
                                  docs=doc_ids,
                                  folder_name=fname,
                                  status="new"))
                    added += 1
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                for row_ in s.exec(select(KBDocument).where(
                        KBDocument.id.in_([d["id"] for d in chunk]))).all():  # type: ignore[attr-defined]
                    row_.status = "mined"
                    row_.last_mined_at = now
                    s.add(row_)
                s.commit()
            added_total += added
            recent = ([f"{t.get('title', '')}"] if topics else []) + recent[:20]
            ctx.set_progress(min(95, 10 + batch_no * 3),
                             f"批 {batch_no}（{fname}）：读 {len(chunk)} 篇 → 新选题 {added}｜累计入池 {added_total}")
            await asyncio.sleep(0.6)
        await asyncio.sleep(0.8)

    if batch_no == 0 and processed_docs == 0:
        exhausted = True

    # 池内待选计数
    with Session(engine) as s:
        pool_new = len(s.exec(select(KBTopic).where(
            KBTopic.status == "new")).all())  # type: ignore[attr-defined]

    msg = (f"全量遍历{'完成' if exhausted else '进行中'}：本轮读 {processed_docs} 篇 / {batch_no} 批，"
           f"新选题 {added_total} 个入池；池内待选 {pool_new}")
    ctx.set_progress(100, msg[:90])
    return {"processed_docs": processed_docs, "batches": batch_no, "added": added_total,
            "pool_new": pool_new, "exhausted": exhausted, "message": msg}
