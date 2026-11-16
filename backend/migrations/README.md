# migrations · schema 演进收口（B 阶段，2026-11-16）

## 现状与口径

- **v0__baseline.sql** = 当前全量 schema 基线（24 表 + 索引，2026-11-16 从干净隔离库导出）。
  历史 ALTER（约 46 条）已全部体现在基线里；`app/db.py::_migrate` 的幂等守卫保留，
  服务于「老库补列」——两条路径（create_all / _migrate）产出都与 v0 对齐，
  由 `app/schema_audit.py` 审计闸强制（模型 DDL ↔ 库 DDL 只读 diff 必须为空）。
- **新库**：create_all 直接建出 v0 同构 schema；**老库**：_migrate 补列/重建后与 v0 对齐。

## 以后改 schema 的固定三步（缺一即漂移，审计闸会打回）

1. **改模型**（`backend/app/models/`）——模型是单一真相；
2. **加迁移文件** `v{N}__一句话描述.sql`（N 递增；内容为对该库生效的 DDL/回填 SQL，
   可重复执行者加 IF NOT EXISTS / 幂等守卫）；
3. **跑审计闸验证对齐**：隔离实例 `python -m app.schema_audit`（先红后绿：
   只改模型不加迁移=红；补迁移=绿），绿了才可发布。

## 当前定位与边界（如实声明，WB #96 中3/中2 采纳）

- **migrations/ 当前为「仅存档」**：v0 是基线存档，目录里还没有可执行的 runner。
  首个真正的 schema 变更落地时，同步引入按文件名升序执行的迷你 runner（约定先行）；
  在那之前，「按文件名升序执行」只是流程约定，不产生任何自动行为。
- **审计只比列名集合**：类型/长度/NOT NULL/DEFAULT 漂移不在审计范围（docstring 如实声明）——
  这类变更靠迁移文件评审把关，不靠闸。
- **部署闸为单机口径**：sync-server.sh 只审计 CS_SYNC_HOST 指向的那一台；
  同一 ROOT 挂多机时，每台都要跑一次（rsync --delete 后只审一台=其余假绿）。
- 生产库路径以 sync-server.sh 在途任务检查同款（$REMOTE_ROOT/data/studio.db）为准，
  闸用 --db 直指该文件，不经 settings 解析（防 CWD/.env 差异审错库）。
