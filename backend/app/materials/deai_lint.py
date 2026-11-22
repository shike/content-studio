"""去AI味机检（2026-11-22 太上老君框架落地 P1-4）：确定性规则检查，三线生成收尾强制跑。

设计原则（与热评预筛、读稿闸同一哲学）：
- 只报语言事实（假对比/空开场/旁白腔/填充词密度…），**不判内容好坏、不拦发布**——
  命中落 payload.issues 给人工终审参考；
- 引用豁免：被「」『』""包裹的原文整段抽掉再扫——公众号引源文原话、口播还原对话是合法命中，
  不能因引用里恰好有「不是…而是」就报；
- 白名单豁免：WHITELIST 放确认过的误报片段，新增前先确认它确实是纠正可核实事实
  或引用原话（太上老君 ai_tell_linter 的 allow.txt 惯例：禁"为了凑允许"而加）。

检查规则全部来自太上老君去AI味规则库（假对比第一优先/口播十条/长文七条），
按本系统三线定阈值（假设值，跑一段时间按 issues 分布再调）。
"""
from __future__ import annotations

import re

# ── 豁免 ──
# 误报白名单：片段在扫描前被整段移除。示例场景：审校修订说明里引用的「不是…而是…」原话。
WHITELIST: tuple[str, ...] = ()

# 引用块（中文直角引号+弯引号）：抽掉后再扫
_QUOTE_RE = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”")

# ── 假对比（第一优先，默认全禁；唯一豁免=纠正可核实事实，机器分不出来，一律报给终审） ──
_FAKE_CONTRAST = (
    re.compile(r"不是[^。，；！？]{1,16}[，,][^。]{0,3}而是"),
    re.compile(r"不是[^。，；！？]{1,16}[，,]是"),
    re.compile(r"并非[^。，；！？]{1,16}[，,]?而是"),
    re.compile(r"你以为[^。，！？]{1,20}其实"),
    re.compile(r"不只是[^。，！？]{1,16}[，,]?更"),
    re.compile(r"不仅[^。，！？]{1,12}[，,]?还(?!有)"),
    re.compile(r"不在于[^。，！？]{1,16}[，,]?而在于"),
    re.compile(r"只是[^。，！？]{1,10}[，,]?[^。！？]{0,6}才是"),
)

# ── 空开场（只看开头） ──
_EMPTY_OPENING = (
    re.compile(r"^大家好"),
    re.compile(r"^随着"),
    re.compile(r"^在当今"),
    re.compile(r"^在这个[^。！？]{0,10}时代"),
    re.compile(r"^今天想跟大家聊"),
    re.compile(r"^今天我们来聊聊"),
    re.compile(r"^在数字化"),
)

# ── 旁白腔/伪互动 ──
_NARRATOR = (
    re.compile(r"聊到这[里儿][^。！？]{0,10}(肯定|可能|估计)有人"),
    re.compile(r"接下来我分[三几两][个点]"),
    re.compile(r"让我们一起"),
    re.compile(r"看到?这里[^。！？]{0,6}(你可能会|肯定)"),
)

# ── 伪深度路标 / 大词商务腔 ──
_DEEP_ROADMAP = ("本质上", "归根结底", "真正的核心是", "这背后反映的是")
_BUZZWORDS = ("赋能", "抓手", "全方位", "底层逻辑", "闭环")

# ── 填充词密度（阈值按线：口播 240~320 字 / 小红书 300~800 / 公众号 1500~2500） ──
_FILLERS = ("然后", "其实", "我觉得", "相当于", "就是说")
_FILLER_LIMIT = {"script": 4, "xhs": 6, "wechat": 10}
_SENT_LIMIT = {"script": None, "xhs": 40, "wechat": 60}  # 口播读稿闸已有句长口径，不重复

# ── 口播自嗨开场（第一句主语=创作者自己） ──
_SELF_OPENING = re.compile(r"^我(做|想|最近|发现|干|带过|今天)|^今天想跟大家")


def _strip_quotes(text: str) -> str:
    for pat in WHITELIST:
        text = text.replace(pat, "")
    return _QUOTE_RE.sub("", text)


def _opening_text(text: str) -> str:
    """开头判定：跳过 markdown 标题行与空行，取前两行内容拼起来。"""
    lines = [ln.strip().lstrip("#").strip() for ln in text.splitlines()]
    body = [ln for ln in lines if ln]
    return "".join(body[:2])[:80]


def lint(text: str, kind: str) -> list[str]:
    """按线机检。kind: "script"|"xhs"|"wechat"。返回 issues（≤8 条，每条 ≤60 字），无命中=空。"""
    text = text or ""
    t = _strip_quotes(text)
    issues: list[str] = []

    n = sum(len(p.findall(t)) for p in _FAKE_CONTRAST)
    if n:
        issues.append(f"有 {n} 处「不是A而是B」式假对比（默认全禁：删掉垫高的半句直接说）")

    opening = _opening_text(text)
    opening_stripped = _opening_text(t)
    for p in _EMPTY_OPENING:
        if p.search(opening_stripped):
            issues.append(f"空开场「{p.search(opening_stripped).group(0)[:14]}…」：删掉，第一句直接进事")
            break
    if kind == "script" and _SELF_OPENING.search(opening_stripped):
        issues.append("第一句在讲创作者自己（自嗨开场）：第一句主语换成观众能得到什么/正在遇到什么")

    for p in _NARRATOR:
        m = p.search(t)
        if m:
            issues.append(f"旁白腔「{m.group(0)[:18]}」：念稿不是说话，改成直接的口语转折/提问")
            break

    deep = sum(t.count(w) for w in _DEEP_ROADMAP)
    if deep:
        issues.append(f"{deep} 处伪深度路标（本质上/归根结底类）：删掉路标，看剩下的话还有没有信息")
    buzz = [w for w in _BUZZWORDS if w in t]
    if buzz:
        issues.append(f"商务腔大词：{'、'.join(buzz[:4])}——换成观众手机里真有的具体名字和动作")

    limit = _FILLER_LIMIT.get(kind)
    if limit:
        fill = sum(t.count(w) for w in _FILLERS)
        if fill > limit:
            issues.append(f"口语填充词 {fill} 次（然后/其实/我觉得类，>{limit} 警戒）：删到只剩必需的")

    sent_limit = _SENT_LIMIT.get(kind)
    if sent_limit:
        sents = [x.strip() for x in re.split(r"[。！？]", t) if x.strip()]
        longest = max((len(x) for x in sents), default=0)
        if longest > sent_limit:
            issues.append(f"最长句 {longest} 字（>{sent_limit}）：拆短，一口气念得下来才留")

    if kind == "wechat" and text.count("——") > 5:
        issues.append(f"破折号 {text.count('——')} 个（>5 基本是 AI 节奏）：改句号或逗号")

    return issues[:8]
