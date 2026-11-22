"""改编器（2026-11-11 源文改编产线）：每日从内容清单取一项，把源文改编成我们号的文章。

与挖坑线/深读线的本质区别：不再凭空综合——清单项就是一篇被评估过的高质量源文，
改编 = 保源文骨架与逐字原话（≤3 处标注引用），换我们号的判断口径与读者钩子。
出口闸门：原创度（与源文重复率 ≤30%，超线定向重写一次仍超=当日空过退回清单）；
违禁词机检沿用 service 的正则与定向重写。当日账落 daily_ledger。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..db import engine
from ..playbook import playbook_block
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import Content, Material, PipelineLedger
from ..prompts import load, render
from .wechat import (
    _article_generate_fail,
    _attach_charts,
    _attach_cover,
    _content_signature,
    _finalize_article,
    _kb_style_violations,
)
from ..articles.fingerprint import (
    fingerprint_brief,
    fingerprint_violations,
    recent_fingerprints,
)
from ..articles.quality import (
    _KB_METAPHOR_TERMS,
    _structure_violations,
    dedupe_paragraphs,
    min_chars_or_raise,
    strip_fence as _strip_fence,
)

_SRC_CAP = 100000     # 源文全文上限（2026-11-18：3 万字腰斩，长手册改编只读到半篇）
_DUP_LINE = 30.0      # 原创度线：与源文重复率百分比上限
_MIN_CHARS = 1500     # 正文下限（规格 1500~2500，2026-11-19 用户定版）
_MAX_CHARS = 2500     # 正文上限（超线定向压缩一次；公众号正文长度限制）

_PUNCT = re.compile(r"[\s，。！？；：、“”‘’「」『』（）()【】\[\]—…·.,;:!?\"'<>《》]")

# 功能型小标题（「## 开头」「## 检验动作」）：模型爱照写，审校常改不动——确定性删除。
_STRUCT_HEAD_RE = re.compile(
    r"(?m)^#{1,3}[ \t]*(开头|结尾|引言|正文|结语|总结|小结|结论|尾声|后记|前言|写在最后|检验动作)[ \t]*$")


def _strip_struct_heads(md: str) -> str:
    out = _STRUCT_HEAD_RE.sub("", md or "")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _norm(t: str) -> str:
    return _PUNCT.sub("", t or "")


def _dup_ratio(adapted: str, source: str) -> tuple[float, list[str]]:
    """原创度检测：源文中 ≥14 字的实质句被原样搬进改编稿的字符占比，及雷同句清单。"""
    a = _norm(adapted)
    total = hit = 0
    hits: list[str] = []
    for sent in re.split(r"[。！？；\n]", source or ""):
        s = _norm(sent)
        if len(s) < 14:
            continue
        total += len(s)
        if s in a:
            hit += len(s)
            hits.append(sent.strip()[:60])
    return (round(hit / total * 100, 1) if total else 0.0), hits[:8]


_CHAPTER_RE = re.compile(r"^#{1,3}\s*第[一二三四五六七八九十百]+章\s*[　:：]?\s*(.{2,40})$", re.M)


def _chapter_terms(source: str) -> list[str]:
    """源文章名核心词（「## 第五章　侦察：…」→「侦察」）。非分章文档返回空。"""
    terms: list[str] = []
    for m in _CHAPTER_RE.finditer(source or ""):
        t = (m.group(1) or "").strip()
        key = re.split(r"[：:，,、\s（(]", t)[0][:8]
        if len(key) >= 2:
            terms.append(key)
    return list(dict.fromkeys(terms))


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_num(s: str) -> int:
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + _CN_NUM.get(s[1:], 0)
    if "十" in s:
        a, _, b = s.partition("十")
        return _CN_NUM.get(a, 1) * 10 + (_CN_NUM.get(b, 0) if b else 0)
    return _CN_NUM.get(s, 0)


def _listed_in_sentence(md: str, n: int) -> bool:
    """一句带冒号的并列清单是否列满 n 项（「七问清单按固定顺序：A、B、C…」）。"""
    for sent in re.split(r"[。！？\n]", md):
        if "：" not in sent and ":" not in sent:
            continue
        parts = [p for p in re.split(r"[、，,]", sent) if len(p.strip()) >= 2]
        if len(parts) >= n and all(len(p.strip()) <= 40 for p in parts):
            return True
    return False


