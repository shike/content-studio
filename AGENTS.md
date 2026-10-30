# 跃迁内容工作室 · Agent 工作规约

单管理员内容生产流水线（登录门禁，PRD R11/TDD §15；多租户/积分体系已于 2026-10-27 拆除）：选题 → 三版口播脚本 → 公众号长文 → 发布物料包（发布永远人工；口播数字人功能已于 2026-10-29 整体退役）。

- 架构：FastAPI + SQLModel + SQLite（`backend/app/`，24 表）· React 18 + Vite + Tailwind v4（`frontend/`）· Python 3.12（`backend/.venv`，uv 管理）
- 五大文档（英文文件名，规格型：不含版本号、不含完成情况）：`docs/PRD.md`（需求总账，改需求先改它）· `docs/TDD.md`（技术契约）· `docs/TEST.md`（验收合同）· `docs/AUDIT.md`（审计规程）· `docs/MRD.md`（市场调研存档）。现状只看运行中的应用。

## 启动与构建（CWD 必须是仓库根目录）

- 后端（8100，绑 0.0.0.0）：正式 `scripts/launchd.sh install`（LaunchAgent 常驻 + 开机自启 + 看门狗）；临时 `scripts/start-absolute.sh`。Linux：`sudo scripts/deploy-linux.sh install`。
- **发服务器唯一流程 `scripts/sync-server.sh`**（rsync + 重启 + 健康检查三步一体；裸 rsync 不重启 = 旧代码照跑；有在途任务拒发，`CS_FORCE_RESTART=1` 强发）。
- 重启自愈：queued 重新入队、running 标 failed（须在 runner.start() 之后跑）。
- 前端改完必须 `cd frontend && npx tsc --noEmit && npm run build`（后端服务 dist）。一键起 `scripts/dev.sh`。

## 验收纪律（违反 = 白干）

- `acceptance/run_p0.py…run_p8.py`（纯标准库；run_p5 已随数字人退役删除；P6=任务 P7=认证与单管理员）。**任何后端改动后必须重跑受影响套件**。
- 任务失败处置（2026-10-30 用户定版：**失败是异常不是工作流**）：瞬时/风控/额度类失败走退避梯子（30min→2h→6h→24h），梯子尽头转**每日慢巡**（24h 一试直到自愈，30 次硬上限后安静终态）；确定性失败不重试安静终态。界面无失败工作流（指纹聚类/标记已修复/复活整批已移除，后端 failures API 保留）：失败行=红徽章+错误+行内重试，侧栏徽章含失败数。任何失败要么自愈要么安静可见。
- 隔离实例（别污染工作台）：`DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 uvicorn app.main:app --app-dir backend --port 8200`；跑套件 `CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 python3 acceptance/run_pX.py`（可加 `CS_REAPER_SEC=5` 加速快巡）。跑完删 `data-acceptance`。
  - 复制后先清继承队列：`UPDATE jobs SET status='failed' WHERE status IN ('queued','running','parked')`——否则验收任务排在真实队列后面必然超时。
  - 手拨/判读到期的任务一律按 UTC（runner 全线 naive-UTC，SQL 用 `datetime('now')`）。
  - 本地跑 P2：软链 `data/asr-models` 进 `data-acceptance/` + `ASR_MODEL=small`（large-v3 在 Mac CPU 拖死 600s 超时）。
- 在线拆解项需 `DOUYIN_TEST_URL`（向用户要真实分享链接）。验收产生的测试数据收尾必须清理（发布假数据会污染选题评分）。

## LLM 硬规矩（思考型模型，Coding Plan 端点）

- **max_tokens = 实测输出 × 2**：贴上限 → JSON 截断 → 解析失败 → 修复循环 → 任务挂死。现行值：article 分段式（title 8192 / outline 8192 / section 12000/节 / editor 20000 / charts 4000，均 thinking disabled）· kg_spec 4000 · poster 3000/8000/6000 · script_generate/polish 20000 · script_finalize 8000 · idea_research/benchmark_analyze 5000 · self_style_extract 12000 · self_profile_update 16000 · research_queries 1024 · style_pick 1024 · trending_topics 8000 · trending_queries 1024 · discover_keywords 1024 · ping 1024。
- httpx 超时 300s；验收轮询常规 ≥600s、脚本生成（三版+自动打磨）≥900s。空正文（推理耗尽预算）必须显式报错，禁静默当成功。
- Coding Plan 端点无 web_search → `SEARCH_PROVIDER=ddg` 锁定；密钥/模型/检索通道走设置页运行时改（存 app_settings，.env 只留引导项）。
- 成本台账已拆除（2026-10-27 用户裁定 2B）：系统不再记录任何成本；要花销概算直接看服务商控制台。

## 前端规矩

