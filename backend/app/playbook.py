"""创作手册（R2-1 2026-11-22）：号主的附加生成规则，三线生成时注入。

原则：提示词文件保持代码管理（质量基座+git 版本），手册是**叠加层**——
号主在设置页写自己的规则（AppSetting `playbook_script`/`playbook_note`/`playbook_article`），
生成时注入 prompt 尾部并声明优先级高于默认规范；与合规红线冲突时红线赢。
空手册=不注入，prompt 零变化。
"""
from __future__ import annotations

_KINDS = ("script", "note", "article")


def _read(kind: str) -> str:
    from sqlmodel import Session

    from .db import engine
    from .models import AppSetting

    with Session(engine) as s:
        row = s.get(AppSetting, f"playbook_{kind}")
    return (row.value or "").strip() if row else ""


def playbook_block(kind: str) -> str:
    """按线取手册块。kind: script|note|article。空手册返回空串（prompt 零变化）。"""
    if kind not in _KINDS:
        return ""
    text = _read(kind)
    if not text:
        return ""
    return (
        f"\n\n## 创作手册（号主附加规则，{kind} 线）\n"
        "以下规则是号主亲手写的，**优先级高于上方所有规范**；"
        "但与「不编造事实」「禁绝对化承诺」两条合规红线冲突时，红线赢：\n\n"
        + text[:4000]
    )
