# TDD · Technical Design Document（技术设计）

> 技术契约（预期侧）：架构 / 数据模型 / API 契约 / 模块设计 / 部署 / 验收对照。
> 契约变更必须同步 PRD.md 与 acceptance/（测试方案见 TEST.md，审计规程见 AUDIT.md）。

# Content Studio · 技术方案

> 定位：实现前技术定稿。API 契约、数据模型以本文为准；各期验收标准以 `acceptance/` 脚本为准（脚本即契约的可执行形式）。

---

## 1. 总体架构

```
┌──────────────────────────── 本机 / 局域网 ────────────────────────────┐
│                                                                      │
│  浏览器 ──► Content Studio Web 站 (:8100，登录门禁)                   │
│              │  FastAPI (backend/)     React 构建产物静态托管         │
│              │    ├─ api/  REST 路由（认证 / 业务 / 管理后台）        │
│              │    ├─ auth  会话 + 租户上下文（ACTOR）                 │
│              │    ├─ jobs   进程内异步任务（下载/ASR/渲染/LLM长任务） │
│              │    ├─ SQLite data/studio.db（单库存一切，租户隔离）    │
│              │    └─ data/  上传素材、配图、成片、备份              │
│              │                                                       │
│              ├──► GLM API（智谱：生成主通道 + 独立搜索端点）          │
│              ├──► 蝉镜开放 API（数字人引擎）+ 本地引擎 faster-whisper │
│              │    / FFmpeg                                          │
│              └──► 人工发布（发布永远人工，R6.5）                       │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

关键决策（对应 MRD.md 4.0/4.7 节）：

- **复用边界**：下载/上传走外部服务或浏览器直连（零代码耦合）；ASR 走 pip 库；公众号排版 vendor doocs/md 渲染核心。自研仅为 Web 控制台 + 工作流编排 + 业务 Prompt 库。
- **进程内任务框架**：不上 celery/redis/message queue，后台任务用进程内 asyncio + 线程池（ASR/FFmpeg 是 CPU 密集），任务状态落 `jobs` 表，前端轮询；heavy 队列跨租户轮转 + 每租户活跃上限（R11.7）。
- **多租户**：租户/成员/三级角色 + 共享积分池（契约见 §15）；查询层全站租户过滤。
- **发布永远人工**：系统只到"物料包 + 半自动填表"，无任何自动发布代码路径。

## 2. 技术栈与版本基线

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | Python 3.12+ / FastAPI / uvicorn | 绑定 0.0.0.0（局域网可访问，登录门禁必开） |
| ORM | SQLModel + SQLite | WAL 模式 |
| LLM | 智谱 GLM（默认 `LLM_MODEL=glm-5`，可改旗舰/Flash） | DeepSeek 仅作备选开关 |
| ASR | faster-whisper（large-v3 起步 + VAD） | 中文场景可换 FunASR SenseVoice，接口不变 |
| 视频 | FFmpeg（brew） | 工具箱角色 |
| 前端 | React 18 + Vite 5 + TailwindCSS | 中文界面；构建产物由 FastAPI 托管 |
| 发布 | 人工（发布永远人工，物料包辅助） | R6.4 裁定 sau 集成移除 |
| 验收 | acceptance/ 纯标准库 Python 脚本 | 见 §13 |

## 3. 目录结构

```
content-studio/
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI 入口 + 静态托管 frontend/dist
│   │   ├── settings.py         # pydantic-settings 读 .env
│   │   ├── api/                # 路由：auth/admin/health/topics/scripts/media/articles/avatar/publishing/jobs/llm/schedules/radar/watch/style/benchmarks
│   │   ├── auth.py             # 口令哈希 / 会话 / 租户上下文（ACTOR）
│   │   ├── credits.py          # 积分池：预扣/充值/流水
│   │   ├── db.py               # 引擎 + 迁移 + 租户隔离三件套
│   │   ├── models/             # SQLModel 表定义（§5）
│   │   ├── jobs/               # 任务框架：提交/轮询/线程池执行器
│   │   ├── llm/                # LLM 网关：provider 抽象、用量记录、ping
│   │   ├── topics/             # idea深研 / 评分
│   │   ├── benchmarks/         # 同行拆解：下载→ASR→LLM拆解
│   │   ├── scripts/            # 脚本工场：模板库 / 3版生成 / 批判打磨
│   │   ├── articles/           # 长文生成 / doocs渲染器 / 导出
│   │   ├── publishing/         # 物料包 / 发布记录 / 指标回流
│   │   └── prompts/            # Prompt 模板（md 文件 + 版本号，代码加载）
│   └── vendor/                 # doocs/md 渲染核心（WTFPL，摘录注明出处）
├── frontend/                   # React + Vite + Tailwind
├── acceptance/                 # 验收脚本（本文档 §13 的可执行形式）
├── data/                       # studio.db + uploads/ artifacts/ backups/
├── docs/
└── .env.example
```

## 4. 配置与密钥（分层：.env 引导项 + 数据库运行时项）

**分层原则**：`.env` 只放"进程启动前就必须知道"的引导/部署项；其余运行时可改项存数据库
（`app_settings` 键值表，随每日备份进快照，服务器上无需改文件）。数据库值优先于 .env 默认值；
密钥类只写不读（GET 只回报是否已配置）。

| 键 | 默认 | 说明 |
|---|---|---|
| `HOST` / `PORT` | 0.0.0.0 / 8100 | 局域网可访问（8000 被其他项目占用） |
| `DATA_DIR` | ./data | 一切落盘根目录 |
| `LLM_PROVIDER` | glm | glm / deepseek |
| `ZHIPU_API_KEY` | — | 主 LLM 密钥 |
| `DEEPSEEK_API_KEY` | — | 备选 |
| `LLM_MODEL` | glm-5 | 以控制台可用型号为准 |
| `ASR_MODEL` | large-v3 | faster-whisper 型号 |
| `CHANJING_APP_ID` / `CHANJING_SECRET_KEY` | — | 蝉镜开放平台凭证（口播数字人引擎） |
| `SEARCH_PROVIDER` | auto | 检索通道：auto=智谱搜索优先 DDG 兜底 / ddg / glm（按量余额可用；设置页运行时切换） |
| `ZHIPU_BASE_URL` / `ZHIPU_SEARCH_BASE_URL` | 智谱 | 生成端点与独立搜索端点（`/web_search`）可分账 |
| `CS_BOOTSTRAP_PASSWORD` | — | 首次启动引导管理员的确定性口令（验收实例必带；不设则随机生成仅日志打印） |
| `CS_REAPER_SEC` | 1800 | reaper 巡逻间隔（验收隔离实例设 5 加速快巡） |
| `CS_MAX_ACTIVE_PER_TENANT` | 30 | 每租户活跃任务上限（R11.7） |

`.env` 不进版本库；`health` 接口回报各依赖配置状态（不回传密钥）。

**数据库运行时配置（`app_settings`，设置页可改，写入即生效）**：
`llm_provider / llm_model / zhipu_api_key / deepseek_api_key / zhipu_base_url /
search_provider / search_enabled / zhipu_search_model / zhipu_search_base_url /
asr_model / watch_scan_enabled / watch_scan_hour / chanjing_app_id / chanjing_secret_key`。
载入时机=启动时（init_db 之后 `settings_store.load_overrides()`）；改 ASR 模型会清进程内模型缓存。

## 5. 数据模型（SQLite）

通用约定：`id` 自增主键；`created_at/updated_at` 自动；JSON 字段用 TEXT 存 JSON；
业务表带 `tenant_id`（租户隔离边界，查询层全站过滤——见 §15）；
`jobs`/`llm_calls`/`bean_ledger`/`credit_transactions` 等账务表按各自口径带租户或系统归属。

| 表 | 关键字段 | 说明 |
|---|---|---|
| `topics` | title, angle, audience(boss/fde/both), source_type(idea/benchmark/manual), source_ref, evidence(JSON), score, score_breakdown(JSON 仅内容评分维度), status(draft/approved/rejected/produced) | 选题库；评分不含发布数据回流（R1.6 不做） |
| `research_reports` | topic_id(FK), content(MD), model, tokens_in/out, cost_est | idea 深研产物 |
| `benchmark_videos` | url, author, title, stats(JSON), transcript, analysis(JSON), media_path, source(douyin/local) | 同行拆解样本 |
| `self_videos` | url, aweme_id, title, transcript, style_analysis(JSON), analyzed_at | 我的账号视频 + 逐条风格标注（R9.2） |
| `style_profiles` | tenant_id, account_name, version, digest, traits(JSON), exemplars(JSON), based_on | 风格画像版本快照，保留历史（R9.3） |
| `scripts` | topic_id(FK), versions(JSON:[{label,hook,body,notes}]), final_text, teleprompter_text, storyboard(JSON), title_candidates(JSON), status(generating/polishing/final) | 一个选题一个脚本对象，内含 3 版本 |
| `script_templates` | name, structure(JSON), source_benchmark_id, usage_count | 结构模板库（从拆解沉淀） |
| `media_assets` | kind(footage/broll/bgm/sticker/intro/outro), tags(JSON), path, duration, meta(JSON) | 素材库 |
| `articles` | topic_id?(FK), script_id?(FK), md, html, status(draft/edited/rendered/published) | 公众号文章 |
| `publish_records` | asset_type, asset_id, platform, published_url, metrics(JSON) | 发布台账（R6.2 裁定不做后仅保留表与模型引用，低优技术债） |
| `tenants` | name, credits, status(active/disabled) | 租户；共享积分池（R11.4） |
| `users` | tenant_id(FK), username(全局唯一), password_hash(scrypt), role(platform_admin/tenant_admin/member), display_name, status, must_change_password | 租户成员 |
| `user_sessions` | token_hash(唯一), user_id, tenant_id, expires_at | 服务端会话（Cookie 只带明文 token） |
| `credit_transactions` | tenant_id, delta, balance_after, reason, kind(consume/topup/adjust), ref | 积分流水，全程审计 |
| `bean_ledger` | tenant_id, video_id, beans, seconds, model | 蝉豆消耗流水（独立于视频行，删记录不丢账） |
| `radar_topics` | tenant_id, ch_id(全局唯一), enabled, notify, seen_ids/recent/hits(JSON), last_checked_at | 话题雷达监控项 |
| `failure_patterns` | fp(唯一), kind, fail_class, label, count, sample_error, status(open/fixed/ignored) | 错误指纹库（失败中台） |
| `schedules` | key, label, enabled, hour 覆盖, last/next_run_at, last_status, run_count | 统一调度中心运行态 |
| `app_settings` | key(主键), value(JSON) | 平台运行时配置（LLM/检索/ASR/数字人凭证/扫描计划；密钥只写不读） |
| `watch_accounts` | tenant_id, platform, name, url, note, kind(competitor/self), creator_cookie(self 创作者中心登录态), enabled, last_scan_at | 对标/自有账号（R3.1/R9.1；评分口径见 MRD §2.1.1） |
| `avatar_configs` | tenant_id, name, engine, chanjing_person_id, chanjing_audio_man, chanjing_pic_url, chanjing_preview_url | 数字人配置（克隆形象+配套音色） |
| `avatar_videos` | tenant_id, avatar_id, script_id, status(generating/done/failed), seconds, beans_used, model(0基础/1高质), audio_path, video_path | 数字人生成记录 |
| `watch_candidates` | tenant_id, keyword, direction, name, sec_uid, follower_count, sample_*, status(open/added/dismissed) | 关键词发现的对标账号候选（R3.6） |
| `dismissals` | scope, ref_id | 「不做」永久出口（可恢复） |
| `jobs` | type, status(queued/running/parked/succeeded/failed/superseded), progress, message, payload/result/history(JSON), error, dedup_key, lane(heavy/light/external/scheduled), retry_count/max_retries/next_retry_at, fail_class, error_fp, archived, retry_of, tenant_id, user_id | 统一异步任务（五要素 + 归属；tenant_id=0 为系统任务） |
| `llm_calls` | tenant_id, purpose, model, tokens_in/out, cost_est, latency_ms, ok | 用量与成本台账（tenant_id=0 为系统调用） |

**内容资产状态机**（串联各表的状态流转）：

```
topic:      draft ──approve──► approved ──产出脚本──► produced
                └──reject───► rejected
