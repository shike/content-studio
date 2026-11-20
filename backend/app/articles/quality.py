"""长文质量与合规：违禁词机检、文风禁令、质量自评闸门。

2026-11-16 从 service.py 拆出（C 阶段结构整治，只挪不改行为）：
违禁词表单一数据源 banned_words.json 由本模块加载（单一数据源 JSON）；
service.py/review.py/adapt.py 经导入继续可用（deepread 线已随产线退役删除）。
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from ..jobs.errors import TransientError
from ..models import AppSetting

# 违禁词表（程序化合规红线）抽单一数据源 banned_words.json：
# 共用同一 JSON，改表只改文件，不再双处同步。裸「最好的」已移除——误杀「千万别挑最好的
# 那条」（反向建议）与「最好的线，设备新」（描述性比较），语义级由 LLM 评审兜底。
_BANNED_TABLE = json.loads((Path(__file__).parent / "banned_words.json").read_text(encoding="utf-8"))
BANNED_PATTERNS = _BANNED_TABLE["banned_patterns"]

# 刻意转化话术（自然收尾红线）：表本体同在 banned_words.json
CTA_PATTERNS = _BANNED_TABLE["cta_patterns"]

_KB_BAN_CONNECTORS = ("综上所述", "由此可见", "说到底", "总而言之")


def strip_fence(body: str) -> str:
    """剥 LLM 输出的 ``` 围壳（adapt/review 两线共用，2026-11-16 合并）。"""
    body = (body or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    return body


def load_cursor(s, key: str) -> tuple[int, int]:
    """AppSetting 游标 "idx:pos"（miner 线专用，2026-11-16 合并；deepread 线已退役）。"""
    from sqlmodel import Session  # noqa: F401 仅类型位
    row = s.get(AppSetting, key)
    raw = (row.value or "").strip() if row is not None else ""
    m = re.match(r"^(\d+):(\d+)$", raw)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def save_cursor(s, key: str, idx: int, pos: int) -> None:
    row = s.get(AppSetting, key)
    val = f"{idx}:{pos}"
    if row is None:
        s.add(AppSetting(key=key, value=val))
    else:
        row.value = val
        s.add(row)
    s.commit()


def min_chars_or_raise(md_text: str, label: str) -> int:
    """1500 字下限守卫（adapt 线专用，2026-11-16 合并；文案里的规格数字随线不同勿写死）。"""
    n_chars = len(re.sub(r"\s", "", md_text))
    if n_chars < 1500:
        raise TransientError(f"{label}仅 {n_chars} 字（下限 1500），疑似截断，稍后自动重试")
    return n_chars


def banned_hits(text: str) -> list[str]:
    """程序化违禁词命中（否定语境放行：'千万别挑最好的'是反向建议不是绝对化承诺——
    2026-10-02 P1 实测误杀后加窗口）。表已抽单一数据源 banned_words.json（与
    banned_words.json 单源）；否定语境窗口算法在本模块内实现。"""
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


def content_signature(t: str) -> tuple:
    """正文守卫签名：数字集合+图片占位标记数。编辑稿/重写稿必须与原稿签名一致，
    变了即弃用（保数字保图是硬规则）——主线编辑守卫与知识库线定向重写共用。"""
    return (sorted(re.findall(r"\d+(?:\.\d+)?", t)), t.count("!["))


_AI_CLICHE = ("赋能", "抓手", "闭环", "底层逻辑", "护城河", "降维打击", "颗粒度",
              "组合拳", "顶层设计", "生态位")
_AI_PHRASES = (
    (r"不仅[^。！？\n]{0,20}(而且|更是)", "「不仅…而且/更是」并列套话"),
    (r"在这个[^。！？\n]{0,12}(时代|背景下)", "「在这个…的时代」开场套话"),
    (r"值得[^。，\n]{0,4}(深思|思考)", "「值得深思」空话"),
    (r"让我们[^。！？\n]{0,6}(一起|来看|看看)", "「让我们…」讲稿腔"),
)
_TRANS_PATTERNS = (
    (r"进行[^。，\n]{0,6}(操作|处理|优化|管理)", "「进行…的操作」翻译腔"),
    (r"通过[^。，\n]{0,10}的?方式", "「通过…的方式」翻译腔"),
    (r"对于[^。，\n]{0,10}(而言|来说)", "「对于…而言」翻译腔"),
    (r"被(广泛)?认为", "无主语的「被认为」"),
    (r"一个由[^。，\n]{4,30}(构成|组成)", "长定语套嵌（「一个由…构成的…」）"),
)
_COLLOQ_MARKS = ("说白了", "其实", "不得不说", "老实说", "讲真", "你有没有发现", "简直")
# 源文比喻性造词（2026-11-19 用户裁决：去造词）——直接搬用即命中，换中性术语
_KB_METAPHOR_TERMS = ("承重动作", "判死刑", "死法", "死在", "赘生物")


def _language_style_violations(md: str) -> list[str]:
    """专业语言机检（2026-11-18 用户裁决：AI味/翻译腔/硬造词/口水）——正则兜底，
    命中走 article_kb_polish 定向重写；硬造词无法机检，交给语言审校（LLM）。"""
    out: list[str] = []
    cliche = [w for w in _AI_CLICHE if w in md]
    if len(cliche) >= 2 or any(md.count(w) >= 2 for w in cliche):
        out.append(f"AI 套话检出：{'、'.join(cliche[:4])}——改为具体动作或对象描述")
    for pat, label in _AI_PHRASES:
        hits = re.findall(pat, md)
        if hits:
            out.append(f"{label}出现 {len(hits)} 处，删除并直陈")
    trans = [label for pat, label in _TRANS_PATTERNS if re.search(pat, md)]
    if trans:
        out.append(f"翻译腔/机翻痕迹：{'；'.join(trans[:3])}——拆成主谓直陈句")
    coll = [w for w in _COLLOQ_MARKS if w in md]
    if len(coll) >= 2 or any(md.count(w) >= 3 for w in coll):
        out.append(f"口水词检出：{'、'.join(coll[:4])}——删掉，改写成有信息量的陈述")
    metaphors = [w for w in _KB_METAPHOR_TERMS if w in md]
    if metaphors:
        out.append(f"源文比喻词直接搬用：{'、'.join(metaphors)}——换成中性术语"
                   "（关键动作/验收判据/失败原因/冗余流程）")
    # 负向模式机检（2026-11-20 引入 jiji262 清单里可正则判定的部分；语义类交给审校）
    cr = re.findall(r"(?:答案|原因|结论|关键|问题|秘密)(?:很)?(?:简单|只有一个|就一个)[：:]", md)
    if cr:
        out.append("「答案很简单：」式冒号揭底——直接给结论，不铺悬念")
    sal = re.findall(r"最(?:关键|重要|值钱|核心)的(?:是|动作|一点)|真正(?:重要|关键)的(?:是|一点)", md)
    if len(sal) >= 2:
        out.append(f"「最关键/真正重要的是」式宣告出现 {len(sal)} 次——删掉宣告，直接说事")
    staged = re.findall(r"(?m)^(?:下面|接下来|我们先|先看|来看|说说)[^。\n]{0,10}", md)
    if len(staged) >= 2:
        out.append(f"预告下文/主持人式过场 {len(staged)} 处——删掉，直接进内容")
    for sent in re.split(r"[。！？\n]", md):
        hedges = len(re.findall(r"可能|或许|也许|大概|某种程度上|在某种意义上|一定程度上", sent))
        if hedges >= 3:
            out.append(f"同句叠限定 {hedges} 层（「{sent.strip()[:18]}…」）——删到一层或直接下判断")
            break
    agency = re.findall(r"(?:数据|指标|报表)[^。，\n]{0,6}(?:会说谎|说谎|会思考|自己决定|替你做决定)", md)
    if agency:
        out.append("把数据/指标写成会说话会决定的行动者——改回「人用它做了什么」")
    if "中小厂" in md:
        out.append("行业口语缩写「中小厂」——改「中小企业」")
    for p in md.split("\n\n"):
        pct = re.findall(r"\d+(?:\.\d+)?%", p)
        if len(pct) >= 4:
            out.append(f"单段堆了 {len(pct)} 个百分比统计——统计数字最多三个，其余改成机制描述")
            break
    return out


def _para_key(p: str) -> str:
    return re.sub(r"\s+", "", p or "")


def _is_prose(p: str) -> bool:
    """长正文段（≥40 字、非标题/表格/图片/列表）——只有这类重复才算结构缺陷。"""
    k = _para_key(p)
    return len(k) >= 40 and not k.startswith(("#", "|", "!", "-", "*"))


def _structure_violations(md: str) -> list[str]:
    """结构机检（2026-11-18 #56 实测缺陷）：同一长段原文重复出现——模型把收尾的
    「检验清单」同时写在开头，读起来像文章从结尾开始。命中走定向重写，兜底靠
    dedupe_paragraphs 的确定性去重。"""
    paras = [p.strip() for p in md.split("\n\n") if p.strip()]
    seen: dict[str, int] = {}
    out: list[str] = []
    for i, p in enumerate(paras, 1):
        if not _is_prose(p):
            continue
        k = _para_key(p)
        if k in seen:
            out.append(f"第 {seen[k]} 段与第 {i} 段完全重复——同一段只说一次"
                       f"（若是检验清单，只留在结尾）：「{p.strip()[:24]}…」")
        else:
            seen[k] = i
    return out[:3]


def dedupe_paragraphs(md: str) -> str:
    """确定性去重兜底：删掉完全重复的长段。若同一段同时出现在开头与结尾
    （前两段正文 ↔ 后两段正文）= 收尾的检验清单被误写到开头，保留结尾那份；
    其余重复保留首份、删后面。"""
    paras = md.split("\n\n")
    keys = [_para_key(p) for p in paras]
    prose = [i for i, p in enumerate(paras) if _is_prose(p)]
    first_p = prose[0] if prose else -1
    last_p = prose[-1] if prose else -1
    drop: set[int] = set()
    for i, p in enumerate(paras):
        if i in drop or not _is_prose(p):
            continue
        dup = [j for j in range(i + 1, len(paras)) if keys[j] == keys[i] and j not in drop]
        if not dup:
            continue
        if i == first_p and last_p in dup:
            # 首段与末段完全相同 = 收尾的检验清单被误写到开头，保留结尾那份
            drop.add(i)
            drop.update(j for j in dup if j != last_p)
        else:
            drop.update(dup)
    return "\n\n".join(p for i, p in enumerate(paras) if i not in drop)


def kb_style_violations(md: str, anchor_texts: list[str]) -> list[str]:
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
    for at in anchor_texts:
        m = difflib.SequenceMatcher(None, md, at).get_matching_blocks()
        longest = max(m, key=lambda b: b.size) if m else None
        if longest is not None and longest.size >= 40:
            out.append(f"正文与范本存在 {longest.size} 字连续重复（「{md[longest.a:longest.a + 30]}…」），必须改为原创表达")
    out.extend(_language_style_violations(md))
    return out


