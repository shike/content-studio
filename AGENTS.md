# 跃迁内容工作室 · Agent 工作规约

单管理员流水线（登录门禁，PRD R11/TDD §15）：选题 → 口播脚本（一版精品直出）→ 公众号长文。发布永远人工；数字人成片/发布台/多租户不在系统范围。
架构：FastAPI + SQLModel + SQLite（`backend/app/`，22 表）· React 18 + Vite + Tailwind v4（`frontend/`）· Python 3.12（`backend/.venv`，uv 管理）。
两大文档=规格型（无版本号/完成情况）：PRD 需求总账（**改需求先改它**，业务口径唯一权威）· TDD 技术契约+验收纪律（§13：套件清单/隔离实例/审计对账）。现状只看运行中的应用。

## 启动与构建（CWD 必须是仓库根）

- 后端 8100：`scripts/launchd.sh install`（正式常驻+看门狗）/ `start-absolute.sh`（临时）；Linux `deploy-linux.sh install`；一键起 `dev.sh`。
- 发服务器唯一流程 `scripts/sync-server.sh`（rsync+重启+健康检查；在途任务拒发，`CS_FORCE_RESTART=1` 强发；裸 rsync 不重启=旧代码照跑）。
- 重启自愈（queued 回队/running 标 failed）须在 `runner.start()` 之后。前端改完必 `npx tsc --noEmit && npm run build`。

## 验收纪律（违反 = 白干）

- 套件 `run_p0/p1/p2/p6/p7/p8` + `e2e_run.py`（P8/e2e 必须用 `backend/.venv/bin/python`）。**后端改动必重跑受影响套件**。
- 隔离实例：`DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 uvicorn app.main:app --app-dir backend --port 8200`（加 `CS_REAPER_SEC=5` 加速快巡；跑完删 `data-acceptance`）。复制库先清继承队列：`UPDATE jobs SET status='failed' WHERE status IN ('queued','running','parked')`。
- 全线 naive-UTC，SQL 用 `datetime('now')`。P2：软链 `data/asr-models` + `ASR_MODEL=small`；在线拆解向用户要 `DOUYIN_TEST_URL`。测试数据收尾必清（假数据污染选题评分）。
- 失败是异常不是工作流：瞬时失败走退避梯子 30min→2h→6h→24h→每日慢巡（30 次上限）；确定性失败安静终态；界面只有红徽章+行内重试（后端 failures API 保留）。

## LLM 硬规矩（GLM Coding Plan，思考型）

- max_tokens=实测输出×2，贴上限→JSON 截断→解析死循环。按值分组：**20000**=editor·adapt_write（全库唯一 thinking=enabled）·script_generate/polish｜**16000**=self_profile_update｜**12000**=section（每节）·self_style_extract｜**8192**=title/outline/kb_write/kb_polish/adapt_reduce/adapt_revise；8000=script_finalize/trending_target/self_story_target｜**5000**=idea_research/benchmark_analyze（struct 未传走默认 4096）｜**4000**=charts/kb_brief/deepread_synth/hotspots（+hotspot_keywords 1024 行内预筛）/leadgen_judge｜**2048**=weekly_report｜**2000**=kb_miner/deepread_digest/article_style_extract｜**1024**=review_facts/reader/title_score/quality_gate/research_queries/style_pick/trending_queries/ping。
- httpx 超时 600s；验收轮询 ≥600s、脚本生成 ≥900s。空正文必须显式报错，禁静默当成功。
- Coding Plan 端点无 web_search → `SEARCH_PROVIDER=ddg` 锁定（按量余额不足报 1113）；密钥/模型/检索走设置页运行时改（存 app_settings，.env 只留引导项）。系统不记录成本，花销看服务商控制台。

## 前端规矩

- 页面六段式：页头带（主操作 ≤2）→回显带（全站此位置）→计数 chips→紧凑行列表+点行展开→空态。UI 操作必有回显，降级必须可见（含原因）；禁 `prompt()`/`alert()`。审阅队列一律手动进入（交互契约=PRD R1.5）；共享组件 QueueBar/ChipRow/relTime 在 `components.tsx`。
- 产线树侧栏：页面直挂侧栏可展开收起（`mx-tree-open`），目的地不藏页内 tab。叶子=今天→人设号（今日靶子/内容看板/账号经营/对标拆解/候选定夺/监控清单）·获客号（选题/脚本/候选/监控/打法卡库）·公众号（内容清单/内容工场/风格画像）→任务/设置；旧 SectionKey 经 NAVMAP 兼容路由。**业务口径唯一权威在 PRD（R1.4/R5.6b/R6），本文件不复述。**
- 徽章（counts.ts 同源，60s 刷新）落叶子自身：靶子=待备稿/获客选题=待审/看板·工场=待定稿/对标·卡库=待定夺/候选=待定夺/清单=待改编源文/内容工场=待决策稿/任务=在途（红警只在终态失败亮）/今天=两线待审合计；组行=子叶求和，过程性数字不上菜单。
- 主题 moxt（墨绿×白纸）：**换色只改 index.css 的 `@theme`，别动组件类名**；改页面前先给交互方案。icon=public/icon.svg，数字用 `.num-serif`。

## 抖音抓取事实（会过期，改前先实测）

- **下"平台拦截"结论前先怀疑自己代码**，必须双出口实跑取证。拦截跟指纹一致性走（平台一致 UA+匿名即可），与 IP/登录无关；等待窗必须 60s 轮询+`_ROUTER_DATA` 兜底（短窗会把可用路线跑成空手）。下载失败按页面实际内容分类。
- 单视频页免登录：拦 `aweme/detail`（自带签名）；主页列表有登录墙 → **锚点视频路线**（任一视频链接 detail 定作者+合集反推）。搜索页移动 UA 非登录墙但 XHR 慢抖。按昵称反查 sec_uid 无解。
- 播放数匿名恒拿不到（只有作者后台可见），闸线/评分只落评论与赞；拆解闸=评论≥20 或赞≥300（设置页可调），不过线标 ignored。
- 出口两条链路：`scrape_proxy`=匿名采集（空=机房直连，实测可用；隧道高抖动只作兜底）；`douyin_proxy`=DDG 出海+创作者中心登录态。**登录态红线：cookie 只用于自有账号数据回流，永不爬他人内容。**
- DDG：境内全断，唯一通路=住宅隧道+本机 7890；限流=IP 级惩罚窗，每查询 ≤2 发；GLM 搜索不吃 site: 不索引抖音，Bing 中国站不可作备胎。
- 本机无 data/ 与 .ms-playwright/（本地验收属临时）——重建：`PLAYWRIGHT_BROWSERS_PATH=$PWD/.ms-playwright backend/.venv/bin/playwright install chromium`；ASR 模型同法重下（small 优先）。依赖 av 钉 `<19`（19 断转写）。视频号无网页接口，只支持本地上传。

## 其他红线

- 数据源国内优先（硬约束）：论据/检索中文源优先，国外数据仅补位不作主锚。
- 发布永远人工。端口 8000 是用户别的项目，**永远不许杀**。
- `data/` 混真实内容：删行先确认归属，删库先备份；`data/backups/` 安全网别动。
- 改 `watchdog.sh` 后必须重跑 `launchd.sh install` 同步副本（副本在 `~/Library/Application Support/content-studio/`）；探活=连续 2 次失败升级，有 running 连续 6 次才强拉。
- 智谱充值/控制台开通等只能用户本人操作。
