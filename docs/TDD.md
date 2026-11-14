# TDD · Technical Design Document（技术设计）

> 技术契约（预期侧）：架构 / 数据模型 / API 契约 / 模块设计 / 部署 / 验收对照。
> 契约变更必须同步 PRD.md 与 acceptance/（脚本即契约）。

# Content Studio · 技术方案

> 定位：实现前技术定稿。API 契约、数据模型以本文为准；各期验收标准以 `acceptance/` 脚本为准（脚本即契约的可执行形式）。

---

## 1. 总体架构

```
┌──────────────────────────── 本机 / 局域网 ────────────────────────────┐
│                                                                      │
│  浏览器 ──► Content Studio Web 站 (:8100，登录门禁)                   │
│              │  FastAPI (backend/)     React 构建产物静态托管         │
│              │    ├─ api/  REST 路由（认证 / 业务）                  │
│              │    ├─ auth  会话（单管理员，ACTOR 作用域）             │
│              │    ├─ jobs   进程内异步任务（下载/ASR/渲染/LLM长任务） │
│              │    ├─ SQLite data/studio.db（单库一切）                │
│              │    └─ data/  上传素材、配图、媒体、备份              │
│              │                                                       │
│              ├──► GLM API（智谱：生成主通道 + 独立搜索端点）          │
│              ├──► 本地引擎 faster-whisper（转写） │
│              │    / FFmpeg                                          │
│              └──► 人工发布（发布永远人工，R6.5）                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

关键决策：

- **复用边界**：下载/上传走外部服务或浏览器直连（零代码耦合）；ASR 走 pip 库；公众号排版=自研 `renderer.py`（思路参考 doocs/md）。自研仅为 Web 控制台 + 工作流编排 + 业务 Prompt 库。
- **进程内任务框架**：不上 celery/redis/message queue，后台任务用进程内 asyncio + 线程池（ASR/FFmpeg 是 CPU 密集），任务状态落 `jobs` 表，前端轮询。
- **单管理员**（契约见 §15）：登录门禁保留，作用域为 tenant_id=1；多租户/成员/积分/成本台账不在系统范围。
- **发布永远人工**：系统不触碰任何发布动作，无任何自动发布代码路径（发布台/物料包不在系统范围）。

## 2. 技术栈与版本基线

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.12+ / FastAPI / uvicorn | 绑定 0.0.0.0（局域网可访问，登录门禁必开） |
| ORM | SQLModel + SQLite | WAL 模式 |
| LLM | 智谱 GLM 单通道（默认 `LLM_MODEL=glm-5.3-flash`，设置页可改 glm-5.3） | 唯一 LLM 通道，无备选 provider |
| ASR | faster-whisper（large-v3 起步 + VAD） | 中文场景可换 FunASR SenseVoice，接口不变 |
| 视频 | FFmpeg（brew） | 工具箱角色 |
| 前端 | React 18 + Vite 5 + TailwindCSS | 中文界面；构建产物由 FastAPI 托管 |
| 发布 | 人工（发布永远人工，在系统外完成） | 无任何发布平台集成（R6.4 范围外） |
| 验收 | acceptance/ 纯标准库 Python 脚本 | 见 §13 |

## 3. 目录结构

```
content-studio/
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口 + 静态托管 frontend/dist
│   │   ├── settings.py         # pydantic-settings 读 .env
│   │   ├── api/                # 路由：auth/health/topics/scripts/articles/jobs/llm/schedules/radar/watch/style/benchmarks/metrics/stats/settings/crawl/public_media/tenant_api
│   │   ├── auth.py             # 口令哈希 / 会话 / 租户上下文（ACTOR）
│   │   ├── db.py               # 引擎 + 迁移 + 作用域三件套（单用户退化）
│   │   ├── models/             # SQLModel 表定义（§5）
│   │   ├── jobs/               # 任务框架：提交/轮询/线程池执行器
│   │   ├── llm/                # GLM 网关：max_tokens 预算、ping
│   │   ├── topics/             # idea深研 / 评分
│   │   ├── benchmarks/         # 同行拆解：下载→ASR→LLM拆解
│   │   ├── scripts/            # 脚本工场：模板库 / 一版精品直出 / 批判打磨
│   │   ├── articles/           # 长文生成 / renderer.py 渲染器 / ima 知识库取材与源文改编线
│   │   ├── selfstyle/          # 自我风格研究：self_scan / 风格画像 / 创作者数据回流
│   │   └── prompts/            # Prompt 模板（md 文件 + 版本号，代码加载）
├── frontend/                   # React + Vite + Tailwind
├── acceptance/                 # 验收脚本（本文档 §13 的可执行形式）
├── scripts/                    # 部署运维脚本（launchd.sh 动态生成 LaunchAgent）
├── data/                       # studio.db + backups/ article_images/ cache/ crawl_probe.json asr-models/ self_videos/ benchmarks/
├── docs/
└── .env.example
```

## 4. 配置与密钥（分层：.env 引导项 + 数据库运行时项）

**分层原则**：`.env` 只放"进程启动前就必须知道"的引导/部署项；其余运行时可改项存数据库
（`app_settings` 键值表，随每日备份进快照，服务器上无需改文件）。数据库值优先于 .env 默认值；
密钥类只写不读（GET 只回报是否已配置）。

| 键 | 默认 | 说明 |
|---|---|---|
| 启动端口 | 由启动脚本直传 uvicorn | Settings 无 host/port 字段——`start-absolute.sh`/launchd 绑 `0.0.0.0:8100`，`dev.sh` 绑 127.0.0.1，Linux `deploy-linux.sh` 默认 127.0.0.1 走 Nginx 反代（8000 被其他项目占用） |
| `DATA_DIR` | ./data | 一切落盘根目录 |
| `ZHIPU_API_KEY` | — | 主 LLM 密钥 |
| `LLM_MODEL` | glm-5.3-flash | 设置页运行时可改（可选 glm-5.3-flash / glm-5.3） |
| `ASR_MODEL` | large-v3 | faster-whisper 型号 |
| `SEARCH_PROVIDER` | auto | 检索通道：auto=智谱搜索优先 DDG 兜底 / ddg / glm（按量余额可用；设置页运行时切换） |
| `ZHIPU_BASE_URL` / `ZHIPU_SEARCH_BASE_URL` | 智谱 | 生成端点与独立搜索端点（`/web_search`）可分账 |
| `CS_BOOTSTRAP_PASSWORD` | — | 首次启动引导管理员的确定性口令（验收实例必带；不设则随机生成仅日志打印） |
| `CS_REAPER_SEC` | 1800 | reaper 巡逻间隔（验收隔离实例设 5 加速快巡） |
| `CS_MAX_ACTIVE_PER_TENANT` | 30 | 每租户活跃任务上限（提交配额，见 §7） |

`.env` 不进版本库；`health` 接口回报各依赖配置状态（不回传密钥）。

**数据库运行时配置（`app_settings`，受管键登记于 `settings_store.MANAGED`，启动载入即生效）**：
`llm_model / zhipu_api_key / zhipu_base_url / search_provider / search_enabled / zhipu_search_base_url /
asr_model / scrape_proxy / douyin_proxy / douyin_ua / watch_scan_enabled / watch_scan_hour /
creator_data_enabled / ima_kb_enabled / dissect_comment_min / dissect_digg_min /
pool_peer_min_comments / hook_speed_cps / hook_ban_prefixes / coverage_min / judge_pass_line /
daily_card_limit / jail_window_days / pool_pick_max`（选题工场参数，2026-11-14）。
载入时机=启动时（init_db 之后 `settings_store.load_overrides()`）；改 ASR 模型会清进程内模型缓存。
设置接口/页面只暴露其中一部分（模型/检索通道/扫描计划/拆解闸线/选题工场参数/采集出口），其余键直接改表。
另有**不在受管登记内的手工配置键**（直接改 app_settings 表，随用随读）：ima 凭证三键
`ima_client_id / ima_api_key / ima_kb_ids` 与 `ima_topic_folders`、
`article_style_note` 等——文档口径为「DB 手工配置」，接口与界面不提供编辑入口。

## 5. 数据模型（SQLite）

通用约定：`id` 自增主键；`created_at/updated_at` 自动；JSON 字段用 TEXT 存 JSON；
业务表带 `tenant_id`（单用户版恒为 1，作用域机制保留见 §15）。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `topics` | title, angle, line(persona/leadgen 两线硬隔离), audience(boss/fde/both), source_type(idea/benchmark/manual/trending/self_story), source_ref, evidence(JSON), score, score_breakdown(JSON 仅内容评分维度), status(draft/approved/rejected/produced) | 选题库（idea/快速/拆解候选/获客线）；评分不含发布数据回流（R1.6 不做）；人设号靶子
  2026-11-14 起走 topic_cards（选题工场） |
| `topic_cards` | batch_date, source(douyin_peer/radar/douyin_hot/external/self_story/manual),
  pool(JSON 四证), spine, hook, spice, takeaways(JSON 评论要点≤3条=主线原料留痕), judge(JSON 三维+verdict+reason), gates(JSON 程序闸记录),
  status(draft/approved/discarded/jail), superseded_by, script_id | 人设号靶子卡（选题工场，R1.4；
  jail=法官弃卡 7 天可捞） |
| `falsify_items` | claim(主张≤60), target(对象≤40), evidence_note(线索自由文本),
  result_note(实测结论自由文本=成稿论据来源), status(pending/testing/tested/scripted/dismissed),
  script_id | 实测打假清单（R1.4③；实测=物理动作，系统管清单与成稿） |
| `research_reports` | topic_id(FK), content(MD), model | idea 深研产物 |
| `benchmark_videos` | url, author, title, stats(JSON), transcript, analysis(JSON), top_comments(JSON 高赞评论原文[{t,d}]≤20条), media_path, source(douyin/local) | 同行拆解样本 |
| `self_videos` | url, aweme_id, title, transcript, style_analysis(JSON), analyzed_at, published_at, duration, stats(JSON) | 我的账号视频 + 逐条风格标注（R9.2） |
| `account_metrics_daily` | tenant_id, date, fans/new_fans/cancel_fans, profile_views, play, digg, comment, share, account_search, post_search | 自有账号日级运营指标（创作者中心 overview 回流，按日列合并不整行覆盖；每日 4 点轻活，kill switch=creator_data_enabled）（R9.7） |
| `video_weekly_reports` | tenant_id, week_start, report(JSON) | 视频表现周报（质量分=收藏率×3+转发率×3+点赞率×1.5+评论率×2+播放规模封顶3；LLM 解读按周缓存）（R9.8） |
| `style_profiles` | tenant_id, account_name, version, digest, traits(JSON), exemplars(JSON), based_on | 风格画像版本快照，保留历史（R9.3） |
| `scripts` | topic_id?(FK), card_id?(FK 靶子卡备稿), falsify_item_id?(FK 实测稿备稿), versions(JSON:[{label,hook,body,notes}]), final_text, teleprompter_text, storyboard(JSON), title_candidates(JSON), status(generating/polishing/generated/final), account_type(persona/leadgen) | 一个选题一个脚本对象（一版精品直出，R2.1；generated=待定稿，徽章计数口径） |
| `script_templates` | name, structure(JSON), source_benchmark_id, usage_count | 结构模板库（从拆解沉淀） |
| `articles` | topic_id?(FK), script_id?(FK), title, note(素材备注), md, html, kb_sources(JSON ima 素材来源), status(pending/generating/failed/draft/edited/rendered/published) | 公众号文章（pending=直出链建稿，failed 可重试） |
| `content_items` | media_id(索引防重), kb_id/folder, title, source_author, quality_score, why_good, adapt_angle, best_quotes(JSON), category, priority, status(pending/running/done/published/dismissed/low), article_id | 内容清单=知识库挖出的高质量源文（挖掘器≥线入清单，改编器取项成文；低分落 low 防回卷重评）（R5.6b 源文改编流） |
| `daily_ledger` | day(UTC), phase(mine/adapt), produced, reason, ok | 产线当日账（防「成功但空转」；anomaly=ok 但无产出无原因，按故障处理） |
| `tenants` | name, brand(JSON 九字段) | 工作室档案（单用户版恒 1 行；R11.2） |
| `users` | tenant_id(FK), username(全局唯一), password_hash(scrypt), role(platform_admin/tenant_admin/member), display_name, status, must_change_password | 租户成员 |
| `user_sessions` | token_hash(唯一), user_id, tenant_id, expires_at | 服务端会话（Cookie 只带明文 token） |
| `radar_topics` | tenant_id, ch_id(全局唯一), enabled, notify, seen_ids/recent/hits(JSON), last_checked_at | 话题雷达监控项 |
| `failure_patterns` | fp(唯一), kind, fail_class, label, count, sample_error, status(open/fixed/ignored) | 错误指纹库（失败中台） |
| `schedules` | key, label, enabled, hour 覆盖, last/next_run_at, last_status, run_count | 统一调度中心运行态 |
| `app_settings` | key(主键), value(JSON) | 平台运行时配置（LLM/检索/ASR/采集出口；密钥只写不读） |
| `watch_accounts` | tenant_id, platform, name, url, note, kind(competitor/self), creator_cookie(self 创作者中心登录态), enabled, last_scan_at | 对标/自有账号（R3.1/R9.1）。评分口径（§6 scores 解析按此执行）：相关性=贴合「赛道主题×口播×短视频获客」三要素；质量=近期互动（近 10 条作品均赞/峰值）+内容实质与可模仿度；综合=相关性×0.6＋质量×0.4；监控门槛：质量分低于阈值（示例 7.5）移除监控 |
| `watch_candidates` | tenant_id, keyword, direction, name, sec_uid, follower_count, sample_*, status(open/added/dismissed) | 关键词发现的对标账号候选（R3.6） |
| `jobs` | type, status(queued/running/succeeded/failed/superseded), progress, message, payload/result/history(JSON), error, dedup_key, lane(heavy/light 两道), retry_count/max_retries/next_retry_at, fail_class, error_fp, archived, retry_of, tenant_id, user_id | 统一异步任务（五要素 + 归属；tenant_id=0 为系统任务） |

**生产库遗留（已删模型/端点的旧数据原地保留，新库不再建；负向契约见 §13）**：旧表 `kb_topics`/`kb_documents`/`harvest_profiles`/`publish_records`/`media_assets`/`dismissals`（模型已删；publish_records 仍受 hygiene.py ② 假数据巡检）、已 DROP 表 `llm_calls`/`bean_ledger`/`credit_transactions`/`tenant_memberships`、旧列 `tenants.credits`/`tenants.status`/`research_reports.tokens_in,tokens_out,cost_est`/`watch_candidates.leadgen_comments,recent_same`/`radar_topics.recent,new_since_last`/`articles.title_alts,kb_doc_ids`。更早的未登记遗留列不逐一维护。

**内容资产状态机**（串联各表的状态流转）：

```
topic:      draft ──approve──► approved ──产出脚本──► produced
                └──reject───► rejected
