"""租户品牌/内容配置（SaaS 产品化，2026-09-30；2026-10-02 中性默认重构）。

租户的栏目名、署名、人设、配色等不再 hardcode 在代码里，
而是**租户级配置**（tenants.brand JSON），让一套部署服务不同定位的客户。

brand 字段（BRAND_KEYS）：
- label_line1 / label_line2 : 数字人角标栏目名（两行，画进成片）
- signature                 : 公众号头图署名
- persona                   : 创作者人设描述（注入全部内容生成 prompt 的人设段）
- accent / primary          : 点缀色 / 图表与头图主色（hex）
- asr_vocab                 : ASR 领域词表（空=平台中性默认）
- cover_slogan              : 公众号头图右下口号（空=不渲染）
- audience_note             : 受众域口径（注入深研/拆解 prompt，替代写死的行业语义）

默认策略（2026-10-02）：**未配置租户回落中性默认**（各行业客户拿到的都是中性话术）；
创始租户（id=1）由 seed_founder_brand() 一次性写入一套填满九字段的**示例品牌**，
用于演示各字段的实际效果，租户可在设置页/管理后台随时改掉。
角标字体：按租户栏目名动态子集化 Noto Sans SC Bold（fontTools），缓存于 data/label_fonts/。
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session

from .db import engine
from .models import Tenant
from .settings import settings

# 中性默认（未配置租户的兜底）：任何行业的客户拿到都不会是"别人的业务话术"
NEUTRAL_DEFAULT_BRAND: dict = {
    "label_line1": "跃迁",
    "label_line2": "内容",
    "signature": "",
    "accent": "#F2C14E",   # 角标/点缀色（暖黄）
    "primary": "#16a34a",  # 主色（图表/头图渐变/卡片强调；与站点绿色体系一致）
    "asr_vocab": "",       # ASR 领域词表（空=平台中性默认）
    "persona": (
        "你是一位企业内容主理人：通过短视频与公众号持续输出所在行业的专业内容，"
        "建立专业信任并获取客户。内容要求：判断具体、有数字有出处、不空谈；"
        "结尾自然收束，不加转化引导。内容平台：抖音/视频号口播短视频 + 微信公众号深度文。"
    ),
    "cover_slogan": "",    # 头图右下口号（空=不渲染）
    "audience_note": "面向企业经营决策者与一线实操者。",
}

# 创始租户（id=1）的示例品牌：seed_founder_brand() 一次性写入（九字段全填的演示值，
# 不含任何真实业务人设；租户在设置页可整体替换）
FOUNDER_BRAND: dict = {
    "label_line1": "跃迁",
    "label_line2": "专栏",
    "signature": "跃迁内容",
    "accent": "#F2C14E",
    "primary": "#5E6AD2",
    "asr_vocab": (
        "以下是企业经营、数字化转型与人工智能应用的中文口播内容。"
    ),
    "persona": (
        "你是一位企业服务领域的内容主理人：面向企业经营决策者，"
        "通过短视频与公众号持续输出所在行业的专业内容，建立专业信任并获取客户。"
        "内容要求：判断具体、有数字有出处、不空谈；结尾自然收束，不加转化引导。"
        "内容平台：抖音/视频号口播短视频 + 微信公众号深度文。"
    ),
    "cover_slogan": "深度 · 专业 · 可落地",
    "audience_note": "面向企业经营决策者与一线实操者。",
}

BRAND_KEYS = ("label_line1", "label_line2", "signature", "asr_vocab", "accent", "primary",
              "persona", "cover_slogan", "audience_note")

_DEFAULT_ASR_VOCAB = "以下是企业经营与行业分享类的中文口播内容。"

_FONT_SRC = Path(__file__).resolve().parent / "assets" / "fonts" / "NotoSansSC-Bold.otf"
_SUBSET_DIR = settings.data_dir / "label_fonts"


def _norm_brand(raw: dict | None) -> dict:
    """读态解析：租户覆盖键 + 中性默认（缺的字段回落中性值）。"""
    b = dict(NEUTRAL_DEFAULT_BRAND)
    for k, v in (raw or {}).items():
        if k in BRAND_KEYS and str(v or "").strip():
            b[k] = str(v).strip()
    return b


def seed_founder_brand() -> bool:
    """创始租户（id=1）一次性写入示例品牌（幂等：brand 已有内容则跳过）。

    启动时调用（init_db 之后）。作用是给全新部署一套"九字段填满"的可见示例；
    租户自行保存过品牌（brand 非空）就绝不覆盖。"""
    try:
        with Session(engine) as s:
            t = s.get(Tenant, 1)
            if t is None:
                return False
            if t.brand:  # dict 或 str 均视为已有配置（旧版把它当 str 处理，dict 会崩 .strip()）
                return False
            t.brand = dict(FOUNDER_BRAND)  # JSON 列直接赋 dict（json.dumps 会双重编码成 str）
            s.add(t)
            s.commit()
            print("[brand] 创始租户（id=1）已写入示例品牌配置")
            return True
    except Exception as e:  # noqa: BLE001 种子失败不拦启动（brand_of 会回落中性默认）
        print(f"[brand] 创始租户品牌种子失败: {type(e).__name__}: {str(e)[:80]}")
        return False


def brand_of(tenant_id: int) -> dict:
    """读租户品牌配置（覆盖键 + 中性默认解析）。"""
    return _norm_brand(brand_raw_cached(tenant_id))


def accent_of(tenant_id: int) -> str:
    """点缀/主色 hex（默认暖黄）。"""
    return _norm_brand(brand_raw_cached(tenant_id)).get("accent", NEUTRAL_DEFAULT_BRAND["accent"])


def primary_of(tenant_id: int) -> str:
    return _norm_brand(brand_raw_cached(tenant_id)).get("primary", NEUTRAL_DEFAULT_BRAND["primary"])


def audience_note_of(tenant_id: int) -> str:
    """受众域口径（注入深研/拆解 prompt；中性默认非空，任何行业都成立）。"""
    return _norm_brand(brand_raw_cached(tenant_id)).get("audience_note", "")


def asr_vocab_of(tenant_id: int) -> str:
    """租户 ASR 领域词表：租户自定义优先，空则平台中性默认（asr.py 同文案）。"""
    return _norm_brand(brand_raw_cached(tenant_id)).get("asr_vocab", "").strip()


def brand_raw_cached(tenant_id: int) -> dict:
    """brand 原始 JSON（无缓存层，直接读库；调用频率低）。"""
    try:
        with Session(engine) as s:
            t = s.get(Tenant, tenant_id)
            return t.brand if t else {}
    except Exception as e:  # noqa: BLE001
        print(f"[brand] 读取租户 {tenant_id} 失败: {type(e).__name__}: {str(e)[:60]}")
        return {}


def accent_rgb_of(tenant_id: int) -> tuple[int, int, int]:
    """点缀色 hex → RGB 元组（角标绘制用）。"""
    hx = (accent_of(tenant_id) or "#F2C14E").lstrip("#")
    try:
        return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (242, 193, 78)


def label_lines_of(tenant_id: int) -> tuple[str, str]:
    b = brand_of(tenant_id)
    return (b["label_line1"] or NEUTRAL_DEFAULT_BRAND["label_line1"],
            b["label_line2"] or NEUTRAL_DEFAULT_BRAND["label_line2"])


def cover_slogan_of(tenant_id: int) -> str:
    return _norm_brand(brand_raw_cached(tenant_id)).get("cover_slogan", "")


def save_brand(tenant_id: int, brand: dict) -> dict:
    """管理员保存租户品牌（校验在 API 层）。

    只持久化提供的覆盖键（空值不落默认值——默认在读取时解析），避免把兜底文案
    写死进租户配置；保存后清该租户角标字体缓存。"""
    norm = {k: str(v).strip() for k, v in (brand or {}).items()
            if k in BRAND_KEYS and str(v or "").strip()}
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            raise ValueError(f"租户 {tenant_id} 不存在")
        t.brand = dict(norm)
        s.add(t)
        s.commit()
    _clear_font_cache(tenant_id)
    return _norm_brand(norm)


def _clear_font_cache(tenant_id: int) -> None:
    for p in _SUBSET_DIR.glob(f"label_{tenant_id}_*.otf"):
        try:
            p.unlink()
        except Exception:  # noqa: BLE001
            pass


def label_font_path(tenant_id: int, label: str, size_hint: str = "") -> Path:
    """按租户栏目名生成/复用字体子集（PIL 可加载；全量 Noto CJK 直接加载会缺 hmtx）。"""
    from fontTools import subset

    cache = _SUBSET_DIR / f"label_{tenant_id}_{abs(hash(label)) % 10**8}.otf"
    if cache.exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    import string

    chars = "".join(dict.fromkeys(
        label + string.ascii_letters + string.digits + "/ .·—：:年月日时分第条期"))
    opts = subset.Options()
    opts.name_IDs = [1, 2, 6]
    opts.notdef_outline = True
    font = subset.load_font(_FONT_SRC, opts)
    ss = subset.Subsetter(options=opts)
    ss.populate(text=chars)
    ss.subset(font)
    subset.save_font(font, cache, opts)
    return cache
