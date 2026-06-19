# lib/repositories/sqlite_update_log.py — 说明文档

## 文件作用摘要

实验更新日志的 SQLite 仓储实现。替代 `YamlUpdateLogRepository`，数据存入 `data.db` 的 `update_logs` 表。实现 `AbstractUpdateLogRepository` 接口。

---

## 代码块详细说明

### 类

#### `SqliteUpdateLogRepository(AbstractUpdateLogRepository)`

**构造参数**: `db_path: str`。自动建表。

**实例属性**: `self.db: sqlite3.Connection`

**方法**:

- `_create_tables() -> None` — 建 `update_logs` 表（entry_id, exp_id, timestamp, source, thread_id, context, changes）。
- `_next_entry_id(exp_id: str) -> str` — 生成 `UPD-NNN-XXX` 格式的条目 ID。
- `append(exp_id, source, changes, context=None, thread_id=None) -> str` — 追加一条更新日志。`changes` 以 JSON 存储。
- `list_recent(exp_id: str, limit: int = 5) -> list[dict]` — 返回最近 N 条，按时间降序。
- `list_all(exp_id: str) -> list[dict]` — 返回全部条目。
- `get_entry(exp_id: str, entry_id: str) -> dict | None` — 获取单条。
