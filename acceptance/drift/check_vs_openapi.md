# check × openapi 差集（`python3 acceptance/drift/check_vs_openapi.py --md`）

| 套件 | 端点数 | 在线 | 不在线 |
| --- | --- | --- | --- |
| run_p0.py | 5 | 4 | 0 |
| run_p1.py | 12 | 9 | 0 |
| run_p6.py | 8 | 8 | 0 |
| run_p7.py | 16 | 7 | 0 |
| run_p8.py | 23 | 19 | 3 |
| e2e_run.py | 0 | 0 | 0 |
| **合计（去重）** | **59** | **56** | **3（5.1%）** |

（另有 12 条负断言/已下线路由有出处豁免，见下节，不计入死端点）

## 不在线端点（待处理）

- `/api/articles/kb-topics` — run_p8.py
- `/api/radar` — run_p8.py
- `/api/radar/mine` — run_p8.py

## 负断言 / 已下线路由（有出处，不计入死端点）

- `/api/admin/tenants` — run_p7:119-131 负断言（多租户拆除 a78e200，2026-10-27）
- `/api/admin/users` — run_p7:119-131 负断言（同上）
- `/api/auth/switch-tenant` — run_p7:119-131 负断言（切换租户拆除，a78e200）
- `/api/auth/tenants` — run_p7:119-131 负断言（同上）
- `/api/avatar/balance` — run_p7:119-131 负断言（数字人退役 4a9ba6a，2026-10-29）
- `/api/avatar/usage` — run_p7:119-131 负断言（同上）
- `/api/credits` — run_p7:119-131 负断言（积分体系拆除，a78e200）
- `/api/jobs/999999` — run_p0 —— 刻意打不存在的任务，验 404 契约
- `/api/llm/calls` — run_p7:119-131 负断言（用量台账不在系统范围，PRD R8.3/X4）
- `/api/llm/usage` — run_p7:119-131 负断言（同上）
- `/api/topics/import/feigua` — run_p1 —— 飞瓜导入已移除，打 404/405 即绿（commit 0cccf40 全删 topics.py）
- `/api/topics/trending` — run_p8 —— 旧热点提炼端点已删，打 404 即绿（选题工场替代，PRD R1.4）
