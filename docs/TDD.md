# TDD · Technical Design Document（技术设计）

> 技术契约（预期侧）：架构 / 数据模型 / API 契约 / 模块设计 / 部署。
> 契约变更必须同步 PRD.md。
> 验证口径=功能冒烟实跑+生产运行（不设验收脚本套件，见 §12）。

---

## 1. 总体架构

```
┌──────────────────────────── 单机 / 单进程 ────────────────────────────┐
│  浏览器 ──► Content Studio Web 站 (:8100，登录门禁)                   │
│              │  FastAPI (backend/)     React 构建产物静态托管         │
│              │    ├─ api/  REST 路由（auth / materials / channels /   │
│              │    │        contents / analytics / jobs / settings）    │
│              │    ├─ auth   会话（单管理员，ACTOR 作用域）             │
│              │    ├─ jobs   进程内异步任务（下载/ASR/LLM/发布）        │
│              │    ├─ PostgreSQL 16（单库，业务事实唯一来源）           │
│              │    └─ data/  上传素材、配图、媒体、备份                │
│              │                                                       │
│              ├──► GLM API（生成主通道 + 独立搜索端点）                │
│              ├──► 微信公众平台 API（仅公众号：建草稿/群发，人工触发）  │
│              ├──► 本地引擎 faster-whisper（转写）/ FFmpeg             │
│              └──► 人工发布（抖音/小红书/视频号）                       │
└──────────────────────────────────────────────────────────────────────┘
```

关键决策：

- **领域分层**：`platform`（认证/设置/任务/调度）· `materials`（采集/素材/加工）·
  `channels`（四渠道共用引擎）· `analytics`（账号/指标/报告）· `integrations`（抖音/ima/LLM/微信）。
- **单体进程**：不上微服务/celery/redis；后台任务用进程内 asyncio + 线程池，
  任务状态落 `jobs` 表，前端轮询。Postgres 只替换存储，不改变任务模型。
- **四渠道共用状态机**：素材 → 选题 → 稿件 → 发布登记 → 数据回流；
  渠道差异由 `channels.config`（取料规则/提示词组/闸线/产物/发布方式）驱动。
- **发布边界**：默认人工；公众号支持官方 API 发布（人工点按触发，失败回退人工）；
  抖音/小红书/视频号无自动发布路径。

## 2. 技术栈与版本基线

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.12+ / FastAPI / uvicorn | 绑定 0.0.0.0（局域网可访问，登录门禁必开） |
| 数据库 | **PostgreSQL 16** | 单库；JSONB 承载渠道差异字段；`timestamp without time zone` 存 naive-UTC |
| ORM / 迁移 | SQLModel（钉 0.0.42）+ SQLAlchemy 2.x + **Alembic** | 驱动 `psycopg[binary]`（psycopg3） |
| LLM | 智谱 GLM 单通道（默认 `glm-5.3-flash`，设置页可改 glm-5.3） | 唯一 LLM 通道，无备选 provider |
| ASR | faster-whisper（large-v3 起步 + VAD） | 中文场景可换 FunASR SenseVoice，接口不变 |
| 视频 | FFmpeg | 工具箱角色 |
| 前端 | React 18 + Vite 5 + TailwindCSS v4 | 中文界面；构建产物由 FastAPI 托管 |
| 发布 | 人工 + 公众号官方 API（人工触发） | 无抖音/小红书发布集成 |
| 备份 | `pg_dump -Fc` | 每日滚动 + 迁移前快照；恢复演练 |

**版本红线**：SQLModel 保持 0.0.42；升级会引入 datetime 带时区语义，与全库 naive-UTC 冲突。

## 3. 目录结构

```
content-studio/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口 + 静态托管 frontend/dist
│   │   ├── settings.py             # pydantic-settings 读 .env
│   │   ├── db.py                   # 引擎 + 会话 + 作用域三件套
│   │   ├── schema_audit.py         # 模型 ↔ 生产库 Postgres 只读审计
│   │   ├── platform/               # auth / settings / jobs / schedules / failure
│   │   ├── materials/              # sources / ingest / enrich / search / usage
│   │   ├── channels/               # engine.py 共享状态机 + 渠道配置与提示词
│   │   ├── analytics/              # accounts / metrics / reports
│   │   ├── integrations/           # douyin / ima / wechat_publisher / llm / search / browser
│   │   ├── api/                    # 路由层（薄）
│   │   ├── models/                 # SQLModel 表定义（§5）
│   │   └── prompts/                # shared/ + channels/{persona,leadgen,wechat,xhs}/
│   ├── migrations/                 # Alembic versions（唯一 schema 变更通道）
│   └── tests/                      # 仅冒烟辅助（无验收套件）
├── frontend/src/pages/             # Today / Materials / Persona / Leadgen / Wechat / Xhs / Analytics / Tasks / Settings
├── scripts/                        # 部署运维（launchd / deploy-linux / sync-server / watchdog / pg-backup）
├── data/                           # 媒体/配图/缓存/备份（业务库在 Postgres）
├── docs/
└── .env.example
```

## 4. 配置与密钥（分层：.env 引导项 + 数据库运行时项）

**分层原则**：`.env` 只放"进程启动前就必须知道"的引导/部署项；其余运行时可改项存数据库
（`app_settings`，随每日备份进快照）。数据库值优先于 .env 默认值；密钥只写不读
（GET 只回报是否已配置）。

