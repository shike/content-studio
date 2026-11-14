"""全局配置：从仓库根目录 .env 读取，架构契约见 docs/TDD.md §4。"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    data_dir: Path = _REPO_ROOT / "data"  # 相对 DATA_DIR 也锚定仓库根，不随进程 CWD 漂移

    zhipu_api_key: str = ""
    llm_model: str = "glm-5.3-flash"

    # 联网检索：auto=有智谱key用GLM web_search，否则DDG；可强制 glm|ddg
    search_provider: str = "auto"
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
    ima_kb_enabled: bool = True      # ima 知识库取材/选题线总开关（执行器层还要求 app_settings 里凭证配齐，两层相与）
    watch_scan_hour: int = 1         # 每天几点执行（本地时区）
    # 拆解闸（2026-11-11 用户定版：评论≥20 或 赞≥300 任一即拆；11-08 的「全拆无闸」
    # 实测 7 天灌入 222 篇低赞拆解+58 篇垃圾选题后撤销）。播放数匿名通道恒 0
    # （只有作者后台可见），闸线只能落评论/赞。
    dissect_comment_min: int = 20   # 拆解闸：评论 ≥ 此值自动拆解
    dissect_digg_min: int = 300     # 拆解闸：赞 ≥ 此值自动拆解（与评论线任一满足即可）
    search_enabled: bool = True  # idea 深研的 DDG 联网检索增强（失败自动降级）
    # 选题工场（PRD R1.4 三原则定版 2026-11-14；三原则=三秒钩子/半句话主线/广度×热度）
    pool_peer_min_comments: int = 50   # 监控号一手池子线：评论 ≥ 此值直产池子卡（用户拍板，替代拆解闸 OR 线）
    hook_speed_cps: int = 5            # 三秒口径语速（字/秒）→ 钩子/主线字数线 = 3×此值 = 15（时长是法律，字数是代理）
    hook_ban_prefixes: str = ("建议,应该,你要,大家要,记住,千万别,一定要,最好,先,快,应,要,记得,"
                              "试试,赶紧,需要,必须,千万,不妨,最后,今天,你知道,聊聊,说下,讲讲,话说,最近")
    coverage_min: str = "job_wide"     # 覆盖面最低档（boss/middle/job_wide；job_narrow 恒拦——广度原则）
    judge_pass_line: int = 7           # 总法官三维（各 1-10）的过线值，任一维低于即 kill
    daily_card_limit: int = 20         # 每日进靶子箱的卡上限（用户拍板；宁缺毋滥，空过合法）
    jail_window_days: int = 7          # 法官弃卡池可捞窗口（过期物理清）
    pool_pick_max: int = 8             # 二手外站精选条数上限（一手信号不限）

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"


settings = Settings()
if not settings.data_dir.is_absolute():
    settings.data_dir = _REPO_ROOT / settings.data_dir
