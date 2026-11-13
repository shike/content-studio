# Content Studio

单管理员 AI 内容生产流水线（跃迁内容工作室）：选题 → 口播脚本（一版精品直出）→ 公众号长文。
**发布动作永远人工**——系统止步于物料与台账，不做自动上传，零风控暴露。

```
选题（今日靶子=观点怼热点 · 一线交付爆料 · idea 深研 · 同行拆解/监测 · 获客挖掘）
  → 脚本工场（一版精品直出 · 自动批判打磨 · 定稿三件套）
  → 公众号（知识库挖掘器→内容清单→改编器四闸 · 热点直出 · 渲染一键复制）
  → 发布登记 + 48h 数据回流（北极星=周私域进线）   [发布永远人工]
```

多租户/积分体系已于 2026-10-27 拆除（tenant 机制退化为单租户兼容层）；
口播数字人 2026-10-29 整体退役；发布台同批砍除。

## 核心特性

### 选题：三条靶子供给线（人设号）

- **今日靶子（观点怼热点）**：每日 6 点六路国内原料（百度/抖音/知乎热榜 + 科技媒体 RSS + GitHub 新库 + AIHot）
  ×租户画像 → ≤6 个「半句话判决靶子」，过程序闸（科技词/半句话/劝告开头/预言腔负向/判词词根）
  + 成色终审法官闸（正样本=已发标题标尺）+ 冷饭闸（判据=已发视频清单，不问人）。
- **一线交付爆料**：已发视频清单盘 franchise 三类靶（断更系列续集/实训营时间线/受众关心零覆盖空白带）。
- **idea 深研 / 快速记选题 / 同行拆解产题**：联网深度研究报告+0-10 评分；拆解候选 14 天防撞车。
- **获客号独立线**：获客选题库硬隔离；同城组局号搜索矩阵 + 关键词发现 + 求带密度探针挖纯获客对标。

### 脚本：一版精品直出

- 定审选题一键生成**一版口播稿**（六打法，240~320 字黄金带/600~900 长版），
  自动**批判打磨一轮**；定稿三件套（提词器/分镜提示/标题封面候选）。
- 风格画像注入：自己的拆解标注+逐字原话 few-shot，稿子像本人写的。
- 双重门禁：程序化违禁词/CTA 红线 + LLM 评审。

### 公众号：知识库挖掘 → 清单 → 改编

- **挖掘器**（每日 4 点）：ima 知识库域轮读 18 篇逐篇评估，≥7.5 入内容清单（低分落 low 防回卷重评）。
- **改编器**（每日 5 点）：清单取项改编成文——保源文骨架+逐字原话 ≤3 处引用+换判断口径，
  过原创度 ≤30% 闸 → 去AI味机检+定向重写 → 事实对源硬闸 → 标题五维评分，rendered 待发。
- **热点直出**（每晚 0 点）：站外热点双锚预筛 → 唯一切角建稿 → 自动成文，早晨改完即发。
- Web 改稿 → 一键复制公众号排版 HTML（bleach 白名单净化 + HMAC 签名配图），去后台粘贴即发。

### 任务引擎：失败是异常不是工作流

- 统一调度中心（13 项周期任务一处注册，设置页可视化开关/改钟点/立即执行）。
- 退避梯子 30min→2h→6h→24h → 每日慢巡（30 次硬上限后安静终态）；确定性失败不重试；
  死内容（已删/私密/转写空）当场归档不挂红。界面无失败工作流：红徽章+错误+行内重试。
- 重启自愈（queued 回队、running 90 秒重试）、stuck 看门狗、欠任务补队、错误指纹库。

### 经营底座

- **单管理员门禁**（R11）：scrypt + 服务端会话 + 首登改密 + 登录限流。
- **品牌九字段**：人设/受众域/栏目/口号/主色/ASR 词表等贯穿生成渲染全链，设置页自助配置。
- **LLM 单通道网关**：GLM（Coding Plan 端点），max_tokens=实测×2 纪律、空正文显式报错；
  检索 DDG 通道（境内经住宅隧道出海）。成本台账已拆——花销概算看服务商控制台。