| 键 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | — | `postgresql+psycopg://user:pass@127.0.0.1:5432/content_studio`（必填） |
| `DATA_DIR` | ./data | 媒体/配图/缓存/备份根目录（数据库不在其中） |
| 启动端口 | 启动脚本直传 uvicorn | macOS 0.0.0.0:8100；Linux 127.0.0.1:8100 走 Nginx 反代 |
| `ZHIPU_API_KEY` | — | 主 LLM 密钥（也可在设置页写库） |
| `LLM_MODEL` | glm-5.3-flash | 设置页运行时可改 |
| `ASR_MODEL` | large-v3 | faster-whisper 型号 |
| `SEARCH_PROVIDER` | auto | auto=智谱搜索优先 DDG 兜底 / ddg / glm |
| `ZHIPU_BASE_URL` / `ZHIPU_SEARCH_BASE_URL` | 智谱 | 生成端点与独立搜索端点 |
| `CS_BOOTSTRAP_PASSWORD` | — | 首次启动引导管理员的确定性口令 |
| `CS_REAPER_SEC` | 1800 | reaper 巡逻间隔（冒烟隔离实例设 5） |
| `CS_MAX_ACTIVE_PER_TENANT` | 30 | 活跃任务上限 |

**数据库运行时配置（`app_settings`，受管键登记于 `settings_store.MANAGED`）**：
`llm_model / zhipu_api_key / zhipu_base_url / search_provider / zhipu_search_base_url /
asr_model / scrape_proxy / douyin_proxy / douyin_ua / watch_scan_enabled / watch_scan_hour /
creator_data_enabled / ima_kb_enabled / dissect_comment_min / dissect_digg_min /
pool_peer_min_comments / hook_speed_cps / hook_ban_prefixes / coverage_min / judge_pass_line /
daily_card_limit / jail_window_days / pool_pick_max / pool_peer_window_hours /
comment_leadgen_route_min / backfill_window_days / adapt_gen_line /
wechat_app_id / wechat_app_secret / wechat_publish_enabled`。

- `wechat_app_secret` 只写不读；`wechat_publish_enabled` 关闭时发布按钮只给人工复制路径。
- ima 凭证三键 `ima_client_id / ima_api_key / ima_kb_ids` 与 `ima_topic_folders` 为手工配置
  （接口与界面不提供编辑入口）。
- 载入时机=启动时（init_db 之后 `settings_store.load_overrides()`）；改 ASR 模型清进程内缓存。

## 5. 数据模型（PostgreSQL）

通用约定：

- 表名/字段 snake_case；主键 `id BIGINT GENERATED BY DEFAULT AS IDENTITY`；
- `created_at/updated_at` 为 `timestamp without time zone`，全库 naive-UTC；
- 结构化扩展字段用 `JSONB`；`tenant_id` 单用户版恒为 1（作用域机制保留，见 §14）；
- 状态字段用短英文枚举（注释在模型里），前端只做展示映射；
- 所有外键建索引；列表查询字段（status/date/channel_id）建二级索引；
- 归档=状态位或 `archived`，不做物理删除（任务/指标/发布史）。
- 搜索：`materials.title/body/transcript` 先建 `pg_trgm` GIN 索引支持 ILIKE 模糊检索；
  中文分词全文检索留待数据量证明需要后引入（不预装扩展）。

### 5.1 平台域（7 表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `tenants` | name, brand(JSONB 九字段) | 工作室档案（恒 1 行） |
| `users` | tenant_id, username(唯一), password_hash(scrypt), role, display_name, status | 单管理员 |
| `user_sessions` | token_hash(唯一), user_id, tenant_id, expires_at | 服务端会话 |
| `app_settings` | key(主键), value(JSONB) | 运行时配置（密钥只写不读） |
| `jobs` | type, status, progress, message, payload/result/history(JSONB), error, dedup_key, lane, retry_count/max_retries/next_retry_at, fail_class, error_fp, archived, retry_of, tenant_id, user_id | 统一异步任务（§7） |
| `schedules` | key(唯一), enabled, hour, last_run_at, next_run_at, last_status, run_count | 调度运行态 |
| `failure_patterns` | fp(唯一), fail_class, label, root_cause, count, sample_error, status | 错误指纹库 |

### 5.2 素材域（4 表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `sources` | tenant_id, kind(peer_account/radar_topic/kb_folder/hotlist/manual), platform(douyin/wechat/xiaohongshu/web/ima), name, handle, ext_id, url, status(candidate/active/disabled), config(JSONB), last_sync_at, note | 采集源与账号候选统一表；候选=status candidate，定夺后转 active |
| `materials` | tenant_id, source_id?, origin_type(douyin_video/douyin_image/wechat_article/web_page/kb_doc/manual_note/test_record/image/audio/video), origin_id, origin_url, author, title, body, transcript, media(JSONB), stats(JSONB), tags(JSONB), quality(JSONB), stage, status(inbox/ready/ignored/archived), content_hash, collected_at, published_at, note | 统一素材；成稿不进本表。`stage` 存来源特有流程态（如 test_record：pending/testing/tested/scripted/dismissed），`status` 只表示素材可用性 |
| `material_analyses` | material_id, kind(transcript/breakdown/summary/comments/style), payload(JSONB), text, model, version | 分析版本留痕；同 material+kind 取最新 version |
| `material_usages` | material_id, target_type(idea/content), target_id, role(source/evidence/quote/inspiration) | 引用追溯（不做效果归因） |

