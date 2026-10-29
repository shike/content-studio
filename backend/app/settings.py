"""全局配置：从仓库根目录 .env 读取，字段定义见 docs/技术方案.md §4。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = _REPO_ROOT / "data"  # 相对 DATA_DIR 也锚定仓库根，不随进程 CWD 漂移

    zhipu_api_key: str = ""
    llm_model: str = "glm-5.3-flash"

    # 联网检索：auto=有智谱key用GLM web_search，否则DDG；可强制 glm|ddg
    search_provider: str = "auto"
    zhipu_search_model: str = "glm-4-flash"  # 检索用的模型（flash 档免费，只付搜索费）
    zhipu_search_base_url: str = "https://open.bigmodel.cn/api/paas/v4"  # 搜索走按量端点（coding 端点无 web_search）
    # GLM 通用 PaaS 端点；若想用 Coding Plan 额度（仅限官方指定工具，见
    # docs.bigmodel.cn/cn/coding-plan/tool/others，自研应用属条款外场景），
    # 改为 https://open.bigmodel.cn/api/coding/paas/v4
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    asr_model: str = "large-v3"
    # 抖音抓取出口分两条链路（2026-10-29 拆分）：
    # scrape_proxy=匿名采集出口（浏览器抓详情/下载直链/探针），空=机房 IP 直连——
    #   实测直连可用（3/3 含隧道下弹验证码的视频），住宅隧道高抖动才是采集失败主因；
    # douyin_proxy=DDG 搜索出海 + 创作者中心登录态采集（出口 IP 与账号日常环境一致性）。
    scrape_proxy: str = ""
    douyin_proxy: str = ""
    douyin_ua: str = ""  # 覆盖抓取 UA（空=按运行平台自动匹配）
    # 部署与安全（云部署：Nginx 反代 + HTTPS 时启用 Cookie Secure；限流防爆破）
    cookie_secure: bool = False   # COOKIE_SECURE=1：会话 Cookie 只经 HTTPS 传输
    login_max_fails: int = 5      # 同一账号在窗口内允许的登录失败次数（超出即锁）
    login_window_sec: int = 900   # 失败计数窗口（秒）
    login_ip_max: int = 40        # 同一来源 IP 窗口内失败上限（防撞库扫号）

    watch_scan_enabled: bool = True   # 定时同行扫描（PRD R3.4c）
    creator_data_enabled: bool = True  # 自有账号日级指标抓取（创作者中心 overview，2026-10-12；kill switch）
    watch_scan_hour: int = 1         # 每天几点执行（本地时区）
    watch_discover_keywords: str = ""  # 每日同行发现的关键词池（逗号/顿号分隔；空=平台默认轮换词）
    # 全自动拆解闸（2026-10-28 用户裁定"全自动化，高质量提前拆解好"）：达标作品自动批准进拆解
    auto_dissect_enabled: bool = True
    auto_dissect_min_score: float = 6.0  # 放行门槛：内容热度分（赞粉比×赞数量，quality.content_score 0~10）
    auto_dissect_daily_cap: int = 6      # 每日自动拆解上限（控下载与 LLM 成本）
    search_enabled: bool = True  # idea 深研的 DDG 联网检索增强（失败自动降级）

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"


settings = Settings()
if not settings.data_dir.is_absolute():
    settings.data_dir = _REPO_ROOT / settings.data_dir
