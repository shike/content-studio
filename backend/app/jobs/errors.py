"""任务失败分类学（任务体系 P1 骨架）：fail_class 决定重试策略。

- transient      瞬时故障（网络/超时/5xx/LLM 通道抖动）→ 按任务类型策略自动重试（指数退避）
- quota          额度窗口耗尽（LLM 套餐/按量余额）→ 任务转 parked 挂起，到点自动放行，不算失败
- captcha        平台风控/验证墙 → 失败，标注"可人工重试"（人工重试会清零计数）
- deterministic  其余一切（默认，含参数/配置/内容问题）→ 立即失败，永不自动重试

历史包袱：LLM 网关与各执行器大量直接 raise RuntimeError，靠 classify() 的消息
特征分诊兜底；新代码请直接抛本模块的异常类。
"""
from __future__ import annotations


class JobError(RuntimeError):
    """带分类标签的任务失败基类。"""

    fail_class = "deterministic"


class TransientError(JobError):
    fail_class = "transient"


class QuotaExhaustedError(JobError):
    fail_class = "quota"


class CaptchaError(JobError):
    fail_class = "captcha"


# 消息特征 → 类别（兜底分诊，覆盖历史上直接 raise RuntimeError 的位置）
_TRANSIENT_MARKS = (
    "Server disconnected", "timed out", "Timeout", "Connection reset",
    "Connection refused", "HTTP 5", "502", "503", "504",
    "空正文", "推理耗尽",  # 思考型模型偶发空正文（间歇性，重试可解）
)
_QUOTA_MARKS = ("1113", "余额不足", "quota", "Quota", "额度")
# 平台风控验证码：IP 级惩罚窗（几十分钟级），等待后重试可解——既非网络抖动也非确定性错误
_CAPTCHA_MARKS = ("验证码", "captcha", "Captcha")


def classify(exc: BaseException) -> str:
    """异常 → fail_class。isinstance 优先，消息特征兜底。"""
    if isinstance(exc, JobError):
        return exc.fail_class
    msg = str(exc)
    if any(k in msg for k in _QUOTA_MARKS):
        return "quota"
    if any(k in msg for k in _CAPTCHA_MARKS):
        return "captcha"
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return "transient"
    if any(k in msg for k in _TRANSIENT_MARKS):
        return "transient"
    return "deterministic"


# ---------- 错误指纹（任务中台）：同类失败聚类 / 按指纹复活 ----------

# 规则表：指纹 → 消息特征（顺序即优先级，具体在前、宽泛在后）
_FP_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("asr:vocab", "ASR 词表越界", ("invalid token id",)),
    ("llm:empty-body", "LLM 空正文", ("空正文", "推理耗尽")),
    ("llm:quota", "LLM 额度/余额", ("1113", "余额不足", "quota", "额度")),
    ("llm:bad-request", "LLM 请求被拒", ("400", "bad request", "invalid request")),
    ("ddg:rate-limited", "DDG 检索限流", ("rate_limited", "ddg 限流", "检索限流")),
    ("douyin:captcha", "抖音验证码", ("验证码", "captcha")),
    ("douyin:risk", "抖音风控", ("风控", "未吐出", "挑战页")),
    ("net:timeout", "网络超时", ("timed out", "timeout")),
    ("net:broken", "网络断连/5xx", ("server disconnected", "connection reset",
                                    "connection refused", "http 5", "502", "503", "504",
                                    "remote end closed")),
    ("db:constraint", "数据库约束（FK 悬空等）", ("foreign key constraint", "integrityerror",
                                                  "no such column", "no such table")),
    ("credit:insufficient", "租户积分不足", ("积分不足",)),
    ("infra:config", "配置缺失", ("未配置", "not configured", "no_key", "不能为空")),
]

_CODE_BUG_EXCS = ("NameError", "TypeError", "KeyError", "AttributeError",
                  "IndexError", "ZeroDivisionError", "UnicodeDecodeError", "JSONDecodeError")


def fingerprint(exc: BaseException) -> str:
    """异常 → 归一化错误指纹。规则命中取规则指纹；疑似代码 bug 取 code:bug；
    其余按归一化消息哈希（数字抹掉，同类错误同指纹）。"""
    msg = str(exc).lower()
    if isinstance(exc, JobError):
        fc = exc.fail_class
        if fc == "quota":
            return "llm:quota"
        if fc == "captcha":
            return "douyin:captcha"
        if fc == "transient":
            for fp, _label, marks in _FP_RULES:
                if any(m.lower() in msg for m in marks):
                    return fp
            return "misc:transient"
    for type_name in (type(exc).__name__, *[b.__name__ for b in type(exc).__mro__[1:3]]):
        if type_name in _CODE_BUG_EXCS:
            return "code:bug"
    for fp, _label, marks in _FP_RULES:
        if any(m.lower() in msg for m in marks):
            return fp
    import hashlib
    import re as _re
    norm = _re.sub(r"\d+", "#", msg)[:120].strip()
    return "misc:" + hashlib.md5(norm.encode()).hexdigest()[:8]


def fingerprint_label(fp: str) -> str:
    for f, label, _marks in _FP_RULES:
        if f == fp:
            return label
    return {"code:bug": "代码缺陷", "misc:transient": "瞬时故障",
            "infra:restart": "服务重启中断", "infra:watchdog": "运行卡死（看门狗）"}.get(fp, fp)