**素材状态机**：`inbox → ready / ignored`；`archived` 为人工归档。
采集器只写 inbox；质量闸可自动置 ignored（quality.gate_reason 留痕，可翻案）。

**quality JSONB 约定**：`{score, heat, comment_kind(persona_ore|leadgen_ore), leadgen_share,
gate_reason, comments_top:[{t,d}], stats_source}`。

### 5.3 内容域（5 表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `channels` | key(唯一: persona/leadgen/wechat/xhs), name, platform(douyin/wechat/xiaohongshu), enabled, config(JSONB) | 渠道注册表（种子 4 行）；config 含 positioning/audience/persona/prompt_set/gates/publish_mode/cadence/cta_policy/content_kind |
| `ideas` | tenant_id, channel_id, batch_date, source_kind(material/hotspot/radar/manual/falsify), title_hint, angle, spine, hook, spice, takeaways(JSONB), judge(JSONB), gates(JSONB), payload(JSONB), status(draft/approved/consumed/discarded/jail), superseded_by, note | 统一选题/靶子卡；渠道差异进 payload |
| `contents` | tenant_id, channel_id, idea_id?, parent_content_id?, kind(video_script/long_article/note), title, body, versions(JSONB), payload(JSONB), status(drafting/review/ready/published/archived/failed) | 统一稿件；payload 存渠道产物（提词器/分镜/标题候选/HTML/封面/标签） |
| `publications` | tenant_id, content_id, account_id, platform, url, external_id, published_at, publish_mode(manual/wechat_draft/wechat_submit), payload(JSONB) | 发布登记；external_id=公众号 draft/media id |
| `channel_styles` | tenant_id, channel_id, version, digest, traits(JSONB), exemplars(JSONB), based_on | 风格画像版本（X12） |

**内容状态机**：

```
idea:    draft ──► approved ──► consumed
            └──► discarded / jail（弃卡，可捞回）
content: drafting ──► review ──► ready ──► published
            └──► failed（可重试）
```

- `drafting`=生成/打磨中；`review`=待定稿/待决策；`ready`=定稿/待发；
- 一个 `content` 可由多个素材支撑（material_usages）；`parent_content_id` 记派生关系
  （脚本→长文）。

### 5.4 数据域（3 表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `accounts` | tenant_id, channel_id, platform, name, handle, ext_id, profile_url, auth(JSONB), enabled, note | 自有账号；creator 登录态引用存 auth（只写不读） |
| `metrics_snapshots` | tenant_id, subject_type(account/publication), subject_id, platform, date, metrics(JSONB), source(manual/creator_center/public) | 指标事实表；唯一键 (tenant,subject_type,subject_id,date) |
| `analytics_reports` | tenant_id, kind(weekly/monthly), channel_id(0=全渠道), period_start, payload(JSONB) | 周报/阶段报告缓存（可重算） |

**metrics JSONB 约定**：

- account：`{fans, new_fans, cancel_fans, profile_views, play, digg, comment, share,
  account_search, post_search, leads}`；
- publication（公众号）：`{reads, welook, follows, leads}`；
- publication（抖音/小红书）：`{exposure, play, likes, comments, shares, collects, follows, leads}`；
- 同日多来源按列合并（空值不覆盖），`source` 记最后一次写入来源。

### 5.5 运维域（1 表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `pipeline_ledger` | tenant_id, day, pipeline(material_kb_mine/wechat_adapt/persona_ideas/xhs_notes...), produced, reason, ok | 产线当日账；`ok=true` 且 produced 空且 reason 空=异常 |

**目标表数：20 张**（平台 7 + 素材 4 + 内容 5 + 数据 3 + 运维 1）。

## 6. API 契约

约定：JSON in/out；异步操作统一返回 `202 {job_id, ...}`，前端轮询 `GET /api/jobs/{id}`；
错误返回 `{"detail": "..."}` + 恰当状态码；列表统一 `{items,total,counts}`。

### 认证（/api/auth，全站门禁）
- `POST /api/auth/login` `{username,password}` → Cookie 会话；停用用户 401；响应含 tenant_name
- `POST /api/auth/logout` / `GET /api/auth/me` / `POST /api/auth/change-password`
- 未登录访问业务接口一律 401；`/api/health` 与登录接口豁免

### 基础
- `GET /api/health` → `{status, app, version, db, llm:{provider,model,configured}, deps:{playwright,postgres}}`
- `GET /api/today` → 跨模块待办计数与入口
  `{materials_inbox, ideas_pending, contents_review, publications_pending, metrics_missing}`
- `POST /api/llm/ping` `{prompt}` → `{reply}`
- `GET /api/jobs?...` / `POST /api/jobs/{id}/retry` / `GET /api/jobs/{id}` /
  `DELETE /api/jobs/{id}`（归档）/ `POST /api/jobs/clear` / `GET /api/jobs/failures`
- `GET /api/schedules` / `POST /api/schedules/{key}/toggle|run` / `PUT /api/schedules/{key}/hour`