script:     generating ──► polishing ──► final
article:    pending（直出链建稿）──► generating ──► draft ──► edited ──► rendered ──► published
                └──失败──► failed（可重试回 generating）
```

## 6. API 契约

约定：JSON in/out；异步操作统一返回 `202 {job_id, ...}`，前端轮询 `GET /api/jobs/{id}`；错误返回 `{"detail": "..."}` + 恰当状态码。

### 认证（/api/auth，全站门禁）
- `POST /api/auth/login` `{username, password}` → Cookie 会话；停用用户 401；响应含 tenant_name（工作室名）
- `POST /api/auth/logout` / `GET /api/auth/me` / `POST /api/auth/change-password` `{old_password, new_password}`
- 未登录访问业务接口一律 401；`/api/health` 与登录接口豁免

### 基础
- `GET /api/health` → `{status:"ok", app, version, db:true, llm:{provider, model, configured}, deps:{playwright}}`
- `POST /api/llm/ping` `{prompt}` → `{reply}`（最小真实调用，验收用）
- `GET /api/jobs?status=&type=&limit=&offset=&tenant_id=` → `{items, total, counts{...}}`（运行中置顶、排队带 queue_position；
  列表不含 result、不含已归档行；非平台管理员强制按本租户过滤，tenant_id 参数仅平台管理员可用）
- `POST /api/jobs/{id}/retry` → 202 `{job_id}`（人工重试：原记录标 superseded、清零自动重试计数、实体先回在途态）
- `POST /api/jobs/retry-failed {type?}` → 202 `{requeued:n}`（批量重试，跳过 deterministic 与已归档）
- `DELETE /api/jobs/{id}` → 归档任务记录（archived=1，不物理删；running 拒绝 400）
- `POST /api/jobs/clear` `{status: failed|superseded|succeeded}` → `{archived:n}`（批量归档终态记录）
- `GET /api/jobs/{id}` → job 终态或进行态（含 `history` 阶段轨迹）；不存在 → 404 JSON
- `GET /api/stats/todos` → 待办统计（服务端真值口径：今日靶子=靶子卡 draft 数/两线待审选题/待定稿脚本/内容工场待决策稿/两线候选待定夺，侧栏徽章与今天页同源）

### 设置（/api/settings，仅平台管理员）
- `GET /api/settings` → LLM 通道/检索通道/扫描计划/各配置态（不回传密钥）
- `PUT /api/settings` → 运行时写库（模型/检索通道/扫描钟点；非法值 400）
- `GET|PUT /api/tenant/brand` → 品牌九字段自助读写（设置页品牌卡）

### 选题中心
- `POST /api/topics/ideas` `{text}` → 202 `{topic_id, job_id}`（深研任务）
- `POST /api/topics/quick` `{title, audience?}` → 201（快速记选题，不触发 LLM；R1.1b）
- **人设号靶子旧产线已退役（2026-11-14，选题工场替代，契约见「选题工场」节）**：调度
  `trending_daily`/`self_story_daily`、执行器 `trending_topics`/`self_story_targets`、
  prompt trending_target_industry/trending_target_judge/self_story_target、
  `POST /api/topics/trending` 端点均删；负向契约见 §13。`PUT /api/topics/{id}/angle`
  保留（idea/获客/拆解候选线仍用）。
- 入库时相似选题检测（R1.1c）：与既有 draft 选题标题相似 → 标记 evidence.similar_to
- `GET /api/topics?status=&source=&audience=&min_score=&limit=` → `{items:[...]}`
- `GET /api/topics/{id}` → 详情含 `research_report`
- `POST /api/topics/{id}/approve` / `POST /api/topics/{id}/reject`
- `PUT /api/topics/{id}/angle` `{angle}` → 靶子备稿选主切角（仅 draft 态；脚本生成读 topic.angle）
- `POST /api/topics/{id}/set-type` `{account_type: persona|leadgen}` → 切换选题线（topics.line 落库，evidence 记 account_type_manual）
- `DELETE /api/topics/{id}` → 删选题并连带删研究报告；文章保留仅解绑 `topic_id`
- `POST /api/analyze/video` `{url}` → 202 `{job_id}`（下载→ASR→LLM 拆解，产出 benchmark + topic 候选）
- `POST /api/analyze/local`（multipart file）→ 202 `{job_id}`（跳过下载，其余同上；手工下载视频走此口）
- `GET /api/benchmarks?limit=&offset=&author=&analyzed=(done|todo)&source=` → `{items, total, counts{all,pending,todo,done,ignored}}`（counts 全量口径；上限 100/页）
- `GET /api/benchmarks/authors` → `{authors:[...]}`（全库作者 distinct，供筛选下拉）
- `DELETE /api/benchmarks/{id}` → 删样本并连带撤回其待审选题候选（返回 `removed_candidates`）
- `POST /api/benchmarks/{id}/resolve` `{action(approve|ignore|pending)}` → 人工定夺（approve 即排队拆解）
- `POST /api/benchmarks/resolve-pending` `{action, author?}` → 批量定夺当前待定夺行（可按作者过滤）

**拆解质量闸**：
- 闸线 = **评论 ≥ `dissect_comment_min`(默认 20) 或 赞 ≥ `dissect_digg_min`(默认 300)**，任一满足即自动批准拆解；两条线均设置页运行时可调（MANAGED int）。
- 数据边界：评论/赞/转/藏匿名可取；**播放数恒 0**——抖音网页端只有作者本人在创作者后台可见，
  匿名通道拿不到，闸线只落评论/赞。
- 流程：待定夺行评论缺失时先 `downloader.probe_video_stats` 补探（每条一次页开，60s 截止）→ 闸判定 → 过线 approved 并排队 `benchmark_analyze`；不过线标 **ignored 保留行**（notes 记判定理由，页面上可见，手动 approve 可强制拆）；探测失败按赞线单判并如实注「评论?」。
- 覆盖三条自动路径：watch_scan 新增行、watch_scan 存量 pending 清扫、话题雷达自动拆解（同一 `auto_dissect_rows` 入口，无旁路）。
- `quality_score` 热度分 = `评论/2 + 赞/60` 封顶 10，公式与闸线同源透明；闸判定时回写。
- **高赞评论存档**（2026-11-14）：`benchmark_analyze` 下载后拦 `aweme/v1/web/comment/list/`
  XHR（免登录；评论面板需一次滚动唤醒，60s 截止轮询——2026-11-02 服务器实测）抓首屏热序
  评论，去重按赞排序取 top20（`[{t≤60字, d=赞数}]`）落 `benchmark_videos.top_comments`；
  抓取失败不挡拆解（评论是增益不是产线）。此存档是选题工场主线观点的唯一矿脉（见 §6）。

### 选题工场（人设号靶子卡，R1.4——2026-11-14 三原则定版）

**三原则**：①三秒钟=钩子吸引力装置（观点说清是底线，抓人是目的；时长 ≤3 秒，字数线=3秒×
`hook_speed_cps`（默认 5 字/秒）=15 字，计长剔除标点）②半句话=视频主线，全程论证但理论上
不出口（不进台词/字幕/收尾）③广度×热度=域内×覆盖人口×此刻有流量。

**调度 `card_line_daily`（每日 6 点）→ 执行器 `card_daily`（单任务五段进度，失败走退避梯子；
当日无卡=诚实空过）**：
- **车间零·信号采集**（程序，0 LLM）：话题雷达巡检（收编自旧 trending 内联驱动；低粉高赞
  自动拆解同口径不变）+ 监控对标号近 24h 新视频互动实数 + 抖音热点榜 + 外站五路
  （百度/知乎/科技RSS/GitHub/AIHot）。
- **车间一·池子构建**：一手直产（程序）——监控号新视频 **评论 ≥`pool_peer_min_comments`
  （默认 50，设置页可调）** 过线→池子卡（douyin_evidence=互动实数）；雷达飙升→池子卡
  （spike 速度）；抖音榜→池子卡。二手精选（pool_pick 1 次 LLM ≤`pool_pick_max`(8) 条，
  产 domain_evidence）。**池子闸（程序）**：域内证据非空（词根表兜底）/coverage≠job_narrow/
  一手免检热度、二手挂 secondary。对标圈素材禁当靶子（纪律不变）。
- **车间二·主线锻造**（spine_forge 1 次 LLM，max_tokens 4000）：输入=由头/域内证据＋
  **拆解要点＋高赞评论原文**（douyin_evidence.top ≤12 条带赞数；无评论的池子只有 60 字截断
  →素材薄可跳过，manual 投喂必须锻出）。每池子卡产 {spine, takeaways, role, spice}：
  先从评论原文抽**评论要点**（大众最强烈的关注点/痛感，≤3 条留痕卡上可检验，提炼不是编造）→
  主线从要点长出来（**共鸣**=替大众说狠 / **错位**=顶大众读法说；检验=必须是评论区里没人
  说出来的那句话，复述评论原文=废）。纪律=单判断/主语=受众角色（公司事件只当论据）/可争议。
  **主线闸（程序）**：15 字线+14 天撞车（归一化 vs 近两周已产卡+已发脚本标题）+批内去重。
- **车间三·钩子铸造**（hook_cast 1 次 LLM）：每主线产 1 钩子，prompt 附拆解库近 30 天高分
  钩子样本作 few-shot（向被抖音验证过的句式靠）。**三秒闸（程序）**：15 字线+劝告/悬念/引入
  词根表（`hook_ban_prefixes` 可编辑）。
- **总法官**（judge_cards 1 次 LLM 批量）：三维 1-10——hook_pull（利害/点名/冲突占其一）/
  spine_single/traffic_potential（一手证据满分起评、二手卡低起评），任一维
  <`judge_pass_line`(默认 7)=kill。
- **落卡**：pass 按总分排序取前 `daily_card_limit`(默认 **20**) 张 draft；kill 落 jail
  （`jail_window_days`=7 可捞）；批次替代（当日无下游旧 draft 让位，approved 不动）；空过合法。
  每日 LLM 共 5 次（pick/forge/cast/judge/hunt——尾段自动猎取实测候选 ≤3 条：拆解库分析+当日热源里挑「一人可实测验证」的主张，claim 归一化去重 ≥0.55 拦；纯观点争议不猎，归靶子卡）。
- **手动投喂**：`POST /api/cards/seed {text, url?}` → 直接成池子卡（coverage/域内由法官补判）。

**API**：
- `GET /api/cards?status=&date=` → `{items, counts{draft,approved,jail}}`（行=靶子卡四面）
- `POST /api/cards/{id}/compose` `{hook?, spine?, angle}` → 202 `{script_id, job_id}`
  （备稿：用户可改钩子/主线后交脚本；卡转 approved 回填 script_id）
- `POST /api/cards/{id}/discard` → 不做
- `GET /api/cards/jail` / `POST /api/cards/{id}/revive` → 弃卡池查看/捞回 draft
- `POST /api/cards/seed` `{text, url?}` → 手动投池子

**备稿→脚本**：`POST /api/scripts/generate` 支持 `{card_id, hook, spine, angle}`（scripts 落
card_id）；script_generate.md 三段纪律=开场逐字用 hook（≤2 字口语化，禁话题引入/悬念替换）·
中段单判断论证 spine 且 spine 不出口·收尾自然收束不复述；script_polish 加「主线单一」评审
维度；spice 辣度随卡传入生成端（辣度不设限落生成通道）。

**prompt 四件**（替代旧三件）：pool_pick / spine_forge / hook_cast / judge_cards
（max_tokens 初值 4000/4000/2000/4000——spine_forge 因 takeaways 并入输出按实测×2 上调；
按实测×2 校准；占位符 render 强制校验）。

### 实测打假（R1.4③，2026-11-14 定版 v1）

- 数据 `falsify_items`：claim/target/evidence_note（线索）/result_note（实测结论=成稿论据来源）/
  status(pending 待实测|testing 实测中|tested 已实测|scripted 已成稿|dismissed 不做)/script_id。
- 自动猎取：`falsify_hunt` 挂 `card_daily` 尾段（96~99%），输入=拆解库近 14 天 analysis 摘要+
  当日 AIHot/科技头条，LLM ≤3 条（falsify_hunt 4000），claim 归一化与既有清单 ≥0.55 拦。
- API（/api/falsify）：
  - `GET ?status=` → `{items, counts}`；`POST` `{claim, target?, evidence_note?}` → 201（手动添加）
  - `PUT /{id}` `{status?, result_note?}` → 状态推进/结论回填（compose 要求 tested+结论非空）
  - `POST /{id}/compose` → 202 `{script_id, job_id}`（成稿：`falsify_script.md` 独立模板——
    钩子=实测结果数字、中段=实测过程复盘只准用 result_note 事实、判词落证据上；
    scripts 落 falsify_item_id，item→scripted 回填；打磨/定稿复用既有链）
  - `POST /{id}/dismiss` → 不做
- 前端：今日靶子页第三 tab「实测打假」（行=主张+对象+状态；展开=线索/结论编辑+状态推进+成稿）。

### 同行监测（对标账号管理与扫描）
- `GET /api/watch/digest` → 同行监测周报（近 7 天实时摘要，不落库：新视频/待定夺按作者分布+高分选题 Top3 带切角一句话）
- `GET /api/watch/accounts` → `{items:[{platform, name, url, note, enabled, last_scan_at, video_total, video_recent_7d, scores}]}`；`scores` 为 `{relevance, quality, overall}` 或 null（从备注评分文本解析，口径见 §5）
- `POST /api/watch/accounts` `{platform(douyin|channels), name, url, note?}` → 201；抖音必须带链接（缺 400）
- `PUT /api/watch/accounts/{id}` `{name?, url?, note?, enabled?}` → 200（部分更新）；不存在 404、空名 400
- `DELETE /api/watch/accounts/{id}` → `{deleted:id}`
- `POST /api/watch/scan` → 202 `{job_id}`（全量扫描，仅 enabled 账号；新视频全量入库（R3.4），尾部过拆解质量闸）
- `POST /api/watch/accounts/{id}/scan` → 202 `{job_id}`（单号扫描，不受 enabled 限制；不存在 404）
- `POST /api/watch/resolve` `{url}` → 202 `{job_id}`（链接自动识别：视频链接免登录 反查昵称+主页，分享主页标题兜底；失败显式报错）
- `GET /api/watch/scan/history?limit=` → `{items:[job...]}`（watch_scan 任务记录，`result.notes` 含每号路线/降级原因）
- `POST /api/watch/accounts` / `PUT` 支持 `kind`：`competitor`（默认）| `self`（我的账号）；`watch_scan` 全量扫描仅处理 competitor

### 关键词发现对标账号（R3.6）
- 路线：DDG `site:douyin.com/video 关键词`（≤3 组查询、攒够 8 条链接提前收手；html 202 即换 lite 端点、
  都封立刻短路不补发——限流是 IP 级分钟惩罚窗且两前端共享，24h 缓存）挖真实视频链接 → 视频页 detail 免登录 反查
  作者（昵称/sec_uid/签名/follower_count）与样本热度 → 按粉丝数排序取 Top 8 → `watch_candidates` 表持久化。
  全程无 LLM；风控样本重试一次后跳过（原因写 notes）；DDG 限流挖空或全部样本风控 → TransientError
  （RetryPolicy max_retries=2、base 600s，到期自动重试）
- `POST /api/watch/discover` `{keyword, direction?}` → 202 `{job_id}`（`watch_discover` 任务，dedup=`discover:{kw}:{direction}`，活跃期幂等）
- `GET /api/watch/discover/candidates?status=` → `{items:[{keyword, direction, name, sec_uid, url(分享主页), signature, follower_count, sample_video_id, sample_title, stats{digg,comment,share,collect}, status(open|added|dismissed)}]}`（粉丝数降序）
- `POST /api/watch/discover/candidates/{id}/add` → 建 WatchAccount（douyin/competitor，备注含来源关键词与粉丝数）+ 候选转 added；同 sec_uid 已在清单则只标记（`duplicate:true`）；已忽略 400
- `POST /api/watch/discover/candidates/{id}/dismiss` → 永久忽略（重新发现不复活）
- `DELETE /api/watch/discover/candidates` → 清掉已处理（added/dismissed），待定夺保留

### 雷达（/api/radar，仅查看域）
- `GET /api/radar/topics` → `{items:[{id, name, enabled, hits, updated_at}]}`（已监测话题与最近命中；updated_at 取 last_checked_at）
- `POST /api/radar/topics` `{name, ch_id}` → 添加监测话题（ch_id 必须数字；重复 409）
- `POST /api/radar/topics/extract` `{url}` → 贴抖音视频链接免登录提取话题标签（name+ch_id，供添加监测）
- `POST /api/radar/topics/{topic_id}/check` → 单话题即时巡检（免登录拉内容样本，新出现低粉高赞照旧自动拆解）

### 风格画像（自我研究，R9）
- `GET /api/style/overview` → `{account: WatchAccount|null, profile: 最新画像|null, stats:{total, analyzed}}`（视频列表走 /style/videos 分页）
- `GET /api/style/videos?status=&limit=&offset=` → `{items,total}`（分页+标注筛选；items 含 analyzed 派生字段）
- `POST /api/style/videos/{id}/resolve` `{action}` → 风格视频人工定夺（批准拆解/忽略）
- `POST /api/topics/cleanup-rejected` `{days=30}` → 清理 N 天前已否决选题（级联删报告/解绑脚本文章）
- `GET /api/metrics/account?days=30` → 自有账号日级指标序列（升序，1~120 天封顶）+ 最新摘要（粉丝/净增/取关/主页访问/播放/互动/搜索）；数据源创作者中心 overview/all httpx 直连回流，每日 4 点轻活（kill switch=creator_data_enabled）（R9.7）
- `GET /api/metrics/weekly` → 视频表现周报（按周聚合视频质量分榜=收藏率×3+转发率×3+点赞率×1.5+评论率×2+播放规模封顶3 + 账号日级归因「发布日 vs 停更日」；LLM 解读按周缓存；任务 video_weekly_report 每日 5 点自愈、按周幂等）（R9.8）。**校准回路（R1.4 M3，2026-11-14）**：report.cards=备稿卡↔已发视频模糊匹配对照（发布日∈[卡创建日,+14天] 且标题归一化相似 ≥0.40 或互含 ≥8 字；一卡取最高分一条）+cards_unmatched 未匹配计数——只出对照表，不回写评分（R1.6 边界内）

### 脚本工场
- `POST /api/scripts/generate` `{topic_id | card_id, hook?, spine?, angle?}` → 202 `{script_id, job_id}`（一版精品直出，含自动打磨一轮；card_id=靶子卡备稿，三段纪律见选题工场节）
- `GET /api/scripts?limit=` / `GET /api/scripts/{id}`
- `POST /api/scripts/{id}/polish` → 202 `{job_id}`（批判打磨，≤2 轮）
- `POST /api/scripts/{id}/finalize` → 终稿 + `teleprompter_text` + `storyboard` + `title_candidates`
- `GET/POST /api/scripts/templates`（结构模板库 CRUD）
- `DELETE /api/scripts/templates/{id}`（护栏：至少保留一个模板，删最后一个 400）
- `DELETE /api/scripts/{id}` → 解绑视频项目/文章引用不删下游；选题仅剩此脚本被删时 produced→approved 回退

### 公众号
- `GET /api/articles/style-summary` → `{note}`（公众号独立风格画像，存 app_settings.article_style_note）
- `POST /api/articles/extract-style` → 202 `{job_id}`（从已成文长文提取公众号风格画像）
- `POST /api/articles/generate` `{topic_id | script_id}` → 202 `{article_id, job_id}`
- `GET /api/articles/{id}` / `PUT /api/articles/{id}` `{md}`（保存人工改稿，status→edited）
- `POST /api/articles/{id}/render` `{md?}` → 完整文章对象（含 html，前端据此即时回显预览/状态/复制）；传 md = 渲染即保存改稿，缺省渲染库内正文（renderer.py 渲染器，内联样式，status→rendered）
- `GET /api/articles/{id}/wechat-copy` → `{html}` 公众号粘贴载荷：净化后的渲染产物，配图地址改写为签名公开地址（前端据此写剪贴板 `text/html`+`text/plain` 双格式；只写纯文本时公众号编辑器贴出来是 HTML 源码）
- `DELETE /api/articles/{id}` → 删文章（引用不删，选题不受影响）
- `GET /api/articles/{id}/wechat.html` → 渲染后 HTML 文件直接下载（手动下载，保留功能）
- `GET /api/articles/images/{filename}` → 配图文件服务（按租户分目录只读本租户，见 §15；
  前缀白名单 gen_/chart_/cover_/kg_/flow_，防目录穿越）
- `GET /api/public/images/{filename}?t=<签名>` → **无登录门禁**的签名配图（HMAC(文件名) 前 22 字符，
  不匹配一律 404）。存在理由：公众号编辑器从 mp.weixin.qq.com 跨域抓 `<img>` 外链再转存素材库，
  带不了本站 Cookie——粘贴载荷必须用签名地址，否则图片在公众号侧是裂图
- 手动报题线已拆除（2026-11-14）：`POST /api/articles/draft`、`POST /api/articles/{id}/write`
  （kb_grounding 检索取材链）与 `article_kb_write` 执行器均删——kb_topics 池退役后写文链
  无输入源（kb_doc_ids 全仓零写入方）。`article_kb_write.md` prompt 保留（深读回滚线的
  成文范本仍挂它）。知识库内容自动线=源文改编产线（R5.6b）；公众号草稿自动线=R5.0 每日直出。
- 深读线（停用中）：调度不注册；执行器与 prompt 保留（回滚窗口，恢复注册一行即复活）。
- **源文改编产线**（从知识库挖掘高质量内容形成清单、持续改编成文；
  数据模型=`content_items`（清单项=一篇具体源文：标题/来源号/质量分/为什么好/改编角度/
  逐字原话 ≤3/栏目/优先级/状态 pending|running|done|published|dismissed|low）+`daily_ledger`
  （当日账：phase=mine|adapt，produced/reason，anomaly=成功但无产出且无原因按故障处理））：
  - **挖掘器** `article_kb_miner`（调度 `article_kb_miner_daily` 每日 4 点，`ima_kb_enabled` 门控）：
    ima 目录游标（app_settings.adapt_cursor="idx:pos"，`adapt_gen_line` 可覆盖入清单线，默认 7.5）
    每日 ≤18 篇全文（单篇 3 万字防异常上限）→ 逐篇评估
    （article_kb_miner_eval：评分四维=具体框架/可抄清单/带数字案例/反共识判断 + 为什么好 +
    改编角度 + 逐字原话 ≤3 + 栏目五选一，目录映射兜底）→ ≥线入清单（media_id 防重），
    低分落 status=low 防回卷重评，全部失败=TransientError 游标不推进走梯子，日账必落（含空过原因）。
    API：`POST /api/articles/mine` 手动补挖 · `GET /content-items`（status/category 过滤+counts）·
    `POST /content-items/{id}/status`（pending/dismissed）· `POST /content-items/{id}/pin`（置顶=9）·
    `GET /ledger`（带 anomaly 标记）。
  - **改编器** `article_adapt`（调度 `article_adapt_daily` 每日 5 点，同门控）：清单取项
    （priority desc→score desc，CAS running 占位，running 超 2h 自愈回 pending）→ 拉源文全文 →
    article_adapt_write（保源文骨架+逐字原话 ≤3 处引用，判断口径/钩子必须是我们的，1900~2200 字，
    下限 1500）→ **原创度闸**（`_dup_ratio`：源文 ≥14 字实质句被原样搬入的字符占比 ≤30%，
    超线 article_adapt_reduce 降重一次，数字签名守卫，仍超=当日空过退回清单）→ 违禁词机检
    （_kb_style_violations+定向重写）→ **审校组**（article_review_facts 事实对源 +
    article_review_reader 读者弃读点，问题清单制；不过 revise_once 修订复审，二次不过 ×1 退
    pending、×2 落 low 防天循环）→ **标题评分**（article_title_score 五维，正题/来源题/备选全进
    候选池自动选主标题）→ 图表/头图 → finalize 排版落 rendered 待发。API：手动改编走调度中心 `POST /api/schedules/article_adapt_daily/run` ·
    `POST /articles/{id}/publish`（发布登记贴 URL→published，清单项联动）·
    `PUT /articles/{id}/reflow`（48h 回流 reads/welook/follows/leads，北极星=进线）。
    幂等：dedup_key=日期；改编类失败统一退项走梯子（例外：原创度/审校不过=确定性空过带原因）。
- 前端（源文改编流）：公众号组三叶——「内容清单」`ContentItems.tsx`（回显带=昨次
  挖掘/改编日账（anomaly 红字）+手动补挖 · 栏目 chips+状态计数 · 行=源文（栏目 tag/质量分/
  来源号），展开=为什么好/改编角度/最佳原话/来源/去向，动作=明天改编(pin)/不做/强制入清单/
  恢复待改编；running 行 60s 自动刷新）·「内容工场」`Workshop.tsx`=决策页（行展开加改编来源、
  发布登记（贴 URL→published）、已发布行回流四数补填；复制公众号格式/不用不变）· 风格画像。
  徽章：内容清单=待改编数（counts.c_items）；内容工场=待决策稿（stats/todos.pendingArticles=
  pending+draft+edited+rendered）；旧 NAVMAP articles→article 兼容不变。

## 7. 后台任务框架（五要素统一任务体系）

- 一切待处理工作都是任务：`jobs` 表 + 进程内 runner。任务五要素 = 类型（type）+ 去重键（dedup_key）+ 分道（lane）+ 重试策略（RetryPolicy）+ 失败分类（fail_class）。
- **提交**：`submit(type, payload, dedup_key="", actor=None)`。dedup_key 非空时，同 type 下活跃任务（queued/running）唯一，重复提交幂等返回原任务（活跃唯一部分索引兜底）。dedup_key 由各提交点按实体给出：拆解=分享链接（实体去重：同链接不建重复行）与 `bm:{id}`；扫描=`scan:{account_id|all}:{YYYYMMDD}`；自我扫描=`selfscan:all:{日期}`；链接识别=提取后的 URL；画像重算=全局 `profile:update`；自我风格分析=`selfanalyze:{video_id}`。
- **分道**：`heavy`=LLM 长任务单 worker 串行队列；`light`=扫描/识别类并发池（默认并发 2，`CS_LIGHT_CONCURRENCY` 可配，同类执行中后来者合并跳过）——实际只有这两道。定时任务由调度循环到点 submit（带当日 dedup 键，天然防重复触发），调度循环本身不是任务。
- **异常分类学**（`jobs/errors.py`，`classify()` 对历史 RuntimeError 按消息特征兜底分诊）：
  - `transient`（网络/超时/5xx/LLM 通道抖动）→ 按类型策略自动重试，指数退避 base×2ⁿ 封顶 cap，`next_retry_at` 到点由重试循环（30s）放行；
  - **引擎统一**：`transient`/`captcha`/`quota` 一切可重试失败共用全局退避梯子
    `RETRY_LADDER = [30min, 2h, 6h, 24h]`（`failed` + `next_retry_at`，到点由重试循环放行）；
    **梯子尽头（`max_retries`=4 跳走完）转每日慢巡**：24h 一试直到自愈，`PATROL_CAP=30` 次硬上限
    后安静终态（进「需要处理」区，可人工重试清零重走梯子）；`deterministic` 立即终态
    （死内容三标记「匿名不可见/已删除/转写空」当场归档不挂红）。
    `fail_class` 只影响文案与失败中台指纹，不影响重试机制。
  - 任务类型策略只含超时阈值：`task_timeout`（硬超时）与 `stuck_after`（看门狗）；
    看门狗卡死同样走梯子；
  - `captcha`（平台风控/验证墙）→ 失败并打标"可人工重试"；
  - `deterministic`（默认；参数/配置/内容问题）→ 立即失败，永不自动重试。
- **重试策略**：`RetryPolicy(max_retries, base, cap, classes, park_backoff, max_parks, stuck_after)` 按 type 注册于 `_POLICIES`，提交时 `max_retries` 落到任务行。人工重试清零自动重试计数；批量"重试全部"跳过 deterministic。
- **闭环判定（失败→恢复的因果链可见）**：`_coverage()` 对每个 failed 任务检测"同 type + 同 dedup_key/同实体 id 且 id 更大的 succeeded 任务"→ 附 `covered_by`。三态语义：**待处置**（未闭环、无排期）/ **重试中**（有 next_retry_at）/ **已闭环**（covered_by 存在，绿标"由 #N 成功重跑"，自动移出待处置）。重试血缘：`jobs.retry_of` 记录由哪个失败任务重试而来（手动重试/批量重试/按指纹复活三条路径都写入），详情页显示链路。
- **统一调度中心**（`jobs/schedules.py`，Q"任务调度管理中心"）：所有周期任务一处注册（`REGISTRY`：key/label/执行体/节奏 daily_hour 或 interval_hours/设置开关字段）、`schedules` 表存运行态（enabled/hour 覆盖/last_run_at/next_run_at/last_status/run_count）、`_schedule_loop`（runner 内受监督循环，每 60s 巡检）到点执行。REGISTRY 共 12 项：`article_hotspots_daily`（0 点，站外热点报题）· `watch_scan`（1 点，`watch_scan_hour` 可配 + `watch_scan_enabled` 门控）· `self_scan`（1 点，同上门控）· `hl_search_daily`（2 点，同城组局号搜索挖掘）· `daily_backup`（3 点，业务库每日备份，调度器内联执行不进任务队列）· `article_kb_miner_daily`（4 点，`ima_kb_enabled` 门控，源文评估入清单）· `creator_data_daily`（4 点，`creator_data_enabled` 门控）· `article_adapt_daily`（5 点，`ima_kb_enabled` 门控，清单取项改编成文）· `video_weekly_report`（5 点，按周幂等自愈）· `card_line_daily`（6 点，人设号选题工场：车间零信号采集→池子→主线→钩子→总法官五段）· `media_cleanup`（每日 6 点，拆解+自有媒体清扫，内联执行）· `crawl_probe`（每 2h，采集探针，内联执行）。API：`GET /api/schedules`、`POST /{key}/toggle`、`PUT /{key}/hour`、`POST /{key}/run`（立即执行并记运行态）；任务页「调度中心」卡片可视化。调度统一经 REGISTRY 注册，无代码内硬编码调度循环。
- **同行发现**（`watch_discover`，无独立日调度，经 `POST /api/watch/discover` 触发，关键词按天轮换）：优先级=设置页配置 > 12 词静态池走 DDG→视频链接→反查作者→粉丝 Top8 入候选表（status=open 待人工定夺；已在清单/已忽略的不重复）；候选粉丝 ≥1 万（_DISCOVER_QUALITY_FANS）计入工作台周报卡「清单外高赞同行」。
- **同城组局号搜索挖掘**（`hl_search_matrix`，调度 `hl_search_daily` 每日 2 点）：城市×关键词搜索矩阵分晚轮跑 → 作者池取证 → 四要素筛 → LLM 分级 → 判 A/B 入候选表待人工定夺；解析到的号落 `watch_candidates` 候选表，已撞过的 sec_uid 记入 app_settings `harvest_seen_secs` 去重池（封顶 3000，防重复撞主页限速；`harvest_hl_search`/`harvest_hl_verify` 键可临时调大预算）。
- **抖音采集探针**（`crawler_probe.py`，调度 key=`crawl_probe`，每 2 小时）：先 TCP 探出口隧道
  （断=`proxy_down` 不浪费浏览器）；**多样本表决**：连测 3 条轮换样本
  （拆解库最近 20 条已定夺视频、避开上次用的），≥1 条通即判可用、detail 带过 N/M 比例——
  单条毒视频不再一票否决；同 URL 连续 2 次探不通→24h 冷却移出样本池；分类 `ok / login_wall /
  captcha / proxy_down / browser_error / no_data / video_gone（视频已删除或不可见，与验证码区分）`；
  结果落 `data/crawl_probe.json`（最近+历史 20，带被动信号：近 1h 爬取类任务真实失败数交叉印证），
  **状态翻转才发系统通知**。API：`GET /api/crawl/status`、`POST /api/crawl/probe`（手动）；
  设置页「数据采集（抖音）」卡；拆解提交两处返回带 `crawl_warning`（探针异常时提交即预警）。
  拆解下载同口径：平台一致指纹（跨平台 UA 伪装=不一致指纹，会被扣数据）；
  下载失败按页面实际内容分类——毒视频「视频已删除」（终态带人工确认指引）／真验证码（进熔断）／
  代理断连「出口代理不通」（transient，不进熔断）。
- **reaper**（周期巡逻，`CS_REAPER_SEC` 可配，默认 1800s，启动快速首巡）：①看门狗——`running` 超过类型策略 `stuck_after`（默认 2h）无更新判卡死，按 transient 自动重试；②欠任务——执行 `register_owed(type, fn)` 声明：fn 返回缺失工作的 `[(payload, dedup_key)]`，reaper 幂等补队。拆解已注册：`approved` 且 `analysis` 为空、无活跃任务、近 6h 无失败 → 补队（6h 退避防确定性失败空烧）。
- **进度约定**：`progress` 0~100 + `message`；阶段轨迹落 `history`（≤80 条）；终态写 `result` 或 `error`（重试中的失败带 `next_retry_at`）。
- **服务重启自愈**（`recover_interrupted_jobs`，须在 `runner.start()` 之后）：`queued` 原行重新入内存队列（不新建任务行，DB 行保持 queued 语义与租户归属）；`running` 按 transient 排 90s 自动重试（重启中断自动续跑，无需人工）；历史 parked 行由启动迁移转为 failed（`next_retry_at` 已持久化）。
- **观测**：任务行携带 `lane/retry_count/max_retries/next_retry_at/fail_class/dedup_key`；任务页展示三态（进行中/需要处理/完成）、重试进度（N/4 与下次时间）与失败分类标签。
- **失败中台（错误指纹 + 聚类盘点 + 按指纹复活；三件套 API 为「无 UI 的运维/验收后门」，界面侧无失败工作流，失败行=红徽章+错误+行内重试）**：
  - 每次失败由 runner 计算**错误指纹**（`errors.fingerprint`：规则表命中如 `llm:empty-body`/`asr:vocab`/`ddg:rate-limited`/`douyin:risk`/`db:constraint`/`code:bug`；未命中按归一化消息哈希），落 `jobs.error_fp` 并 upsert `failure_patterns` 表（fp 唯一，count 累计、样本错误、处置状态 open/fixed/ignored；fixed 后再出现自动回 open）。
  - `GET /api/jobs/failures` → 失败盘点：失败且无重试排期且未归档的任务按指纹聚类 + **实体孤儿**组（实体 failed 且无活跃任务，合成指纹 `entity:{type}`）；界面不渲染该盘点——任务徽章=运行中+排队+未归档 failed 全量（failed 红）。
  - `POST /api/jobs/retry-by-fingerprint {fp}` → 整批复活（运维后门）：重排前先**实体复活**（`jobs/revive.py`：failed 实体按类型拉回在途态，防二次幽灵）；实体孤儿组则直接重提生成任务。`POST /api/jobs/failures/{fp}/resolve {status, note}` → 标记已修复/忽略后移出盘点。
  - 自动重试放行（`_reenqueue`）与人工重试同样先做实体复活，failed 状态不跨重试周期。
  - 执行器可注册 `on_fail(payload, exc)` 回调（`register_executor(type, on_fail=...)`），失败时 runner 统一调用做实体状态回退；实体型执行器（script_generate/polish/article_generate）的实体回退统一由 on_fail 承担，执行器体内不带 try/except 回退。
- **归档制**：终态任务（failed/superseded/succeeded）超过 7 天由 reaper 归档（`archived=1`）而非物理删除；任务列表/counts/clear/单条删除均只作用于未归档行，错误指纹库的统计证据永不丢失。`POST /jobs/clear` 返回 `{archived: N}`。
- **执行器注册表**（23 类）：`article_generate / article_hotspots（站外热点报题建稿）/ article_style_extract / benchmark_analyze / script_generate（一版精品直出，含自动打磨一轮；card_id 备稿走三段纪律：hook 逐字开场/spine 不出口/单判断推进）/ script_polish / script_finalize（定稿+标题候选）/ self_scan / self_analyze / self_profile_update / creator_data_scan / video_weekly_report / idea_research / hl_search_matrix（同城组局号搜索矩阵，硬超时 3600）/ watch_scan（payload.account_id 单号扫描，全量仅 competitor）/ watch_resolve（自动提取口令全文中的链接）/ watch_discover（关键词发现对标账号，light 并发道）/ article_kb_miner（挖掘器，源文评估入内容清单）/ article_adapt（改编器，原创度≤30%+去AI味机检+事实对源硬闸） / article_deepread（深读写，调度不注册，保留回滚）/ card_daily（选题工场五段：信号采集→池子→主线→钩子→总法官）/ card_seed（手动投喂：单池子即走主线→钩子→法官）/ falsify_hunt（实测候选猎取，挂 card_daily 尾段）`。
- **热点雷达（话题查看域）**：`radar_topics` 表（ch_id 全局唯一/seen_ids 防重/recent 最近新视频）。每日 6 点随 `card_line_daily` 车间零巡检话题新视频，低粉高赞同口径自动进拆解（R3.7）；
  雷达飙升（新增视频速度）兼作选题工场一手池子信号（R1.4）；`/api/radar` 仅保留查看域 4 端点（契约见 §6 雷达节）；独立话题雷达面与 topic_radar 任务并入每日热点。
- **内容合规守卫**：`article_section` 逐节写作带两道程序化守卫——①截断守卫（结尾无句读重写一次）②合规守卫（`BANNED_PATTERNS` 违禁词命中定向重写一次，与 acceptance/quality.py 同表）；均只在命中时多花一次 LLM 调用。
- **媒体即用即弃**：`benchmark_analyze` 成功即删视频文件（转写/拆解已入库；重拆解自动重新下载，本地拆解的原文件删后需重新上传）；reaper 巡检兜底清扫已完成/已忽略条目的遗留媒体，进行中任务的媒体不动。
- **实体级去重**：同选题已有 generating/polishing 脚本 → 拒绝再生成（409）；同选题/同脚本已有 generating 长文 → 拒绝（409）；相同原文正在深研 → 拒绝（409）。
- **任务归属（租户链）**：`jobs.tenant_id/user_id` 由 `auth.ACTOR`（(tenant,user,role) contextvar）在提交时自动落库——请求上下文由 `require_user` 设值；执行器运行期由 runner 从任务行恢复上下文（**同步执行器必须经 `asyncio.to_thread` 派发——`run_in_executor` 不传播 contextvars**）；人工重试继承原任务归属，系统调度与自动重试归 0（系统）。执行器内的一切查询、实体创建随任务租户。
- **按租户队列**：heavy 消费者跨租户轮转（租户内 FIFO）；每租户活跃任务上限（提交配额 `_check_tenant_quota`）在提交时先查后建（超限 429）。

- **风格注入（R9.4）**：`script_generate` / `script_polish` 的「他说话的方式」段为 `{{MY_STYLE}}` 占位符——有画像时注入"摘要+特征标签+代表原句"，无画像回落内置人设文案；`generate` 请求体 `use_style`（默认 true）可按次关闭。

## 9. 模块设计要点

### 9.1 选题中心
- 深研链：`idea → LLM 多轮（检索增强可选）→ 结构化报告 → 评分入库`。评分 = 相关度×0.4 + 痛点强度×0.4 + 差异化×0.2（各 0~10，加权总分）。
- 同行监控链：对标账号扫描（锚点视频反推作者作品）→ 新视频自动排队拆解 → 选题候选入库（source=benchmark）。全网热榜不在范围（PRD R1.2）。
- 选题工场链（R1.4）：新模块 `backend/app/cards/`（service.py 五段流水线+三闸+总法官）+ `api/cards.py`；
  热源抓取复用 topics/hotlist.py，雷达巡检复用 radar/service.py，监控号新视频复用 watch 采集链。

### 9.2 脚本工场
- `script_templates.structure` 示例：`[{step:"hook",requirement:"前3秒点名受众+痛点"},{step:"pain"},{step:"proof"},{step:"method"},{step:"cta"}]`；模板来自人工整理 + 拆解沉淀（`source_benchmark_id` 溯源）。
- 终稿三产物：提词器文本（停顿"/"、重音**加粗**标记）、分镜提示（B-roll 槽位 + 字幕卡点位）、标题候选（抖音/视频号/话题标签）。


### 9.4 公众号
- 生成：topic/script → LLM 长文（MD，含小标题/案例/自然收尾）→ 人工改稿（PUT）→ render。
- 渲染：自研 `renderer.py`（思路参考 doocs/md）MD→内联样式 HTML，主题一份默认样式；输出存 `articles.html`，前端"一键复制"（clipboard）+ iframe 预览。
- 发布纯人工：无任何公众号 API 调用。

## 10. 外部服务部署

### 备份与看门狗（系统层健壮性）
- **自动备份**：`db.backup_db(tag)` = SQLite `.backup` 在线快照（WAL 安全）。①每日滚动快照（调度项 `daily_backup` 每日 3 点执行，保留 14 份，`data/backups/studio-daily-*`）②**迁移前快照**（init_db 在 create_all/_migrate 前执行，保留 5 份）——业务库只有一份，磁盘/迁移/误删事故的唯一恢复手段。
- **看门狗**（独立 LaunchAgent `com.content-studio-watchdog`，每 5 分钟）：`GET /api/health` 探活 → 连续 2 次失败升级通知；有 running 任务时连续 6 次（约 30 分钟）才 `launchctl kickstart` 强拉（任务自身硬超时会先自愈），恢复时通知。主服务死透或 launchd 节流时由它兜底（launchd.sh install 统一装/卸，status 可查）。
- **磁盘防御**：`download_video` 前检查余量（<2GB 拒绝下载，报错进失败中台待处置）；reaper 每巡 <5GB 告警日志；拆解媒体即用即弃。
- 启动/重启纪律：改启动路径（lifespan/runner.start）的代码必须 kickstart 后立即探活。

| 服务 | 端口 | 部署 |
|---|---|---|
| Content Studio（macOS） | 0.0.0.0:8100 | LaunchAgent 常驻（`scripts/launchd.sh` install/uninstall/status）+ 看门狗（脚本副本装于 Application Support，TCC 规避） |
| Content Studio（Linux 服务器） | 127.0.0.1:8100 | systemd 常驻（`scripts/deploy-linux.sh` install/uninstall/status）+ 看门狗 timer；Nginx 反代 + HTTPS（`deploy/nginx-content-studio.conf.example`） |
| ASR 模型 | faster-whisper large-v3 | hf-mirror 下载至 data/asr-models/faster-whisper-large-v3/，`.env ASR_MODEL=large-v3` |
| 进程守护 | macOS launchd | 由 `scripts/launchd.sh install` 动态生成 LaunchAgent 开机自启 |
| 版本管理 | git | 仓库根 `git init`；.env/data/venv/浏览器缓存不入库（.gitignore 已就绪） |

启动顺序：应用进程（无容器）。`/api/health` 回报 db/llm/deps{playwright}，不阻塞主流程。

## 11. 错误处理与降级

| 故障 | 行为 |
|---|---|
| LLM 调用失败 | 网关内重试 3 次（指数退避）→ job failed，可重跑 |
| ASR 无语音/空转写 | job failed(message=转写结果为空) |

## 12. 安全

- 登录门禁：全站业务接口/页面未登录 401（`require_user` 路由级依赖）；scrypt 口令哈希、
  服务端会话（Cookie HttpOnly+SameSite=Lax，库存 sha256(token)）；停用用户/停用租户即失效。
- 租户隔离：查询层全站过滤 + get 拦截 + 新行自动归属（实现见 §15）；平台管理员豁免。
- 密钥只存 .env（不入库不入前端）；`health` 只回报配置状态不回传密钥。
- 上传文件类型/大小白名单（mp4/mov/aac/mp3/png/jpg，≤2GB）。
- **应用安全加固**：
  ①**文章 HTML 白名单净化**（bleach 6 + CSS 白名单）：渲染出口与交付前双重净化
    （`GET /articles/{id}`、`/articles/{id}/wechat.html`），
    剥脚本/事件属性/危险协议——防"租户成员在文章 md 埋 <script>、平台管理员查看时执行"的跨租户提权；
    wechat.html 另带 `Content-Security-Policy: default-src 'none'`；
  ②**任务纳入租户作用域**（`Job` 挂 TenantScoped）：任务列表/详情/删除/重试一律跨租户 404；
  ③**会话失效语义**：登出即服务端作废该 token；改密作废该用户全部会话并补发新会话（全端重登）；
    `require_user` 每次校验租户状态（停用租户残留会话一律失效）；
  ④**上传体积上限**：拆解视频 2GB；
  ⑤**登录时序均匀化**：用户不存在时同样执行一次 scrypt（防按响应耗时枚举用户名）；
  ⑥**SSRF 闸门**：服务端会主动请求的用户 URL（贴链接拆解/链接识别/话题提取/监控账号写入）
    一律经 `is_douyin_url()` 校验——仅放行 `*.douyin.com` / `*.iesdouyin.com` 的 http(s)
    （内网地址、云元数据端点 169.254.169.254、伪装域、危险协议全部拒绝），API 层 400 + 下载器/扫描器内部兜底；
  ⑦**首登强制改密（服务端强制）**：业务与管理路由用 `require_ready_user` 依赖，
    `must_change_password=1` 的会话访问业务接口一律 403（仅 `/auth/*` 放行以完成改密）；
  ⑧**任务错误脱敏**：任务详情对租户可见，落库前把服务器绝对路径（仓库根/主目录）替换为 `<server>`；
  ⑨**应用级安全响应头**：全局 CSP（`default-src 'self'`，脚本仅同源；wechat.html 端点自设更严 CSP 不被覆盖）
    + `X-Content-Type-Options: nosniff` + `Referrer-Policy: same-origin`；
  ⑩**前端动态链接协议守卫**：`safeHref()` 仅放行 http/https（防 DB 混入 `javascript:` 链接被点击执行）。
- **云部署安全**：
  ①`COOKIE_SECURE=1`（.env）→ 会话 Cookie 加 Secure 属性，只经 HTTPS；
  ②**登录限流**（`auth.LoginGuard`）：账号维度（`LOGIN_MAX_FAILS`，默认 5/15 分钟）
    与来源 IP 维度（`LOGIN_IP_MAX`，默认 40/15 分钟）双计数，成功即清零，触顶返回 429 + Retry-After；
    真实 IP 经 `client_ip()` 解析——仅当直连方为回环（本机 Nginx 反代）时信任 X-Forwarded-For 首段；
    限流为进程内内存态（重启清零，单进程部署足够）；
  ③**Linux 部署**：`sudo scripts/deploy-linux.sh install`（systemd 常驻 + 看门狗 timer 每 5 分钟探活、
    失败自动 restart）；Nginx 反代 + HTTPS 模板见 `deploy/nginx-content-studio.conf.example`
    （含 `client_max_body_size 2048m`、X-Forwarded-For/Proto 透传）。

## 13. 验收对照与纪律

验收脚本位于 `acceptance/`（`python3 acceptance/run_pX.py`；拆解实链路的验证由生产运行承担——每晚 1 点 `watch_scan` 与每日自我扫描即真实使用，失败可见；**P8/e2e 必须用 `backend/.venv/bin/python`**——白盒 import 需要 sqlmodel、E2E 需要 playwright+PIL）。判定规则：**FAIL=契约被打破**（退出码 1）；**SKIP=外部依赖缺失**（注明原因，不算破坏契约）。

### 13.1 套件清单

| 套件 | 脚本 | 范围 | 对应需求 | 通过门槛 |
|---|---|---|---|---|
| P0 骨架 | run_p0.py | 服务/health/前端页/jobs 契约/LLM 真实调用/设置接口（读·幂等写·非法值拒） | R8.1/8.2, X6 | 全 PASS；未配密钥时 LLM 项允许 SKIP |
| P1 内容核心 | run_p1.py | idea 深研→入库评分+定审；快速选题+相似标记+否决；脚本一版精品直出（默认档）+自动打磨+定稿三产物；长文深度版唯一档（长度断言 2250~6250 汉字，style=auto 自动透镜）+改稿+渲染（[CHART] 块剥离）；内容质量评审（口播尺/长文尺，X11） | R1.1/1.1b/1.1c/1.3/1.5/1.7, R2.1-2.4, R5.1/5.2/5.1a, X11 | 全 PASS |
| P6 任务体系 | run_p6.py | 去重幂等（活跃唯一/实体去重）/失败分类（确定性不重试）/到期重试放行/欠任务补队/五要素字段/失败中台（指纹聚类·按指纹复活·指纹处置·实体孤儿复活） | — | 全 PASS |
| P7 认证与单管理员 | run_p7.py | 未登录 401/错口令 401/登录会话与 me/首登强制改密（服务端 403）/改密全端重登/登出失效/裁撤面 9 条负向断言（switch-tenant、auth/tenants、credits、admin/*、llm/usage、llm/calls、avatar/* 全 404）/业务数据可见/设置页管理员依赖 | R11 | 全 PASS |
| P8 功能补全 | run_p8.py | 可靠性白盒（组局号钟点/验证码分类/退避梯子+硬超时/熔断）；采集探针（状态接口/手动探测六态分类）；调度中心（列表含探针/开关往返）；品牌九字段 round-trip（GET 全量/PUT 覆盖/清空回落中性默认）；自动透镜受理与同源去重 409；选题工场契约（靶子卡四面结构/三闸负向：窄主语·超15字·劝告开头拦/一手评论≥50 直产/jail 捞回/手动投喂受理/卡备稿→脚本链回归）；产线闸白盒（拆解质量闸三态/改编原创度闸/self_story·hl_search 硬超时/深读调度与知识库选题池执行器=裁撤面负向断言）；源文改编产线契约（内容清单/当日账）；prompt 契约（frontmatter 完整+关键模板可加载）；裁撤面负向契约（独立雷达端点 404；知识库选题池 GET 422/POST 404） | R1.8, R7.3, X10, R11.2, R5.1, R1.4, R5.6b | 全 PASS |
| E2E 端到端 | e2e_run.py | Playwright 驱动真实 UI：登录（错误拒/正确进）；侧栏 17 页逐页零报错；工作台快速记选题；内容工场发布登记闭环（行展开 HTML 预览）；设置页品牌卡与保存回显 | —（界面冒烟与门禁自动化） | 全 PASS |


### 13.2 运行与环境变量

- `CS_API_BASE`（目标实例）、`CS_BOOTSTRAP_PASSWORD`（登录态：runner 首请求前自动以 admin 登录携 Cookie；凭据缺失用例以 401 显式 FAIL）、`CS_REAPER_SEC=5`（隔离实例快巡，P6 owed 用例必带）。

### 13.3 隔离实例纪律（不污染真实数据）

验收产生真实 LLM 调用与库写入，必须跑隔离实例。用复制库约定（bootstrap 只在空库触发，全新 DATA_DIR 无已知口令；部分套件直插 sqlite 夹具，需与生产同构的列默认值）：

```bash
cp -R data data-acceptance
# 清继承队列（含 parked）与继承会话，重置已知口令
sqlite3 data-acceptance/studio.db "UPDATE jobs SET status='failed' WHERE status IN ('queued','running','parked')"
sqlite3 data-acceptance/studio.db "DELETE FROM user_sessions"
DATA_DIR=data-acceptance backend/.venv/bin/python -c "import sys; sys.path.insert(0,'backend'); from app.auth import hash_password; import sqlite3; db=sqlite3.connect('data-acceptance/studio.db'); db.execute(\"UPDATE users SET password_hash=?, must_change_password=0 WHERE username='admin'\", (hash_password('accept-12345'),)); db.commit()"
DATA_DIR=data-acceptance LOGIN_IP_MAX=1000 backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8200 &   # 验收含预期失败登录，放宽 IP 限流
CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 DATA_DIR=data-acceptance \
  python3 acceptance/run_p0.py    # …p1/p6/p7（标准库即可）
backend/.venv/bin/python acceptance/run_p8.py    # P8 必须 venv
backend/.venv/bin/python acceptance/e2e_run.py   # E2E 必须 venv
# 跑完：停 8200，rm -rf data-acceptance
```

### 13.4 重跑红线

- 任何后端改动后重跑受影响套件（全套隔离实例约 30~40 分钟）——「上次全绿」只对当时代码成立。
- 前端改动至少跑 P0（页面可达）+ 浏览器逐页冒烟。LLM 预算/超时改动注意实测耗时：打磨最长 ~350s、定稿 ~80s、轮询预算 ≥600s、脚本生成（一版+自动打磨两段）≥900s。

### 13.5 内容质量验收（X11）

- P1 对生成内容加 LLM 质量评审（尺随内容类型走，0-10 分）：**口播尺**（钩子强度/口语化/证明具体性/结尾自然收尾/合规红线/人设回扣）；**长文尺**（deep 唯一档：铺垫/深度/论证/收束/合规/语言）。断言：单项 ≥6 且总评 ≥7。
- **程序化违禁词**（与 backend 共用 banned_words.json 单一表）：绝对化/收益承诺类命中即 FAIL；**否定语境窗口**：命中位前 4 字含否定词放行；裸「最好的」不进程序化表（避免误杀反向建议），语义级由评审 compliance 项兜底。分节流程另有 **CTA 程序化守卫**（评论区/私信/关注/扣字/领资料命中即定向重写，同表）。评审 prompt 与阈值维护在 `acceptance/quality.py`。

### 13.6 数据卫生

`acceptance/hygiene.py [data_dir]`：一条命令扫库内测试残留（拆解 fixture 行/演示链接发布记录/评分回流残留字段/未收敛「服务重启中断」记录=提示级）；退出码 0 干净 / 1 有残留。验收与对账前必跑。

### 13.7 审计对账清单

- [ ] openapi 端点 ↔ §6 逐条一致；反向：前端 api() 每个路径都有后端实现
- [ ] 页面零报错/未登录业务页被门禁拦截/队列仅手动进入（e2e 自动化覆盖）
- [ ] 生产库无测试残留（hygiene.py 干净）
- [ ] 选题评分无发布数据回流残留（R1.6 边界）
- [ ] 思考型模型调用无 JSON 截断（输出未贴 max_tokens 上限）
- [ ] PRD 每条 R/X 均有验证方式
- [ ] 单管理员全量视角口径：platform_admin 豁免过滤=全量视角、系统任务归租户 0、响应体无积分/金额残留（P7 断言覆盖）

## 14. 风险与技术债清单

1. SQLModel 钉 0.0.42——0.0.47 起 datetime 字段要求带时区，与本库全量 naive-UTC 约定冲突（写入即 ValueError、读回带 tzinfo 导致比较报错）；迁移到 aware 语义需专项改造（requirements.txt 钉版注记）。
2. faster-whisper large-v3 首次下载模型 ~3GB——启动脚本预下载。
3. GLM 结构化输出偶发不合 schema——网关层 1 次修复重试 + 失败落 job error 供重跑。
4. 单进程任务并发——heavy 串行 + 跨租户轮转 + 每租户活跃上限已内建；单机吞吐不够时再上独立 worker 进程（明确记录）。
5. 雷达话题 `ch_id` 全局唯一——多话题来源同加一个话题受限；需时做表重建改复合唯一（R11.3）。
6. 媒体文件按租户分目录仅覆盖生成配图；历史下载/成片文件名空间共享（文件名不可猜测，随租户删除按归属行清理）。
7. 列表端点全量拉取反模式（benchmarks 两遍全表含 transcript 大列、jobs 分页 500 封顶 Python 侧切片）——数据量增长后的第一个性能瓶颈点。
8. /api/health 无门禁且每次触发 sync_playwright 探测——匿名可反复触发的开销型端点（部署面有 Nginx 缓解，登记待改）。
9. 登录限流 XFF 只信回环直连——反代非同机部署时 login_ip_max 变全站共享窗口（当前 Nginx 同机，不触发）。


## 15. 认证与工作室作用域（实现契约）

> **边界声明**：多租户/成员/积分/成本台账不在产品范围（遗留表清单见 §5 生产库遗留）；
> 作用域机制保留（tenant_id 列恒为 1=工作室档案，平台管理员豁免过滤=全量视角），
> 供历史数据兼容与未来可能的多人化回接。

- **调度运行契约**：
  ①**调度失败重试**——执行失败首次 2 小时后补跑一次（给 job 层 1h 退避让路），
  连续失败才回落常规节奏；
  ②**时区显式化**——systemd unit 显式 `Environment=TZ=Asia/Shanghai`（daily_hour 语义=
  服务器本地钟点）。
  已收编统一调度的周期任务见 §7 REGISTRY（现行 12 项）；reaper 内嵌的
  owed 补队/归档/磁盘告警属基础设施双轨（非业务定时），保留。边界：进程崩在"记账后执行前"
  该次丢失（罕见，dedup+次日兜底）。
- **任务运行时纪律**：
  ①**心跳真实化**——`set_progress`/拾起时显式刷 `updated_at`（reaper 的 stuck 判定=
  「无更新 2h」，量的是无进展时长而非任务总寿命）；
  ②**任务级硬超时**——RetryPolicy.task_timeout 按类型配（拆解/自析 45min、长文 60min、脚本
  40min、热点 15min 等），heavy 执行包 `asyncio.wait_for`：超时转 transient 重试，防唯一
  heavy worker 被单任务无限占死（to_thread 线程不可取消，超时后以僵尸形态跑完，完成回调有
  状态护栏不覆写重试行；`_handle_failure` 同护栏）；
  ③**reaper stuck 尊重 max_retries**——达上限转终态 failed；
  ④**重启恢复 queued 原行入内存队列**（不新建任务行，DB 行保持 queued 语义、租户归属不丢）；
  ⑤`_reenqueue` light 分道先查同类 inflight 再置 queued（顺序颠倒会让行永久滞留 queued）；
  ⑥**gateway 全局 LLM 信号量（并发 2）**——不入队的直调 LLM 端点（ping 等）
  也被统一限流；
  ⑦crawl/probe 全局单飞（429）。
- **上下文（auth.ACTOR）**：`ContextVar[(tenant_id, user_id, role)]`，`require_user`（async 依赖）在请求上下文设值；
  系统上下文 = `(0, 0, "")`；`role=platform_admin` 时豁免租户过滤。
- **作用域三件套（`db.install_tenant_scope()`，进程级一次接入；单用户版恒为 tenant_id=1，过滤被平台管理员豁免=全量视角，保留机制兼容历史与回接）**：
  ①`do_orm_execute` 事件——所有 ORM select 按语句实体自动追加 `tenant_id == 当前租户` 条件
  （用具体实体+具体条件构造，平台管理员/系统上下文跳过）；
  ②`Session.get` 补丁——get 不走 do_orm_execute，命中他租户行返回 None（端点自然 404）；
  ③`before_flush` 事件——新行 `tenant_id=0` 时自动回填当前租户。
- **归属链**：任务提交（`runner.submit` 读 ACTOR）→ 任务行带 tenant_id/user_id → 执行器运行期 runner
  从任务行恢复 ACTOR（finally 复位）→ 执行器内查询/实体随任务租户；
  同步执行器必须经 `asyncio.to_thread`（contextvars 传播；`run_in_executor` 会丢）；
  调度产生的实体（扫描入库等）从父实体显式继承租户；人工重试继承原任务归属。
- **豁免与系统口径**：唯一管理员 role=platform_admin，查询不过滤；租户 0 = 系统任务与系统调用
  （调度、自动重试、ping）。
- **会话**：随机 token 走 HttpOnly Cookie，库存 sha256(token) + 过期时间；登出即服务端失效；
  改密即全端重登（作废全部旧会话并补发新会话）。
- **一次性密码**：建号/重置生成随机口令，仅当次响应返回，明文不落库；首登强制改密。
- **抖音抓取出口与指纹**：抓取 UA 由 `browser.ua()` 按运行平台生成
  （UA 与 navigator.platform 不一致会被抖音挑战）；浏览器用完整版 Chromium
  （`channel="chromium"`，headless-shell 指纹更易被挑战）；匿名采集默认机房直连
  （2026-10-27 实测可用），`scrape_proxy` 可选住宅出口兜底（本机 `scripts/home-proxy.sh`
  提供 SOCKS5+反向隧道）；`douyin_proxy` 只用于 DDG 搜索出海与创作者中心登录态采集。
  出口不可达时任务失败信息显式标注所用代理（降级可见）。
- **媒体边界**：生成配图按 `article_images/{tenant_id}/` 分目录，服务端点只读本租户目录
  （平台管理员全局可读；存量平铺文件迁移归首租户）。
- **金额与品牌隐藏（拆除面兜底）**：后端为权威口径——非平台管理员的响应剔除 cost/yuan 字段、
  不含引擎品牌文字；前端按角色条件渲染。
