"""抖音抓取的浏览器出口：统一应用可选代理（机房 IP 风控的绕行通道）。

背景（2026-09-27 实测）：抖音对机房 IP 的视频详情接口返回风控页，
但住宅 IP 正常。解法=把抖音流量从住宅出口走（本机 SOCKS5 + SSH 反向隧道，
见 scripts/home-proxy.sh）；该出口以 `DOUYIN_PROXY` 配置（如
socks5://127.0.0.1:11080），空=直连。只作用于抖音相关抓取，不影响 LLM/蝉镜等外部服务。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .settings import settings

import platform as _platform

# 浏览器固定装在项目目录（仅 Mac）：系统清理工具曾把 ~/Library/Caches/ms-playwright 整个
# 清掉导致抖音链路全瘫（2026-09-25 事故）。项目目录不受"清缓存"波及；服务器（Linux）
# 用默认 ~/.cache/ms-playwright，无此风险，不受影响。
# 注意：PLAYWRIGHT_BROWSERS_PATH 由 playwright 的 node driver 在启动时读取，
# 在 launch 前写入 os.environ 即生效；setdefault 保留外部显式指定的能力。
if _platform.system() == "Darwin":
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(Path(__file__).resolve().parents[2] / ".ms-playwright"),
    )
del sys

# UA 必须与实际运行平台一致：抖音风控会校验 UA 与 navigator.platform 是否匹配，
# 不一致（如 UA 声称 Mac、实际跑在 Linux）→ 详情接口 100% 返回空体挑战页
# （2026-09-27 实测：Mac UA 在 Linux 上 0/4，Linux UA 4/4）。可用 DOUYIN_UA 覆盖。
_UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
_UA_LINUX = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
_UA_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")


def ua(mobile: bool = False) -> str:
    override = (settings.douyin_ua or "").strip()
    if override:
        return override
    if mobile:
        return _UA_MOBILE
    return _UA_MAC if _platform.system() == "Darwin" else _UA_LINUX


def launch(p, *, mobile: bool = False):
    """统一启动 Chromium：反自动化特征 + 可选代理 + **完整版内核**。

    用完整版（channel="chromium"，headless 新模式）而非 playwright 的
    chromium-headless-shell：shell 是精简构建，指纹更易被抖音挑战
    （2026-09-27 实测同环境下 shell 0/5、完整版 4/4）。
    """
    proxy = (settings.douyin_proxy or "").strip()
    return p.chromium.launch(
        headless=True,
        channel="chromium",  # 完整内核（依赖 playwright install chromium 装的完整包）
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        proxy={"server": proxy} if proxy else None,
    )


def httpx_proxy() -> str | None:
    """给 httpx 用的代理参数（None=直连）。"""
    proxy = (settings.douyin_proxy or "").strip()
    return proxy or None
