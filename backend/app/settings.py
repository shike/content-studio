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

    llm_provider: str = "glm"  # glm | deepseek
    zhipu_api_key: str = ""
    deepseek_api_key: str = ""
    llm_model: str = "glm-5"

    # 联网检索：auto=有智谱key用GLM web_search，否则DDG；可强制 glm|ddg
    search_provider: str = "auto"
    zhipu_search_model: str = "glm-4-flash"  # 检索用的模型（flash 档免费，只付搜索费）
    zhipu_search_base_url: str = "https://open.bigmodel.cn/api/paas/v4"  # 搜索走按量端点（coding 端点无 web_search）
    # GLM 通用 PaaS 端点；若想用 Coding Plan 额度（仅限官方指定工具，见
    # docs.bigmodel.cn/cn/coding-plan/tool/others，自研应用属条款外场景），
    # 改为 https://open.bigmodel.cn/api/coding/paas/v4
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    asr_model: str = "large-v3"
    # 抖音抓取出口（机房 IP 被风控时的绕行）：如 socks5://127.0.0.1:11080；空=直连
    douyin_proxy: str = ""
    douyin_ua: str = ""  # 覆盖抓取 UA（空=按运行平台自动匹配）
    # 蝉镜开放 API（数字人主线引擎，2026-09-24 定版）：appid + secretKey，设置页可改
    chanjing_app_id: str = ""
    chanjing_secret_key: str = ""
    # 部署与安全（云部署：Nginx 反代 + HTTPS 时启用 Cookie Secure；限流防爆破）
    cookie_secure: bool = False   # COOKIE_SECURE=1：会话 Cookie 只经 HTTPS 传输
    login_max_fails: int = 5      # 同一账号在窗口内允许的登录失败次数（超出即锁）
    login_window_sec: int = 900   # 失败计数窗口（秒）
    login_ip_max: int = 40        # 同一来源 IP 窗口内失败上限（防撞库扫号）

    watch_scan_enabled: bool = True   # 定时同行扫描（PRD R3.4c）
    watch_scan_hour: int = 1         # 每天几点执行（本地时区）
    search_enabled: bool = True  # idea 深研的 DDG 联网检索增强（失败自动降级）

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"


settings = Settings()
if not settings.data_dir.is_absolute():
    settings.data_dir = _REPO_ROOT / settings.data_dir
