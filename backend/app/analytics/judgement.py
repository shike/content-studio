"""三平台判卷尺（2026-11-21 阶段 4；2026-11-22 R2-3 可调化）：发布作品的爆款/过线/待改进判定，单一真源。

默认线=**调研假设值**（抖音 3 万=自有范本收割线、小红书互动率 3%=爆文线、
公众号完读率 40% 合格/65% 优质/30% 死线）——跑 2~4 周后按自家分布重定；
R2 起设置页可调（AppSetting `judgement_lines` JSON 覆盖，缺省=下方常数，60 秒缓存）。
判卷权在数据：这里只折算，不猜爆款。
"""
from __future__ import annotations

import json
import time

# 默认线（假设值；设置页 judgement_lines 可覆盖）
VIRAL_PLAY = 30000       # 抖音播放爆款线（=self_scan 范本收割线，统一走 viral_play()）
PASS_PLAY = 10000
XHS_VIRAL_RATE = 0.03    # 小红书互动率爆款线 =（赞+藏+评）/曝光
XHS_PASS_RATE = 0.02
WECHAT_VIRAL_RATE = 0.65  # 公众号完读率优质线
WECHAT_PASS_RATE = 0.40   # 合格线
WECHAT_DEAD_RATE = 0.30   # 死线（低于=推荐终止区）

VERDICT_LABEL = {"viral": "爆款", "pass": "过线", "warn": "待改进", "nodata": "未回流"}

# 可覆盖键（judgement_lines JSON）：键名 → 默认值
_LINES_DEFAULT: dict[str, float] = {
    "viral_play": VIRAL_PLAY, "pass_play": PASS_PLAY,
    "xhs_viral_rate": XHS_VIRAL_RATE, "xhs_pass_rate": XHS_PASS_RATE,
    "wechat_viral_rate": WECHAT_VIRAL_RATE, "wechat_pass_rate": WECHAT_PASS_RATE,
    "wechat_dead_rate": WECHAT_DEAD_RATE,
}
_CACHE: tuple[float, dict[str, float]] = (0.0, dict(_LINES_DEFAULT))
_CACHE_TTL = 60.0


def get_lines() -> dict[str, float]:
    """六条判卷线真值：AppSetting judgement_lines 覆盖 → 常数兜底（60 秒缓存）。

    覆盖键只收已知键、只收正数；坏值/旧格式静默回落默认（判卷不能因配置错误停摆）。"""
    global _CACHE
    ts, cached = _CACHE
    if time.time() - ts < _CACHE_TTL:
        return cached
    lines = dict(_LINES_DEFAULT)
    try:
        from sqlmodel import Session

        from ..db import engine
        from ..models import AppSetting

        with Session(engine) as s:
            row = s.get(AppSetting, "judgement_lines")
        if row is not None and (row.value or "").strip():
            raw = json.loads(row.value)
            if isinstance(raw, dict):
                for k, d in _LINES_DEFAULT.items():
                    v = raw.get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        lines[k] = float(v)
    except Exception:  # noqa: BLE001 配置读不到=用默认，不让判卷停摆
        pass
    _CACHE = (time.time(), lines)
    return lines


def viral_play() -> int:
    """抖音范本收割线（self_scan 收割与 judge 同源，勿再开常数）。"""
    return int(get_lines()["viral_play"])


def reset_cache() -> None:
    """判卷线覆盖写入后立即生效（设置页 PUT 调用，不等 60 秒 TTL）。"""
    global _CACHE
    _CACHE = (0.0, dict(_LINES_DEFAULT))


def judge(platform: str, metrics: dict, lines: dict[str, float] | None = None) -> dict:
    """按平台判一组成指标。返回 {verdict, value(判卷指标值), line(对齐的线), detail}。

    抖音指标键 play；小红书 exposure/likes/collects/comments；公众号 reads/read_done。
    lines=None 时自动读一次（高频循环建议外部取一次传入，防 N 次读库）。"""
    L = lines or get_lines()
    m = {k: int((metrics or {}).get(k) or 0) for k in
         ("play", "exposure", "likes", "collects", "comments", "reads", "read_done")}
    if platform == "douyin":
        if m["play"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": L["viral_play"], "detail": "播放未回流"}
        if m["play"] >= L["viral_play"]:
            return {"verdict": "viral", "value": m["play"], "line": int(L["viral_play"]),
                    "detail": f"播放 {m['play']} ≥ 收割线 {int(L['viral_play'])}，已进范本库"}
        if m["play"] >= L["pass_play"]:
            return {"verdict": "pass", "value": m["play"], "line": int(L["pass_play"]),
                    "detail": f"播放 {m['play']} ≥ 过线 {int(L['pass_play'])}"}
        return {"verdict": "warn", "value": m["play"], "line": int(L["pass_play"]),
                "detail": f"播放 {m['play']} < 过线 {int(L['pass_play'])}"}
    if platform == "xiaohongshu":
        if m["exposure"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": L["xhs_viral_rate"] * 100, "detail": "曝光未回流"}
        rate = (m["likes"] + m["collects"] + m["comments"]) / m["exposure"]
        pct = rate * 100
        if rate >= L["xhs_viral_rate"]:
            return {"verdict": "viral", "value": round(pct, 1), "line": round(L["xhs_viral_rate"] * 100, 1),
                    "detail": f"互动率 {pct:.1f}% ≥ 爆文线 {L['xhs_viral_rate'] * 100:.0f}%"}
        if rate >= L["xhs_pass_rate"]:
            return {"verdict": "pass", "value": round(pct, 1), "line": round(L["xhs_pass_rate"] * 100, 1),
                    "detail": f"互动率 {pct:.1f}% ≥ 过线 {L['xhs_pass_rate'] * 100:.0f}%"}
        return {"verdict": "warn", "value": round(pct, 1), "line": round(L["xhs_pass_rate"] * 100, 1),
                "detail": f"互动率 {pct:.1f}% < 过线 {L['xhs_pass_rate'] * 100:.0f}%"}
    if platform == "wechat":
        if m["reads"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": L["wechat_pass_rate"] * 100, "detail": "阅读未回流"}
        if m["read_done"] <= 0:
            return {"verdict": "nodata", "value": 0, "line": L["wechat_pass_rate"] * 100,
                    "detail": "缺读完数（后台读完率×阅读数或直接填读完）"}
        rate = m["read_done"] / m["reads"]
        pct = rate * 100
        if rate >= L["wechat_viral_rate"]:
            return {"verdict": "viral", "value": round(pct, 1), "line": round(L["wechat_viral_rate"] * 100, 1),
                    "detail": f"完读率 {pct:.1f}% ≥ 优质线 {L['wechat_viral_rate'] * 100:.0f}%"}
        if rate >= L["wechat_pass_rate"]:
            return {"verdict": "pass", "value": round(pct, 1), "line": round(L["wechat_pass_rate"] * 100, 1),
                    "detail": f"完读率 {pct:.1f}% ≥ 合格线 {L['wechat_pass_rate'] * 100:.0f}%"}
        return {"verdict": "warn", "value": round(pct, 1), "line": round(L["wechat_pass_rate"] * 100, 1),
                "detail": f"完读率 {pct:.1f}%（<{L['wechat_pass_rate'] * 100:.0f} 合格线；<{L['wechat_dead_rate'] * 100:.0f} 推荐终止区）"}
    return {"verdict": "nodata", "value": 0, "line": 0, "detail": "未知平台"}