script:     generating ──► polishing ──► final
article:    draft ──► edited ──► rendered ──► published（由 publish_records 触发）
```

## 6. API 契约

约定：JSON in/out；异步操作统一返回 `202 {job_id, ...}`，前端轮询 `GET /api/jobs/{id}`；错误返回 `{"detail": "..."}` + 恰当状态码。

### 认证（/api/auth，全站门禁）
- `POST /api/auth/login` `{username, password}` → Cookie 会话；停用用户 401、停用租户 403
- `POST /api/auth/logout` / `GET /api/auth/me` / `POST /api/auth/change-password` `{old_password, new_password}`
- 未登录访问业务接口一律 401；`/api/health` 与登录接口豁免

### 管理后台（/api/admin）
- 权限：租户/用户管理类仅 platform_admin；成员读列表与租户流水/用量放宽至 tenant_admin（限本租户）
- `GET /api/admin/tenants` → 含 credits/member_count/cost30/active_members30；`POST` `{name, credits, admin_username?}` → 建租户（返回一次性密码）
- `POST /api/admin/tenants/{id}/topup` `{points, reason?}`（正充负扣）/ `POST .../status` `{status}`（停用清全部成员会话）
- `GET /api/credits` → 当前租户积分余额 + 各计量任务价目（租户自查用；此前余额只在管理后台可见）
- `GET /api/admin/tenants/{id}/transactions` → 积分流水；`GET .../usage?days=` → 用量看板（LLM 台账/任务统计/蝉豆/积分/成员排行）
- `GET /api/admin/tenants/{id}/brand` → 读租户品牌（覆盖键+中性默认解析后返回）；`PUT` 九字段全量可选（label_line1/2、signature、persona、accent、primary、asr_vocab、cover_slogan、audience_note）→ 只持久化非空覆盖键、保存并即时生效（tenant_brand.py，注入角标/头图/图表/生成 prompt/ASR 词表/头图口号）。**默认策略（2026-10-02）**：未配置租户回落中性默认（通用人设/通用词表/口号不渲染）；创始租户（id=1）由 `seed_founder_brand()` 启动时一次性写入 FDE 全套（幂等），存量行为不变。受众域口径 `audience_note` 注入 idea_research_report/idea_research_struct/benchmark_analyze 三处 prompt（替代写死的行业语义；audience 枚举 boss/fde/both 的键保留，语义解释走配置）。
- `GET /api/admin/tenants/{id}/export` → 全业务表 JSON 下载；`DELETE /api/admin/tenants/{id}?confirm_name=租户名` → 物理删除（自动先备份；有活跃任务拒绝）
- `GET /api/admin/users`（platform 全平台 / tenant_admin 本租户）/ `POST`（一次性密码只显示一次）
- `POST /api/admin/users/{id}/role`（platform）/ `POST .../status` / `POST .../reset-password`（重置即踢下线+首登强制改密）
- `GET /api/admin/overview` → 平台总览（KPI/14 天趋势/租户成本排行/最近动态）

### 基础
- `GET /api/health` → `{status:"ok", app, version, db:true, llm:{provider, model, configured}, deps:{ffmpeg,docker,playwright}}`
- `POST /api/llm/ping` `{prompt}` → `{reply}`（最小真实调用，验收用）
- `GET /api/llm/usage` → `{calls, tokens_in, tokens_out, cost_est, by_purpose:[...]}`（按用途分账，R8.3；
  非平台管理员只看本租户且响应不含金额字段——R11.6）
- `GET /api/llm/calls?limit=&offset=&purpose=` → 流水明细（非平台管理员同上剔除金额）
- `GET /api/jobs?status=&type=&limit=&offset=&tenant_id=` → `{items, total, counts{...}}`（运行中置顶、排队带 queue_position；
  列表不含 result、不含已归档行；非平台管理员强制按本租户过滤，tenant_id 参数仅平台管理员可用）
- `POST /api/jobs/{id}/retry` → 202 `{job_id}`（人工重试：原记录标 superseded、清零自动重试计数、实体先回在途态；计量任务重新预扣积分）
- `POST /api/jobs/retry-failed {type?}` → 202 `{requeued:n}`（批量重试，跳过 deterministic 与已归档）
- `DELETE /api/jobs/{id}` → 归档任务记录（archived=1，不物理删；running 拒绝 400）
- `POST /api/jobs/clear` `{status: failed|superseded|succeeded}` → `{archived:n}`（批量归档终态记录）
- `GET /api/jobs/{id}` → job 终态或进行态（含 `history` 阶段轨迹）；不存在 → 404 JSON

### 设置（/api/settings，仅平台管理员）
- `GET /api/settings` → LLM 通道/检索通道/扫描计划/各配置态（不回传密钥）
- `PUT /api/settings` → 运行时写回 .env（模型/检索通道/扫描钟点；非法值 400）
- `POST /api/llm/search-probe` `{query}` → GLM 与 DDG 同查询原始证据并排返回（检索通道人工体检）

### 选题中心
- `POST /api/topics/ideas` `{text}` → 202 `{topic_id, job_id}`（深研任务）
- `POST /api/topics/quick` `{title, audience?}` → 201（快速记选题，不触发 LLM；R1.1b）
- `POST /api/topics/trending` → 202 `{job_id}`（今日热点选题提炼，R1.4；10 积分）。executor
  `trending_topics`：站内对标议题（近 2 天非 ignored 视频标题+互动）+ 联网检索（单查询纪律）×
  租户画像 → `complete_json(purpose="trending_topics", max_tokens=4000)` → 1~3 条
  `Topic(source_type="trending", evidence={hotspot, why_now, material})` draft 落库；
  原料收集失败不阻塞（对标空窗/联网关闭均降级并在 evidence.material 留痕）；
  prompt 占位符 PERSONA/AUDIENCE_NOTE/MATERIAL（render 强制校验）。
- 入库时相似选题检测（R1.1c）：与既有 draft 选题标题相似 → 标记 evidence.similar_to
- `GET /api/topics?status=&source=&audience=&min_score=&limit=` → `{items:[...]}`
- `GET /api/topics/{id}` → 详情含 `research_report`
- `POST /api/topics/{id}/approve` / `POST /api/topics/{id}/reject`
- `DELETE /api/topics/{id}` → 删选题并连带删研究报告；文章保留仅解绑 `topic_id`
- `POST /api/analyze/video` `{url}` → 202 `{job_id}`（下载→ASR→LLM 拆解，产出 benchmark + topic 候选）
- `POST /api/analyze/local`（multipart file）→ 202 `{job_id}`（跳过下载，其余同上；手工下载视频走此口）
- `GET /api/benchmarks?limit=&offset=&author=&analyzed=(done|todo)&source=` → `{items, total, counts{all,todo,done,online,local}}`（counts 全量口径；上限 100/页）
- `GET /api/benchmarks/{id}` → 详情
- `DELETE /api/benchmarks/{id}` → 删样本并连带撤回其待审选题候选（返回 `removed_candidates`）
- `POST /api/benchmarks/{id}/resolve` `{action(approve|ignore|pending)}` → 人工定夺（approve 即排队拆解）
- `POST /api/benchmarks/resolve-pending` `{action, author?}` → 批量定夺当前待定夺行（可按作者过滤）

### 同行监测（对标账号管理与扫描）
- `GET /api/watch/accounts` → `{items:[{platform, name, url, note, enabled, last_scan_at, video_total, video_recent_7d, scores}]}`；`scores` 为 `{relevance, quality, overall}` 或 null（从备注评分文本解析，口径见 MRD §2.1.1）
- `POST /api/watch/accounts` `{platform(douyin|channels), name, url, note?}` → 201；抖音必须带链接（缺 400）
- `PUT /api/watch/accounts/{id}` `{name?, url?, note?, enabled?}` → 200（部分更新）；不存在 404、空名 400
- `DELETE /api/watch/accounts/{id}` → `{deleted:id}`
- `POST /api/watch/scan` → 202 `{job_id}`（全量扫描，仅 enabled 账号；每号新视频入队 ≤10 条）
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

### 口播数字人（R10，蝉镜引擎）
- 引擎：蝉镜开放 API（API 克隆形象 + 形象配套音色原生 TTS + 平台自动字幕）；凭证 `CHANJING_APP_ID/CHANJING_SECRET_KEY`
- `GET /api/avatar/configs` / `POST` `{name, chanjing_person_id?, chanjing_audio_man?}` / `PUT /{id}` / `DELETE /{id}`
- `POST /api/avatar/configs/{id}/clone_video`（multipart 出镜视频）→ 克隆预扣 300 积分
- `GET /api/avatar/estimate?script_id=&avatar_id=&model=` → 预估（租户口径 `points`/`credits`；
  平台侧另返 `beans`/`yuan` 成本口径）；`GET /api/avatar/usage` → 成片数/秒/积分合计（`points` 对所有人，
  `beans`/`yuan` 仅平台管理员）
  （余额闸先于凭证校验；形象提交成功后扣减，训练失败不退；R11.4）
- `GET /api/avatar/persons/{person_id}` → 克隆详情（status/progress/pic_url/audio_man_id）
- `GET /api/avatar/balance` → `{beans}`；`GET /api/avatar/usage` → 累计消耗台账（yuan 字段仅平台管理员）
- 配图/成片等媒体文件按租户边界服务（见 §15）；租户侧接口不含引擎品牌与金额（R11.6）
- `GET /api/avatar/estimate?script_id=&avatar_id=&model=` → 成本预估（秒/豆/余额充足性；yuan 仅平台管理员）
- `POST /api/avatar/generate` `{script_id, avatar_id, model(0基础|1高质)}` → 202 `{job_id}`
  （executor：大纲→分节口播脚本同款分段思想不适用于此；蝉镜逐段合成→拼接→检查点续跑；成片入媒体库 kind=avatar）
- `GET /api/avatar/videos`（分页+筛选，含定稿标题/预览/三件套统计/画质档）；`DELETE /api/avatar/videos/{id}`（生成中不可删）
- `GET /api/avatar/videos/{id}/file` → 成片文件下载（租户边界随实体归属）
- 字幕：平台 asr_type=0 自动字幕（白字底部居中）；音频走 wav_url 通道（file_id 通道会静默丢音轨）

### 我的风格（自我研究，R9）
- `GET /api/style/overview` → `{account: WatchAccount|null, profile: 最新画像|null, profile_history:[...], videos:[{id,title,analyzed_at,style_analysis}], stats:{total, analyzed, pending}}`
- `POST /api/style/scan` → 202 `{job_id}`（扫描我的账号，新视频自动排队风格拆解；无 self 账号 400）。**双路扫描（2026-10-09）**：自我账号配 `creator_cookie`（创作者中心登录态，存 watch_accounts 行内，API 回显只给 has_cookie 不回凭据）→ Playwright 注入 cookie 打开内容管理页拦列表 XHR、页面上下文同源重放翻页，**全量抓取+每次刷新存量**（播放/点赞/评论/转发，self_videos.stats）；cookie 失效/任何失败→notes 可见降级公开路线（点赞快照、只抓新增）。凭据绝不出后端。
- `POST /api/style/profile/update` → 202 `{job_id}`（手动重算画像；无已分析视频 400）
- `GET /api/style/videos?status=&limit=&offset=` → `{items,total}`（分页+标注筛选；items 含 analyzed 派生字段）
- `POST /api/style/videos/{id}/resolve` `{action}` → 风格视频人工定夺（批准拆解/忽略）
- `POST /api/topics/cleanup-rejected` `{days=30}` → 清理 N 天前已否决选题（级联删报告/解绑脚本文章）
- `GET /api/style/profile/history` → `{items:[{version, digest, traits, exemplars, based_on, created_at}]}`

### 脚本工场
- `POST /api/scripts/generate` `{topic_id}` → 202 `{script_id, job_id}`（3 版生成）
- `GET /api/scripts?limit=` / `GET /api/scripts/{id}`
- `POST /api/scripts/{id}/polish` → 202 `{job_id}`（批判打磨，≤2 轮）
- `POST /api/scripts/{id}/finalize` → 终稿 + `teleprompter_text` + `storyboard` + `title_candidates`
- `GET/POST /api/scripts/templates`（结构模板库 CRUD）
- `DELETE /api/scripts/templates/{id}`（护栏：至少保留一个模板，删最后一个 400）
- `DELETE /api/scripts/{id}` → 解绑视频项目/文章引用不删下游；选题仅剩此脚本被删时 produced→approved 回退

### 公众号
- `POST /api/articles/generate` `{topic_id | script_id}` → 202 `{article_id, job_id}`
- `GET /api/articles/{id}` / `PUT /api/articles/{id}` `{md}`（保存人工改稿，status→edited）
- `POST /api/articles/{id}/render` `{md?}` → 完整文章对象（含 html，前端据此即时回显预览/状态/复制）；传 md = 渲染即保存改稿，缺省渲染库内正文（doocs 渲染器，内联样式，status→rendered）
- `GET /api/articles/{id}/wechat-copy` → `{html}` 公众号粘贴载荷：净化后的渲染产物，配图地址改写为签名公开地址（前端据此写剪贴板 `text/html`+`text/plain` 双格式；只写纯文本时公众号编辑器贴出来是 HTML 源码）
- `DELETE /api/articles/{id}` → 删文章（引用不删，选题不受影响）
- `GET /api/articles/{id}/wechat.html` → 渲染后 HTML 文件直接下载
- `POST /api/articles/generate-image` `{caption, keywords}` → AI 生图（CogView，落盘后本地服务）
- `GET /api/articles/images/{filename}` → 配图文件服务（按租户分目录只读本租户，见 §15；
  前缀白名单 gen_/chart_/cover_/flow_，防目录穿越）
- `GET /api/public/images/{filename}?t=<签名>` → **无登录门禁**的签名配图（HMAC(文件名) 前 22 字符，
  不匹配一律 404）。存在理由：公众号编辑器从 mp.weixin.qq.com 跨域抓 `<img>` 外链再转存素材库，
  带不了本站 Cookie——粘贴载荷必须用签名地址，否则图片在公众号侧是裂图

### 发布台（极简：只做物料包，发布人工）
- `POST /api/publishing/packages` `{asset_type(script/article), asset_id}` → `{douyin_title, douyin_title_alt, douyin_tags[], channels_caption, cover_text, wechat_html?}`
- ~~records/metrics 台账与指标回流~~（不做，见 PRD R6.2）；~~sau 上传~~（不做，R6.4）
- dismissals 仅保留 article scope（不做长文出口）
- `DELETE /api/publishing/dismissals/{scope}` → 撤销该范围的"不做"标记（对应文章重新回待办）

## 7. 后台任务框架（五要素统一任务体系）

- 一切待处理工作都是任务：`jobs` 表 + 进程内 runner。任务五要素 = 类型（type）+ 去重键（dedup_key）+ 分道（lane）+ 重试策略（RetryPolicy）+ 失败分类（fail_class）。
- **提交**：`submit(type, payload, dedup_key="", actor=None, points=0)`。dedup_key 非空时，同 type 下活跃任务（queued/running/parked）唯一，重复提交幂等返回原任务（活跃唯一部分索引兜底）。dedup_key 由各提交点按实体给出：拆解=分享链接（实体去重：同链接不建重复行）与 `bm:{id}`；扫描=`scan:{account_id|all}:{YYYYMMDD}`；自我扫描=`selfscan:all:{日期}`；链接识别=提取后的 URL；画像重算=全局 `profile:update`；数字人=`avatar:{script_id}`；自我风格分析=`selfanalyze:{video_id}`。
- **分道**：`heavy`=LLM 长任务串行队列；`light`=扫描/识别类并发池（上限 3，同类执行中后来者合并跳过）；`scheduled`/`external` 预留。定时任务由调度循环到点 submit（带当日 dedup 键，天然防重复触发），调度循环本身不是任务。
- **异常分类学**（`jobs/errors.py`，`classify()` 对历史 RuntimeError 按消息特征兜底分诊）：
  - `transient`（网络/超时/5xx/LLM 通道抖动）→ 按类型策略自动重试，指数退避 base×2ⁿ 封顶 cap，`next_retry_at` 到点由重试循环（30s）放行；
  - `quota`（LLM 额度窗口/余额不足）→ 任务转 `parked` 挂起（不算失败），按 `park_backoff` 周期重查，超过 `max_parks` 转失败；
  - `captcha`（平台风控/验证墙）→ 失败并打标"可人工重试"；
  - `deterministic`（默认；参数/配置/内容问题）→ 立即失败，永不自动重试。
- **重试策略**：`RetryPolicy(max_retries, base, cap, classes, park_backoff, max_parks, stuck_after)` 按 type 注册于 `_POLICIES`，提交时 `max_retries` 落到任务行。人工重试清零自动重试计数；批量"重试全部"跳过 deterministic。
- **闭环判定（失败→恢复的因果链可见）**：`_coverage()` 对每个 failed 任务检测"同 type + 同 dedup_key/同实体 id 且 id 更大的 succeeded 任务"→ 附 `covered_by`。三态语义：**待处置**（未闭环、无排期）/ **重试中**（有 next_retry_at）/ **已闭环**（covered_by 存在，绿标"由 #N 成功重跑"，自动移出待处置）。重试血缘：`jobs.retry_of` 记录由哪个失败任务重试而来（手动重试/批量重试/按指纹复活三条路径都写入），详情页显示链路。
- **统一调度中心**（`jobs/schedules.py`，Q"任务调度管理中心"）：所有周期任务一处注册（`REGISTRY`：key/label/执行体/节奏 daily_hour 或 interval_hours/设置开关字段）、`schedules` 表存运行态（enabled/hour 覆盖/last_run_at/next_run_at/last_status/run_count）、`_schedule_loop`（runner 内受监督循环，每 60s 巡检）到点执行。内置五项：同行扫描/自我扫描/话题雷达（每日，钟点默认取 `watch_scan_hour` 可按项覆盖）、业务库每日备份（4 点）、拆解媒体清扫（每 6h）。API：`GET /api/schedules`、`POST /{key}/toggle`、`PUT /{key}/hour`、`POST /{key}/run`（立即执行并记运行态）；任务页「调度中心」卡片可视化。旧 main.py 硬编码调度循环已删除。
- **同行发现·每日**（调度 key=`watch_discover_daily`，每日 2 点，随 watch_scan_enabled 开关）：关键词按天轮换（企业AI落地/AI落地 培训/企业AI 数字化转型/AI 提效 获客）走 DDG→视频链接→反查作者→粉丝 Top8 入候选表（status=open 待人工定夺；已在清单/已忽略的不重复）；候选粉丝 ≥1 万（_DISCOVER_QUALITY_FANS）计入工作台周报卡「清单外高赞同行」。
- **抖音采集探针**（`crawler_probe.py`，调度 key=`crawl_probe`，15 分钟）：先 TCP 探出口隧道
  （断=`proxy_down` 不浪费浏览器），再开真实视频页（轮换拆解库最近 20 条已定夺视频、避开上次用的）
  按形态分类 `ok / login_wall / captcha / proxy_down / browser_error / no_data`；结果落
  `data/crawl_probe.json`（最近+历史 20），**状态翻转才发系统通知**。API：`GET /api/crawl/status`
  （近 1h 爬取类失败数被动印证）、`POST /api/crawl/probe`（手动）；设置页「数据采集（抖音）」卡；
  拆解提交两处返回带 `crawl_warning`（探针异常时提交即预警）。
- **reaper**（周期巡逻，`CS_REAPER_SEC` 可配，默认 1800s，启动快速首巡）：①看门狗——`running` 超过类型策略 `stuck_after`（默认 2h）无更新判卡死，按 transient 自动重试；②欠任务——执行 `register_owed(type, fn)` 声明：fn 返回缺失工作的 `[(payload, dedup_key)]`，reaper 幂等补队。拆解已注册：`approved` 且 `analysis` 为空、无活跃任务、近 6h 无失败 → 补队（6h 退避防确定性失败空烧）。
- **进度约定**：`progress` 0~100 + `message`；阶段轨迹落 `history`（≤80 条）；终态写 `result` 或 `error`（重试中的失败带 `next_retry_at`）。
- **服务重启自愈**（`recover_interrupted_jobs`，须在 `runner.start()` 之后）：`queued` 重新入队（旧记录标 `superseded`=已续跑）；`running` 按 transient 排 90s 自动重试（重启中断不再需要人工）；`parked` 不受影响（`next_retry_at` 已持久化）。
- **观测**：任务行携带 `lane/retry_count/max_retries/next_retry_at/fail_class/dedup_key`；任务页展示分道徽章、parked 计数与自动重查时间、失败自动重试排期与失败分类标签。
- **失败中台（错误指纹 + 待处置 + 按指纹复活）**：
  - 每次失败由 runner 计算**错误指纹**（`errors.fingerprint`：规则表命中如 `llm:empty-body`/`asr:vocab`/`ddg:rate-limited`/`douyin:risk`/`db:constraint`/`code:bug`；未命中按归一化消息哈希），落 `jobs.error_fp` 并 upsert `failure_patterns` 表（fp 唯一，count 累计、样本错误、处置状态 open/fixed/ignored；fixed 后再出现自动回 open）。
  - `GET /api/jobs/failures` → 待处置盘点：失败且无重试排期且未归档的任务按指纹聚类 + **实体孤儿**组（实体 failed 且无活跃任务，合成指纹 `entity:{type}`）；`disposal`=待处置总数（进工作台计数：任务徽章=运行中+排队+待处置，非空=系统带病运行必须可见）。
  - `POST /api/jobs/retry-by-fingerprint {fp}` → 整批复活：重排前先**实体复活**（`jobs/revive.py`：failed 实体按类型拉回在途态，防二次幽灵）；实体孤儿组则直接重提生成任务。`POST /api/jobs/failures/{fp}/resolve {status, note}` → 标记已修复/忽略后移出待处置。
  - 自动重试放行（`_reenqueue`）与人工重试同样先做实体复活，failed 状态不跨重试周期。
  - 执行器可注册 `on_fail(payload, exc)` 回调（`register_executor(type, on_fail=...)`），失败时 runner 统一调用做实体状态回退；实体型执行器（script_generate/polish/article_generate/avatar_video）已全部迁移，执行器体内不再自带 try/except 回退。
- **归档制**：终态任务（failed/superseded/succeeded）超过 7 天由 reaper 归档（`archived=1`）而非物理删除；任务列表/counts/clear/单条删除均只作用于未归档行，错误指纹库的统计证据永不丢失。`POST /jobs/clear` 返回 `{archived: N}`。
- **执行器注册表**：`idea_research / benchmark_analyze / script_generate(含自动打磨一轮) / script_polish / script_finalize(定稿+标题候选) / article_generate / watch_scan(payload.account_id 单号扫描，全量仅 competitor) / watch_resolve(自动提取口令全文中的链接) / watch_discover(关键词发现对标账号，light 并发道) / self_scan / self_analyze / self_profile_update / topic_radar(话题雷达：话题下新增视频速度测热度，飙升发 macOS 通知) / avatar_video(蝉镜引擎：脚本文本→克隆形象+配套音色+自动字幕，超长自动分段)`。
- **话题雷达**：`radar_topics` 表（ch_id 唯一/seen_ids 防重/recent 最近新视频）。数据源=m.douyin.com challenge/aweme 接口（免登录，无互动统计字段）→ 热度代理=话题下新增视频速度，单次巡检新增 ≥5 条判飙升并发 macOS 通知。`GET/POST/DELETE /api/radar`、`POST /{id}/check`（单话题即时巡检）、`POST /{id}/toggle`（启停）、
`POST /check-all`（走 topic_radar 任务）、`POST /extract-hashtags` `{url}`（贴视频链接提取话题）、
`POST /mine`（从拆解库挖话题候选）；空表自动种入 3 个已知泛 AI 话题；每日随定时扫描巡检一次（X10 调度）。
- **内容合规守卫**：`article_section` 逐节写作带两道程序化守卫——①截断守卫（结尾无句读重写一次）②合规守卫（`BANNED_PATTERNS` 违禁词命中定向重写一次，与 acceptance/quality.py 同表）；均只在命中时多花一次 LLM 调用。
- **媒体即用即弃**：`benchmark_analyze` 成功即删视频文件（转写/拆解已入库；重拆解自动重新下载，本地拆解的原文件删后需重新上传）；reaper 巡检兜底清扫已完成/已忽略条目的遗留媒体，进行中任务的媒体不动。
- **实体级去重**：同选题已有 generating/polishing 脚本 → 拒绝再生成（409）；同选题/同脚本已有 generating 长文 → 拒绝（409）；相同原文正在深研 → 拒绝（409）。
- **任务归属（租户链）**：`jobs.tenant_id/user_id` 由 `auth.ACTOR`（(tenant,user,role) contextvar）在提交时自动落库——请求上下文由 `require_user` 设值；执行器运行期由 runner 从任务行恢复上下文（**同步执行器必须经 `asyncio.to_thread` 派发——`run_in_executor` 不传播 contextvars**）；人工重试继承原任务归属，系统调度与自动重试归 0（系统）。执行器内的一切查询、实体创建、LLM 台账随任务租户。
- **按租户队列**：heavy 消费者跨租户轮转（租户内 FIFO）；每租户活跃任务上限（R11.7）在提交时先查后建（超限 429）。
- **积分预扣**：`submit(points=N)` 在任务落库后、入队前扣减（幂等命中/合并跳过不扣；扣减失败响亮报错不拦任务）；余额闸（402）在各计量端点提交前把守。
  - **数字人视频按秒计费**（2026-09-27 用户拍板）：`avatar_video_points(seconds, model)` = ceil(秒 × 费率)，
    费率基础 3 积分/秒、高质 6 积分/秒（= 蝉镜成本 1 豆/秒、2 豆/秒 ×3 积分/豆，1 豆 = ¥0.03）；
    提交时按 `estimate()` 预估秒数预扣，成片后 `settle()` 按实际秒数多退少补（差额各记一笔流水）。
- **蝉豆流水（bean_ledger）**：每次成功渲染记一行（video_id/beans/seconds/model），独立于视频行——删视频/清记录不影响消耗统计；`GET /api/avatar/usage` 优先读流水（空则回退 avatar_videos 老口径），返回 `{videos, seconds, beans, yuan?}`（yuan 仅平台管理员）。
- **按形象原生尺寸渲染**（2026-09-28 修）：`chanjing.person_size(person_id)` 从 `person_detail`
  读形象原生 width/height，`create_video_tts` 按它设 person 框 / screen / `_scaled_subtitle(w)`
  （字幕字号与 y 以 1080 为基准等比缩放）；短标 `render_label_png(text, path, frame)` 按成片
  实际画幅缩放（`_probe_size` 探测）。背景：形象原生 720×1280 曾被硬拉到 1080×1920 渲染——
  同口径脸区高频能量 32 vs 原生 129~153（4~5 倍细节被上采样吃掉）。

- **左上角栏目角标**（avatar_video 内，`chanjing.render_label_png/burn_label`）：形式=两行栏目名
  + 暖黄细括号 + 右侧日期与期号（参照罗振宇《视频日记》，2026-09-28 用户选定 B 方案）。
  平台 `create_video` 无文字层 → 成片后本地叠一张透明 PNG（PIL，字号/坐标以 1080 为基准按画幅缩放）；
  期号 Day N = 本租户 done 成片数 + 1（查库自算），日期取北京时间当天。**租户化（P0）**：
  栏目两行=`brand.label_line1/label_line2`（`tenant_brand.label_lines_of`）、强调色=`brand.accent`
  （`accent_rgb_of`）、字体=`label_font_path(tenant)` 按租户栏目名现做 Noto Sans SC Bold 子集
  （缓存 `data/label_fonts/`；OFL 免费商用）。全程叠加无 enable 窗口；
  失败只降级（交付无角标版本 + `avatar_videos.notes` 写明）。开关：`POST /api/avatar/generate {bigtext}`。

- **海报提示词工场**（2026-09-30，`api/poster.py`）：把文章/任意内容转成即梦(Seedream)「图文一体海报」
  的完整生成提示词。出图引擎在系统外（即梦网页版免费额度 / 豆包 / 火山方舟 Seedream API 0.2 元/张），
  系统只产出提示词。`POST /api/articles/{id}/poster-prompt {slot_index, style?}` 单配图位；
  `POST /api/articles/{id}/poster-pack` 整篇全部配图位；`POST /api/poster/freetext {content, poster_type?, count?}`
  任意内容（培训课件场景）。LLM（glm-5 thinking disabled）抽取标题/要点/结论并逐字给出图内中文文案；
  LLM 失败自动回落关键词模板拼接（mode=template）。风格预设：banner(16:9) / recruit(9:16) /
  knowledge(3:4) / marketing(9:16) / auto。前端落点：公众号配图位旁「海报提示词」、文章级「全套配图提示词」、
  发布台「配图提示词」、独立「海报提示词」页。

- **一页纸知识图解**（2026-10-02，`articles/kg.py`）：文章 → LLM 抽取结构化内容（purpose=kg_spec，
  thinking disabled，4000 预算：title/lead/sections[{h,points[]}]/conclusion/conclusion_sub，
  要点强制带数字案例）→ PIL 两遍绘制（2400 高画布按内容裁切）→ 一页纸知识图 PNG。
  文字全部排版层渲染零错字（CogView 文生图中文字已否决）；品牌 chip 取租户 label_line1/2；
  字体=`app/assets/fonts/NotoSansSC-{Bold,Regular}.otf`（noto-cjk SubsetOTF，完整大小
  Bold 8543168 / Regular 8331336 字节——下载截断时 PIL 报 hmtx missing）。成图落租户配图目录
  `gen_kg_*.png`（img 白名单含 kg_/gen_ 前缀）。两个入口：`article_generate` 出文后自动追加
  （失败降级不阻塞成文）；`POST /api/articles/{id}/knowledge-graphic` 手动（重新）生成
  （<200 字正文 400）。前端：文章展开行操作区「生成知识图解」，成功回显缩略图+标题。

- **蝉镜检查点续跑**：`avatar_video` 的 run_dir 按视频行确定（`v{video_row_id}`），`state.json` 记录每段蝉镜任务 id 与产物文件；重试时已完成段复用文件、已提交未收货段只轮询不重复提交（蝉豆不重复扣）。

## 8. LLM 网关

- `provider` 抽象：`complete(messages, json_schema=None, purpose="")`；GLM 默认，DeepSeek 备选（`LLM_PROVIDER` 切换）。
- 结构化输出：传 `json_schema` 时解析+校验，失败自动带错误重试 1 次。
- 每次调用写 `llm_calls` 台账（tenant_id 随 ACTOR/purpose/model/tokens/成本估算/时延），Web 设置页展示按用途分账——验收点之一；金额字段仅平台管理员视图（R11.6）。
- 搜索：独立智谱 `/web_search` 端点（按量计费，落台账 purpose=web_search）优先，DDG 兜底（全局限流闸：最小间隔+每小时配额）；通道三选 auto/glm/ddg 运行时切换。
- Prompt 全部放 `backend/app/prompts/*.md`，文件头含 `version` 与 `purpose`，代码加载；迭代 Prompt 不改代码。
- 关键 Prompt 与输出 schema：
  - `idea_research`：输出 `{title, angle, audience, pain_points[], hooks[], business_fit{boss,fde}, competitors, score, score_breakdown}`
  - `benchmark_analyze`：输出 `{hook{type,position_sec,text}, structure[], topic_value, comment_insights[], replicable_points[], score}`
  - `script_generate`：按结构模板出 3 版。**打法与时长按 2026-10-09 流量诊断重定**：六打法（反常识判断/身份定位喊话/行动指令/提问/对比打假/数字落差，观点判断导向）、默认档 240~320 字（50~70 秒黄金档）、前 3 秒甩判断禁铺垫、黑话必须大白话化、每版必含一个可转述的点赞钩子判断；选题链路（idea_research/benchmark_analyze/trending）同步禁纪实类角度与黑话标题——依据：自有账号24 条实测（观点类均播 384 vs 纪实类 56，黑话标题 213 vs 大白话 369，问句标题 479 vs 246）
  - `script_critique`：输出问题清单 + 定向重写
  - `style_pick`：长文透镜自动挑选（style=auto 时生成开头小调用，输出 `{style, reason}`，无效回落 deep_dive）
  - `article_title`+`article_outline`+`article_section`：长文标题组/大纲/逐节写作（深研报告全文注入，
    thinking 可关闭防空正文）。**双档位（2026-10-02）**：请求体 `length` 二选一，默认 `feed`——
    `feed`=公众号版（1200~1800 字、3~4 节、开头即答案（ASC，禁铺垫式开场）、判断式小节标题、
    信息流标题公式（人群+数字+冲突+可带走判断，禁论文对仗腔与悬念钩子腔）、金句收尾，
    完读率优先——依据：公众号推荐流赛马制下完读率权重 35% 居首、完读率 <30% 推荐终止）；
    `deep`=深度版（3000~5000 字、5~7 节、铺垫式开头、机制拆解/方法框架/边界条件三件套，
    判决句式标题（禁论文对仗腔）、全文至少一个可转述的原创判断框架（2026-10-08 卡兹克拆解四条，
    另含术语类比纪律/数字体感锚/人物年代一句话叙事锚——不破去故事化裁定），
    风格 `style=auto` 时生成开头先经 style_pick 小调用按素材挑透镜（在精选五透镜 deep_dive/practice/inquiry/anatomy/comparison 里挑，无效回落 deep_dive），
    专业书面向文体，适合搜一搜长尾与发客户）。规格文本存 `articles/service.py` 的
    `ARTICLE_LENGTHS`，分别注入三个 prompt 的 `{{TITLE_SPEC}}/{{LENGTH_SPEC}}/{{SECTION_SPEC}}`；
    缺占位符由 `prompts.render()` 抛错拦截。质量评审尺随档位走（quality.py：feed 尺/长文尺/口播尺）。
    **大纲节数守卫**：节数低于档位下限（feed 3 / deep 5）时带明确节数要求重试一次大纲（模型偶尔
    只出 1 节，2026-10-02 实测）。**渲染器剥离 `[CHART]...[/CHART]` 规格块**（renderer.py）：
    规格块是分节写作的中间产物、成图由流水线以 `(chart:文件)` 图片标记另行插入，不剥则原始 JSON
    透进公众号 HTML（已发布旧文实测泄漏）。
    分节合规守卫拦违禁词与刻意转化话术（评论区/私信/关注/扣字/领资料，与 quality.py 同表；
    编辑守卫另有**元指令防线**：编辑模型偶发把「全文已符合修订标准…原样输出」类流水线自述
    写进正文，命中标记即弃用编辑稿保留原稿——数字/占位标记校验之外的新增防线，2026-10-02）；
    分节合规守卫拦违禁词与刻意转化话术（评论区/私信/关注/扣字/领资料，与 quality.py 同表；
    裸「最好的」已移出程序化表——误杀「千万别挑最好的」反向建议与「最好的线」描述性比较，
    语义级绝对化由 LLM 评审 compliance 项兜底）。
    **数据论据国内源优先（2026-10-02 用户硬要求）**：idea_research_report / script_generate /
    article_outline / article_section / kg_spec 五处 prompt 同约束——优先国内公开数据/行业报告/
    国内企业实践（注明来源与年份），国外机构数据只在国内无同类时作补充、不作主锚点。
  - `self_style_extract`（R9.2）：转写 → `{opening_pattern, rhythm, phrases[], tone, argument_habit, closing_style, exemplars[]}`（只提形式特征，不判内容价值）
  - `self_profile_update`（R9.3）：全部风格标注 + 上一版画像 → `{digest(200~300字), traits[6~10], exemplars[≤6 原句], notes}`（增量演化，保留可复现风格）
- **风格注入（R9.4）**：`script_generate` / `script_polish` 的「他说话的方式」段为 `{{MY_STYLE}}` 占位符——有画像时注入"摘要+特征标签+代表原句"，无画像回落内置人设文案；`generate` 请求体 `use_style`（默认 true）可按次关闭。

## 9. 模块设计要点

### 9.1 选题中心
- 深研链：`idea → LLM 多轮（检索增强可选）→ 结构化报告 → 评分入库`。评分 = 相关度×0.4 + 痛点强度×0.4 + 差异化×0.2（各 0~10，加权总分）。
- 同行监控链：对标账号扫描（锚点视频反推作者作品）→ 新视频自动排队拆解 → 选题候选入库（source=benchmark）。全网热榜已按 PRD R1.2 移除。

### 9.2 脚本工场
- `script_templates.structure` 示例：`[{step:"hook",requirement:"前3秒点名受众+痛点"},{step:"pain"},{step:"proof"},{step:"method"},{step:"cta"}]`；模板来自人工整理 + 拆解沉淀（`source_benchmark_id` 溯源）。
- 终稿三产物：提词器文本（停顿"/"、重音**加粗**标记）、分镜提示（B-roll 槽位 + 字幕卡点位）、标题候选（抖音/视频号/话题标签）。


### 9.4 公众号
- 生成：topic/script → LLM 长文（MD，含小标题/案例/CTA）→ 人工改稿（PUT）→ render。
- 渲染：vendor doocs/md 的 MD→内联样式 HTML 核心（WTFPL，摘录处注明），主题一份默认样式；输出存 `articles.html`，前端"一键复制"（clipboard）+ iframe 预览。
- 发布纯人工：无任何公众号 API 调用。

### 9.5 发布台
- 物料包来源：`title_candidates`（脚本）、视频封面文案、文章 HTML；`douyin_tags` 由 LLM 从选题关键词生成 3~5 个。
- ~~上传集成（P4）：subprocess 调 `sau`~~（不做，R6.4 移除）：发布台只产出物料包，发布动作完全人工。

## 10. 外部服务部署

### 备份与看门狗（系统层健壮性）
- **自动备份**：`db.backup_db(tag)` = SQLite `.backup` 在线快照（WAL 安全）。①每日滚动快照（reaper 跨天触发，保留 14 份，`data/backups/studio-daily-*`）②**迁移前快照**（init_db 在 create_all/_migrate 前执行，保留 5 份）——业务库只有一份，磁盘/迁移/误删事故的唯一恢复手段。
- **看门狗**（独立 LaunchAgent `com.content-studio-watchdog`，每 5 分钟）：`GET /api/health` 探活 → 连续 2 次失败自动 `launchctl kickstart` 拉起 + macOS 通知；恢复时通知。主服务死透或 launchd 节流时由它兜底（launchd.sh install 统一装/卸，status 可查）。
- **磁盘防御**：`download_video` 前检查余量（<2GB 拒绝下载，报错进失败中台待处置）；reaper 每巡 <5GB 告警日志；拆解媒体即用即弃。
- 启动/重启纪律：改启动路径（lifespan/runner.start）的代码必须 kickstart 后立即探活。

| 服务 | 端口 | 部署 |
|---|---|---|
| Content Studio（macOS） | 0.0.0.0:8100 | LaunchAgent 常驻（`scripts/launchd.sh` install/uninstall/status）+ 看门狗（脚本副本装于 Application Support，TCC 规避） |
| Content Studio（Linux 服务器） | 127.0.0.1:8100 | systemd 常驻（`scripts/deploy-linux.sh` install/uninstall/status）+ 看门狗 timer；Nginx 反代 + HTTPS（`deploy/nginx-content-studio.conf.example`） |
| ASR 模型 | faster-whisper large-v3 | hf-mirror 下载至 data/asr-models/faster-whisper-large-v3/，`.env ASR_MODEL=large-v3` |
| 进程守护 | macOS launchd | `scripts/com.content-studio.plist` 开机自启（§10b） |
| 版本管理 | git | 仓库根 `git init`；.env/data/services venv 不入库（.gitignore 已就绪） |

启动顺序：容器 → 应用。`/api/health.services` 逐个回报，缺谁降级谁，不阻塞主流程。

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
- **应用安全加固（已实现）**：
  ①**文章 HTML 白名单净化**（bleach 6 + CSS 白名单）：渲染出口与交付前双重净化
    （`GET /articles/{id}`、`/articles/{id}/wechat.html`、发布台物料包），
    剥脚本/事件属性/危险协议——防"租户成员在文章 md 埋 <script>、平台管理员查看时执行"的跨租户提权；
    wechat.html 另带 `Content-Security-Policy: default-src 'none'`；
  ②**任务纳入租户作用域**（`Job` 挂 TenantScoped）：任务列表/详情/删除/重试一律跨租户 404；
  ③**会话失效语义**：登出即服务端作废该 token；改密作废该用户全部会话并补发新会话（全端重登）；
    `require_user` 每次校验租户状态（停用租户残留会话一律失效）；
  ④**上传体积上限**：数字人克隆素材 500MB（分块读取，超限 413）、拆解视频 2GB；
  ⑤**登录时序均匀化**：用户不存在时同样执行一次 scrypt（防按响应耗时枚举用户名）；
  ⑥**SSRF 闸门**：服务端会主动请求的用户 URL（贴链接拆解/链接识别/话题提取/监控账号写入）
    一律经 `is_douyin_url()` 校验——仅放行 `*.douyin.com` / `*.iesdouyin.com` 的 http(s)
    （内网地址、云元数据端点 169.254.169.254、伪装域、危险协议全部拒绝），API 层 400 + 下载器/扫描器内部兜底；
  ⑦**首登强制改密（服务端强制）**：业务与管理路由用 `require_ready_user` 依赖，
    `must_change_password=1` 的会话访问业务接口一律 403（仅 `/auth/*` 放行以完成改密）——
    此前仅前端拦截，直接调 API 可绕过；
  ⑧**任务错误脱敏**：任务详情对租户可见，落库前把服务器绝对路径（仓库根/主目录）替换为 `<server>`；
  ⑨**应用级安全响应头**：全局 CSP（`default-src 'self'`，脚本仅同源；wechat.html 端点自设更严 CSP 不被覆盖）
    + `X-Content-Type-Options: nosniff` + `Referrer-Policy: same-origin`；
  ⑩**前端动态链接协议守卫**：`safeHref()` 仅放行 http/https（防 DB 混入 `javascript:` 链接被点击执行）。
- **云部署安全（已实现）**：
  ①`COOKIE_SECURE=1`（.env）→ 会话 Cookie 加 Secure 属性，只经 HTTPS；
  ②**登录限流**（`auth.LoginGuard`）：账号维度（`LOGIN_MAX_FAILS`，默认 5/15 分钟）
    与来源 IP 维度（`LOGIN_IP_MAX`，默认 40/15 分钟）双计数，成功即清零，触顶返回 429 + Retry-After；
    真实 IP 经 `client_ip()` 解析——仅当直连方为回环（本机 Nginx 反代）时信任 X-Forwarded-For 首段；
    限流为进程内内存态（重启清零，单进程部署足够）；
  ③**Linux 部署**：`sudo scripts/deploy-linux.sh install`（systemd 常驻 + 看门狗 timer 每 5 分钟探活、
    失败自动 restart）；Nginx 反代 + HTTPS 模板见 `deploy/nginx-content-studio.conf.example`
    （含 `client_max_body_size 2048m`、X-Forwarded-For/Proto 透传）。

## 13. 验收对照

验收脚本位于 `acceptance/`（纯标准库，`python3 acceptance/run_pX.py`），判定规则：**FAIL=契约被打破；SKIP=外部依赖缺失（注明原因）**；任何 FAIL → 退出码 1。

| 套件 | 脚本 | 范围 | 通过门槛 |
|---|---|---|---|
| P0 骨架 | `run_p0.py` | 服务/health/前端页/jobs 契约/LLM 通道 | 全 PASS；LLM 真实调用项在未配密钥时允许 SKIP |
| P1 内容核心 | `run_p1.py` | idea深研→选题入库评分；脚本 3 版+打磨+终稿三产物；公众号长文+改稿+渲染；LLM 用量台账 | 全 PASS |
| P2 拆解流水线 | `run_p2.py` | 本地视频拆解全链（转写非空+拆解 schema+选题候选）；模板库 CRUD；在线拆解 | 本地链全 PASS；在线拆解未配 `DOUYIN_TEST_URL` 时 SKIP |
| P4 发布台 | `run_p4.py` | 物料包字段完整+忽略清单；台账/指标回流/sau 移除负向契约（404） | 全 PASS |
| P5 口播数字人 | `run_p5.py` | 配置 CRUD/克隆契约/余额/预估/生成记录/删除边界 | 全 PASS；真实合成设 `CS_CHANJING_LIVE=1` 才跑 |
| P6 任务体系 | `run_p6.py` | 去重幂等/失败分类/到期放行/欠任务补队/失败中台（指纹聚类·按指纹复活·实体孤儿） | 全 PASS |
| P7 多租户隔离 | `run_p7.py` | 双租户可见性/越权 404/归属/任务收窄/积分预扣与余额闸/金额与品牌隐藏/导出/删除/停用租户登录拦截 | 全 PASS |

fixture 说明：`acceptance/fixtures/make_video.py` 用 ffmpeg(+macOS `say` 中文语音) 生成口播形态测试视频；`--check` 检查依赖。P2 依赖语音轨（系统需有中文 TTS 声音）。

## 14. 风险与技术债清单

2. doocs/md 渲染核心 vendor 后与上游漂移——只在升级公众号样式需求出现时手动同步。
3. faster-whisper large-v3 首次下载模型 ~3GB——启动脚本预下载。
4. GLM 结构化输出偶发不合 schema——网关层 1 次修复重试 + 失败落 job error 供重跑。
5. 单进程任务并发——heavy 串行 + 跨租户轮转 + 每租户活跃上限已内建；单机吞吐不够时再上独立 worker 进程（明确记录）。
6. 雷达话题 `ch_id` 全局唯一——多租户加同一话题受限；需时做表重建改复合唯一（R11.9）。
7. 媒体文件按租户分目录仅覆盖生成配图；历史下载/成片文件名空间共享（文件名不可猜测，随租户删除按归属行清理）。


## 15. 认证与多租户隔离（实现契约）

- **定时调度审计修复（2026-10-07 第二轮）**：
  ①**钟点覆盖 bug**——watch_discover_daily 注册 2 点但 hour_from_settings=True 使全局
  watch_scan_hour(默认 1) 永久覆盖它，四项日任务全挤 1 点、"每日 2 点发现"设计失效
  （10-06 01:00 双失败的根因）；修复=去掉该项的 settings 覆盖，2 点与扫描错峰；
  ②**调度失败不再静默**——执行失败首次 2 小时后补跑一次（给 job 层 1h 退避让路），
  连续失败才回落常规节奏（此前零重试零告警当天丢失）；
  ③**时区显式化**——systemd unit 加 `Environment=TZ=Asia/Shanghai`（daily_hour 语义=
  服务器本地钟点，此前依赖隐式行为）；④runner `_backup_day` 死遗迹清理。
  已收编统一调度的 7 项：watch_scan(1 点随设置)/watch_discover_daily(2 点)/self_scan(1 点)/
  topic_radar(1 点)/daily_backup(4 点)/media_cleanup(6h)/crawl_probe(15min)；reaper 内嵌的
  owed 补队/归档/磁盘告警属基础设施双轨（非业务定时），保留。边界：进程崩在"记账后执行前"
  该次丢失（罕见，dedup+次日兜底）。
- **调度审计加固（2026-10-07，内存事故复盘）**：
  ①**心跳真实化**——`set_progress`/拾起时显式刷 `updated_at`（此前该列从不更新，reaper 的
  stuck 判定实为"出生满 2h"而非"无进展 2h"：活跃长任务被误杀、真挂死要等满 2h）；
  ②**任务级硬超时**——RetryPolicy.task_timeout 按类型配（拆解/自析 45min、长文 60min、脚本
  40min、热点 15min 等），heavy 执行包 `asyncio.wait_for`：超时转 transient 重试，唯一 heavy
  worker 不再被单任务无限占死（to_thread 线程不可取消，超时后以僵尸形态跑完，完成回调有
  状态护栏不覆写重试行；`_handle_failure` 同护栏）；
  ③**reaper stuck 尊重 max_retries**——达上限转终态 failed（此前无限 2h 轮重试）；
  ④**重启恢复 queued 原行入内存队列**（旧行为"改 superseded 再 submit"因 dedup 必 miss，
  每次重启每任务膨胀一行且租户归属丢成 0——id 膨胀到 750+ 的事故来源）；
  ⑤`_reenqueue` light 分道先查同类 inflight 再置 queued（顺序颠倒会让行永久滞留 queued）；
  ⑥**gateway 全局 LLM 信号量（并发 2）**——直调 LLM 的端点（知识图解/海报三端点/ping）不入队
  也被统一限流；⑦clone_video 流式落盘（原 500MB 全量读内存）；⑧search-probe 两路探测
  to_thread（原同步 httpx 90s 卡死整个事件循环）；⑨crawl/probe 全局单飞（429）；⑩发布台
  `_gen_tags` 未 await 协程修复（标签 LLM 生成此前从未生效，一直走兜底）；⑪ffmpeg concat
  硬超时 30min。
- **上下文（auth.ACTOR）**：`ContextVar[(tenant_id, user_id, role)]`，`require_user`（async 依赖）在请求上下文设值；
  系统上下文 = `(0, 0, "")`；`role=platform_admin` 时豁免租户过滤。
- **一人多租户归属（2026-10-03）**：`tenant_memberships(user_id, tenant_id, role)` 多对多，unique(user_id, tenant_id)；
  `users.tenant_id`=默认租户（登录落点），`users.role` 仅承载 platform_admin 全局位（member/tenant_admin 语义在 membership）。
  `require_user` 返回 `CurrentUser`（User 兼容形态）：`tenant_id`=会话活跃租户（UserSession.tenant_id 状态位，校验归属+租户
  active，失效自动回落可用归属并回写会话，无可回落 403）；`role`=有效角色（platform_admin 全局位恒定，否则=活跃归属的
  membership.role）。登录响应与 `/api/auth/me` 扩展 `tenant_name` + `tenants[]`；`POST /api/auth/switch-tenant` 切活跃租户
  （校验归属+active，改会话行，cookie 不变）。管理后台：users 列表行内嵌 memberships 与 manageable_tenants；
  `POST /admin/users/{id}/memberships`（upsert 改角色）/`DELETE .../memberships/{tenant_id}`（至少保留一个归属；默认租户
  自动迁移）；全局角色端点只收 platform_admin/member；租户删除级联归属（唯一归属用户连删，多归属保留并迁移默认租户）。
  存量迁移：membership 表空时按 users.tenant_id 全量回填（tenant_admin→tenant_admin，其余→member）。
- **隔离三件套（`db.install_tenant_scope()`，进程级一次接入）**：
  ①`do_orm_execute` 事件——所有 ORM select 按语句实体自动追加 `tenant_id == 当前租户` 条件
  （用具体实体+具体条件构造，平台管理员/系统上下文跳过）；
  ②`Session.get` 补丁——get 不走 do_orm_execute，命中他租户行返回 None（端点自然 404）；
  ③`before_flush` 事件——新行 `tenant_id=0` 时自动回填当前租户。
- **归属链**：任务提交（`runner.submit` 读 ACTOR）→ 任务行带 tenant_id/user_id → 执行器运行期 runner
  从任务行恢复 ACTOR（finally 复位）→ 执行器内查询/实体/LLM 台账随任务租户；
  同步执行器必须经 `asyncio.to_thread`（contextvars 传播；`run_in_executor` 会丢）；
  调度产生的实体（扫描入库等）从父实体显式继承租户；人工重试继承原任务归属。
- **豁免与系统口径**：平台管理员查询不过滤（跨租户管理）；租户 0 = 系统任务与系统调用
  （调度、自动重试、ping），不计入租户用量与排行。
- **会话**：随机 token 走 HttpOnly Cookie，库存 sha256(token) + 过期时间；停用用户/停用租户即清会话
  且登录被拒；重置密码即踢下线。
- **一次性密码**：建号/重置生成随机口令，仅当次响应返回，明文不落库；首登强制改密。
- **抖音抓取出口与指纹（云部署实证）**：抓取 UA 由 `browser.ua()` 按运行平台生成
  （UA 与 navigator.platform 不一致会被抖音挑战）；浏览器用完整版 Chromium
  （`channel="chromium"`，headless-shell 指纹更易被挑战）；机房 IP 需经住宅出口
  （`DOUYIN_PROXY`，本机 `scripts/home-proxy.sh` 提供 SOCKS5+反向隧道），空=直连。
  出口不可达时任务失败信息显式标注所用代理（降级可见）。
- **媒体边界**：生成配图按 `article_images/{tenant_id}/` 分目录，服务端点只读本租户目录
  （平台管理员全局可读；存量平铺文件迁移归首租户）。
- **金额与品牌隐藏（R11.6）**：后端为权威口径——非平台管理员的响应剔除 cost/yuan 字段、
  不含引擎品牌文字；前端按角色条件渲染。
