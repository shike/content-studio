"""文章图表渲染器：LLM 输出图表规格（[CHART]{json}[/CHART]），matplotlib 本地渲染。

数据类配图（对比/趋势/占比）的正确生产方式：免费、无限量、数字绝对准确——
AI 文生图画不了带真实数字的图表，这是工具错配。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # 无界面渲染
import matplotlib.pyplot as plt  # noqa: E402

from ..settings import settings


def _tenant_image_dir():
    """配图按租户分目录（媒体租户边界）；归属=当前 ACTOR，系统上下文回落租户 1。"""
    from ..auth import ACTOR
    tenant_id, _u, _r = ACTOR.get()
    d = settings.data_dir / "article_images" / str(tenant_id or 1)
    d.mkdir(parents=True, exist_ok=True)
    return d
  # noqa: E402

_BRAND = "#5E6AD2"
_PALETTE = ["#5E6AD2", "#8B93E8", "#3D4577", "#B8BDF2", "#2B3160"]


_FONT_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    # Linux（部署服务器）：文泉驿正黑随 fonts-wqy-zenhei 提供
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
)
_FONT_FAMILY = ["PingFang SC", "Hiragino Sans GB", "Heiti SC",
                "WenQuanYi Zen Hei", "Noto Sans CJK SC", "sans-serif"]


def _setup_font() -> None:
    """按平台注册中文字体：Mac 用苹方，Linux 用文泉驿/Noto——缺中文字体时
    matplotlib 渲染中文全是豆腐块（乱码），所以候选字体逐个探测并显式注册。
    注册后清除 matplotlib 的字体缓存：长驻进程里缓存过期会导致新注册字体
    不生效（服务器上中文乱码的根因）。"""
    import shutil

    from matplotlib import font_manager

    for f in _FONT_CANDIDATES:
        if os.path.exists(f):
            font_manager.fontManager.addfont(f)
    cachedir = matplotlib.get_cachedir()
    cache_json = os.path.join(cachedir, "fontlist-*.json")
    import glob

    if glob.glob(cache_json):
        for stale in glob.glob(cache_json):
            os.remove(stale)
        font_manager._load_fontmanager(try_read_cache=False)
    plt.rcParams["font.family"] = _FONT_FAMILY
    plt.rcParams["axes.unicode_minus"] = False


def validate_spec(raw: str | bytes) -> dict[str, Any]:
    """解析并校验图表规格，非法抛 ValueError。"""
    spec = json.loads(raw if isinstance(raw, str) else raw.decode())
    if not isinstance(spec, dict):
        raise ValueError("图表规格必须是对象")
    ctype = spec.get("type") or "bar"
    if ctype not in ("bar", "hbar", "line", "pie"):
        raise ValueError(f"不支持的图表类型 {ctype}")
    cats = [str(c)[:20] for c in (spec.get("categories") or [])]
    vals = [float(v) for v in (spec.get("values") or [])]
    if not cats or not vals or len(cats) != len(vals):
        raise ValueError("categories 与 values 缺失或不等长")
    if len(cats) > 8:
        cats, vals = cats[:8], vals[:8]
    return {**spec, "type": ctype, "categories": cats, "values": vals}


def render_chart(spec: dict[str, Any], primary: str = _BRAND) -> str:
    """primary = 租户主色 hex（图表柱/线颜色）。"""
    """渲染图表规格 → PNG 文件路径。"""
    _setup_font()
    ctype = spec["type"]
    cats: list[str] = spec["categories"]
    vals: list[float] = spec["values"]
    unit = str(spec.get("unit") or "")
    title = str(spec.get("title") or "图表")

    out_dir = _tenant_image_dir()
    path = out_dir / f"chart_{int(time.time() * 1000)}.png"

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    fig.patch.set_facecolor("white")
    labels = [f"{v:g}{unit}" for v in vals]

    if ctype == "bar":
        bars = ax.bar(cats, vals, color=primary, width=0.55)
        ax.bar_label(bars, labels=labels, padding=3, fontsize=10, color="#374151")
        ax.set_ylabel(unit, fontsize=10)
    elif ctype == "hbar":
        bars = ax.barh(cats[::-1], vals[::-1], color=primary, height=0.5)
        ax.bar_label(bars, labels=labels[::-1], padding=3, fontsize=10, color="#374151")
    elif ctype == "line":
        ax.plot(cats, vals, color=primary, marker="o", linewidth=2)
        for x, v, lb in zip(cats, vals, labels):
            ax.annotate(lb, (x, v), textcoords="offset points", xytext=(0, 8),
                        ha="center", fontsize=10, color="#374151")
    elif ctype == "pie":
        ax.pie(vals, labels=cats, colors=_PALETTE[: len(vals)],
               autopct="%1.1f%%", startangle=90,
               wedgeprops={"edgecolor": "white", "linewidth": 1.5})

    ax.set_title(title, fontsize=13, color="#16161B", pad=12)
    if ctype in ("bar", "line"):
        ax.spines[["top", "right"]].set_visible(False)
        ax.spines[["left", "bottom"]].set_color("#E4E4E7")
        ax.grid(axis="y", color="#F1F1F4", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors="#52525B", labelsize=10)
    if ctype in ("hbar", "pie"):
        ax.spines[:].set_visible(False) if ctype == "hbar" else ax.set_xticks([])
        if ctype == "hbar":
            ax.grid(axis="x", color="#F1F1F4", linewidth=0.8)
            ax.set_axisbelow(True)
            ax.tick_params(colors="#52525B", labelsize=10)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return str(path)


def render_flow(spec: dict[str, Any]) -> str:
    """渲染框架/流程图：stages=[{name, desc}] 横向圆角框+箭头+要点小字。"""
    _setup_font()
    from matplotlib.patches import FancyBboxPatch

    stages = spec.get("stages") or []
    stages = [s for s in stages if isinstance(s, dict) and str(s.get("name") or "").strip()]
    if not 2 <= len(stages) <= 5:
        raise ValueError("流程图 stages 需 2~5 个")
    title = str(spec.get("title") or "")[:40]

    out_dir = _tenant_image_dir()
    path = out_dir / f"flow_{int(time.time() * 1000)}.png"

    fig, ax = plt.subplots(figsize=(9.6, 2.9), dpi=150)
    fig.patch.set_facecolor("white")
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    n = len(stages)
    gap = 0.55
    box_w = (10 - gap * (n - 1)) / n
    for i, st in enumerate(stages):
        x = i * (box_w + gap)
        ax.add_patch(FancyBboxPatch((x, 2.6), box_w, 4.8,
                                    boxstyle="round,pad=0.1",
                                    facecolor="#EEF0FA", edgecolor=_BRAND, linewidth=1.6))
        ax.text(x + box_w / 2, 6.1, str(st.get("name") or "")[:10],
                ha="center", fontsize=13, fontweight="bold", color="#1F2430")
        desc = str(st.get("desc") or "")[:44]
        if desc:
            import textwrap
            ax.text(x + box_w / 2, 4.6, "\n".join(textwrap.wrap(desc, 11)),
                    ha="center", va="top", fontsize=9, color="#52525B", linespacing=1.5)
        if i < n - 1:
            ax.annotate("", xy=(x + box_w + gap - 0.08, 5.0),
                        xytext=(x + box_w + 0.08, 5.0),
                        arrowprops=dict(arrowstyle="-|>", color=_BRAND, lw=2.2))
    if title:
        ax.set_title(title, fontsize=13, color="#16161B", pad=10)
    fig.tight_layout()
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return str(path)


def render_cover(title: str, account: str = "", primary: str = _BRAND, slogan: str = "") -> str:
    """primary = 租户主色 hex（渐变底部色）。"""
    """公众号头图（1080x460）：深靛蓝渐变底 + 白色标题大字自动断行 + 账号名。"""
    _setup_font()
    from PIL import Image, ImageDraw, ImageFont

    def _font(sz: int) -> ImageFont.FreeTypeFont:
        # 字体路径按平台探测（与 _setup_font 的候选一致）；全部缺失时退回
        # load_default(size=sz)——新版 Pillow 支持指定字号，至少比 10px 默认体面
        for cand in _FONT_CANDIDATES:
            if os.path.exists(cand):
                return ImageFont.truetype(cand, sz)
        return ImageFont.load_default(size=sz)

    W, H = 1080, 460
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    top = (33, 38, 74)
    hx = (primary or _BRAND).lstrip("#")
    try:
        bot = tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))  # 渐变底部 = 租户主色
    except ValueError:
        bot = (94, 106, 210)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)],
               fill=tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(3)))

    clean = " ".join(str(title).split())
    f_title = _font(56)
    max_w = W - 190
    # 断行单元：连续拉丁字母/数字为一个 token（英文词不拆），中文逐字
    tokens = re.findall(r"[A-Za-z0-9%-]+|.", clean)
    lines: list[str] = []
    cur = ""
    for tk in tokens:
        if d.textlength(cur + tk, font=f_title) > max_w and cur:
            lines.append(cur.strip())
            cur = tk.lstrip()
            if len(lines) == 2:
                break
        else:
            cur += tk
    if cur.strip():
        lines.append(cur.strip())
    while len(lines) > 3:
        lines = lines[:3]

    y = 150 if len(lines) >= 2 else 170
    d.rectangle([64, y - 14, 72, y + len(lines) * 78 - 30], fill=(255, 255, 255))
    for i, ln in enumerate(lines):
        d.text((100, y + i * 78), ln, font=_font(56), fill="white")
    f_small = _font(26)
    d.text((100, H - 74), account, font=f_small, fill=(216, 219, 240))
    if slogan:  # 头图右下口号=租户 brand 配置（空=不渲染；平台写死口径已废弃）
        d.text((W - 300, H - 74), slogan, font=f_small, fill=(216, 219, 240))

    out_dir = _tenant_image_dir()
    path = out_dir / f"cover_{int(time.time() * 1000)}.png"
    img.save(path)
    return str(path)