### 素材库
- `GET /api/materials?status=&origin_type=&source_id=&q=&tag=&limit=&offset=`
  → `{items,total,counts{inbox,ready,ignored,archived}}`
- `GET /api/materials/{id}` → 素材 + 最新分析 + 引用记录（usages）
- `POST /api/materials`（一手素材：JSON 或 multipart）→ 201
- `PUT /api/materials/{id}` `{title?,body?,tags?,note?,status?}` → 200
- `POST /api/materials/{id}/status` `{status: ready|ignored|archived}` → 200
- `POST /api/materials/{id}/enrich` `{kind: transcript|breakdown|comments}` → 202 `{job_id}`
- `POST /api/materials/analyze-url` `{url}` → 202 `{job_id}`（贴链接在线拆解）
- `POST /api/materials/upload`（multipart 视频/音频）→ 202 `{job_id}`（本地上传拆解）
- `POST /api/materials/backfill` `{scope: peer|topic}` → 202 `{job_id}`

### 采集源
- `GET /api/sources?kind=&status=&q=` → `{items,total}`
- `POST /api/sources` `{kind, platform, name, url?, ext_id?, config?}` → 201
- `PUT /api/sources/{id}` / `DELETE /api/sources/{id}`
- `POST /api/sources/{id}/sync` → 202 `{job_id}`（单源扫描/同步）
- `POST /api/sources/discover` `{keyword, direction?}` → 202 `{job_id}`（账号候选发现）
- `POST /api/sources/hl-search` → 202 `{job_id}`（同城矩阵挖掘）
- `GET /api/sources?status=candidate` → 候选清单；`POST /api/sources/{id}/activate|dismiss`

### 渠道与选题
- `GET /api/channels` → `{items:[{key,name,platform,enabled,config}]}`
- `GET|PUT /api/channels/{key}` → 渠道配置读写（仅管理员）
- `GET /api/channels/{key}/ideas?status=&date=` → `{items,counts}`
- `POST /api/channels/{key}/ideas`（手动选题）→ 201
- `POST /api/channels/{key}/ideas/generate` → 202 `{job_id}`（渠道选题生成；人设号=选题工场）
- `POST /api/channels/{key}/ideas/{id}/approve|discard|revive` → 200
- `POST /api/channels/{key}/ideas/{id}/compose` `{hook?,spine?,angle?}` → 202 `{content_id,job_id}`

### 稿件与发布
- `GET /api/channels/{key}/contents?status=&limit=&offset=` → `{items,total,counts}`
- `GET /api/contents/{id}` / `PUT /api/contents/{id}` `{title?,body?,payload?}`（人工改稿→review）
- `POST /api/contents/{id}/polish` → 202 `{job_id}`
- `POST /api/contents/{id}/finalize` → 202 `{job_id}`（视频三产物/长文排版/笔记封面）
- `POST /api/contents/{id}/render` `{md?}` → 完整对象（含 html；公众号）
- `GET /api/contents/{id}/wechat-copy` → `{html}`（签名图片地址）
- `POST /api/contents/{id}/publish`
  `{mode: manual|wechat_draft|wechat_submit, account_id?, url?}` → 202 `{job_id}` 或 201 publication
- `GET /api/publications?channel=&limit=` / `PUT /api/publications/{id}`（补 URL/时间）
- `DELETE /api/contents/{id}` → 解绑引用不删下游

### 实测打假（人设号）
- 底层数据=`materials`（origin_type=test_record，`stage` 走 pending/testing/tested/scripted/dismissed）；
  `/api/falsify` 是面向该子集的领域门面，不单独建表。
- `GET /api/falsify?status=` / `POST /api/falsify` `{claim,target?,evidence_note?}`
- `PUT /api/falsify/{id}` `{status?,result_note?}` / `POST /api/falsify/{id}/compose` → 202

### 风格画像（X12）
- `GET /api/style/overview?channel=` → 账号/最新画像/分析进度
- `GET /api/style/videos?channel=&status=&limit=&offset=`
- `POST /api/style/videos/{id}/resolve` `{action: approve|ignore}`
- `POST /api/style/recompute` `{channel}` → 202 `{job_id}`

### 数据
- `GET /api/accounts?channel=` / `POST /api/accounts` / `PUT /api/accounts/{id}` / `DELETE`
- `POST /api/accounts/{id}/sync` → 202 `{job_id}`（创作者中心日级回流）
- `GET /api/metrics/accounts?account_id=&days=30` → 升序序列 + 最新摘要
- `GET /api/metrics/publications?channel=&days=30`
- `PUT /api/metrics/publications/{id}` `{date, metrics}`（人工补录 48h/小红书）
- `GET /api/analytics/overview?days=30` → 各渠道汇总与趋势
- `GET /api/analytics/channels?days=30` → 渠道对比
- `GET /api/analytics/accounts?days=30` → 账号趋势
- `GET /api/analytics/publications?channel=&limit=` → 作品表现
- `GET /api/analytics/weekly?week=` → 周报（缓存）
- `POST /api/analytics/weekly/recompute` → 202 `{job_id}`

