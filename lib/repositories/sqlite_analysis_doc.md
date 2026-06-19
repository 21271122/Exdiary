# lib/repositories/sqlite_analysis.py — 说明文档

## 文件作用摘要

跨实验分析报告的 SQLite 仓储实现。替代 `YamlAnalysisRepository`，数据存入 `data.db` 的 `analyses` 表。实现 `AbstractAnalysisRepository` 接口。

---

## 代码块详细说明

### 模块级辅助函数

- `_now() -> str` — 当前时间字符串。

### 类

#### `SqliteAnalysisRepository(AbstractAnalysisRepository)`

**构造参数**: `db_path: str` — 数据库文件路径。构造函数自动建表并设置 WAL 模式。

**实例属性**: `self.db: sqlite3.Connection`

**方法**:

- `_create_tables() -> None` — 建 `analyses` 表（id, timestamp, question, selected_ids, analysis, created_at）。
- `next_id() -> str` — 生成下一个分析报告 ID（`ANAL-YYYY-NNN` 格式）。
- `save(analysis: dict) -> str` — 保存分析报告。`INSERT OR REPLACE`。
- `load(aid: str) -> dict | None` — 按 ID 加载。返回 id/timestamp/question/selected_ids/analysis。
- `list_all() -> list[dict]` — 列出全部，按时间降序。
- `delete(aid: str) -> bool` — 按 ID 删除。
