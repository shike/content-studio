# 跃迁内容工作室 验收脚本

实存套件 `run_p0 / run_p1 / run_p2 / run_p6 / run_p7 / run_p8` + `e2e_run.py`。
（P3 随视频生产线移除、P4 随发布台砍除删除、P5 随数字人退役删除，编号不复用。）
各套件的验收门槛与 docs/TDD.md §13 一一对应。**脚本即契约**：实现阶段以让这些脚本全绿为目标。

## 判定规则

- `PASS`：契约满足
- `FAIL`：契约被打破（导致整期验收不通过，退出码 1）
- `SKIP`：外部依赖缺失（脚本注明原因，不算失败）

## 运行方式

```bash
# 前置：隔离实例见 docs/TEST.md §3（复制库 + 清继承队列 + 重置口令 + 登录态）
python3 acceptance/run_p0.py    # P0 骨架
python3 acceptance/run_p1.py    # P1 内容核心（需 ZHIPU_API_KEY）
python3 acceptance/run_p2.py    # P2 拆解流水线（本地链可离线验收）
python3 acceptance/run_p6.py    # P6 任务体系（需 CS_REAPER_SEC=5 快巡）
python3 acceptance/run_p7.py    # P7 认证与单管理员（需确定性口令）
python3 acceptance/run_p8.py    # P8 功能补全（可靠性白盒/探针/调度/品牌九字段/靶子提炼/产线闸/prompt 契约）
python3 acceptance/e2e_run.py   # E2E 端到端（Playwright 真实 UI；Mac 自动用项目根 .ms-playwright 浏览器）

# 可选环境变量
export CS_API_BASE=http://127.0.0.1:8200            # 服务地址（隔离实例）
export CS_BOOTSTRAP_PASSWORD=accept-12345          # 管理员口令（登录态）
export DOUYIN_TEST_URL=...                         # P2 在线拆解的真实抖音分享链接
export CS_WATCH_SCAN=1                             # P2 同行扫描/发现全链路（重链路）
```

P0/P6/P7/P8 与 e2e 仅用 Python 标准库 +（e2e 另需）playwright；runner/quality/hygiene 同为标准库。

## 依赖说明

- P1 需要 `ZHIPU_API_KEY`（否则 LLM 相关项 SKIP）
- P2 在线拆解需要 `DOUYIN_TEST_URL`（真实分享链接，向用户要）；本地拆解链需要 ffmpeg+TTS fixture
  （`fixtures/make_video.py`）与 ASR small 模型（本地跑：软链 `data/asr-models` 进隔离目录 + `ASR_MODEL=small`）
- P7 需要 `CS_BOOTSTRAP_PASSWORD`（隔离实例确定性口令），且预期失败登录多、需 `LOGIN_IP_MAX=1000` 放宽限流
- 验收产生的测试数据收尾必须清理（发布假数据会污染选题评分）；生产库跑 `acceptance/hygiene.py` 只读体检
