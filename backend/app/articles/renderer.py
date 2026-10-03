"""MD → 公众号内联样式 HTML。

样式方案参考 doocs/md（WTFPL）默认主题的思路，用 Python 实现：
python-markdown 负责 MD→HTML，本模块把顶层块逐段包进 <section>，
并为常用标签注入内联样式（公众号编辑器只认内联样式）。
"""
from __future__ import annotations

from html.parser import HTMLParser

import re

import bleach
from bleach.css_sanitizer import CSSSanitizer
from markdown import markdown

# 渲染产物白名单净化（防存储型 XSS）：文章 md 由 LLM 生成且用户可改（PUT /articles），
# python-markdown 默认放行原始 HTML——不净化则租户成员可在 md 里埋 <script>，
# 平台管理员打开文章/打开 wechat.html 即在其会话下执行（跨租户提权）。
_ALLOWED_TAGS = ["section", "p", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em",
                 "i", "u", "s", "del", "blockquote", "ul", "ol", "li", "hr", "br", "img",
                 "a", "code", "pre", "table", "thead", "tbody", "tr", "td", "th", "span",
                 "sup", "sub", "figure", "figcaption"]
_ALLOWED_ATTRS = {
    "*": ["style"],
    "a": ["href", "title"],
    "img": ["src", "alt", "width", "height"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
_CSS_SANITIZER = CSSSanitizer(allowed_css_properties=[
    "margin", "padding", "font-size", "font-weight", "font-family", "font-style",
    "color", "background", "background-color", "line-height", "letter-spacing",
    "text-align", "text-decoration", "text-indent", "word-break", "white-space",
    "border", "border-left", "border-right", "border-top", "border-bottom",
    "border-radius", "border-color", "border-width", "border-style",
    "display", "width", "max-width", "height", "vertical-align", "list-style", "box-sizing",
])


def sanitize_html(html: str) -> str:
    """白名单净化：剥脚本/事件属性/危险协议，保留渲染器注入的内联样式。"""
    return bleach.clean(
        html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS,
        protocols=["http", "https", "mailto"], strip=True, strip_comments=True,
        css_sanitizer=_CSS_SANITIZER,
    )

_ROOT_STYLE = (
    "margin:0;padding:0;"
    "font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue','PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',sans-serif;"
    "color:#3f3f3f;font-size:15px;line-height:1.9;letter-spacing:0.5px;"
    "word-break:break-word;"
)

_STYLES: dict[str, str] = {
    "h1": "margin:26px 0 14px;font-size:20px;font-weight:700;color:#1f1f1f;",
    "h2": "margin:24px 0 12px;font-size:18px;font-weight:700;color:#1f1f1f;",
    "h3": "margin:20px 0 10px;font-size:16px;font-weight:700;color:#2a2a2a;",
    "p": "margin:14px 0;",
    "blockquote": ("margin:16px 0;padding:10px 14px;background:#f7f7f7;"
                   "border-left:3px solid #d0d0d0;color:#666;font-size:14px;"),
    "ul": "margin:14px 0;padding-left:22px;",
    "ol": "margin:14px 0;padding-left:22px;",
    "li": "margin:6px 0;",
    "strong": "font-weight:700;color:#0f6f4f;",
    "em": "font-style:italic;color:#555555;",
    "img": "max-width:100%;border-radius:6px;margin:10px 0;",
    "hr": "border:none;border-top:1px solid #e5e5e5;margin:22px 0;",
    "code": ("background:#f6f8fa;padding:2px 5px;border-radius:4px;"
             "font-family:Menlo,Consolas,monospace;font-size:13px;color:#c7254e;"),
    "pre": ("background:#f6f8fa;padding:14px;border-radius:8px;overflow-x:auto;"
            "margin:14px 0;font-size:13px;line-height:1.6;"),
    "a": "color:#576b95;text-decoration:none;border-bottom:1px solid #d9d9d9;",
    "table": "border-collapse:collapse;width:100%;margin:14px 0;font-size:14px;",
    "th": "border:1px solid #e5e5e5;padding:8px;background:#f7f7f7;font-weight:700;",
    "td": "border:1px solid #e5e5e5;padding:8px;",
}

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
               "blockquote", "pre", "table", "hr", "img"}
_VOID = {"br", "img", "hr", "meta", "link", "input", "source", "wbr"}
_SKIP = {"html", "body"}


def _esc(data: str) -> str:
    return data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _WechatTransformer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._depth = 0
        self._in_section = False

    def _styled_open(self, tag: str, attrs) -> str:
        attrs_str = "".join(
            f' {k}="{(v or "").replace(chr(34), "&quot;")}"'
            for k, v in attrs if k != "style")
        style = _STYLES.get(tag)
        style_attr = f' style="{style}"' if style else ""
        return f"<{tag}{attrs_str}{style_attr}>"

    def _img_html(self, attrs) -> str | None:
        """<img> 专项：本地配图 → 内联本地地址；search: 关键词 → 虚线占位卡。

        返回 None 表示交给通用标签路径（外链图等）。注意两种本地图写法都要认：
        裸文件名（gen_x.png）与 md 拼装实际用的 scheme 形态（cover:x.png / chart:x.png）——
        scheme 不在 bleach 协议白名单里，若不在此剥掉会被整条丢弃，渲染产物变成无 src 的裂图
        （2026-09-27 修：公众号粘贴后图片永远是裂图，根因即此）。"""
        attrs_d = dict(attrs)
        srcv = (attrs_d.get("src") or "").strip()
        if ":" in srcv and srcv.split(":", 1)[0].lower() in ("cover", "chart", "gen", "flow"):
            srcv = srcv.split(":", 1)[1].strip()
        if srcv.startswith(("gen_", "chart_", "cover_", "flow_")):
            # 已生成的本地图片（AI 插画/数据图表）：直接内嵌，公众号粘贴即带图
            keep = [(k, v) for k, v in attrs if k not in ("src", "style")]
            return self._styled_open("img", keep + [("src", f"/api/articles/images/{srcv}")])
        if srcv.startswith("search:"):
            kw = srcv[len("search:"):].strip()
            cap = (attrs_d.get("alt") or "").strip()
            card = (
                '<section style="margin:16px 0;border:1px dashed #b9b9b9;'
                'border-radius:8px;padding:18px 14px;text-align:center;background:#fafafa">'
                '<div style="font-size:22px;line-height:1">🖼️</div>'
                f'<div style="margin-top:8px;font-size:14px;color:#444">配图：{_esc(cap)}</div>'
                f'<div style="margin-top:4px;font-size:12px;color:#999">搜图/制图关键词：{_esc(kw)}'
                ' ｜ 插图后删除本占位框</div></section>'
            )
            if self._depth == 0:
                return f'<section style="{_ROOT_STYLE}">{card}</section>'
            return card
        return None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            return
        if tag == "img":
            repl = self._img_html(attrs)
            if repl is not None:
                self.out.append(repl)
                return
        if self._depth == 0 and tag in _BLOCK_TAGS:
            self._in_section = True
            self.out.append(f'<section style="{_ROOT_STYLE}">')
        self.out.append(self._styled_open(tag, attrs))
        if tag not in _VOID:
            self._depth += 1

    def handle_startendtag(self, tag, attrs):
        if tag in _SKIP:
            return
        if tag == "img":
            repl = self._img_html(attrs)
            if repl is not None:
                self.out.append(repl)
                return
        styled = self._styled_open(tag, attrs) + f"</{tag}>"
        if self._depth == 0 and tag in _BLOCK_TAGS:
            self.out.append(f'<section style="{_ROOT_STYLE}">{styled}</section>')
        else:
            self.out.append(styled)

    def handle_endtag(self, tag):
        if tag in _SKIP:
            return
        self.out.append(f"</{tag}>")
        if tag not in _VOID:
            self._depth -= 1
            if self._depth == 0 and self._in_section:
                self.out.append("</section>")
                self._in_section = False

    def handle_data(self, data):
        if self._depth == 0:
            if data.strip():
                self.out.append(
                    f'<section style="{_ROOT_STYLE}">'
                    f'<p style="{_STYLES["p"]}">{_esc(data)}</p></section>')
        else:
            self.out.append(_esc(data))


def render_wechat_html(md_text: str) -> str:
    text = md_text or ""
    # 公众号标题在编辑器单独填写：剥掉正文开头的 H1
    stripped = text.lstrip()
    if stripped.startswith("# "):
        text = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    # 剥掉 [CHART]{...}[/CHART] 规格块：那是分节写作的中间产物，成图由流水线以
    # (chart:文件) 图片标记另行插入正文——不剥则原始 JSON 原样透进 HTML，
    # 读者在公众号里看到一段代码（已发布旧文实测泄漏，2026-10-02 修）
    text = re.sub(r"\[CHART\].*?\[/CHART\]", "", text, flags=re.S)
    html = markdown(text, extensions=["extra", "nl2br", "sane_lists"])
    transformer = _WechatTransformer()
    transformer.feed(html)
    transformer.close()
    # 统一出口净化：原始 HTML（含 md 里手写的标签/事件属性）在此被白名单过滤；
    # 样式是本模块注入的可信内联样式，经 CSS 白名单保留。
    return sanitize_html("".join(transformer.out).strip())