### 设置
- `GET /api/settings` → 模型/检索/采集/闸线/公众号发布配置态（不回传密钥）
- `PUT /api/settings` → 运行时写库（非法值 400）
- `GET|PUT /api/tenant/brand` → 品牌九字段
- `GET /api/crawl/status` / `POST /api/crawl/probe`

## 7. 后台任务框架与调度

- 一切待处理工作都是任务：`jobs` 表 + 进程内 runner。五要素=类型+去重键+分道+重试策略+失败分类。
- **提交**：`submit(type, payload, dedup_key="", actor=None)`；活跃任务同 type+dedup_key 唯一。
- **分道**：`heavy`=LLM 长任务单 worker 串行；`light`=扫描/识别类并发池（默认 2，
  `CS_LIGHT_CONCURRENCY` 可配）。
- **异常分类**：`transient/captcha/quota` 共用退避梯子 `30min→2h→6h→24h`，
  之后每日慢巡 `PATROL_CAP=30` 次；`deterministic` 立即终态；失败指纹落 `failure_patterns`。
- **重试策略**：`RetryPolicy` 按 type 注册；人工重试清零自动重试计数。
- **闭环判定**：同 type+同 dedup/实体且 id 更大的 succeeded 任务存在=`covered_by`。
- **服务重启自愈**：`queued` 原行回内存队列；`running` 排 90s 自动重试。
- **归档制**：终态任务 7 天后 `archived=1`，不物理删除。

**调度 REGISTRY（12 项，均为代码事实）**：

| 钟点 | key | 执行体 | 说明 |
|---|---|---|---|
| 0:00 | `wechat_hotspot_daily` | `wechat_hotspot` | 站外热点报题建稿 |
| 1:00 | `material_watch_scan` | `material_watch_scan` | 同行采集源扫描（`watch_scan_hour` 可配） |
| 1:00 | `material_self_scan` | `material_self_scan` | 自有账号作品回流/风格素材 |
| 2:00 | `material_hl_search` | `material_hl_search` | 同城组局号搜索矩阵 |
| 3:00 | `daily_backup` | 内联 | `pg_dump` 每日快照 |
| 4:00 | `material_kb_mine` | `material_kb_mine` | ima 知识库评估入素材（`ima_kb_enabled`） |
| 4:00 | `data_creator_sync` | `data_creator_sync` | 自有账号日级指标（`creator_data_enabled`） |
| 5:00 | `wechat_adapt_daily` | `wechat_adapt` | 清单取项改编成文（`ima_kb_enabled`） |
| 5:00 | `data_weekly_report` | `data_weekly_report` | 周报自愈（按周幂等） |
| 6:00 | `persona_idea_daily` | `persona_idea_factory` | 人设号选题工场 |
| 6:00 | `media_cleanup` | 内联 | 拆解/自有媒体清扫 |
| 每 2h | `crawl_probe` | 内联 | 抖音采集探针 |

**执行器（按域，约 20 类）**：

- 素材：`material_watch_scan / material_self_scan / material_resolve / material_discover /
  material_backfill / material_topic_backfill / material_hl_search / material_analyze /
  material_style_analyze / style_profile_update`
- 内容：`persona_idea_factory / script_generate / script_polish / script_finalize /
  article_generate / article_adapt / wechat_hotspot / article_style_extract /
  note_generate / wechat_publish`
- 数据：`data_creator_sync / data_weekly_report`

**任务类型策略要点**：`material_analyze` 45min / `article_generate` 60min /
`script_generate` 40min / `wechat_hotspot` 15min / `wechat_publish` 10min /
`note_generate` 20min；看门狗 `stuck_after` 默认 2h；所有进度落 `history`（≤80 条）。

**LLM max_tokens 预算（实测输出×2，贴上限即截断）**：

| 值 | 用途 |
|---|---|
| 20000 | article_adapt_write / script_generate / script_polish / falsify_script（thinking 开启） |
| 16000 | style_profile_update |
| 12000 | article_section_* / style_extract |
| 8192 | article_title / article_outline / kb_polish / adapt_reduce / adapt_revise |
| 8000 | script_finalize |
| 5000 | material_analyze（拆解） |
| 4000 | charts / hotspots / leadgen_judge / pool_pick / judge_cards / falsify_hunt / spine_forge |
| 2048 | weekly_report |
| 2000 | kb_miner / hook_cast / note_polish |
| 1024 | review_facts / review_reader / title_score / style_pick / ping |

## 8. 模块设计要点

### 8.1 素材采集与加工（materials/）

- **采集源统一**：`sources` 一行一个源；peer_account 用锚点视频/分享主页/PC 主页三链路瀑布；
  radar_topic 走话题精选集；kb_folder 走 ima 目录游标；hotlist 走百度/知乎/科技 RSS/GitHub/AIHot；
  manual 走手填/上传。
- **入库**：采集器只写 `materials`（status=inbox），按 `content_hash` 去重；
  媒体下载→转写→拆解→评论存档→分型，分析落 `material_analyses`。
- **质量闸**：评论/赞双线过闸自动批准加工；不过线保留行标 ignored（原因可见）；
  存量回填同一入口；播放数匿名恒不可得，不参与闸线。
- **搜索**：`title/body/transcript` 的 `pg_trgm` GIN + ILIKE；结果按来源/状态/标签过滤。
- **引用**：建稿/改编时写 `material_usages`；素材详情展示被引用位置（只追溯）。

