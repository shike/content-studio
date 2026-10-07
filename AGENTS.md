# 跃迁内容工作室 · Agent 工作规约

多租户内容生产流水线（登录门禁 + 租户隔离 + 积分池，见 PRD R11/TDD §15）：选题（idea 深研/同行监控拆解）→ 三版口播脚本 → 口播数字人（直接出片）→ 公众号长文 → 发布物料包（发布永远人工）。

- 架构：FastAPI + SQLModel + SQLite（`backend/app/`，22 表）· React 18 + Vite + Tailwind v4（`frontend/`）· Python 3.12（`backend/.venv`，uv 管理）
- 五大文档（英文文件名，全部规格型：不含版本号、不含当前完成情况）：`docs/PRD.md`（需求总账+使用场景，改需求先改它）· `docs/TDD.md`（技术契约）· `docs/TEST.md`（测试方案/验收合同）· `docs/AUDIT.md`（审计规程与检查清单）· `docs/MRD.md`（市场与调研存档）。现状一律看运行中的应用，不写进文档。

## 启动与构建（CWD 必须是仓库根目录）

```bash
# 起后端（8100，绑 0.0.0.0 = 局域网可访问 http://$(ipconfig getifaddr en0 || ipconfig getifaddr en1):8100；8000 被用户另一个项目占用，永远不许杀）
scripts/launchd.sh install   # 正式方式：LaunchAgent 常驻（RunAtLoad+KeepAlive，崩了自动拉起、开机自启）；status/uninstall 同脚本
scripts/start-absolute.sh    # 临时手动方式（nohup 挂的后台进程会随 agent 会话清理被杀，别当常驻用）
# Linux 服务器：sudo scripts/deploy-linux.sh install（systemd 常驻 + 看门狗 timer；Nginx/HTTPS 模板在 deploy/）
# 发服务器唯一正规流程（rsync + 重启 + 健康检查三步一体）：scripts/sync-server.sh
#   —— 服务器是常驻进程，rsync 后不重启 = 照跑旧代码（2026-09-27 头图乱码复发根因），禁止只 rsync 不跑本脚本
# 重启自愈：启动时 queued 任务自动重新入队、running 标 failed（jobs/runner.py recover_interrupted_jobs，须在 runner.start() 之后跑）
# 前端（改完必须 tsc + build，后端服务的是 dist）
cd frontend && npx tsc --noEmit && npm run build
# 一键起：scripts/dev.sh
```

## 验收纪律（违反 = 白干）

- `acceptance/run_p0.py … run_p7.py`（P3 视频生产线已随功能移除；P5=数字人、P6=任务体系、P7=多租户隔离），纯标准库，PASS/FAIL/SKIP 语义，**任何后端改动后必须重跑受影响套件**（"上次全绿"只对当时代码成立——P1 长文截断 bug 就是没重跑漏掉的）。
- **任务失败处置红线（失败中台）**：任何失败任务要么自动恢复（transient 重试/quota 挂起），要么进任务页「待处置」区带诊断线索等人修——修完根因点「标记已修复」再「复活」整批重跑；**待处置非空 = 系统带病运行**（计入工作台任务徽章），不许静默清掉了事。
- 重跑验收用**隔离实例**，别污染用户工作台：登录门禁上线后实例须带确定性口令启动 `DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 uvicorn … --port 8200`，跑套件时 `CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 python3 acceptance/run_pX.py`（runner 自动登录，重活用例可加 `CS_REAPER_SEC=5` 加速 reaper 快巡），跑完删 `data-acceptance`。**复制后必须先清继承队列**：`sqlite3 data-acceptance/studio.db "UPDATE jobs SET status='failed' WHERE status IN ('queued','running','parked')"`——否则验收任务排在真实队列后面必然超时（2026-09-21 P2 三连 FAIL 的根因）。
- 在线拆解项需 `DOUYIN_TEST_URL`（问用户要真实分享链接）。
- 验收产生的测试数据（脚本/文章/记录/fixture 行）**收尾必须清理**；发布假数据会经回流污染选题评分。

## LLM 硬规矩（思考型模型 glm-5.3-flash，Coding Plan 端点）