def _quantity_gaps(md: str) -> list[str]:
    """声称的数量与正文实际展开数不符（「七个问题」只写四问）——提示词约束不住，代码兜底。
    两种算展开：编号（第一问…）或一句带冒号的并列清单（≥N 项）。"""
    claims = {_cn_num(m.group(1)) for m in
              re.finditer(r"([一二三四五六七八九十]{1,2}|\d{1,2})\s*(?:个)?问题", md)}
    claims = {n for n in claims if 3 <= n <= 20}
    if not claims:
        return []
    nums = {m.group(1) for m in re.finditer(r"第([一二三四五六七八九十]{1,2})问", md)}
    out: list[str] = []
    for n in sorted(claims):
        if len(nums) >= n or _listed_in_sentence(md, n):
            continue
        shown = "、".join(f"第{x}问" for x in sorted(nums, key=_cn_num)) or "无编号"
        out.append(f"声称 {n} 个问题，正文只展开 {len(nums)} 个（{shown}）"
                   f"——补齐其余问题，或把数量改成实际展开数")
    return out


def _enumeration_gaps(md: str, terms: list[str]) -> list[str]:
    """行内同时出现 ≥3 个章名 = 文章在列举阶段；列举过的章必须在正文再次出现（≥2 次）。
    返回只出现在列举行、没有后续内容的章名（2026-11-19「五阶段只写三段」事故）。"""
    if len(terms) < 3:
        return []
    for line in (md or "").splitlines():
        present = [t for t in terms if t in line]
        if len(present) >= 3:
            gaps = [t for t in present if (md or "").count(t) <= 1]
            return gaps
    return []


_KHAZIX_BAN = ("说白了", "本质上", "换句话说", "这意味着", "意味着什么",
               "不可否认", "综上所述", "值得注意的是", "不难发现")


def _khazix_violations(md: str) -> list[str]:
    """卡兹克文风机检（2026-11-20 用户确认方向）：小标题/列表/加粗/冒号/破折号/双引号/顾问口吻。"""
    out: list[str] = []
    # 引出原话的冒号合规（写作规则里明确允许：「他说：『……』」），先抹掉再查
    t = re.sub(r"[：:]\s*「", "「", md or "")
    t = re.sub(r"「[^」]*」", "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\d+\s*[:：]\s*\d+", "", t)
    if "：" in t or ":" in t:
        out.append("冒号，改逗号（只有引原话时可以留）")
    if "——" in md:
        out.append("破折号，改逗号或句号")
    if "“" in md or "”" in md:
        out.append("双引号，改「」")
    if re.search(r"(?m)^#{2,3}\s", md):
        out.append("小标题，去掉，用口语转场")
    if re.search(r"(?m)^\s*[-*]\s+", md) or re.search(r"(?m)^\s*\d+[.、]\s+", md):
        out.append("列表，改成一段话")
    if "**" in md:
        out.append("加粗，去掉")
    for w in _KHAZIX_BAN:
        if w in md:
            out.append(f"禁用词「{w}」，换口语说法")
    if re.search(r"首先[^。！？\n]{0,40}其次", md):
        out.append("「首先…其次」，用自然转场")
    if "我的判断是" in md:
        out.append("「我的判断是」是顾问口吻，改「我一直觉得/我自己的感受是」")
    if re.search(r"这篇文章|这个故事|本文", t):
        out.append("元评论（这篇文章/这个故事/本文），删掉，直接讲事")
    if re.search(r"(?m)^(先说|再说|另一方面|说到这里|接下来|回到那件事|回到这件事)", t):
        out.append("段落路标（先说/再说/接下来/回到那件事），用事情本身接上")
    if re.search(r"这件事我想|这里只挑|这句话是关键|说的具体一点|问题看起来[^。！？\n]{0,12}不够|"
                 r"真问题找到之后", t):
        out.append("解释结构的元叙述，删掉，直接讲下一件事")
    if re.search(r"我心里|我当时也没想到|让我印象|我心里有数|我心里一沉", t):
        out.append("素材没写的心理活动，删掉")
    return out