### 8.2 共用内容引擎（channels/）

- `engine.py` 提供统一状态机：取料→选题→稿件→流转；渠道插件提供：
  `select_materials(config, n)`、`generate_ideas(config, materials)`、
  `generate_content(config, idea)`、`gates(config, content)`、`finalize(config, content)`、
  `publish_adapter(config, content)`。
- **人设号插件**：选题工场五段（信号→池子→主线→钩子→法官）+ 三段脚本纪律 + 实测打假。
- **获客号插件**：获客矿取料（comment_kind=leadgen_ore）+ 需求清单 + 引导纪律。
- **公众号插件**：热点直出 + 源文改编四道闸 + 长文五视角 + 渲染器。
- **小红书插件**：图文笔记 + 封面模板 + 标签；手动发起。
- 所有渠道共用：合规词表、标题候选、风格注入、发布登记、指标回流。
- Prompt 目录：`prompts/shared/`（脚手架/合规/风格）+ `prompts/channels/{channel}/`；
  模板占位符 `render()` 强制校验，未提供的键直接 RuntimeError（防静默失效）。

### 8.3 公众号发布适配器（integrations/wechat_publisher.py）

- 凭证：`wechat_app_id / wechat_app_secret`（设置页只写）+ 公众号后台 IP 白名单（服务器 IP）。
- `access_token` 进程内缓存（7200s，提前 5 分钟刷新）；失败重取一次。
- 封面：从 `contents.payload.cover` 或品牌默认头图；`media/upload` 得 `thumb_media_id`。
- 建草稿：`draft/add`（title/author/digest/content HTML/thumb_media_id）→ 回写
  `publications.external_id`（draft media_id）与 `publish_mode=wechat_draft`。
- 群发：`freepublish/submit` → `publish_mode=wechat_submit`；失败按微信 errcode 分类
  （可重试/确定性），失败可回退人工复制。
- **只由人工点按触发**；无定时/无人值守调用路径。

### 8.4 小红书（channels/xhs/）

- 选题：素材库手动选择或热点；渠道配置决定语气/长度/标签规则。
- 笔记稿：标题≤20 字、正文 300~800 字、标签 3~8；`payload.cover` 由模板渲染
  （Pillow：品牌底色+标题大字+角标），输出 `data/covers/{tenant}/xhs_*.png`。
- 发布：人工；`publications` 登记链接/时间；指标手工补录。
- 不做浏览器自动化（平台无官方接口，遵守 PRD R5.5）。

### 8.5 数据（analytics/）

- 账号日指标：创作者中心 overview/all httpx 直连，按日 upsert `metrics_snapshots`（列合并）。
- 作品指标：公众号 48h 手工补录；抖音/小红书作品手工补录；来源标记 `manual`。
- 看板：只读事实表聚合；缺数显式「未回流」，不补零。
- 周报：按周聚合 + 人设号靶子卡对照表（模糊匹配，只对照不评分），缓存 `analytics_reports`。
- 不做素材/内容效果归因；`material_usages` 仅供追溯。

### 8.6 风格画像（X12）

- 自有账号新作品：下载→ASR→形式特征拆解→`channel_styles` 版本；
  每新增 ≥5 条自动重算，可手动触发；历史版本保留。
- 注入：脚本/笔记生成时把最新画像渲染进 prompt；无画像回落内置人设并显式提示。

## 9. 部署与运维

| 服务 | 端口 | 部署 |
|---|---|---|
| Content Studio（macOS） | 0.0.0.0:8100 | LaunchAgent 常驻 + 看门狗 |
| Content Studio（Linux） | 127.0.0.1:8100 | systemd 常驻（`After=postgresql.service`）+ 看门狗 timer；Nginx 反代 |
| PostgreSQL 16 | 127.0.0.1:5432 | 同机 systemd；仅本机监听；独立数据库 `content_studio` |
| ASR 模型 | — | `data/asr-models/faster-whisper-large-v3/` |

- **发版唯一流程**：`scripts/sync-server.sh` = rsync → `alembic upgrade head` →
  `schema_audit`（模型↔Postgres 只读 diff，缺表/多表/缺列/残留列/类型不符阻断）→ 重启 →
  健康检查；在途任务拒发（`CS_FORCE_RESTART=1` 强发）。
- **备份**：`pg_dump -Fc`（每日 3 点，保留 14 份）+ 迁移前快照（保留 5 份）；
  恢复=`pg_restore`；每季度做一次恢复演练。媒体目录随文件系统备份。
- **看门狗**：`pg_isready` + `GET /api/health`；连续 2 次失败升级通知，
  有 running 连续 6 次才强拉；改 `watchdog.sh` 后必须重装 LaunchAgent/systemd timer。
- **磁盘防御**：下载前检查余量（<2GB 拒绝）；reaper 每巡 <5GB 告警；
  拆解媒体即用即弃。

## 10. 错误处理与降级

| 故障 | 行为 |
|---|---|
| LLM 调用失败 | 网关内重试 3 次（指数退避）→ job failed，可重跑 |
| ASR 无语音/空转写 | job failed(message=转写结果为空) |
| 抖音风控/验证码 | `captcha` 分类，退避重试；降级原因可见 |
| 采集源列表失败 | 任务成功但 notes 记每源降级原因；上次同步时间不推进 |
| 公众号 API 失败 | 按 errcode 分类；重取 token 一次；仍失败→job failed + 回退人工复制 |
| 数据缺回流 | 看板/周报显式「未回流」；不补零 |
| 数据库不可用 | health 503；看门狗重启；发版闸阻断 |

