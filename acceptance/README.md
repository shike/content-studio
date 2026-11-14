# 跃迁内容工作室 验收脚本

实存套件 `run_p0 / run_p1 / run_p6 / run_p7 / run_p8` + `e2e_run.py`。
（P2/P3/P4/P5 已不存在，编号不复用——拆解实链路的验证由生产运行承担：每晚 watch_scan 与每日自我扫描即真实使用。）
各套件的验收门槛与 docs/TDD.md §13 一一对应。**脚本即契约**：实现阶段以让这些脚本全绿为目标。

## 判定规则

- `PASS`：契约满足
- `FAIL`：契约被打破（导致整期验收不通过，退出码 1）
- `SKIP`：外部依赖缺失（脚本注明原因，不算失败）

## 运行方式

```bash
# 前置：隔离实例见 docs/TDD.md §13.3（复制库 + 清继承队列 + 重置口令 + 登录态）
python3 acceptance/run_p0.py    # P0 骨架
python3 acceptance/run_p1.py    # P1 内容核心（需 ZHIPU_API_KEY）
python3 acceptance/run_p6.py    # P6 任务体系（需 CS_REAPER_SEC=5 快巡）
python3 acceptance/run_p7.py    # P7 认证与单管理员（需确定性口令）
backend/.venv/bin/python acceptance/run_p8.py    # P8 功能补全（白盒 import 需要 sqlmodel，必须走 venv）
backend/.venv/bin/python acceptance/e2e_run.py   # E2E 端到端（playwright+PIL 必须走 venv；Mac 自动用项目根 .ms-playwright 浏览器）

# 可选环境变量
export CS_API_BASE=http://127.0.0.1:8200            # 服务地址（隔离实例）
export CS_BOOTSTRAP_PASSWORD=accept-12345          # 管理员口令（登录态）
```

P0/P1/P6/P7 仅用 Python 标准库（P8/e2e 走 venv：另需 sqlmodel、playwright+PIL）；runner/quality/hygiene 同为标准库。

## 依赖说明

- P1 需要 `ZHIPU_API_KEY`（否则 LLM 相关项 SKIP）
- P7 需要 `CS_BOOTSTRAP_PASSWORD`（隔离实例确定性口令），且预期失败登录多、需 `LOGIN_IP_MAX=1000` 放宽限流
- 验收产生的测试数据收尾必须清理（发布假数据会污染选题评分）；生产库跑 `acceptance/hygiene.py` 只读体检