- 页面六段式：①页头带（图标+一句话+主操作 ≤2）②回显带（进度/结果/错误，全站此位置）③计数 chips ④紧凑行列表+点行展开（详情两栏：左内容右元信息）⑤空态 EmptyState。内容区 max-w-7xl。
- UI 操作必有状态回显；降级必须可见（含原因）。禁 `prompt()`/`alert()`，用行内表单。
- 审阅队列（一屏一件、A/S/X 键、审完自动下一条、有"不做"出口）一律**手动进入**，禁止打开页面自动劫持。共享组件 QueueBar/ChipRow/relTime 在 `frontend/src/components.tsx`。
- 菜单三组两段（2026-10-28 IA 重构定版）：每日动线（今日工作台）→ 内容产线（人设号产线/获客号产线）→ 情报·自动（情报中心/发布台）→ 沉底（任务/设置）。徽章（counts.ts 同源，60s 刷新）= 待办计数：选题库=待审 · 脚本工场=待定稿 · 公众号=待写长文 · 拆解库=待定夺+待拆 · 我的风格=待定夺 · 任务=待处置（无待处置时显示在途数）；生成中/排队等过程性数字不上菜单。P0~P4 里程碑标签不进菜单。
- 主题 moxt 墨绿×白纸：页面纯白、侧栏 #0A2F24（选中金光条 #E8C97F→#C9A25C）、品牌绿主按钮药丸 #29C16A、卡片白+发丝线 #E8ECF0（16px 圆角）、文字 #171929。Tailwind v4 `@theme` 重映射 sky/blue 两套色阶实现全站换肤——**换色只改 index.css，别动组件类名**；语义色不变。**改页面前先给交互方案**。
- icon=轨道环徽标（public/icon.svg），数字用 `.num-serif`/`.mono-num` 工具类；btn-primary=近黑药丸、btn-accent=品牌绿药丸。

## 抖音抓取事实（会过期，改前先实测）

- **拦截跟"指纹一致性"走，不跟 IP 也不跟登录**：跨平台 UA 伪装（Mac UA 跑 Linux）才是数据被扣的原因；平台一致 + 匿名即可完整下载（2026-10-27 定论）。此前"被封禁/风控"全部是代码误报，四个人造源已修：跨平台伪装自造被扣、代理断连误标验证码、熔断级联放大、**等待窗太短（12s/5s < 隧道下 XHR 实际 22~23s，2026-10-28 定论，现已 60s 轮询）**；下载器失败按页面实际内容分类（毒视频→「视频已删除」/真验证码=标题命中/慢加载）。**下"平台拦截"结论的铁律：默认先怀疑自己代码，必须生产代码双出口实跑拿原始证据。**
- 单视频页免登录可行：Playwright 拦页面自身 `aweme/detail`（自带签名），`_ROUTER_DATA` 兜底；用完整版 Chromium（`channel="chromium"`）非 headless-shell。主页作品列表被登录墙挡 → 同行扫描走**锚点视频路线**（贴作者任一视频链接，detail 定作者 + `mix/aweme` 合集反推近期作品）。
- 搜索页/话题页/推荐流全部登录墙，按昵称反查 sec_uid 无解——新对标号只能用户在 App 贴视频链接，或 DDG 挖具体 `/video/` 链接。
- **出境分两条链路（2026-10-29 拆分）**：①**匿名采集（抓详情/下载直链/探针）走机房直连**——`scrape_proxy` 空=直连，实测可用（3/3，含此前隧道下弹验证码的视频）；曾被写为"机房 IP 直连 100% 被风控"的结论已被直连实验推翻（大概率是当时跨平台 UA 污染了实验）。住宅隧道（本机 `scripts/home-proxy.sh install`，服务器曾配 `socks5://127.0.0.1:11080`）高抖动（XHR 6~62s、传中断连）才是采集失败主因，隧道保留作采集兜底：设置 `scrape_proxy` 即切回。②**douyin_proxy 语义收窄**：只剩 DDG 搜索出海（直连必死）+ 创作者中心登录态采集（出口 IP 与账号日常环境一致性是刻意设计）。Mac 关机只影响这两条，匿名采集不受影响。**登录态红线：创作者中心 cookie 只用于自有账号数据回流，永不参与对他人内容的爬取。**
- **DDG**：境内全断，唯一通路=经住宅隧道 + 本机 7890 出海；限流是 IP 级惩罚窗，每查询 ≤2 发（html 202 即换 lite）+ 调用方长周期重试；GLM 搜索不吃 site: 且不索引抖音（带限定符查询强制走 DDG），Bing 中国站无视 site: 不可作备胎。
- 本地实例已退役（2026-10-27 用户裁定）：本机无 data/、无 .ms-playwright/，本地跑验收属临时行为——需要 Playwright 时 `PLAYWRIGHT_BROWSERS_PATH=$PWD/.ms-playwright backend/.venv/bin/playwright install chromium` 一键重建；ASR 模型同法重下（small 优先）。生产服务器浏览器在服务器自己的缓存，与本机无关。Python 依赖 av 钉 `<19`（19 移除 faster-whisper 在用的参数，会打断转写）。
- 视频号无公开网页接口，只支持本地上传拆解。

## 其他红线

- **调研与内容数据源国内优先**（用户硬要求）：数据论据优先国内公开数据、行业报告、企业实践与媒体；国外机构数据仅在国内无同类时补充，不作主锚点；检索查询词同样中文源优先。
- 发布动作永远人工。端口 8000 是用户别的项目，**永远不许杀**。
- `data/` 混着用户真实内容，删任何行之前先确认归属，删库先备份；正式安全网=每日自动快照+迁移前快照（`data/backups/`），别动。
- 看门狗副本装在 `~/Library/Application Support/content-studio/`（macOS TCC 禁 LaunchAgent 读 Desktop 仓库）——**改 `scripts/watchdog.sh` 后必须重跑 `launchd.sh install` 同步副本**，status 会提示漂移。
- 用户侧操作（智谱充值、控制台开通等）只能用户本人做，别当代码任务。
