#!/usr/bin/env python3
"""内容质量评审器（X11）：LLM 评审员按评分细则打分 + 违禁词程序化硬检查。

被 run_p1.py 引用；纯标准库（urllib），独立于应用进程。
密钥从仓库根 .env 读取（ZHIPU_API_KEY / ZHIPU_BASE_URL / LLM_MODEL）。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 违禁词（合规红线）：命中即 FAIL，不进 LLM 评审
# 注意避开合法用词（"第一步/第一条产线"不含"行业第一"这类绝对化模式）。
# 裸「最好的」已移除（2026-10-02 P1 实测：误杀率过高——「千万别挑最好的那条」是反向建议、
# 「最好的线，设备新」是描述性比较，都不是绝对化承诺；语义级超级由 LLM 评审 compliance 项
# 兜底（绝对化承诺直接 0 分），程序化表只留模式明确的营销违规）。
# 否定语境窗口仍保留：其余模式命中时，前 4 字含否定词（"别说行业第一"）同样放行。
BANNED_PATTERNS = [
    "行业第一", "全网第一", "业界第一", "排名第一",
    "保证收益", "保证回本", "保证效果", "保证赚钱", "保证翻倍",
    "百分百", "稳赚", "包赚", "躺赚", "轻松月入", "闭眼入",
]

# 刻意 CTA 话术（自然收尾约束的程序化辅助信号，最终由评审员判定）
CTA_PATTERNS = [
    r"评论区[打回留发扣]", r"评论区?扣[「『]?[验关资1-9]", r"私信[我领发回复要]", r"关注我", r"扣[个1-9]", r"回复关键?词",
    r"加[我微].{0,2}信", r"领.{0,4}资料", r"收藏.{0,6}转发",
]

_ARTICLE_RUBRIC = """你是苛刻的公众号深度长文内容评审。这类文章的定位=专业书面深度分析
（不是口播稿，不要用"口语化/3秒钩子"的短视频标准量它）。按六项评分（每项 0-10 分）：

1. opener：开头是否有客观铺垫并较快亮出核心判断（不要求 3 秒钩子，但禁止空话开场）
2. depth：拆解深度——有机制/分层/边界，论证推进清晰，不是观点堆砌
3. proof：论证具体性——有具体案例/数字/交付物/可对账标准，不空谈
4. ending：结尾完整自然收束——观点讲完即止，**无刻意转化话术**（"评论区打X/私信领/关注我"等直接扣 4 分起）
5. compliance：合规——无绝对化承诺与夸大（"最好/第一/保证收益/百分百"等直接 0 分）
6. language：语言质量——中文为主、书面但不 AI 腔（无"首先其次最后/赋能/抓手/闭环"），中英不混排成句

只输出 JSON（不要解释、不要代码块标记）：
{"scores": {"opener": n, "depth": n, "proof": n, "ending": n, "compliance": n, "language": n},
 "overall": n, "worst_problem": "一句话"}

overall = 六项平均。内容："""

_FEED_ARTICLE_RUBRIC = """你是苛刻的公众号推荐流内容评审。这类文章的定位=中小企业老板/业务负责人
在手机上扫读的公众号文章（1200~1800 字，完读率优先；不是学术长文，也不要用 3 秒短视频钩子的标准量它）。
按六项评分（每项 0-10 分）：

1. opener：开头即答案——前 3 段内给出全文最重的结论或最反直觉的数字；铺垫式开场
   （"随着…/近年来…/在…的大背景下"）直接 0~2 分
2. proof：数字与证据密度——有具体数字/金额/案例/出处，不空谈；全文干货密度对得起 2 分钟阅读时长
3. scannable：可扫读性——小节标题是判断句/利益句（不是「XX 的机制拆解」式名词标题）、
   段落短有锚点，手机上一路扫得下去
4. judgment：判断直给——结论明确、可执行，不和稀泥、不堆免责声明
5. compliance：合规——无绝对化承诺与夸大（"最好/第一/保证收益/百分百"等直接 0 分）；
   无刻意转化话术（"评论区打X/私信领/关注我"等直接扣 4 分起）
6. voice：语言人味——像资深行业编辑写给老板的话，无机翻腔、无 AI 套话、无论文腔

只输出 JSON（不要解释、不要代码块标记）：
{"scores": {"opener": n, "proof": n, "scannable": n, "judgment": n, "compliance": n, "voice": n},
 "overall": n, "worst_problem": "一句话"}

overall = 六项平均。内容："""

_RUBRIC = """你是苛刻的短视频口播/公众号长文内容评审。对下面的内容按六项评分（每项 0-10 分）：

1. hook：开头 3 秒是否点名受众+痛点，有让人停下来的力道
2. colloquial：口语化程度——说人话、短句，无"首先其次最后"类书面连接词
3. proof：论证具体性——有具体案例/数字/交付物，不空谈
4. ending：结尾自然收尾——观点讲完即止，**无刻意转化话术**（"评论区打X/私信领/关注我/加微信"等引导直接扣 4 分起）
5. compliance：合规——无绝对化承诺与夸大（"最好/第一/保证收益/百分百"等直接 0 分）
6. persona：人设一致性——证明环节落到"可验收交付"视角（验收单/可量化结果），像真正做过交付的人在讲话

