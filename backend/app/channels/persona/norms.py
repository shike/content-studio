"""靶子卡归一层：归一/计长/覆盖面判定与词根、闸门常量（零依赖叶子模块）。

2026-11-16 C 阶段从 service.py 拆出（对抗讨论 #115-#117 定稿：两用助手下沉为叶子，
gates 与 forge 都只 import 本模块，gates 不 import forge）。
"""
from __future__ import annotations

import re

from ...settings import settings

_DOMAIN_KW = ("AI", "人工智能", "大模型", "模型", "智能体", "agent", "机器人", "芯片", "算力",
              "开源", "自动驾驶", "智能驾驶", "代码", "软件", "互联网", "科技", "数字", "算法",
              "半导体", "云计算", "无人机", "手机", "汽车",
              "字节", "阿里", "腾讯", "百度", "华为", "小米", "京东", "美团", "网易", "快手",
              "deepseek", "openai", "anthropic", "谷歌", "google", "微软", "meta", "苹果",
              "特斯拉", "英伟达", "亚马逊", "claude", "chatgpt", "gemini", "copilot",
              "豆包", "通义", "文心", "夸克", "可灵", "即梦", "扣子", "coze", "dify",
              "飞书", "钉钉", "workbuddy", "智谱", "kimi", "minimax", "阶跃",
              "gpu", "显卡", "服务器", "数据中心", "v100", "a100", "h100", "4090", "5090",
              "鸿蒙", "昇腾", "问界", "比亚迪", "蔚来", "小鹏", "极氪", "联想", "大疆",
              "宇树", "unitree", "manus", "suno", "cursor", "notion", "figma", "vscode",
              "qwen", "grok", "xai", "deepmind", "saas", "办公", "ios", "android", "app", "github", "仓库")


_ADVICE_OPENER = re.compile(r"^(先|快|应|该|要|记得|建议|试试|赶紧|需要|必须|最好|千万|不妨)")


_PREDICT_KW = ("迟早", "注定", "必死", "要凉")


_CLAUSE_PUNCT = re.compile(r"[，,。？?：:；;、]")


_PUNCT_STRIP = re.compile(r"[\s，,。.！!？?：:；;、「」『』《》\"'（）()·|…—\-+#]")


_NORM_RE = re.compile(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]")


_COVER_TABLE = {
    "boss": ("老板", "企业主", "厂长", "创始人", "个体", "创业", "小微", "开厂", "开公司", "做生意的", "生意人"),
    "middle": ("中层", "经理", "主管", "部门", "管理岗", "组长"),
    "job_wide": ("财务", "会计", "销售", "行政", "人事", "文员", "工人", "车间", "客服",
                 "运营", "设计师", "程序员", "司机", "仓管", "采购", "质检", "员工",
                 "打工", "上班", "职场"),
}


_COVER_RANK = {"boss": 3, "middle": 2, "job_wide": 1, "job_narrow": 0}


def _norm(t: str) -> str:
    return _NORM_RE.sub("", str(t)).lower()


def _pron_len(t: str) -> int:
    """可发音字符计长（三秒时长的字数代理）：标点不发音，剔除后计数。"""
    return len(_PUNCT_STRIP.sub("", str(t)))


def _max_chars() -> int:
    """三秒字数线 = 3 秒 × 语速口径（时长是法律，字数是代理，不设任何宽裕）。"""
    return 3 * max(1, int(settings.hook_speed_cps or 5))


def _coverage_of(role: str) -> str:
    """主语角色→覆盖面档位（广度闸：认不出的角色一律按窄处理——宁拦勿放）。"""
    r = _norm(role)
    for tier, kws in _COVER_TABLE.items():
        if any(k in r for k in kws):
            return tier
    return "job_narrow"


def _kw_hit(text: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in _DOMAIN_KW)
