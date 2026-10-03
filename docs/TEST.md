# TEST · 测试方案（验收合同）

> 验收脚本即契约：`acceptance/run_p0.py ~ run_p7.py`（P3 随视频生产线移除），纯 Python 标准库、零依赖。
> 判定规则：**FAIL = 契约被打破**（退出码 1）；**SKIP = 外部依赖缺失**（注明原因）。
> 需求 ↔ 套件的映射见 PRD.md 各条"验收"标注；审计规程与检查清单见 AUDIT.md。

## 1. 套件清单

| 套件 | 范围 | 对应需求 | 通过门槛 |
|---|---|---|---|
| P0 骨架 | 服务/health/前端页/jobs 契约/LLM 真实调用/用量台账/设置接口（读·幂等写·非法值拒） | R7.2, R8.1/8.2/8.3, X4, X6 | 全 PASS；未配密钥时 LLM 项允许 SKIP |
| P1 内容核心 | idea 深研→入库评分+定审；快速选题+相似标记+否决；脚本三版（长短版可选）+自动打磨+定稿三产物；长文（**feed 公众号版默认档**：1200~1800 规格、长度断言 800~3600 字；**风格 style=auto 自动透镜**）+改稿+渲染（含 [CHART] 块剥离断言、知识图解端点与随文追加、**海报提示词包契约**）；**内容质量评审**（三版用口播尺、长文用 feed 尺，X11） | R1.1/1.1b/1.1c/1.3/1.5/1.7, R2.1-2.4, R5.1/5.2/5.4/5.1a, R6.6, X11（R1.8 话题雷达暂无脚本项，A6 对账缺口） | 全 PASS |
| P2 拆解流水线 | 本地视频拆解全链（转写+拆解 schema+选题候选）；结构模板 CRUD（含删除护栏）；同行账号增删改查与启停（含 kind=self 标记）、单号扫描与扫描历史、关键词发现对标账号（离线契约；全链路含一键加入设 CS_WATCH_SCAN=1）（含负向契约：假视频 ID 扫描显式终结、不存在账号 404、列表统计与评分字段）；自我风格 overview/历史接口契约；在线拆解 | R1.3, R2.2, R3.1/3.3, R9.1/9.5 | 本地链全 PASS；自我风格扫描全链路无条件跑；在线拆解未配 DOUYIN_TEST_URL 时 SKIP、同行扫描与关键词发现全链路设 CS_WATCH_SCAN=1 才跑（重链路） |
| P4 发布台 | 物料包字段完整（抖音标题/话题≥2/封面文案；「备选」字段后端存在、套件未断言）；**已移除功能负向契约 8 条**（records/metrics/summary/sau 检测与上传等 404/400）；**「不做长文」出口 3 条** | R6.1, R5.3 | 全 PASS |
| P5 口播数字人 | 配置 CRUD/余额/成本预估（租户积分口径：points=ceil(秒×3)、高质翻倍）/生成记录/删除边界（真实合成烧豆项默认 SKIP；克隆的计费闸在 P7「零余额 402」覆盖） | R10.1-10.4 | 全 PASS；真实合成设 CS_CHANJING_LIVE=1 才跑 |
| P6 任务体系 | 去重幂等（活跃唯一/实体去重）/失败分类（确定性不重试）/到期重试放行/欠任务补队/五要素字段/失败中台（指纹聚类、按指纹复活、指纹处置、实体孤儿复活） | X 任务体系 | 全 PASS |
| P8 功能补全 | 话题雷达契约（列表种入/添加/重复 409/ch_id 非数字 400/非抖音链接 400/挖矿受理）；采集探针（状态接口/手动探测六态分类）；调度中心（列表含探针/开关往返）；海报 freetext（太短 400/生成带 prompt）；**租户品牌九字段 round-trip**（GET 全量/PUT 覆盖/清空回落中性默认）；自动透镜受理与同源去重 409 | R1.8, R6.6, R7.3, X10, R11.3a, R5.1 | 全 PASS |
| E2E 端到端 | Playwright 驱动真实 UI：登录（错误拒/正确进）；**全站 14 页逐页零报错**（页头断言，401 探活噪声除外）；工作台快速记选题；公众号行展开与渲染；管理后台品牌卡九字段与保存回显；成员白名单冒烟 | X/A3（AUDIT 界面冒烟自动化） | 全 PASS |
| P7 多租户隔离 | 双租户账号准备/数据可见性隔离/越权 404/归属正确（flush 填充）/任务列表租户收窄/积分预扣与余额闸（402）/金额隐藏（usage/calls 无 cost、蝉豆无折¥）/媒体边界（配图越权 404、穿越不放行）/租户导出/租户删除全链路（确认校验、会话失效、登录拒绝）/停用租户重登拦截/克隆计费闸（零余额 402）/**安全加固**（文章 HTML 净化、任务 IDOR 404、蝉豆台账隔离、登出即失效、登录限流 429、首登改密服务端强制 403） | R11 全部 | 全 PASS |

## 2. 运行方法

```bash
python3 acceptance/run_p1.py                       # 默认打 http://127.0.0.1:8100
CS_API_BASE=http://127.0.0.1:8200 python3 acceptance/run_p1.py   # 指定实例
DOUYIN_TEST_URL=<真实抖音分享链接> python3 acceptance/run_p2.py  # 启用在线拆解项
```

- 环境变量：`CS_API_BASE`（目标实例）、`DOUYIN_TEST_URL`（在线拆解）、`CS_WATCH_SCAN=1`（同行扫描/发现全链路）、
  `CS_REAPER_SEC=5`（隔离实例快速巡逻，owed 用例必带）、
  `CS_BOOTSTRAP_PASSWORD` 或 `CS_ADMIN_PASSWORD`（**登录态**：登录门禁上线后全业务接口 401，
  runner 首请求前自动以 `admin` + 该口令登录并携带 Cookie；凭据缺失时用例以 401 显式 FAIL）。
- fixture：`acceptance/fixtures/make_video.py` 用 ffmpeg + macOS 中文 TTS 生成口播测试视频
  （`--check` 查依赖），P2 不需要真拍视频。

## 3. 隔离实例纪律（不污染真实数据）

验收会产生真实 LLM 调用并写入数据库（脚本/文章/记录/拆解行），**必须**在隔离实例上跑：

```bash
cp -R data data-acceptance
# 复制后先清继承队列（含 parked）：隔离库会带走真实排队任务，验收任务排在后面必然超时
sqlite3 data-acceptance/studio.db "UPDATE jobs SET status='failed' WHERE status IN ('queued','running','parked')"
# 清继承会话，并给 admin 重置为已知口令（登录门禁必需）
sqlite3 data-acceptance/studio.db "DELETE FROM user_sessions"
DATA_DIR=data-acceptance backend/.venv/bin/python -c \
  "import sys; sys.path.insert(0,'backend'); from app.auth import hash_password; \
   import sqlite3; db=sqlite3.connect('data-acceptance/studio.db'); \
   db.execute(\"UPDATE users SET password_hash=?, must_change_password=0 WHERE username='admin'\", \
   (hash_password('accept-12345'),)); db.commit()"
DATA_DIR=data-acceptance LOGIN_IP_MAX=1000 backend/.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host 127.0.0.1 --port 8200 &   # 验收含预期失败登录，放宽 IP 限流
DATA_DIR=data-acceptance CS_API_BASE=http://127.0.0.1:8200 CS_BOOTSTRAP_PASSWORD=accept-12345 \
  python3 acceptance/run_p0.py   # …p1~p7
# 跑完：停 8200，rm -rf data-acceptance
```

> 纪律：隔离实例一律用**复制库**约定（bootstrap 只在空库触发，全新 DATA_DIR 的库没有已知口令，
> 且部分套件直插 sqlite 夹具，需与生产同构的列默认值）。P6 夹具直插需 `CS_REAPER_SEC=5`。

## 4. 重跑纪律（红线）

- **任何后端改动后全套重跑**（用户裁定，隔离实例约 30~40 分钟）——"上次全绿"只对当时代码成立。
  实例：长文生成曾因 max_tokens 贴上限导致 JSON 截断，改动后未重跑而漏掉。
- 前端改动至少跑 P0（页面可达）+ 浏览器逐页冒烟（零报错、JS 零异常、页面默认表格视图、
  队列手动进入可用、登录门禁生效）。
- 涉及 LLM 预算/超时的改动，注意思考型模型实测耗时：打磨最长 ~350s、定稿 ~80s、
  轮询预算 ≥600s、脚本生成（三版+自动打磨两段 LLM）≥900s（基线实测值）。

## 5. 内容质量验收（X11）

- 结构断言之外，P1 对生成内容增加**质量评审**（LLM 评审员按尺打分 0-10，三把尺随内容类型走）：
  **口播尺**（脚本三版）：钩子强度（3秒点名受众+痛点）/ 口语化 / 证明具体性 / 结尾自然收尾
  （无刻意转化话术）/ 合规红线 / 人设回扣；
  **feed 尺**（长文公众号版，默认档）：开头即答案（前三段给最重结论，铺垫式开场 0~2 分）/
  数字与证据密度 / 可扫读性（判断式小节标题、段落短）/ 判断直给 / 合规（绝对化 0 分+无刻意 CTA）/
  语言人味（无机翻腔/论文腔）；
  **长文尺**（deep 深度版，备用）：书面深度分析六项（铺垫/深度/论证/收束/合规/语言）。
  断言：单项 ≥6 且总评 ≥7。
- **程序化违禁词**（与 backend 同表）：行业第一/全网第一/业界第一/排名第一、保证收益/回本/效果/
  赚钱/翻倍、百分百/稳赚/包赚/躺赚/轻松月入/闭眼入——命中即 FAIL；**否定语境窗口**：命中位前 4 字
  含否定词（别/不/没/非…）放行（「千万别挑最好的那条」是反向建议非绝对化承诺，2026-10-02 教训）。
  裸「最好的」已移出程序化表（误杀反向建议与描述性比较），语义级由评审 compliance 项兜底。
- 分节流程另有 **CTA 程序化守卫**（评论区/私信/关注/扣字/领资料命中即定向重写，与 backend 同表）。
- 评审 prompt 与阈值维护在 `acceptance/quality.py`，评审本身记入 llm_calls 台账。

## 6. 数据卫生自动化

`acceptance/hygiene.py [data_dir]`：一条命令扫生产库的测试残留与假数据（对齐 AUDIT A4 清单）——
拆解库 fixture 行（sample_speech）、演示/验收链接的发布记录、选题评分回流残留字段、
jobs 表未收敛的"服务重启中断"记录（提示级）；退出码 0 干净 / 1 有残留；验收与审计前必跑。

## 7. 判定语义与输出

- 每套输出逐项 `[PASS]/[FAIL]/[SKIP]` + 关键返回值，末尾汇总 `N PASS / N FAIL / N SKIP` 与结论。
- 任何 FAIL → 退出码 1，可直接当 CI 门禁；SKIP 必须带原因（外部依赖缺失不算破坏契约）。