# 标题禁区（2026-11-20 用户定版）：公众号标题不是内部纪要。
# 读者一遍要懂：人 + 老办法 + AI 拿已有的东西 + 结果；行业词/判断词进标题就会硬。
_TITLE_JARGON = ("AI落地", "需求预测", "配送量", "口径", "交付", "闭环", "赋能",
                 "抓手", "底层逻辑", "真问题", "最值钱", "最难的", "核心是", "本质是")


def _title_bad(title: str) -> str:
    """标题不合规原因（空串=合规）。写稿与重拟共用同一把尺。"""
    t = (title or "").strip()
    if len(t) > 24:
        return f"{len(t)} 字超 24"
    if "：" in t or "——" in t or "“" in t or "”" in t:
        return "冒号/破折号/双引号"
    if any(w in t for w in _KB_METAPHOR_TERMS):
        return "源文比喻词"
    if any(w in t for w in _TITLE_JARGON):
        return "行业词/判断词"
    if not re.search(r"AI|FDE", t, re.I):
        return "缺 AI/FDE"
    return ""


def _title_trim(t: str) -> str:
    """超 24 字的标题截成合规短句（选题会标题/重拟产物共用）。
    标点分句后优先保留含 AI/FDE 的分句（标题公式后半句才是 AI+动作+结果），
    整体放下才拼回去；都不行再按标点截断。"""
    t = (t or "").strip()
    if len(t) <= 24:
        return t
    clauses = [x.strip() for x in re.split(r"(?<=[，；])", t) if x.strip("，；")]
    ai_clauses = [c for c in clauses if re.search(r"AI|FDE", c, re.I) and len(c) <= 24]
    if ai_clauses:
        keep = ai_clauses[-1]
        rest = [c for c in clauses if c != keep]
        for r in rest:  # 尽量把另一分句拼回去
            cand = f"{r}，{keep}" if clauses.index(r) < clauses.index(keep) else f"{keep}，{r}"
            if len(cand) <= 24:
                return cand
        return keep
    cut = max(t.rfind("，", 0, 25), t.rfind("；", 0, 25))
    return t[:cut] if cut >= 10 else t[:24]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@register_executor("article_adapt", on_fail=_article_generate_fail)