## 11. 安全

- 登录门禁：全站业务接口/页面未登录 401（`require_user` 路由级依赖）；scrypt 口令哈希、
  服务端会话（Cookie HttpOnly+SameSite=Lax，库存 sha256(token)）；停用用户即失效；
  登录限流（账号/IP 双窗口，429 + Retry-After）。
- 租户隔离：查询层全站过滤 + get 拦截 + 新行自动归属（§14）；平台管理员豁免。
- 密钥：LLM/检索/公众号 AppSecret/ima 只存库且只写不读；`health` 只回报配置状态。
- **公众号发布安全**：仅管理员可触发；`wechat_publish_enabled` 总开关；
  服务器 IP 白名单；发布前必须 `contents.status=ready`；发布动作记 `publications`
  与任务历史（谁、何时、草稿 id/群发结果）。
- 上传白名单：mp4/mov/aac/mp3/png/jpg，≤2GB；文章 HTML 白名单净化（bleach + CSS 白名单），
  wechat.html 带严格 CSP。
- SSRF 闸门：服务端主动请求的用户 URL 一律 `is_douyin_url()` / 域名白名单校验；
  拒绝内网地址、云元数据端点、危险协议。
- 任务错误脱敏：落库前把服务器绝对路径替换为 `<server>`。
- 全局安全响应头：CSP（`default-src 'self'`）+ nosniff + Referrer-Policy；
  前端 `safeHref()` 只放行 http/https。

## 12. 冒烟纪律（取代验收套件）

- **验收=功能冒烟实跑**：真实链路把新功能本身跑通；长周期验证由生产调度承担，
  失败可见于失败中台指纹。
- **隔离实例（Postgres 版）**：

```bash
createdb content_studio_test        # 首次
DATABASE_URL=postgresql+psycopg://cs:cs@127.0.0.1:5432/content_studio_test \
DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 \
  uvicorn app.main:app --app-dir backend --port 8200
# 跑完：pkill 干净 + dropdb content_studio_test + rm -rf data-acceptance
```

- 本机没有系统 PostgreSQL 时，开发/冒烟可用 `pgserver`（仅本地，不写进生产 requirements）：

```bash
uv pip install pgserver
backend/.venv/bin/python - <<'PY'
import pgserver
s = pgserver.get_server("data/pgdata", cleanup_mode=None)  # 常驻，不随进程退出
s.ensure_postgres_running()
s.psql("CREATE DATABASE content_studio;")                  # 首次
print(s.get_uri(database="content_studio"))
PY
# 把打印出的 postgresql://... 改成 postgresql+psycopg://... 写进 .env 的 DATABASE_URL
```

- 复制生产数据做冒烟：`pg_dump -Fc content_studio | pg_restore -d content_studio_test`；
  先清继承队列 `UPDATE jobs SET status='failed' WHERE status IN ('queued','running','parked')`。
- **隔离边界**：数据库走 `DATABASE_URL`，媒体/配图走 `DATA_DIR`；两者都必须显式覆盖，
  漏一个就会摸到生产。
- **调度类执行器**必须在隔离实例至少触发一次真实调度；模块拆分后用 `dis` 全量扫描
  本次涉及模块的顶层函数与嵌套 code object 的 LOAD_GLOBAL，逐一核对 globals 可解析。
- **数据卫生**：测试残留=0；生产对账走每日备份与失败中台指纹。
- **审计对账清单**：
  - [ ] openapi 端点 ↔ §6 逐条一致；前端 api() 每个路径都有后端实现
  - [ ] 页面零报错/未登录业务页被门禁拦截/队列仅手动进入
  - [ ] 生产库无测试残留
  - [ ] 思考型模型调用无 JSON 截断（输出未贴 max_tokens 上限）
  - [ ] PRD 每条 R/X 均有验证方式
  - [ ] 模型 ↔ 生产库零漂移（schema_audit 通过）

## 13. 风险与技术债清单

1. **Postgres 迁移**：SQLite 专属 SQL（`datetime('now')`/`PRAGMA`/`VACUUM`/`sqlite3.IntegrityError`）
   需全部替换；`db.py` 重建迁移退役，改 Alembic；`schema_audit` 重写为 information_schema 版。
2. **SQLModel 钉 0.0.42**：0.0.47 起 datetime 要求带时区，与 naive-UTC 冲突；升级需专项改造。
3. **JSONB 与旧数据**：迁移时 TEXT→JSONB 需逐列 cast，非法 JSON 先清洗（迁移脚本记录异常行）。
4. **中文检索**：Postgres 默认分词不支持中文；先 `pg_trgm`/ILIKE，全文检索等数据量证明需要。
5. **公众号 API**：认证号/AppID/Secret/IP 白名单缺一不可；`freepublish` 权限与账号类型相关；
   失败必须可回退人工。