- **采集防御体系**：平台一致 UA + 完整版 Chromium + 60s 轮询等待窗 + 真验证码判据 +
  下载节流/熔断 + 采集探针每 2h 表决 + 拆解质量闸（评论≥20 或 赞≥300）。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLModel · SQLite（WAL，每日快照+迁移前快照）· faster-whisper · Playwright · matplotlib/Pillow |
| 前端 | React 18 · Vite 5 · Tailwind CSS v4 · TypeScript（3 个运行时依赖，无路由/状态库；构建产物由 FastAPI 静态托管） |
| LLM | GLM 单通道（Coding Plan 端点）；检索 GLM web_search / DDG 双通道，运行时设置页切换，密钥只写不读回 |

## 快速开始

```bash
cp .env.example .env        # 填 LLM API Key
scripts/dev.sh              # 一键起：缺 venv 建 venv、缺前端构建先构建，后端 :8100
open http://127.0.0.1:8100
```

首次启动自动创建工作室与管理员 `admin`：初始密码打印在启动日志里（也可用
`CS_BOOTSTRAP_PASSWORD` 指定）。

环境要求：Python 3.12、Node.js ≥ 18。可选依赖：

- 同行拆解（抖音采集）：`PLAYWRIGHT_BROWSERS_PATH=$PWD/.ms-playwright backend/.venv/bin/playwright install chromium`
- 拆解 ASR：faster-whisper 模型（首选 small，`ASR_MODEL` 可调，国内可配 HF 镜像）

## 配置

- `.env` 只放引导项（host/port/data_dir/安全阈值/LLM key）；LLM 通道、检索、采集出口、拆解闸线等
  运行时配置在 **设置页** 修改，存数据库、即时生效。
- 密钥一律服务端持有、只写不读回（API 仅回报"已配置"标记）。

## 验收测试

```bash
# 1) 起隔离实例（务必在仓库根执行；复制库纪律见 docs/TEST.md §3）
DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8200

# 2) 跑套件（仓库根执行）
export CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 DATA_DIR=data-acceptance
python3 acceptance/run_p0.py    # … run_p1/p2/p6/p7/p8
backend/.venv/bin/python acceptance/e2e_run.py    # E2E（Playwright，需已构建前端）

# 3) 收尾
rm -rf data-acceptance
```

另：`bash acceptance/lint_names.sh` 是 F821 静态门禁（NameError 类 bug 靠它拦）。

## 部署

- **Linux 服务器**：`sudo scripts/deploy-linux.sh install`（systemd 常驻 + 看门狗 timer，Nginx/HTTPS 模板在 `deploy/`）。
- **macOS 常驻**：`scripts/launchd.sh install`（崩了自动拉起、开机自启 + 看门狗）。
- **发服务器**：`scripts/sync-server.sh`（rsync → 重启 → 健康检查三步一体，在途任务拒发）。
  部署目标不入库：`scripts/.deploy.env`（git 忽略）配 `CS_SYNC_HOST` / `CS_SYNC_KEY`。

## 文档

| 文档 | 角色 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | **需求** · 需求总账（做什么/不做什么，唯一权威）+ 使用场景 |
| [docs/TDD.md](docs/TDD.md) | **设计** · 技术契约（架构/数据模型/API/部署） |
| [docs/TEST.md](docs/TEST.md) | **测试** · 验收合同（套件清单/运行方法/隔离纪律） |
| [docs/AUDIT.md](docs/AUDIT.md) | **审计** · 审计规程（八维度+清单；不含版本号与完成情况） |
| [docs/MRD.md](docs/MRD.md) | **市场** · 调研方法论模板（真实运营调研存档不入库） |

[AGENTS.md](AGENTS.md) 是协作者/agent 工作规约（启动命令、验收纪律、红线）。

## 联系

微信：`usk021`

## License

[MIT](LICENSE)