async def article_adapt(ctx: JobContext, payload: dict) -> dict:
    """每日改编：清单取项（CAS）→ 拉源文全文 → 保骨架保原话换口径成文 → 原创度闸 → 机检 → 排版。

    payload.item_id 存在时改编指定清单项（手动生成直接执行），否则取队首（每日 05:00 调度）。"""
    from ..tenant_brand import brand_of
    from ..articles.ima_kb import fetch_full_doc, kb_config

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("改编器未启用：缺少 ima_kb_enabled / 凭证配置")
        # 卡死自愈：改编中超 2 小时无进展的项退回待改编（job 被杀/超时的残留）
        stale_cut = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        for it in s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
                Material.stage == "running")).all():  # type: ignore[attr-defined]
            if it.updated_at and it.updated_at < stale_cut:
                it.stage = "pending"
                s.add(it)
        s.commit()
        target_id = int(payload.get("item_id") or 0)  # 2026-11-18：手动生成直接改编指定项（知识库「生成内容」入口）
        if target_id:
            item = s.get(Material, target_id)
            if item is None or item.origin_type != "kb_doc":  # type: ignore[attr-defined]
                raise RuntimeError(f"改编目标不存在（#{target_id}）")
            if (item.stage or "") != "pending":
                raise RuntimeError(f"清单项 #{target_id} 状态为 {item.stage}，不重复改编")
        else:
            item = s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
                Material.stage == "pending")  # type: ignore[attr-defined]
                .order_by(Material.quality.op('->')('priority').desc().nulls_last(),
                          Material.quality.op('->')('score').desc().nulls_last(),
                          Material.id)).first()  # type: ignore[attr-defined]
        if item is None:
            s.add(PipelineLedger(day=_today(), pipeline="wechat_adapt", ok=True,
                              produced="", reason="清单空：无待改编项（宁缺勿滥）"))
            s.commit()
            ctx.set_progress(100, "清单空，今日不出稿")
            return {"adapted": False, "message": "清单空"}
        item.stage = "running"  # CAS 占位（单 worker + 日级 dedup_key，无并发竞争）
        s.add(item)
        s.commit()
        s.refresh(item)
        item_id, mid, src_title = item.id, item.origin_id, item.title
        q0 = item.quality or {}
        angle = str(q0.get("adapt_angle") or "")
        quotes = [str(x) for x in (q0.get("best_quotes") or [])]
        score = float(q0.get("score") or 0)
        # 知识库文档默认是号主自有（2026-11-20 用户确认：全部都是我的）；单篇可显式置 false
        own = bool(q0.get("own_source", True))
        # 两步改编（2026-11-20 A 方案）：payload 带人选定的判断时，判断与证据从这里来，
        # 写稿只喂证据包，不再喂全书——防读书笔记，也防判断永远是源文的那一个。
        pitch = payload.get("pitch") if isinstance(payload.get("pitch"), dict) else None
        if pitch:
            thesis = str(pitch.get("thesis") or "").strip() or angle
            scene = str(pitch.get("scene") or "").strip()
            angle = f"现场：{scene}\n判断：{thesis}" if scene else thesis
            ev0 = [str(x).strip() for x in (pitch.get("evidence") or []) if str(x).strip()]
            if ev0:
                quotes = ev0
    client_id, api_key = cfg["client_id"], cfg["api_key"]

    def _release(reason: str, item_status: str = "pending", tag: str = "") -> None:
        """空过/未过收尾：清单项退回指定状态 + 当日账写原因。"""
        with Session(engine) as s:
            it = s.get(Material, item_id)
            if it is not None:
                it.stage = item_status
                it.status = "ignored" if item_status == "low" else "ready"
                if tag:
                    it.note = ((it.note or "") + f"｜{tag}").strip("｜")[:300]
                s.add(it)
            s.add(PipelineLedger(day=_today(), pipeline="wechat_adapt", ok=True,
                              produced="", reason=reason))
            s.commit()

    # ── 拉源文全文 → 建稿 → 成文 → 原创度闸 → 机检（瞬时失败统一退项走梯子）──
    try:
        try:
            source = await fetch_full_doc(client_id, api_key, mid, cap=_SRC_CAP)
        except Exception as e:  # noqa: BLE001
            raise TransientError(f"源文全文拉取失败（{type(e).__name__}），稍后自动重试")
        if not source.strip():
            _release(f"源文全文不可读（media 已失效），跳过清单项 #{item_id}")
            ctx.set_progress(100, "源文不可读，今日不出稿")
            return {"adapted": False, "message": "源文不可读"}

        # 建稿（复用同源遗留行，防梯子重试堆行）
        with Session(engine) as s:
            stale = [a for a in s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.kind == "long_article",  # type: ignore[attr-defined]
                Content.status.in_(["drafting", "failed"]))).all()  # type: ignore[attr-defined]
                if ((a.payload or {}).get("note") or "").startswith(f"改编：{src_title[:20]}")]
            from ..channels.engine import new_article_content

            if stale:
                art = stale[0]
                p0 = dict(art.payload or {})
                p0["article_state"] = "generating"
                art.payload = p0
                art.status = "drafting"
            else:
                art = new_article_content(
                    s, tenant_id=1, state="generating",
                    note=f"改编：{src_title[:40]}（源文评分 {score}）｜角度：{angle[:80]}")
            p = dict(art.payload or {})
            p["note"] = (f"改编：{src_title[:40]}（源文评分 {score}）｜角度：{angle[:80]}")
            art.payload = p
            s.add(art)
            s.commit()
            s.refresh(art)
            article_id = art.id

        # ── 改编成文（单次写入；标题随稿出，2026-11-19 收拢流水线）──
        persona_text = brand_of(1)["persona"]
        identity = (
            "这是号主自己的稿子：源文里的「我」就是号主。用第一人称写，直接写「我第二天到了现场」；"
            "不写「手册作者」「源文里写」「作者记录」——那是把自己的话转述成别人的。"
            if own else
            "源文作者的第一人称不是号主的经历：他的「我」写成第三人称；引用原话要写明「手册作者记录」。"
        )
        ctx.set_progress(30, "改编成文（保骨架保原话换口径）")
        write_source = source
        if pitch:
            ev = [str(x).strip() for x in (pitch.get("evidence") or []) if str(x).strip()]
            if ev:
                write_source = "\n\n".join(f"〔证据 {i}〕{x}" for i, x in enumerate(ev, 1))
                ctx.set_progress(30, f"改编成文（按选定判断，证据 {len(ev)} 段）")
        if pitch:
            q = [x for x in quotes if "「" in x][:3]
            quotes_block = "\n".join(f"- {x}" for x in q) or "（无，本次不引用原话）"
        else:
            quotes_block = "\n".join(f"- {q}" for q in quotes) or "（无，本次不引用原话）"
        title_hint = (str(pitch.get("title") or "").strip() if pitch else "") or "（无，自行拟）"
        hist = recent_fingerprints(3, exclude_id=article_id)
        body = await gateway.complete(
            [{"role": "user", "content": render("article_adapt_write",
                                                PERSONA=persona_text,
                                                IDENTITY=identity,
                                                SOURCE=write_source, ANGLE=angle,
                                                QUOTES=quotes_block, TITLE_HINT=title_hint,
                                                FINGERPRINT=fingerprint_brief(hist)) + playbook_block("article")}],
            max_tokens=20000, thinking="disabled")  # 2026-11-19：4 万字源文开思考链会无限推理，关掉后 83s 出稿
        body = _strip_fence(body)
        lines = body.split("\n")
        if not lines or not lines[0].lstrip().startswith("# "):
            raise TransientError("改编输出缺「# 标题」首行（疑似空正文或截断），稍后自动重试")
        title = lines[0].lstrip()[2:].strip() or src_title[:30]
        if pitch:  # 人选定的标题优先（2026-11-20 用户裁决：场景标题比论文式论点好）
            pt = _title_trim(str(pitch.get("title") or "").strip())
            if pt:
                title = pt
        md_text = _strip_struct_heads("\n".join(lines[1:]).strip())
        # 短稿扩写（2026-11-20 kz 实测：卡兹克文体每个证据只写一两句，稳定落在 1300~1400，
        # 贴着 1500 下限会反复触发瞬时失败）。只扩已有场景，不加事实；扩写后仍由审校兜底。
        if len(md_text) < 1500:
            ctx.set_progress(35, f"正文 {len(md_text)} 字偏短，扩写已有场景")
            try:
                expanded = _strip_fence(await gateway.complete(
                    [{"role": "user", "content": render(
                        "article_adapt_expand", SOURCE=write_source, DRAFT=md_text)}],
                    max_tokens=8192, thinking="disabled")).strip()
                if expanded.startswith("# "):
                    expanded = "\n".join(expanded.split("\n")[1:]).strip()
                if expanded and len(expanded) > len(md_text):
                    md_text = expanded
            except Exception as e:  # noqa: BLE001
                print(f"[adapt] 扩写失败（保留原稿）: {type(e).__name__}: {str(e)[:80]}")
        min_chars_or_raise(md_text, "改编稿")
        # 标题闸：3 次重拟，每次把上一稿的具体毛病喂回去；候选里挑合规的优先。
        # 2026-11-21 修：旧循环跑满次数后最后一次不合规也照样采用（#83 实测收了缺 AI/FDE 的标题）。
        title_candidates: list[tuple[str, str]] = [(title, _title_bad(title))]
        for _try in range(3):
            if title_candidates[-1][1] == "":
                break
            bad = title_candidates[-1][1]
            ctx.set_progress(32, f"标题不合规（{bad}），重拟第 {_try + 1} 次")
            td = await gateway.complete_json(
                [{"role": "user", "content":
                  "为这篇公众号长文重拟一个标题，≤24 字。像一个公众号标题，不是一个内部判断："
                  "前半句写一个人+一个具体动作或困境，后半句写 AI（或 FDE）+一个具体动作+一个结果或转折，"
                  "中间用逗号。例：店长靠经验订了十年货，AI用旧监控把浪费砍了一半"
                  "（例子只示范结构，不要照抄内容）。"
                  "必须出现 AI 或 FDE，但不许当口号前缀；标题里不放 AI落地/需求预测/配送量/口径/"
                  "交付/闭环/赋能/抓手/底层逻辑/真问题/最值钱/最难的；"
                  "不用冒号、破折号、双引号、问句、比喻造词；标题里的数量必须在正文有对应。"
                  + (f"\n上一稿标题「{title_candidates[-1][0]}」的毛病是「{bad}」，必须改掉这个毛病。"
                     if title_candidates[-1][0] else "") + "\n"
                  + md_text[:3000] + '\n输出 JSON：{"title": "..."}'}],
                max_tokens=512, thinking="disabled")
            nt = _title_trim(str((td or {}).get("title") or "").strip()[:40])
            if not nt:
                continue
            title_candidates.append((nt, _title_bad(nt)))
        title, bad = next(((t, b) for t, b in reversed(title_candidates) if b == ""),
                          title_candidates[-1])
        title_warning = bad  # 非空=三次重拟后仍不合规，终审要人工改标题

        # ── 原创度闸：与源文重复率 ≤30%，超线定向重写一次，仍超=当日空过退回清单 ──
        ctx.set_progress(55, "原创度检测（与源文比对）")
        ratio, hits = _dup_ratio(md_text, source)
        if not own and ratio > _DUP_LINE:  # 自有稿：重复自己的句子不是抄袭，禁止降重改写自己的话
            ctx.set_progress(62, f"重复率 {ratio}% 超线，定向降重写")
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_adapt_reduce",
                                                    HITS="\n".join(f"- {h}" for h in hits),
                                                    DRAFT=md_text, SOURCE=source[:_SRC_CAP])}],
                max_tokens=8192, thinking="disabled")
            polished = _strip_fence(polished)
            p_lines = polished.split("\n")
            if p_lines and p_lines[0].lstrip().startswith("# "):
                polished_md = "\n".join(p_lines[1:]).strip()
                # 签名守卫：重写不许动正文里的数字与结构（动了就弃用重写稿）
                if polished_md and _content_signature(polished_md) == _content_signature(md_text):
                    md_text = polished_md
                    ratio, hits = _dup_ratio(md_text, source)
        if not own and ratio > _DUP_LINE:
            _release(f"改编稿重复率 {ratio}% 超 {_DUP_LINE}% 线（重写一次仍超），退回清单 #{item_id}")
            with Session(engine) as s:
                art = s.get(Content, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = title
                    art.body = md_text
                    p = dict(art.payload or {})
                    p["article_state"] = "failed"
                    p["note"] = (p.get("note") or "") + f"｜原创度未过（重复率 {ratio}%），不进待发"
                    art.payload = p
                    s.add(art)
                    s.commit()
            ctx.set_progress(100, f"原创度未过（{ratio}%），本项退回清单")
            return {"adapted": False, "ratio": ratio, "message": "原创度未过"}

        # ── 长度闸（先压到位再送审，避免审完再压把审校白做）──
        async def _trim_once(text: str, round_no: int) -> str:
            ctx.set_progress(81, f"正文 {len(text)} 字超上限 {_MAX_CHARS}，压缩第 {round_no} 轮")
            out = _strip_fence(await gateway.complete(
                [{"role": "user", "content":
                 f"这篇公众号长文 {len(text)} 字，公众号正文上限 {_MAX_CHARS} 字。请做硬压缩：\n"
                 "保留：论证主线的每一块（每块只留最核心的判断与 1~2 个关键证据）、"
                 "开头的可检验主张、结尾的判断；正文里的原话引用原样保留。\n"
                 "删除：例子里的展开细节、重复解释、修饰句、背景铺垫、过渡句、枝节。\n"
                 "只删不增，不许新增内容或改写事实。目标 2000 字上下，不得超过 2500。\n"
                 "输出完整修订后全文（# 标题开头）：\n" + text}],
                max_tokens=8192, thinking="disabled"))
            t_lines = out.split("\n")
            t_md = ("\n".join(t_lines[1:]).strip()
                    if t_lines and t_lines[0].lstrip().startswith("# ") else "")
            if not t_md or len(t_md) >= len(text) or len(t_md) < _MIN_CHARS:
                return ""  # 没压下来（或压穿了下限）就别继续折腾
            return t_md

        for _round in range(2):
            if len(md_text) <= _MAX_CHARS:
                break
            t = await _trim_once(md_text, _round + 1)
            if not t:
                break
            md_text = t

        # ── 合并审校（2026-11-19 收拢：hard=事实/归属/整章缺失，soft=语言/表述建议）──
        from ..articles.review import review_adapt, revise_once

        anchor_texts = [x for x in load("article_kb_anchors").split("## 范本")[1:] if len(x) > 200]
        def _mech(text: str) -> list[str]:
            out = (_kb_style_violations(text, anchor_texts) + _structure_violations(text)
                   + fingerprint_violations(text, hist) + _quantity_gaps(text)
                   + _khazix_violations(text))
            if _STRUCT_HEAD_RE.search(text):
                out.append("小标题使用功能词（开头/结尾/引言/正文/结语/总结/检验动作），改成内容短语或删掉")
            if own and re.search(r"手册作者|源文", text):
                out.append("自己的稿子被写成了别人的（出现「手册作者/源文」）——直接用第一人称，删掉转述")
            elif re.search(r"(?:手册作者|源文|作者)的?(?:判断|观点|结论)是", text):
                out.append("判断归到了源文作者（「手册作者的判断是」）——用「我的判断是」给一次，其余直接陈述")
            judge_n = len(re.findall("我的判断是", text))
            if judge_n >= 2:
                out.append(f"判断句模板化：「我的判断是」出现 {judge_n} 次，只留一处，其余直接陈述")
            attr_n = len(re.findall("源文|手册作者", text))
            if attr_n >= 4:
                out.append(f"转述腔：对源文的归属声明 {attr_n} 处，转述事实直接陈述，只保留引用原话的说话人")
            paras = [p.strip() for p in text.split("\n\n")
                     if p.strip() and not p.startswith(("!", "#"))]
            if len(paras) >= 3:
                dup = set(re.findall(r"\d+(?:\.\d+)?", paras[-1])) & set(
                    re.findall(r"\d+(?:\.\d+)?", "\n\n".join(paras[:-1])))
                if len(dup) >= 2:
                    out.append(f"结尾复述前文数字（{'、'.join(sorted(dup)[:3])}）"
                               f"——结尾给动作或新判断，不复述")
            return out

        mech = _mech(md_text)
        chapter_terms = _chapter_terms(source)

        def _with_enum_gaps(hard_list: list[str], text: str) -> list[str]:
            gaps = _enumeration_gaps(text, chapter_terms)
            if gaps:
                return [f"列举了阶段（{'、'.join(gaps)}）但正文没有对应内容：每一环至少 2~4 句，"
                        f"篇幅不够就删掉列举，不许列了又缺"] + hard_list
            return hard_list

        ctx.set_progress(80, "合并审校（事实/归属/体裁/语言/口吻）")
        hard, soft = await review_adapt(md_text, source, own)
        hard = _with_enum_gaps(hard, md_text)
        # 卡兹克格式硬线（确定性机检，不靠模型判断）：冒号/破折号/双引号/小标题/列表/
        # 加粗/禁用词/元评论/路标——命中即拦，改不干净不进待发。
        hard = [f"格式：{v}" for v in _khazix_violations(md_text)] + hard
        if mech:
            soft = [f"机检：{v}" for v in mech[:6]] + soft
        for _round in range(2):  # 最多两轮修订：修订本身可能引入新问题，复审兜底
            if not hard:
                break
            ctx.set_progress(82, f"审校命中 hard {len(hard)}/soft {len(soft)}，定向修订第 {_round + 1} 轮")
            # 只按 hard 改：soft 是风格建议，混进修订会让模型整段重写，
            # kz3 实测第二轮为改 soft 补出「不铺系统」这种新事实，hard 反而 1→2。
            fix_list = list(hard)
            revised = await revise_once(md_text, fix_list, source, own)
            # 审校修订不再做数字签名守卫：事实类问题（编造数字/口径）本来就要动数字，
            # 签名一致才收等于禁止修正；改完由复审兜底。
            if revised and len(revised) >= 800:
                md_text = revised
            hard, soft = await review_adapt(md_text, source, own)
            hard = _with_enum_gaps(hard, md_text)
            hard = [f"格式：{v}" for v in _khazix_violations(md_text)] + hard
            mech2 = _mech(md_text)
            if mech2:
                soft = [f"机检：{v}" for v in mech2[:6]] + soft
        md_text = _strip_struct_heads(md_text)  # 修订可能把功能型小标题写回来，确定性兜底
        soft_left = soft
        if hard:
            with Session(engine) as s:
                it0 = s.get(Material, item_id)
                fails = (it0.note or "").count("审校未过") if it0 is not None else 0
            tag = f"审校未过×{fails + 1}"
            brief = "；".join(hard[:2])
            _release(f"审校二次未过（{brief}），清单项 #{item_id} 落 low 不再自动重试"
                     if fails >= 1 else f"审校未过（{brief}），退回清单 #{item_id}",
                     item_status="low" if fails >= 1 else "pending", tag=tag)
            with Session(engine) as s:
                art = s.get(Content, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = title
                    art.body = md_text
                    p = dict(art.payload or {})
                    p["article_state"] = "failed"
                    p["note"] = (p.get("note") or "") + f"｜审校未过（{brief[:60]}），不进待发"
                    art.payload = p
                    s.add(art)
                    s.commit()
            ctx.set_progress(100, f"审校未过（hard {len(hard)} 项），本篇不进待发")
            return {"adapted": False, "issues": len(hard), "message": "审校未过"}

        # ── 收尾长度兜底（修订可能加长；超线最多再压两轮，压完不再审，只删不增）──
        for _round in range(2):
            if len(md_text) <= _MAX_CHARS:
                break
            t = await _trim_once(md_text, _round + 1)
            if not t:
                break
            md_text = t

        # ── 结构兜底：完全重复的长段确定性去重（#56 实测：检验清单同时出现在开头与结尾）──
        deduped = dedupe_paragraphs(md_text)
        if deduped != md_text:
            md_text = deduped
            ctx.set_progress(85, "结构兜底：删除完全重复的段落")
    except TransientError:
        with Session(engine) as s:
            it = s.get(Material, item_id)
            if it is not None and it.stage == "running":
                it.stage = "pending"  # 梯子重试时同一清单项重新来过
                s.add(it)
                s.commit()
        raise

    # ── 图表与头图（失败不阻塞）──
    md_text = await _attach_charts(md_text, 1, "[adapt]")
    ctx.set_progress(90, "渲染头图")
    md_text = await _attach_cover(md_text, title, 1, "[adapt]")

    # ── 定稿排版 + 清单项收尾 + 当日账 ──
    _finalize_article(article_id, title=title,
                      kb_sources=[{"media_id": mid, "title": src_title}], md_text=md_text)
    from ..articles.renderer import render_wechat_html
    with Session(engine) as s:
        art = s.get(Content, article_id)
        if art is not None and (art.body or "").strip():
            p = dict(art.payload or {})
            p["html"] = render_wechat_html(art.body)
            p["article_state"] = "rendered"
            if title_warning:  # 三次重拟仍不合规的标题：落 note 给终审（人工改标题）
                p["note"] = (p.get("note") or "") + f"｜标题待人工改（机检：{title_warning}）"
            if soft_left:  # 语言/表述建议修订后仍有残留：落 note 给用户终审参考（不拦发布）
                p["note"] = (p.get("note") or "") + "｜语言建议残留：" + "；".join(
                    x[:44] for x in soft_left[:2])[:200]
            # 去AI味机检（2026-11-22 太上老君框架 P1-4）：公众号提示词不动，只接红字；
            # 引用原文（「」内）自动豁免，不误伤源文原话
            from ..materials.deai_lint import lint

            deai = lint(art.body, "wechat")
            if deai:
                p["issues"] = deai
            art.payload = p
            art.status = "ready"
            s.add(art)
            s.commit()
        it = s.get(Material, item_id)
        if it is not None:
            it.stage = "done"
            q1 = dict(it.quality or {})
            q1["article_id"] = article_id
            it.quality = q1
            s.add(it)
            s.commit()
            from ..materials.service import record_usage

            record_usage(s, tenant_id=it.tenant_id or 1, material_id=item_id,
                         target_type="content", target_id=article_id, role="source")
        s.add(PipelineLedger(day=_today(), pipeline="wechat_adapt", ok=True,
                          produced=f"清单项 #{item_id} → 文章 #{article_id}《{title[:24]}》（重复率 {ratio}%）"))
        s.commit()
    ctx.set_progress(100, f"完成：《{title[:24]}》已排版待发（改编自《{src_title[:20]}》，重复率 {ratio}%）"
                     + (f"｜标题待人工改（{title_warning}）" if title_warning else ""))
    return {"adapted": True, "article_id": article_id, "item_id": item_id,
            "title": title, "ratio": ratio, "source": src_title,
            "title_warning": title_warning}
