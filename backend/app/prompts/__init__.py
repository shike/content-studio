"""Prompt 模板加载：md 文件 + frontmatter（purpose/version），代码只负责填充。"""
from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> str:
    text = (_DIR / f"{name}.md").read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5:]
    elif text[:200].lstrip().startswith(("purpose:", "version:")):
        # 2026-11-13 新增闸：frontmatter 缺开头 --- 时，purpose/version/--- 三行会原样
        # 进 prompt（静默失效——article_hotspots/self_style_extract/self_profile_update
        # 三篇实际坏过）。与 render() 的防静默哲学同源，坏格式直接抛而不是带病运行。
        raise RuntimeError(f"prompt {name}.md frontmatter 坏：缺开头 ---，元数据会原样进 prompt")
    return text.strip()


def render(name: str, **values: str) -> str:
    """注入占位符并强制校验：模板里没有该占位符就直接抛错。

    为什么要校验：`str.replace` 没匹配上不会报错——长短版长度、结构模板都曾因为
    prompt 里没有 `{{LENGTH}}`/`{{STRUCTURE}}` 而**静默失效**（设置界面照收、任务照跑、
    模型收到的却是写死的老规格）。同类静默失效已踩三次，闸门放进库函数里。
    """
    text = load(name)
    missing = [k for k in values if "{{%s}}" % k not in text]
    if missing:
        raise RuntimeError(f"prompt {name}.md 缺少占位符 {missing}——注入会静默失效，已拦下")
    for k, v in values.items():
        text = text.replace("{{%s}}" % k, v)
    return text
