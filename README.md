# Content Studio

多租户 AI 内容生产流水线（跃迁内容工作室）：从一句话 idea 到口播脚本、数字人成片、公众号长文与发布物料包。

```
选题库（idea 深研 · 同行拆解 · 话题雷达 · 同行监测）
  → 脚本工场（三版生成 · 批判打磨 · 定稿三产物）
  → 口播数字人（克隆形象 + 复刻音色 · 1080×1920 竖版直出 · 租户栏目角标）
  → 公众号（feed/deep 双档位长文 · 头图/图表/知识图解 · 一键复制排版）
  → 发布台（物料包 · 数据回流反哺选题评分）        [发布动作永远人工]
```

## 核心特性

- **多租户 SaaS**：登录门禁 + 租户数据隔离 + 租户积分池（数字人按秒计费）；平台管理员管租户/成员/成本台账，普通租户只见积分口径。
- **租户品牌全链注入**：栏目角标、头图署名/口号、创作者人设、图表主色、ASR 领域词表等九项品牌字段，租户自助配置，注入全部生成与渲染链路。
- **脚本工场**：一次生成三版口播稿，批判 Agent 打磨循环定向重写；程序化 + LLM 评审双质量门禁。
- **口播数字人**：接入开放 API 数字人服务，定稿脚本直接产出竖版成片（基础/高质双档口型），无剪辑软件依赖。
- **公众号双档位**：默认 feed 信息流版（1200~1800 字，开头即答案）+ deep 深度版；封面头图、数据图表、一页纸知识图解自动生成；MD → 公众号内联样式 HTML 一键复制。
- **情报侧**：同行监测（锚点视频反推作者作品）、话题雷达（低粉高赞飙升发现）、每日同行发现、价值周报。
- **任务体系**：统一队列/进度/重试，失败进「待处置」区带诊断线索，支持标记修复后整批复活重跑；启动时自动恢复中断任务，看门狗探活自愈。
- **验收即契约**：`acceptance/` 下 P0~P8 全量功能套件 + Playwright E2E，纯标准库、PASS/FAIL 语义，任何后端改动必须重跑受影响套件。

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLModel · SQLite（单文件全量备份）· faster-whisper · Playwright · matplotlib/Pillow |
| 前端 | React 18 · Vite 5 · Tailwind CSS v4 · TypeScript（构建产物由 FastAPI 静态托管） |
| LLM | LLM 网关：双通道热切换，统一出口 + 成本台账；运行时在设置页切换，密钥只写不读回 |
| 数字人 | 第三方数字人开放 API（可选功能，需在平台侧配置凭证） |

## 快速开始

```bash
cp .env.example .env        # 填 LLM API Key
scripts/dev.sh              # 一键起：缺 venv 建 venv、缺前端构建先构建，后端 :8100
open http://127.0.0.1:8100
```

首次启动自动创建租户「示例工作室」和管理员 `admin`：初始密码打印在启动日志里（也可用
`CS_BOOTSTRAP_PASSWORD` 指定），首次登录会要求改密。

手动起后端：`backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8100`

环境要求：Python 3.12、Node.js ≥ 18。可选依赖：

- 同行拆解（抖音采集）：`backend/.venv/bin/playwright install chromium`
- 拆解 ASR：faster-whisper 模型首次运行自动下载（默认 large-v3，可用 `ASR_MODEL` 调整）

## 配置

- `.env` 只放引导项（host/port/data_dir/安全阈值/LLM key）；LLM 通道、检索、平台凭证等运行时配置在 **设置页** 修改，存数据库、即时生效。
- 密钥一律服务端持有、只写不读回（API 仅回报"已配置"标记），不入前端不入库。

## 验收测试

```bash
# 1) 起隔离实例（务必在仓库根执行；DATA_DIR 相对路径锚定仓库根）
DATA_DIR=data-acceptance CS_BOOTSTRAP_PASSWORD=accept-12345 \
  backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8200

# 2) 跑套件（仓库根执行；DATA_DIR 必须与实例一致，否则个别用例会写错数据目录）
export CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 DATA_DIR=data-acceptance
python3 acceptance/run_p0.py    # … run_p1 ~ run_p8（P3 随剪映线移除）
backend/.venv/bin/python acceptance/e2e_run.py    # E2E（Playwright，需已构建前端）

# 3) 收尾
rm -rf data-acceptance
```

另：`bash acceptance/lint_names.sh` 是 F821 静态门禁（NameError 类 bug 靠它拦）。

## 部署

- **Linux 服务器**：`sudo scripts/deploy-linux.sh install`（systemd 常驻 + 看门狗 timer，Nginx/HTTPS 模板在 `deploy/`）。
- **macOS 常驻**：`scripts/launchd.sh install`（崩了自动拉起、开机自启 + 看门狗）。
- **发服务器**：`scripts/sync-server.sh`（rsync → 重启 → 健康检查三步一体）。部署目标不入库：在 `scripts/.deploy.env`（git 忽略）里配置 `CS_SYNC_HOST` / `CS_SYNC_KEY`。

## 文档

| 文档 | 角色 |
|---|---|
| [docs/PRD.md](docs/PRD.md) | **需求** · 需求总账（做什么/不做什么，唯一权威）+ 使用场景 |
| [docs/TDD.md](docs/TDD.md) | **设计** · 技术契约（架构/数据模型/API/部署） |
| [docs/TEST.md](docs/TEST.md) | **测试** · 验收合同（套件清单/运行方法/隔离纪律） |
| [docs/AUDIT.md](docs/AUDIT.md) | **审计** · 需求↔实现↔验收对账快照（更新制） |
| [docs/MRD.md](docs/MRD.md) | **市场** · 调研方法论模板（真实运营调研存档不入库） |

[AGENTS.md](AGENTS.md) 是协作者/agent 工作规约（启动命令、验收纪律、红线）。

## 联系

微信：`usk021`

## License

[MIT](LICENSE)
