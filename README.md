# Content Studio

单管理员 AI 内容生产流水线（跃迁内容工作室）：选题 → 口播脚本（一版精品直出）→ 公众号长文。
**发布动作永远人工**——系统止步于物料与台账，不做自动上传，零风控暴露。

```
选题（今日靶子=观点怼热点 · 一线交付爆料 · idea 深研 · 同行拆解/监测 · 获客挖掘）
  → 脚本工场（一版精品直出 · 自动批判打磨 · 定稿三件套）
  → 公众号（知识库挖掘→内容清单→改编 · 热点直出 · 渲染一键复制）
  → 发布登记 + 48h 数据回流（北极星=周私域进线）   [发布永远人工]
```

## 它能做什么

**选题**
- 每日靶子：六路国内热点原料 × 人设画像，程序化闸 + LLM 成色终审 + 冷饭闸（判据=已发视频清单），
  产出能直接开拍的「半句话判决靶子」。
- 一线交付爆料：从已发视频清单盘续集与空白带；idea 深研（联网报告+评分）、快速记选题、
  同行拆解产题（14 天防撞车）；获客号独立选题线（同城搜索矩阵+关键词发现+密度探针）。

**脚本**
- 定审选题一键出一版口播稿（长短两档），自动批判打磨一轮，定稿三件套（提词器/分镜/标题封面）。
- 风格画像注入（自己的拆解标注+逐字原话 few-shot）+ 违禁词机检 + LLM 评审双重门禁。

**公众号**
- ima 知识库挖掘器每日评估入内容清单；改编器取项成文：保源文骨架、换判断口径，
  过原创度/去AI味/事实对源/标题四道闸才进待发。
- 热点直出（每晚自动成文）+ Web 改稿 + 一键复制公众号排版 HTML，去后台粘贴即发。
- 发布登记 + 48h 数据回流（阅读/在看/涨粉/进线）。

**任务引擎**
- 13 项周期任务统一调度，设置页可视化开关/改钟点/立即执行。
- 失败是异常不是工作流：瞬时失败走退避梯子+每日慢巡直到自愈，确定性失败安静终态；
  界面只有红徽章+错误+行内重试。
- 重启自愈、stuck 看门狗、欠任务补队、错误指纹库。

**底座**
- 单管理员门禁：scrypt + 服务端会话 + 首登改密 + 登录限流。
- 品牌九字段（人设/受众域/署名/主色/ASR 词表等）贯穿生成渲染全链，设置页自助配置。
- GLM 单通道 LLM 网关（密钥只写不读回）+ DDG 检索通道（境内经住宅隧道出海）。
- 抖音采集防御：平台一致 UA + 完整版 Chromium + 轮询等待窗 + 真验证码判据 + 下载节流/熔断
  + 拆解质量闸。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLModel · SQLite（WAL，每日快照+迁移前快照）· faster-whisper · Playwright · matplotlib/Pillow |
| 前端 | React 18 · Vite 5 · Tailwind CSS v4 · TypeScript（3 个运行时依赖，无路由/状态库；构建产物由 FastAPI 静态托管） |
| LLM | GLM 单通道（Coding Plan 端点）；检索 GLM web_search / DDG 双通道，运行时设置页切换 |

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

- `.env` 只放引导项（data_dir/安全阈值/LLM key；端口由启动脚本直传 uvicorn）；LLM 通道、检索、
  采集出口、拆解闸线等运行时配置在 **设置页** 修改，存数据库、即时生效。
- 密钥一律服务端持有、只写不读回（API 仅回报"已配置"标记）。

## 验收测试

```bash
# 1) 起隔离实例（务必在仓库根执行；完整纪律见 docs/TDD.md §13.3）
DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8200

# 2) 跑套件（仓库根执行）
export CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 DATA_DIR=data-acceptance
python3 acceptance/run_p0.py                       # …p1/p2/p6/p7（标准库即可）
backend/.venv/bin/python acceptance/run_p8.py      # P8 白盒 import 需要 sqlmodel，必须走 venv
backend/.venv/bin/python acceptance/e2e_run.py     # E2E（playwright+PIL，必须走 venv）

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
| [docs/TDD.md](docs/TDD.md) | **设计** · 技术契约 + 验收对照与纪律（架构/数据模型/API/部署/验收） |

[AGENTS.md](AGENTS.md) 是协作者/agent 工作规约（启动命令、验收纪律、红线）。

## 联系

微信：`usk021`

## License

[MIT](LICENSE)
