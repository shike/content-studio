# 跃迁内容工作室 验收脚本

各套件的验收门槛，与 docs/TDD.md §13 一一对应。**脚本即契约**：实现阶段以让这些脚本全绿为目标。
（P3 视频生产线套件随剪映草稿功能移除，编号保留空缺。）

## 判定规则

- `PASS`：契约满足
- `FAIL`：契约被打破（导致整期验收不通过，退出码 1）
- `SKIP`：外部依赖缺失（脚本注明原因，不算失败）

## 运行方式

```bash
# 前置：隔离实例见 docs/TEST.md §3（复制库 + 重置口令 + 登录态）
python3 acceptance/run_p0.py    # P0 骨架
python3 acceptance/run_p1.py    # P1 内容核心（需 ZHIPU_API_KEY）
python3 acceptance/run_p2.py    # P2 拆解流水线（本地链可离线验收）
python3 acceptance/run_p4.py    # P4 发布台（P3 已随功能移除）
python3 acceptance/run_p8.py    # P8 功能补全（雷达/探针/调度/海报/品牌九字段/自动透镜受理）
python3 acceptance/e2e_run.py   # E2E 端到端（Playwright 真实 UI，需 8200 实例在跑；Mac 自动用项目根 .ms-playwright 浏览器）
python3 acceptance/run_p5.py    # P5 口播数字人
python3 acceptance/run_p6.py    # P6 任务体系（需 CS_REAPER_SEC=5 快巡）
python3 acceptance/run_p7.py    # P7 多租户隔离

# 可选环境变量
export CS_API_BASE=http://127.0.0.1:8200            # 服务地址（隔离实例）
export CS_BOOTSTRAP_PASSWORD=accept-12345          # 管理员口令（登录态）
export DOUYIN_TEST_URL=...                         # P2 在线拆解的真实抖音分享链接
```

零第三方依赖（仅 Python 标准库）。

## 依赖说明

- P1 需要 `ZHIPU_API_KEY`（否则 LLM 相关项 SKIP）
- P2 在线拆解需要住宅出口代理（home_proxy 隧道）+ `DOUYIN_TEST_URL`；本地拆解链无外部依赖
- P5 需要 `ZHIPU_API_KEY`；真实合成烧豆项设 `CS_CHANJING_LIVE=1` 才跑（默认 SKIP）
- P7 需要 `CS_BOOTSTRAP_PASSWORD`（隔离实例确定性口令）
