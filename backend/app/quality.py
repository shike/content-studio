"""内容热度评分（2026-10-28 两类号策略）：赞粉比 × 赞数量 结合，0~10 分。

- 赞粉比（赞/粉丝）：内容效率信号——小号爆款（低粉高赞）含金量高，
  头部大号的赞被粉丝基数摊薄后单位价值低；
- 赞数量：规模信号（log10 尺度：100 赞≈5 分、1000 赞≈7.5、10000 赞=10 封顶）；
- 总分 = 两者各 0.5 权重。粉丝数未知（0/缺失）退化为纯赞数量分。

自动拆解闸、选题候选排序共用本口径（settings.auto_dissect_min_score 为放行门槛）。
"""
from __future__ import annotations

import math


def content_score(digg: int, followers: int = 0) -> float:
    """按 赞粉比×赞数量 计算内容热度分（0~10，一位小数）。"""
    digg = max(0, int(digg or 0))
    followers = max(0, int(followers or 0))
    digg_s = min(math.log10(digg + 1) / 4, 1) * 10  # 100→5，1000→7.5，10000→10
    if followers > 0:
        ratio = digg / followers
        ratio_s = min(ratio / 20, 1) * 10  # 赞粉比 ≥20 即满分（小号爆款信号）
        return round(0.5 * ratio_s + 0.5 * digg_s, 1)
    return round(digg_s, 1)
