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

## 约定

- 迁移文件按文件名升序执行（未来接入 runner 时以文件名为版本号）；
- 部署闸在 `scripts/sync-server.sh`（rsync 后、重启前，只读）：
  新代码模型 ↔ 生产库 diff 非空 = 中止重启（旧进程继续服务，生产零影响）；
- 禁止手改生产库结构绕过此流程（11-15 两次漂移事故的根因）。
