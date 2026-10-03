# Content Studio

FDE 内容生产流水线（本地单机）：从一句话 idea 到口播脚本、剪映草稿、公众号长文与发布台账。

```
选题库（idea 深研 · 同行拆解 · 飞瓜 CSV）
  → 脚本工场（三版生成 · 批判打磨 · 定稿三产物）
  → 视频生产线（ASR 对齐 · 剪映模板模式）
  → 公众号（长文 · 内联样式渲染 · 一键复制）
  → 发布台（物料包 · 台账 · 数据回流反哺选题评分）   [发布动作永远人工]
```

## 快速开始

```bash
cp .env.example .env      # 填 ZHIPU_API_KEY（或 DeepSeek）
scripts/dev.sh            # 起后端 :8100 + 构建前端
open http://127.0.0.1:8100
```

手动起后端：`backend/.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8100`

## 验收

```bash
python3 acceptance/run_p0.py   # …run_p1~p4；任何后端改动后必须重跑
```

## 五大文档

| 文档 | 角色 |
|---|---|
| [docs/MRD.md](docs/MRD.md) | **市场** · 目标用户/赛道/对标账号/开源调研与原始决策（只读存档） |
| [docs/PRD.md](docs/PRD.md) | **需求** · 需求总账（做什么/不做什么，唯一权威）+ 使用场景与 FAQ |
| [docs/TDD.md](docs/TDD.md) | **设计** · 技术契约（架构/数据模型/API/部署） |
| [docs/TEST.md](docs/TEST.md) | **测试** · 验收合同（五套脚本/运行方法/隔离纪律/重跑红线） |
| [docs/AUDIT.md](docs/AUDIT.md) | **审计** · 需求↔实现↔验收对账快照（更新制） |

另：[AGENTS.md](./AGENTS.md) 是 agent/协作者工作规约（从六大派生：启动命令、验收纪律、LLM 预算定律、红线）。
