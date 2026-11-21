"""三平台判卷尺（2026-11-21 阶段 4）：发布作品的爆款/过线/待改进判定，单一真源。

线全是**假设值**（2026-11-20 调研口径：抖音 3 万=自有范本收割线、小红书互动率 3%=爆文线、
公众号完读率 40% 合格/65% 优质/30% 死线）——跑 2~4 周后按自家分布重定，改这里的常数即可。
判卷权在数据：这里只折算，不猜爆款。
"""
from __future__ import annotations

VIRAL_PLAY = 30000       # 抖音播放爆款线（=self_scan 范本收割线，勿两处改）
PASS_PLAY = 10000
XHS_VIRAL_RATE = 0.03    # 小红书互动率爆款线 =（赞+藏+评）/曝光
XHS_PASS_RATE = 0.02
WECHAT_VIRAL_RATE = 0.65  # 公众号完读率优质线
WECHAT_PASS_RATE = 0.40   # 合格线
WECHAT_DEAD_RATE = 0.30   # 死线（低于=推荐终止区）

VERDICT_LABEL = {"viral": "爆款", "pass": "过线", "warn": "待改进", "nodata": "未回流"}


def judge(platform: str, metrics: dict) -> dict:
    """按平台判一组成指标。返回 {verdict, value(判卷指标值), line(对齐的线), detail}。

    抖音指标键 play；小红书 exposure/likes/collects/comments；公众号 reads/read_done。"""
    m = {k: int((metrics or {}).get(k) or 0) for k in
         ("play", "exposure", "likes", "collects", "comments", "reads", "read_done")}
    if platform == "douyin":
        if m["play"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": VIRAL_PLAY, "detail": "播放未回流"}
        if m["play"] >= VIRAL_PLAY:
            return {"verdict": "viral", "value": m["play"], "line": VIRAL_PLAY,
                    "detail": f"播放 {m['play']} ≥ 收割线 {VIRAL_PLAY}，已进范本库"}
        if m["play"] >= PASS_PLAY:
            return {"verdict": "pass", "value": m["play"], "line": PASS_PLAY,
                    "detail": f"播放 {m['play']} ≥ 过线 {PASS_PLAY}"}
        return {"verdict": "warn", "value": m["play"], "line": PASS_PLAY,
                "detail": f"播放 {m['play']} < 过线 {PASS_PLAY}"}
    if platform == "xiaohongshu":
        if m["exposure"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": XHS_VIRAL_RATE, "detail": "曝光未回流"}
        rate = (m["likes"] + m["collects"] + m["comments"]) / m["exposure"]
        pct = rate * 100
        if rate >= XHS_VIRAL_RATE:
            return {"verdict": "viral", "value": round(pct, 1), "line": XHS_VIRAL_RATE * 100,
                    "detail": f"互动率 {pct:.1f}% ≥ 爆文线 3%"}
        if rate >= XHS_PASS_RATE:
            return {"verdict": "pass", "value": round(pct, 1), "line": XHS_PASS_RATE * 100,
                    "detail": f"互动率 {pct:.1f}% ≥ 过线 2%"}
        return {"verdict": "warn", "value": round(pct, 1), "line": XHS_PASS_RATE * 100,
                "detail": f"互动率 {pct:.1f}% < 过线 2%"}
    if platform == "wechat":
        if m["reads"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": WECHAT_PASS_RATE * 100, "detail": "阅读未回流"}
        if m["read_done"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": WECHAT_PASS_RATE * 100,
                    "detail": "缺读完数（后台读完率×阅读数或直接填读完）"}
        rate = m["read_done"] / m["reads"]
        pct = rate * 100
        if rate >= WECHAT_VIRAL_RATE:
            return {"verdict": "viral", "value": round(pct, 1), "line": WECHAT_VIRAL_RATE * 100,
                    "detail": f"完读率 {pct:.1f}% ≥ 优质线 65%"}
        if rate >= WECHAT_PASS_RATE:
            return {"verdict": "pass", "value": round(pct, 1), "line": WECHAT_PASS_RATE * 100,
                    "detail": f"完读率 {pct:.1f}% ≥ 合格线 40%"}
        return {"verdict": "warn", "value": round(pct, 1), "line": WECHAT_DEAD_RATE * 100,
                "detail": f"完读率 {pct:.1f}%（<40 合格线；<30 推荐终止区）"}
    return {"verdict": "nodata", "value": 0, "line": 0, "detail": "未知平台"}