只输出 JSON（不要解释、不要代码块标记）：
{"scores": {"hook": n, "colloquial": n, "proof": n, "ending": n, "compliance": n, "persona": n},
 "overall": n, "worst_problem": "一句话"}

overall = 六项平均。内容："""


def _env() -> dict:
    conf = {}
    path = os.path.join(_REPO, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    return conf


def banned_words(text: str) -> list[str]:
    """程序化违禁词检查，返回命中的词。

    否定语境不算命中：「千万别挑最好的那条」是反向建议，不是绝对化承诺（2026-10-02 P1
    实测误杀：三版脚本与长文小节标题都以「别挑/别选最好的」的正当修辞被拦）——往前看
    4 字内含否定词即放行。与 backend/app/articles/service.py 的守卫同表同逻辑，改一处
    必须同步另一处。"""
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


def cta_hits(text: str) -> list[str]:
    return [m.group(0) for p in CTA_PATTERNS for m in [re.search(p, text)] if m]


def judge(text: str, *, min_per_item: int = 6, min_overall: float = 7.0,
          timeout: int = 300, kind: str = "script") -> tuple[bool, str]:
    """LLM 评审。返回 (通过, 说明)。违禁词命中直接 FAIL 不调模型。

    kind="script" 用口播尺（hook/口语化/结尾…）；kind="article" 用深度长文尺
    （铺垫/深度/收束/语言…）；kind="feed" 用公众号推荐流尺（开头即答案/可扫读/人味…，
    2026-10-02 双档位默认档）。长文超 6000 字时喂开头+结尾采样——只喂前 6000 字会让
    评审员看到被拦腰砍断的文本，误报"句中截断无收尾"（2026-09-28 实测踩到）。
    """
    if len(text.strip()) < 50:
        return False, "内容过短（<50 字），不构成可评审内容"
    hits = banned_words(text)
    if hits:
        return False, f"违禁词命中：{hits}"
    rubric = {"article": _ARTICLE_RUBRIC, "feed": _FEED_ARTICLE_RUBRIC}.get(kind, _RUBRIC)
    if kind in ("article", "feed") and len(text) > 6000:
        # 只喂前 6000 字会让评审员看到被拦腰砍断的文本，误报"句中截断无收尾"——喂开头+结尾
        text = text[:4200] + "\n\n……（中略）……\n\n" + text[-1800:]
    else:
        text = text[:6000]

    conf = _env()
    key = conf.get("ZHIPU_API_KEY") or os.environ.get("ZHIPU_API_KEY", "")
    base = (conf.get("ZHIPU_BASE_URL")
            or os.environ.get("ZHIPU_BASE_URL")
            or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    model = conf.get("LLM_MODEL") or os.environ.get("LLM_MODEL", "glm-5.3-flash")
    if not key:
        return False, "未配置 ZHIPU_API_KEY（.env），无法评审"

    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": rubric},
                     {"role": "user", "content": text}],
        "temperature": 0.2,
        # 思考型模型：评审 JSON 输出小，但推理链要留足
        "max_tokens": int(os.environ.get("CS_JUDGE_MAX_TOKENS", 4000)),
    }).encode("utf-8")

    last_err = ""
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                f"{base}/chat/completions", data=body,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if not content:
                last_err = "模型返回空正文（推理耗尽预算？）"
                continue
            m = re.search(r"\{.*\}", content, re.S)
            if not m:
                last_err = f"评审输出无 JSON：{content[:80]}"
                continue
            scores = json.loads(m.group(0))
            sc = scores.get("scores") or {}
            # 六项键名随尺子走：口播尺 vs 深度长文尺 vs 推荐流尺（写死一套键名会把另一套全部判 0）
            expected = ({"article": ("opener", "depth", "proof", "ending", "compliance", "language"),
                         "feed": ("opener", "proof", "scannable", "judgment", "compliance", "voice")}
                        .get(kind, ("hook", "colloquial", "proof", "ending", "compliance", "persona")))
            items = {k: float(sc.get(k, 0)) for k in expected}
            bad = [f"{k}={v}" for k, v in items.items() if v < min_per_item]
            overall = float(scores.get("overall", sum(items.values()) / 6))
            note = f"overall={overall} worst={scores.get('worst_problem', '')[:60]}"
            if bad:
                return False, f"单项低于{min_per_item}：{bad}；{note}"
            if overall < min_overall:
                return False, f"总评 {overall} < {min_overall}；{note}"
            return True, note
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            time.sleep(3)
    return False, f"评审调用失败（重试后）：{last_err}"


if __name__ == "__main__":
    demo = "做制造的老板，这件事很多人被坑了几十万才搞明白……"
    ok, note = judge(demo)
    print(ok, note)
