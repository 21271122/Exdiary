# lib/repositories/sqlite_experiment.py — 说明文档

## 文件作用摘要

实验记录的 SQLite 仓储实现。替代 `YamlExperimentRepository`，将数据存入 `data.db` 的 `experiments` 表。实现 `AbstractExperimentRepository` 接口，路由层、服务层、Agent 层零改动。

---

## 代码块详细说明

### 模块级常量

- `_JSON_FIELDS: set[str]` — 需要 JSON 序列化/反序列化的字段集合。包含 tags、materials、equipment、experimental_plan、sop、process_parameters、observations、characterization、results、next_steps、references、analyzed_in、attachments。用于 `_dict_to_row` 和 `_row_to_dict` 转换。

### 模块级函数

- `_dict_to_row(d: dict) -> dict` — 实验 dict → 数据库行。数组和对象字段 JSON 序列化，简单字段原样保留。
- `_row_to_dict(row: sqlite3.Row) -> dict` — 数据库行 → 实验 dict。`_JSON_FIELDS` 中的字段反序列化为 Python 对象，其余字段原样保留。
- `_now() -> str` — 返回当前时间字符串 `YYYY-MM-DD HH:MM:SS`。

### 类

#### `SqliteExperimentRepository(AbstractExperimentRepository)`

**构造参数**: `db_path: str` — SQLite 数据库文件路径。构造函数自动创建表（`CREATE TABLE IF NOT EXISTS`）并设置 WAL 模式。

**实例属性**: `self.db: sqlite3.Connection`

**方法** (实现 ABC 全部 9 个抽象方法):

- `_create_tables() -> None` — 建表逻辑。创建 `experiments` 表（含 22 列）和 `experiments_fts` 全文搜索虚拟表。幂等（`IF NOT EXISTS`）。
- `next_id() -> str` — 生成下一个实验 ID。查询当年已存在的最大编号 +1。
- `save(experiment: dict) -> str` — 新建或覆盖保存。`INSERT OR REPLACE` 兼顾两者。自动设置 `id` 和 `updated_at`。
- `load(exp_id: str) -> dict | None` — 按 ID 加载单条实验。返回完整 dict，不存在返回 None。
- `list_all() -> list[dict]` — 列出全部实验摘要（id/title/date/experimenter/status/tags），按日期降序。
- `list_all_full() -> list[dict]` — 列出全部实验完整数据，按日期降序。
- `update(exp_id: str, experiment: dict) -> bool` — 覆盖更新。委托给 `save()`。
- `delete(exp_id: str) -> bool` — 按 ID 删除。返回是否实际删除了行。
- `count() -> int` — 返回实验总数。
- `search(query: str) -> list[dict]` — FTS5 全文搜索。返回匹配行，按相关性排序。
- `summarize_all(exp_ids: list[str] | None = None) -> str` — 生成实验摘要文本。格式与 YAML 版一致（`### ID: Title\nDate: ...`）。用在分析线程中喂给 LLM。