- **max_tokens 定律**：输出 = 思考链 + 正文，预算按"实测输出 × 2"给。现行值：article_generate 分段式（article_title 8192 + article_outline 8192 + article_section 12000/节 + article_editor 20000 + article_charts 4000，均 thinking disabled）· kg_spec 4000 · poster 三端点 3000/8000/6000 · script_generate/polish 12000 · script_finalize 8000 · idea_research_report/benchmark_analyze 5000 · self_style_extract 12000 · self_profile_update 16000 · research_queries 1024 · style_pick 1024 · trending_topics 4000 · ping 1024。贴上限 → JSON 截断 → 解析失败 → 修复循环 → 任务挂死。
- httpx 超时 300s（打磨实测 346s 曾把 180s 打穿）；验收轮询：常规 ≥600s、脚本生成（三版+自动打磨两段）≥900s。
- 空正文（推理耗尽预算）必须显式报错，不许静默；gateway 已修过"空串当成功返回"的 bug，别改回去。
- Coding Plan 套餐端点 `ZHIPU_BASE_URL` 不含 web_search → `SEARCH_PROVIDER=ddg` 锁定；智谱按量充值后才可切 auto/glm。切模型/检索通道/密钥走 设置页（运行时生效，存数据库 app_settings；.env 只留引导项 host/port/data_dir/安全阈值）。
- 成本台账 llm_calls 按（估算）牌价记，套餐额度下数值是成本上界。

## 前端规矩

- **全站统一范式**：页面默认紧凑表格存档（行内操作、点行展开、计数 chips）；审阅队列（一屏一件、A/S/X 键、审完自动下一条、有"不做"永久出口）一律**手动进入**——任何模块禁止打开页面时自动劫持进队列。共享组件 QueueBar/ChipRow/relTime 在 `frontend/src/components.tsx`。
- **菜单三组两段**：工作台 → 流水线（选题库→脚本工场→口播数字人→公众号→发布台→海报提示词）→ 情报（同行监测/我的风格/拆解库/话题雷达）→ 管理后台/设置/任务沉底；菜单徽章（counts.ts 同源，App 层 60s 刷新）按 **9-28 定版映射**：选题库=待审 · 脚本工场=待定稿 · 公众号=待写长文 · 拆解库=待定夺+待拆 · 我的风格=待定夺 · 任务=待处置（另数字人=生成中）；生成中/排队等过程性数字不上菜单（旧 Q1 收紧映射已作废）。开发里程碑标签（P0~P4）不进菜单。
- **页面六段式骨架**：①页头带（图标+页名+一句话+主操作 ≤2 个）②回显带（进度/结果摘要/错误，全站统一此位置）③筛选计数 chips ④紧凑行列表 + 点行展开（详情两栏：左主体内容、右元信息/历史/联动）⑤空态 EmptyState；内容区 max-w-7xl。
- **UI 操作必须有状态回显**；**降级必须可见**（检索未生效、cookie 兜底等一律显示原因）。禁用 `prompt()`/`alert()`，用行内表单。
- 主色 **moxt 墨绿×白纸（2026-10-02 用户定，参考 drop-harness-framework 后台）**：页面底纯白、侧栏墨绿 #0A2F24（选中=金色光条 #E8C97F→#C9A25C）、卡片白 + 发丝线 #E8ECF0（16px 圆角，hover 只加深描边）、品牌绿 #22C55E 系（主按钮药丸 #29C16A）、文字 #171929。Tailwind v4 `@theme` 重映射 `sky`(=品牌绿系)与 `blue`(=深绿同族)两套色阶，全站 sky/blue 类自动换肤——**换色只改 index.css 的 @theme，别动组件类名**；语义色（emerald/amber/red/teal）不变；btn-primary=近黑药丸、btn-accent=品牌绿药丸。icon=轨道环徽标（public/icon.svg，favicon 同源）；数字用 `.num-serif`（衬线）/`.mono-num`（等宽）工具类。色板与 `.btn-*/.card/.badge/.segment` 见 index.css；改页面前先给交互方案（用户的硬要求）。

## 抖音抓取事实（会过期，改前先实测）