6. **小红书无官方接口**：发布与数据人工；不做浏览器自动化。
7. **单进程任务并发**：heavy 串行 + light 并发池；吞吐不够时再上独立 worker（明确记录）。
8. **列表端点全量拉取反模式**：素材/拆解库带 transcript 大字段——分页与字段裁剪必须做在 SQL 层。
9. **媒体文件**：按 tenant 分目录；文件名不可猜测；删除按归属行清理。

## 14. 认证与作用域（实现契约）

> 边界：多租户/成员/积分/成本台账不在产品范围；作用域机制保留（tenant_id 恒为 1，
> 平台管理员豁免过滤=全量视角），供历史数据兼容。

- **上下文（auth.ACTOR）**：`ContextVar[(tenant_id,user_id,role)]`；`require_user` 设值；
  系统上下文=`(0,0,"")`；`role=platform_admin` 豁免租户过滤。
- **作用域三件套**（`db.install_tenant_scope()`）：
  ①`do_orm_execute` 自动追加 `tenant_id` 条件；②`Session.get` 补丁（他租户返回 None）；
  ③`before_flush` 新行回填当前租户。
- **归属链**：任务提交读 ACTOR → 任务行带 tenant/user → 执行器运行期恢复 ACTOR →
  实体随任务租户；同步执行器必须 `asyncio.to_thread`（contextvars 传播）。
- **调度时区**：systemd `Environment=TZ=Asia/Shanghai`（daily_hour=本地钟点）；
  库内时间全 naive-UTC（Python `utcnow()`）。
- **抖音抓取出口**：UA 由 `browser.ua()` 按平台生成（UA 与 navigator.platform 一致）；
  匿名采集默认机房直连，`scrape_proxy` 可配住宅兜底；`douyin_proxy` 只用于 DDG 出海与
  创作者中心登录态；出口不可达时错误信息显式标注所用代理。
- **公众号登录态**：仅用于自有账号数据回流，永不爬取他人内容（红线）。

## 15. 迁移映射（迁移期专用，P5 收口后删除）

**原则**：Expand-Contract。先建 Postgres 等价库并全量搬数据；新域表与旧表并存，
双写/回填后切读；旧表在 P5 停写并 DROP。所有旧 id 原样保留，迁移后重置 identity 序列。

| 旧表/字段 | 新表 | 映射规则 |
|---|---|---|
| `watch_accounts`(competitor) | `sources` | kind=peer_account, status=active/disabled, config 存评分/账号类型 |
| `watch_accounts`(self) | `accounts` | channel_id=persona（或按 account_type），auth 存 creator cookie 引用 |
| `watch_candidates` | `sources` | kind=peer_account, status=candidate；keyword/direction/sample 进 config |
| `radar_topics` | `sources` | kind=radar_topic, ext_id=ch_id；seen_ids/hits 进 config |
| `benchmark_videos` | `materials` + `material_analyses` | origin_type=douyin_video/local；transcript/analysis/top_comments 拆到 analyses；comment_kind/leadgen_share 进 quality |
| `content_items` | `materials` | origin_type=kb_doc, origin_id=media_id；评分/改编角度/原话进 quality/payload；pending→inbox，low→ignored，done/published→ready |
| `topic_cards` | `ideas` | channel_id=persona；pool/spine/hook/judge/gates 原样；status 映射 draft/approved/discarded/jail |
| `falsify_items` | `materials`(test_record) | claim→title, evidence_note→body, result_note→note/body；status→stage（pending/testing/tested/scripted/dismissed）；成稿引用走 usages |
| `scripts` | `contents` | kind=video_script；channel 由 account_type 决定；versions/final_text/teleprompter/storyboard 进 body/payload；card_id→idea_id，ore_id→material_usages |
| `articles` | `contents` + `publications` | kind=long_article, channel_id=wechat；md/html/note→body/payload；script_id→parent_content_id；publish_url/published_at→publications；stats→metrics_snapshots |
| `self_videos` | `publications` + `metrics_snapshots` + `channel_styles` | channel=persona；style_analysis→publication.payload/风格素材；stats→metrics |
| `account_metrics_daily` | `metrics_snapshots` | subject_type=account, date 原样；source=creator_center |
| `video_weekly_reports` | `analytics_reports` | kind=weekly, period_start=week_start |
| `style_profiles` | `channel_styles` | 按 account_name 归渠道；version/based_on 原样 |
| `daily_ledger` | `pipeline_ledger` | phase→pipeline；produced/reason/ok 原样 |
| `jobs/schedules/failure_patterns/app_settings/users/sessions/tenants` | 同名表 | 结构等价迁移；jobs 历史保留；schedules 旧 key 按映射改名 |

**调度 key 映射**：`watch_scan→material_watch_scan` · `self_scan→material_self_scan` ·
`article_hotspots_daily→wechat_hotspot_daily` · `article_kb_miner_daily→material_kb_mine` ·
`article_adapt_daily→wechat_adapt_daily` · `card_line_daily→persona_idea_daily` ·
`creator_data_daily→data_creator_sync` · `video_weekly_report→data_weekly_report` ·
`hl_search_daily→material_hl_search` · `daily_backup/media_cleanup/crawl_probe` 不变。

**迁移安全**：切换前 `pg_dump` + 旧 SQLite 快照双备份；ETL 后核对每表行数与关键状态
校验和；旧 SQLite 只读保留 N 天可回滚；schema_audit 通过后才允许发版。