- 单视频页**免登录 可行**：Playwright 拦页面自身的 `aweme/v1/web/aweme/detail`（自带签名），`_ROUTER_DATA` 兜底。分享主页（`share/user/<sec_uid>`）移动端 UA 可渲染出简介+近期作品。
- 博主**主页作品列表被登录墙挡**（资料可见、网格空白、不发 aweme/post）→ 同行扫描用**锚点视频路线**：贴作者任意视频链接，从 detail 定作者 + `mix/aweme` 合集流反推近期作品。路线与降级原因写进任务 notes。
- **搜索页/话题页/推荐流全部登录墙**（PC 搜索、`/hashtag/*`、m.douyin.com 搜索均不出数据）→ **按昵称反查 sec_uid 无解**，新对标号只能：用户在 App 里分享任一视频链接贴进来（锚点路线自动解析作者），或搜索引擎先挖到具体 `/video/` 链接（账号发现的 DDG 路线）。
- **DDG 检索纪律（2026-09-28 更新）**：①**DDG 境内全断**——腾讯云直连 Errno 101，本机家宽直连也超时（此前"限流"后恶化）；唯一通路=**DDG 经住宅隧道出海**：home_proxy 已加 `--upstream 7890`（duckduckgo.com 目标经本机 7890 外网代理 HTTP CONNECT 出海，抖音流量仍直连家宽），search.py 的 DDG 请求走 douyin_proxy 隧道——实测 site:douyin.com/video 能挖到真实链接。②限流仍是 **IP 级惩罚窗**（走 7890 出口后惩罚算在出口 IP 上），纪律不变：每查询最多 2 发（html 202 即换 lite）+ 调用方长周期重试。③**GLM 搜索不吃 site:/inurl: 限定符且不索引抖音站内**（恒空/无关页）——带限定符的查询 search.py 已强制走 DDG；Bing 中国站（cn.bing.com）同样无视 site:，不可用作 site: 备胎。
- **出境与指纹三条硬约束（2026-09-27 服务器部署实测）**：
  ①**UA 必须与运行平台一致**——抖音会校验 UA 与 navigator.platform；Mac UA 跑在 Linux 上视频详情 100% 被挑战（返回空体），换 Linux UA 后 4/4 成功。已实现 `browser.ua()` 平台感知（`DOUYIN_UA` 可覆盖）；
  ②**用完整版 Chromium**（`channel="chromium"`）而非 playwright 的 headless-shell：同环境实测 shell 0/5、完整版 4/4；
  ③**机房 IP 出网被拦**——云服务器直连抖音详情 100% 风控；解法=住宅出口代理：本机 `scripts/home-proxy.sh install`（SOCKS5 + SSH 反向隧道到服务器，launchd 自愈），服务器 `DOUYIN_PROXY=socks5://127.0.0.1:11080`（存数据库配置）。本机关机时该出口不通，任务会明确报"经出口代理 …"并降级提示本地拆解。
- **playwright 浏览器在项目根 `.ms-playwright/`（仅 Mac；2026-09-28 根治清理工具误删）**：browser.py 在 darwin 上写 `PLAYWRIGHT_BROWSERS_PATH`，服务器 Linux 用默认 `~/.cache` 不受影响。用户侧须知：**别手动删项目根 `.ms-playwright/`**（删了用 `PLAYWRIGHT_BROWSERS_PATH=$PWD/.ms-playwright backend/.venv/bin/playwright install chromium` 重装）；`~/Library/Caches/ms-playwright` 已迁移不再使用。
- 视频号无公开网页接口：只支持本地上传拆解。

## 其他红线

- **调研与内容的数据源国内优先（2026-10-02 用户硬要求）**：无论是我做的调研还是流水线生成的内容（深研报告/脚本/长文/图解），数据论据一律优先国内公开数据、国内行业报告、国内企业实践与国内媒体；国外机构数据（MIT/麦肯锡/Gartner 类）只在国内无同类数据时作补充，不作主锚点。我自己的检索查询词也按此 bias（中文源、国内站点优先）。
- 发布动作永远人工（sau 半自动上传已按 PRD R6 移除）。
- 端口 8000 是用户别的项目，**永远不许杀**。
- `data/` 里混着用户真实内容（选题/拆解/台账），删任何行之前先确认归属，删库先备份。
- **数据安全网**：业务库每日自动快照+迁移前快照（`data/backups/`，db.backup_db）；服务死活由看门狗 LaunchAgent 兜底（每 5 分钟探活、自动拉起、macOS 通知）——两者都是 launchd.sh install 的一部分，别卸。**看门狗脚本是安装时拷贝到 `~/Library/Application Support/content-studio/` 的副本**（macOS TCC 禁止 LaunchAgent 读取 Desktop 等受保护目录——实测 /bin/bash 读仓库内脚本报 Operation not permitted，看门狗可静默失效数日；改 `scripts/watchdog.sh` 后必须重跑 `launchd.sh install` 同步副本，status 会提示漂移）。
- 用户侧操作（智谱按量充值、火山/蝉镜控制台开通等）只能用户本人做，别当成代码任务。
