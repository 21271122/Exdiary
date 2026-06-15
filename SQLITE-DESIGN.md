# Exdiary SQLite 迁移实施方案

> 零依赖、并行运行、可回滚。Python 标准库 `sqlite3`，不引入 ORM。

---

## 一、总体策略

- YAML 和 SQLite 两套 Repository 实现并存，通过 `config.yaml` 中的 `STORAGE` 配置项切换
- 首次启动自动迁移现有 YAML 数据到 SQLite，迁移后 YAML 文件**保留不删**（双重保险）
- 所有 Repository 类实现相同的抽象接口（`lib/repositories/base.py`），上层 Service 和路由代码**零改动**
- 迁移脚本 `lib/repositories/migrate.py` 可独立运行

```yaml
# config.yaml 新增项
STORAGE: yaml    # yaml | sqlite（默认 yaml 保持向后兼容）
```

---

## 二、数据库 Schema

单文件 `experiments/exdiary.db`，6 张表。

### 2.1 实验表 `experiments`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `TEXT PRIMARY KEY` | `EXP-2026-001` |
| `title` | `TEXT` | 实验标题 |
| `date` | `TEXT` | `YYYY-MM-DD` |
| `experimenter` | `TEXT` | 实验者 |
| `status` | `TEXT` | `planned \| running \| done \| failed \| repeated` |
| `tags` | `TEXT` | JSON 数组 `["photocatalysis","thin-film"]` |
| `purpose` | `TEXT` | 实验目的 |
| `materials` | `TEXT` | JSON 对象数组 `[{name,purity,vendor,amount,notes}]` |
| `equipment` | `TEXT` | JSON 对象数组 `[{device,model,location}]` |
| `experimental_plan` | `TEXT` | JSON 对象数组 `[{group,condition,expected}]` |
| `sop` | `TEXT` | JSON 字符串数组 |
| `process_parameters` | `TEXT` | JSON 对象数组 `[{step,parameter,setpoint,actual,deviation}]` |
| `observations` | `TEXT` | JSON 对象 `{no_anomalies:bool,items:[string]}` |
| `characterization` | `TEXT` | JSON 对象数组 `[{method,sample_id,preparation,submission_date,data_path}]` |
| `results` | `TEXT` | JSON 对象 `{qualitative:string,key_data:[{metric,value}],figures:[]}` |
| `conclusion` | `TEXT` | 结论 |
| `next_steps` | `TEXT` | JSON 字符串数组 |
| `references` | `TEXT` | JSON 字符串数组（`@EXP-xxx` 引用） |
| `referenced_by` | `TEXT` | JSON 字符串数组（被谁引用） |
| `analyzed_in` | `TEXT` | JSON 字符串数组（被哪些分析引用） |
| `original_notes` | `TEXT` | 原始笔记全文 |
| `created_at` | `TEXT` | 创建时间 `YYYY-MM-DD HH:MM:SS` |
| `updated_at` | `TEXT` | 最后修改时间 |

**设计要点**：
- 标量字段（`title`/`date`/`status`/`purpose`/`conclusion`）使用普通列——可直接 SQL `WHERE` 查询和建索引
- 嵌套结构（`materials`/`sop`/`results` 等）使用 JSON TEXT 列——通过 `json_extract()` 按需穿透查询
- **不做多表范式化**：材料/设备/参数不拆子表。当前体量（<1000 条实验）不需要，徒增 JOIN 复杂度

```sql
CREATE INDEX idx_exp_status ON experiments(status);
CREATE INDEX idx_exp_date   ON experiments(date);
```

### 2.2 分析报告表 `analyses`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `TEXT PRIMARY KEY` | `ANAL-2026-001` |
| `timestamp` | `TEXT` | 分析时间 |
| `question` | `TEXT` | 分析问题 |
| `selected_ids` | `TEXT` | JSON 数组 `["EXP-001","EXP-002"]` |
| `analysis` | `TEXT` | Markdown 报告全文 |
| `created_at` | `TEXT` | 创建时间 |

### 2.3 对话线程表 `threads`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `TEXT PRIMARY KEY` | `THR-2026-001` |
| `type` | `TEXT` | `record \| analyze` |
| `status` | `TEXT` | `active \| done` |
| `title` | `TEXT` | 线程标题 |
| `summary` | `TEXT` | 摘要 |
| `exp_generated` | `TEXT` | 生成的 EXP ID |
| `anal_generated` | `TEXT` | 生成的 ANAL ID |
| `selected_exps` | `TEXT` | JSON 数组 |
| `experiment_type` | `TEXT` | 实验类型 |
| `messages` | `TEXT` | JSON 数组（OpenAI 消息格式） |
| `branches` | `TEXT` | JSON 数组 |
| `created` | `TEXT` | 创建时间 |
| `updated` | `TEXT` | 更新时间 |

```sql
CREATE INDEX idx_threads_status ON threads(status);
```

### 2.4 置顶表 `pinned`

| 字段 | 类型 | 说明 |
|------|------|------|
| `exp_id` | `TEXT PRIMARY KEY` | 实验 ID（FK → experiments.id） |
| `position` | `INTEGER NOT NULL` | 排序位置（1-3） |

外键级联删除：实验被删除时自动清除置顶记录。

```sql
CREATE TABLE pinned (
    exp_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    FOREIGN KEY (exp_id) REFERENCES experiments(id) ON DELETE CASCADE
);
```

### 2.5 收藏夹明细表 `collection_items`

| 字段 | 类型 | 说明 |
|------|------|------|
| `collection_name` | `TEXT NOT NULL DEFAULT '默认收藏夹'` | 收藏夹名称 |
| `exp_id` | `TEXT NOT NULL` | 实验 ID（FK → experiments.id） |

```sql
CREATE TABLE collection_items (
    collection_name TEXT NOT NULL DEFAULT '默认收藏夹',
    exp_id TEXT NOT NULL,
    PRIMARY KEY (collection_name, exp_id),
    FOREIGN KEY (exp_id) REFERENCES experiments(id) ON DELETE CASCADE
);
```

### 2.6 更新日志表 `update_logs`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | 自增主键 |
| `exp_id` | `TEXT NOT NULL` | 关联实验 ID |
| `entry_id` | `TEXT NOT NULL` | `UPD-NNN-XXX` 格式 |
| `timestamp` | `TEXT` | 记录时间 |
| `source` | `TEXT` | `manual_edit \| parent_agent \| child_agent \| system` |
| `thread_id` | `TEXT` | 关联线程 ID |
| `context` | `TEXT` | JSON 对象 `{"summary":"修改了 3 个字段"}` |
| `changes` | `TEXT` | JSON 数组 `[{path,field,old,new}]` |

```sql
CREATE UNIQUE INDEX idx_logs_entry ON update_logs(exp_id, entry_id);
CREATE INDEX idx_logs_exp_id ON update_logs(exp_id);
```

### 2.7 键值存储表 `kv_store`

存放 ThreadStore 的元数据（索引、运行时状态、全局上下文、子 Agent 状态）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | `TEXT PRIMARY KEY` | `"index" \| "current_state" \| "global_context" \| "child_state:THR-xxx"` |
| `value` | `TEXT` | JSON |
| `updated_at` | `TEXT` | 更新时间 |

一条 `key="index"` 的行存全部索引数据（threads 列表 + exp_to_thread 映射 + anal_to_thread 映射 + user_profile），一条 `key="current_state"` 的行存父 Agent 运行时状态，`key="child_state:{thread_id}"` 储存各子 Agent 状态。结构与原 `index.yaml`、`_current_state.yaml`、`*_child_state.yaml` 中的内容一一对应。

---

## 三、新增文件清单

全部在 `lib/repositories/` 下：

```
lib/repositories/
  sqlite_connection.py    # 共享的 SQLite 连接管理 + Schema 初始化 DDL
  sqlite_experiment.py    # SqliteExperimentRepository（9 方法）
  sqlite_analysis.py      # SqliteAnalysisRepository（5 方法）
  sqlite_thread.py        # SqliteThreadRepository（22 方法 + kv_store 操作）
  sqlite_favorites.py     # SqliteFavoritesRepository（12 方法，范式化存储）
  sqlite_update_log.py    # SqliteUpdateLogRepository（8 方法）
  migrate.py              # YAML → SQLite 数据迁移脚本
```

---

## 四、核心模块设计

### 4.1 `sqlite_connection.py` —— 连接管理 + Schema 初始化

```python
import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    experimenter TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    tags TEXT NOT NULL DEFAULT '[]',
    purpose TEXT NOT NULL DEFAULT '',
    materials TEXT NOT NULL DEFAULT '[]',
    equipment TEXT NOT NULL DEFAULT '[]',
    experimental_plan TEXT NOT NULL DEFAULT '[]',
    sop TEXT NOT NULL DEFAULT '[]',
    process_parameters TEXT NOT NULL DEFAULT '[]',
    observations TEXT NOT NULL DEFAULT '{"no_anomalies":true,"items":[]}',
    characterization TEXT NOT NULL DEFAULT '[]',
    results TEXT NOT NULL DEFAULT '{"qualitative":"","key_data":[],"figures":[]}',
    conclusion TEXT NOT NULL DEFAULT '',
    next_steps TEXT NOT NULL DEFAULT '[]',
    references TEXT NOT NULL DEFAULT '[]',
    referenced_by TEXT NOT NULL DEFAULT '[]',
    analyzed_in TEXT NOT NULL DEFAULT '[]',
    original_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    selected_ids TEXT NOT NULL DEFAULT '[]',
    analysis TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    exp_generated TEXT NOT NULL DEFAULT '',
    anal_generated TEXT NOT NULL DEFAULT '',
    selected_exps TEXT NOT NULL DEFAULT '[]',
    experiment_type TEXT NOT NULL DEFAULT 'other',
    messages TEXT NOT NULL DEFAULT '[]',
    branches TEXT NOT NULL DEFAULT '[]',
    created TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pinned (
    exp_id TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    FOREIGN KEY (exp_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS collection_items (
    collection_name TEXT NOT NULL DEFAULT '默认收藏夹',
    exp_id TEXT NOT NULL,
    PRIMARY KEY (collection_name, exp_id),
    FOREIGN KEY (exp_id) REFERENCES experiments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS update_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    exp_id TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    thread_id TEXT,
    context TEXT NOT NULL DEFAULT '{}',
    changes TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_exp_status ON experiments(status);
CREATE INDEX IF NOT EXISTS idx_exp_date ON experiments(date);
CREATE INDEX IF NOT EXISTS idx_logs_exp_id ON update_logs(exp_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_logs_entry ON update_logs(exp_id, entry_id);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """创建连接、启用 WAL 模式、执行 Schema DDL。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row        # 查询结果可通过列名访问
    conn.execute("PRAGMA journal_mode=WAL")     # 读写并发
    conn.execute("PRAGMA foreign_keys=ON")      # 启用外键约束
    conn.executescript(SCHEMA_SQL)
    return conn


def need_migration(conn: sqlite3.Connection) -> bool:
    """判断是否需要从 YAML 迁移：experiments 表为空则需迁移。"""
    return conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0
```

**关键设计决策**：
- 每个 Repository 初始化时接收同一个 `sqlite3.Connection` 实例，共享连接
- WAL 模式保证单连接上的读写并发安全——单用户本地应用完全够用
- `row_factory = sqlite3.Row` 使查询结果可同时用索引和列名访问
- 所有 JSON 字段设置 `NOT NULL DEFAULT '[]'` 或 `'{}'`，避免 NULL 处理

### 4.2 `sqlite_experiment.py` —— 实验仓库

```python
import json
from datetime import datetime
from lib.repositories.base import AbstractExperimentRepository


class SqliteExperimentRepository(AbstractExperimentRepository):
    """SQLite 实现的实验仓库。对外 dict 格式与 YAML 版本完全一致。"""

    def __init__(self, conn):
        self.conn = conn

    # ---- ID 生成 ----

    def next_id(self) -> str:
        year = datetime.now().strftime("%Y")
        row = self.conn.execute(
            "SELECT id FROM experiments WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
            (f"EXP-{year}-%",)
        ).fetchone()
        n = int(row["id"].split("-")[-1]) + 1 if row else 1
        return f"EXP-{year}-{n:03d}"

    # ---- CRUD ----

    def save(self, experiment: dict) -> str:
        exp_id = experiment.get("id") or self.next_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("""INSERT OR REPLACE INTO experiments
            (id, title, date, experimenter, status, tags, purpose,
             materials, equipment, experimental_plan, sop,
             process_parameters, observations, characterization,
             results, conclusion, next_steps, references,
             referenced_by, analyzed_in, original_notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exp_id,
             experiment.get("title", ""),
             experiment.get("date", ""),
             experiment.get("experimenter", ""),
             experiment.get("status", "planned"),
             json.dumps(experiment.get("tags", []), ensure_ascii=False),
             experiment.get("purpose", ""),
             json.dumps(experiment.get("materials", []), ensure_ascii=False),
             json.dumps(experiment.get("equipment", []), ensure_ascii=False),
             json.dumps(experiment.get("experimental_plan", []), ensure_ascii=False),
             json.dumps(experiment.get("sop", []), ensure_ascii=False),
             json.dumps(experiment.get("process_parameters", []), ensure_ascii=False),
             json.dumps(experiment.get("observations",
                        {"no_anomalies": True, "items": []}), ensure_ascii=False),
             json.dumps(experiment.get("characterization", []), ensure_ascii=False),
             json.dumps(experiment.get("results",
                        {"qualitative": "", "key_data": [], "figures": []}), ensure_ascii=False),
             experiment.get("conclusion", ""),
             json.dumps(experiment.get("next_steps", []), ensure_ascii=False),
             json.dumps(experiment.get("references", []), ensure_ascii=False),
             json.dumps(experiment.get("referenced_by", []), ensure_ascii=False),
             json.dumps(experiment.get("analyzed_in", []), ensure_ascii=False),
             experiment.get("original_notes", ""),
             now,
             now))
        self.conn.commit()
        return exp_id

    def load(self, exp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM experiments WHERE id = ?", (exp_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def update(self, exp_id: str, experiment: dict) -> bool:
        experiment["id"] = exp_id
        experiment["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save(experiment)
        return True

    def delete(self, exp_id: str) -> bool:
        cur = self.conn.execute("DELETE FROM experiments WHERE id = ?", (exp_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ---- 查询 ----

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, title, date, experimenter, status, tags "
            "FROM experiments ORDER BY id DESC"
        ).fetchall()
        return [
            {"id": r["id"], "title": r["title"], "date": r["date"],
             "experimenter": r["experimenter"], "status": r["status"],
             "tags": json.loads(r["tags"]) if r["tags"] else []}
            for r in rows
        ]

    def list_all_full(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM experiments ORDER BY id DESC"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]

    def summarize_all(self, exp_ids: list[str] | None = None) -> str:
        if exp_ids:
            placeholders = ",".join("?" * len(exp_ids))
            rows = self.conn.execute(
                f"SELECT * FROM experiments WHERE id IN ({placeholders})",
                exp_ids
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM experiments").fetchall()
        exps = [self._row_to_dict(r) for r in rows]

        parts = []
        for exp in exps:
            results = exp.get("results", {}) or {}
            obs = exp.get("observations", {}) or {}
            obs_items = obs.get("items", []) if isinstance(obs, dict) else []
            parts.append(
                f"### {exp['id']}: {exp.get('title','')}\n"
                f"Date: {exp.get('date','')} | Status: {exp.get('status','')} "
                f"| Tags: {', '.join(exp.get('tags',[]))}\n"
                f"Purpose: {str(exp.get('purpose',''))[:300]}\n"
                f"Conclusion: {str(exp.get('conclusion',''))[:300]}\n"
                f"Key Results: {str(results.get('qualitative',''))[:200]}\n"
                f"Observations: {'; '.join(obs_items)[:200]}\n"
            )
        return "\n---\n".join(parts) if parts else "No experiments found."

    # ---- 内部辅助 ----

    def _row_to_dict(self, row) -> dict:
        """将数据库行还原为与 YAML 格式完全兼容的 dict。"""
        json_arrays = ["tags", "materials", "equipment", "experimental_plan",
                       "sop", "process_parameters", "characterization",
                       "next_steps", "references", "referenced_by", "analyzed_in"]
        json_objects = ["observations", "results"]

        d = dict(row)
        for f in json_arrays:
            d[f] = json.loads(d[f]) if d[f] else []
        for f in json_objects:
            d[f] = json.loads(d[f]) if d[f] else {}
        return d
```

**设计要点**：
- 所有 JSON 字段的序列化/反序列化集中在 `_row_to_dict()` 一个方法里——调用方无感知
- `list_all()` 只 SELECT 需要的 6 列，不加载 `materials` 等大型 JSON 字段
- `INSERT OR REPLACE` 语义：主键冲突时覆盖——`save()` 同时用于新建和更新
- `summarize_all()` 支持 `IN (...)` 参数化查询，避免 SQL 注入

### 4.3 `sqlite_analysis.py` —— 分析报告仓库

```python
class SqliteAnalysisRepository(AbstractAnalysisRepository):
    def __init__(self, conn):
        self.conn = conn

    def next_id(self) -> str:
        year = datetime.now().strftime("%Y")
        row = self.conn.execute(
            "SELECT id FROM analyses WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
            (f"ANAL-{year}-%",)
        ).fetchone()
        n = int(row["id"].split("-")[-1]) + 1 if row else 1
        return f"ANAL-{year}-{n:03d}"

    def save(self, analysis: dict) -> str:
        aid = analysis.get("id") or self.next_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("""INSERT OR REPLACE INTO analyses
            (id, timestamp, question, selected_ids, analysis, created_at)
            VALUES (?,?,?,?,?,?)""",
            (aid,
             analysis.get("timestamp", ""),
             analysis.get("question", ""),
             json.dumps(analysis.get("selected_ids", []), ensure_ascii=False),
             analysis.get("analysis", ""),
             now))
        self.conn.commit()
        return aid

    def load(self, aid: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM analyses WHERE id = ?", (aid,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["selected_ids"] = json.loads(d["selected_ids"]) if d["selected_ids"] else []
        return d

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM analyses ORDER BY id DESC"
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["selected_ids"] = json.loads(d["selected_ids"]) if d["selected_ids"] else []
            results.append(d)
        return results

    def delete(self, aid: str) -> bool:
        cur = self.conn.execute("DELETE FROM analyses WHERE id = ?", (aid,))
        self.conn.commit()
        return cur.rowcount > 0
```

### 4.4 `sqlite_favorites.py` —— 范式化存储

```python
class SqliteFavoritesRepository(AbstractFavoritesRepository):
    def __init__(self, conn):
        self.conn = conn

    def is_pinned(self, exp_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM pinned WHERE exp_id = ?", (exp_id,)
        ).fetchone() is not None

    def toggle_pin(self, exp_id: str) -> dict:
        if self.is_pinned(exp_id):
            self.conn.execute("DELETE FROM pinned WHERE exp_id = ?", (exp_id,))
            self.conn.commit()
            return {"ok": True, "pinned": False}
        count = self.conn.execute("SELECT COUNT(*) FROM pinned").fetchone()[0]
        if count >= 3:
            return {"ok": False, "error": "最多只能置顶 3 个实验"}
        self.conn.execute(
            "INSERT INTO pinned (exp_id, position) VALUES (?, ?)",
            (exp_id, count + 1))
        self.conn.commit()
        return {"ok": True, "pinned": True}

    def get_pinned(self) -> list[str]:
        return [r["exp_id"] for r in
                self.conn.execute("SELECT exp_id FROM pinned ORDER BY position").fetchall()]

    def toggle_favorite(self, exp_id: str, collection: str = "默认收藏夹") -> dict:
        row = self.conn.execute(
            "SELECT 1 FROM collection_items WHERE collection_name=? AND exp_id=?",
            (collection, exp_id)
        ).fetchone()
        if row:
            self.conn.execute(
                "DELETE FROM collection_items WHERE collection_name=? AND exp_id=?",
                (collection, exp_id))
            self.conn.commit()
            return {"ok": True, "favorited": False}
        self.conn.execute(
            "INSERT INTO collection_items (collection_name, exp_id) VALUES (?,?)",
            (collection, exp_id))
        self.conn.commit()
        return {"ok": True, "favorited": True}

    def get_collections(self) -> dict:
        rows = self.conn.execute(
            "SELECT collection_name, exp_id FROM collection_items ORDER BY collection_name"
        ).fetchall()
        collections = {}
        for r in rows:
            collections.setdefault(r["collection_name"], []).append(r["exp_id"])
        if "默认收藏夹" not in collections:
            collections["默认收藏夹"] = []
        return collections

    def create_collection(self, name: str) -> dict:
        existing = self.conn.execute(
            "SELECT 1 FROM collection_items WHERE collection_name=? LIMIT 1", (name,)
        ).fetchone()
        if existing:
            return {"ok": False, "error": "收藏夹已存在"}
        return {"ok": True}

    def delete_collection(self, name: str) -> dict:
        if name == "默认收藏夹":
            return {"ok": False, "error": "不能删除默认收藏夹"}
        cur = self.conn.execute(
            "DELETE FROM collection_items WHERE collection_name=?", (name,))
        self.conn.commit()
        return {"ok": cur.rowcount > 0}
```

**与 YAML 版的关键差异**：
- `is_pinned` 不是读全量 pinned 列表然后 `in` 检查——直接 SELECT 单行
- `get_collections` 用 `GROUP BY` 式的应用层聚合替代 YAML 的嵌套 dict
- `create_collection` 不需要立即插入任何行——收藏夹只是逻辑概念，等第一次收藏时才会有 `collection_items` 行

### 4.5 `sqlite_update_log.py`

```python
class SqliteUpdateLogRepository(AbstractUpdateLogRepository):
    def __init__(self, conn):
        self.conn = conn

    def _next_entry_id(self, exp_id: str) -> str:
        m = re.match(r"EXP-\d{4}-(\d{3})", exp_id)
        exp_num = m.group(1) if m else "000"
        row = self.conn.execute(
            "SELECT entry_id FROM update_logs WHERE exp_id=? AND entry_id LIKE ? "
            "ORDER BY entry_id DESC LIMIT 1",
            (exp_id, f"UPD-{exp_num}-%")
        ).fetchone()
        n = int(row["entry_id"].split("-")[-1]) + 1 if row else 1
        return f"UPD-{exp_num}-{n:03d}"

    def append(self, exp_id: str, source: str, changes: list[dict],
               context: dict | None = None, thread_id: str | None = None) -> str:
        entry_id = self._next_entry_id(exp_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT INTO update_logs (exp_id, entry_id, timestamp, source, "
            "thread_id, context, changes) VALUES (?,?,?,?,?,?,?)",
            (exp_id, entry_id, now, source, thread_id,
             json.dumps(context or {}, ensure_ascii=False),
             json.dumps(changes, ensure_ascii=False)))
        self.conn.commit()
        return entry_id

    def list_recent(self, exp_id: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM update_logs WHERE exp_id=? ORDER BY id DESC LIMIT ?",
            (exp_id, limit)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row) -> dict:
        d = dict(row)
        d["context"] = json.loads(d["context"]) if d["context"] else {}
        d["changes"] = json.loads(d["changes"]) if d["changes"] else []
        return d
    # ... list_all, get_entry 类似实现
```

### 4.6 `sqlite_thread.py` —— KV 存储辅助

ThreadStore 是方法最多的仓库（22 个公开方法 + 若干辅助方法）。核心差异是将原来对 `index.yaml`、`_current_state.yaml` 等文件的读写改为对 `kv_store` 表的操作。

```python
class SqliteThreadRepository(AbstractThreadRepository):
    def __init__(self, conn):
        self.conn = conn
        self._index_cache = None

    def _kv_get(self, key: str) -> dict:
        row = self.conn.execute(
            "SELECT value FROM kv_store WHERE key=?", (key,)
        ).fetchone()
        return json.loads(row["value"]) if row and row["value"] else {}

    def _kv_set(self, key: str, value: dict) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value, updated_at) VALUES (?,?,?)",
            (key, json.dumps(value, ensure_ascii=False), now))
        self.conn.commit()

    def get_index(self) -> dict:
        if self._index_cache is not None:
            return dict(self._index_cache)
        idx = self._kv_get("index")
        idx.setdefault("active_thread", None)
        idx.setdefault("threads", [])
        idx.setdefault("exp_to_thread", {})
        idx.setdefault("anal_to_thread", {})
        idx.setdefault("user_profile", {
            "experimenter_counts": {},
            "default_experimenter": "",
            "tag_counts": {},
            "frequent_tags": [],
            "last_updated": "",
        })
        self._index_cache = idx
        return dict(idx)

    def update_index(self, thread_data: dict) -> None:
        idx = self.get_index()
        # ... 与 YAML 版完全一致的更新逻辑 ...
        self._index_cache = idx
        self._kv_set("index", idx)

    def save_current_state(self, agent_state: dict) -> None:
        self._kv_set("current_state", agent_state)

    def load_current_state(self) -> dict | None:
        state = self._kv_get("current_state")
        return state if state else None

    def save_child_state(self, thread_id: str, agent_state: dict) -> None:
        self._kv_set(f"child_state:{thread_id}", agent_state)

    def load_child_state(self, thread_id: str) -> dict | None:
        state = self._kv_get(f"child_state:{thread_id}")
        return state if state else None

    def delete_child_state(self, thread_id: str) -> None:
        self.conn.execute("DELETE FROM kv_store WHERE key=?", (f"child_state:{thread_id}",))
        self.conn.commit()

    # ... 其余 CRUD 方法（create/save/load/list_recent/build_global_summary 等）...
```

**关键设计**：
- `_index_cache` 机制保留——与 YAML 版行为一致
- `get_l0_generated_at()` 可以通过 `kv_store` 中的特殊键或 `_l0_generated_at` 实例变量实现（与 YAML 版逻辑相同）
- `build_global_summary()` 通过 SQL 聚合查询替代手动遍历——可以直接 `SELECT status, COUNT(*) FROM experiments GROUP BY status` 替代 YAML 版的 `for e in all_exps: statuses[s] += 1`

---

## 五、数据迁移（`migrate.py`）

一次性脚本，在首次切换到 SQLite 时自动执行。也可独立运行：`python lib/repositories/migrate.py`。

```python
"""YAML → SQLite 数据迁移。扫描 experiments/ 目录，全量写入 SQLite。"""
import yaml, json, sys
from pathlib import Path
from datetime import datetime

def migrate_yaml_to_sqlite(conn, experiments_dir: Path):
    """扫描 experiments/ 下所有 YAML 文件，写入 SQLite。幂等——重复执行不产生重复数据。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 实验 ----
    names = set()
    exp_dir = experiments_dir
    for yaml_file in exp_dir.glob("EXP-*.yaml"):
        with open(yaml_file, encoding="utf-8") as f:
            exp = yaml.safe_load(f)
        exp_id = exp.get("id", "")
        if not exp_id or exp_id in names:
            continue
        names.add(exp_id)
        conn.execute("""INSERT OR REPLACE INTO experiments
            (id,title,date,experimenter,status,tags,purpose,
             materials,equipment,experimental_plan,sop,
             process_parameters,observations,characterization,
             results,conclusion,next_steps,references,
             referenced_by,analyzed_in,original_notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exp_id, exp.get("title",""), exp.get("date",""),
             exp.get("experimenter",""), exp.get("status","planned"),
             json.dumps(exp.get("tags",[]), ensure_ascii=False),
             exp.get("purpose",""),
             json.dumps(exp.get("materials",[]), ensure_ascii=False),
             json.dumps(exp.get("equipment",[]), ensure_ascii=False),
             json.dumps(exp.get("experimental_plan",[]), ensure_ascii=False),
             json.dumps(exp.get("sop",[]), ensure_ascii=False),
             json.dumps(exp.get("process_parameters",[]), ensure_ascii=False),
             json.dumps(exp.get("observations",
                        {"no_anomalies":True,"items":[]}), ensure_ascii=False),
             json.dumps(exp.get("characterization",[]), ensure_ascii=False),
             json.dumps(exp.get("results",
                        {"qualitative":"","key_data":[],"figures":[]}), ensure_ascii=False),
             exp.get("conclusion",""),
             json.dumps(exp.get("next_steps",[]), ensure_ascii=False),
             json.dumps(exp.get("references",[]), ensure_ascii=False),
             json.dumps(exp.get("referenced_by",[]), ensure_ascii=False),
             json.dumps(exp.get("analyzed_in",[]), ensure_ascii=False),
             exp.get("original_notes",""),
             exp.get("created_at", now),
             exp.get("updated_at", now)))
    print(f"  实验: {len(names)} 条")

    # ---- 收藏夹 ----
    fav_file = experiments_dir / "_favorites.yaml"
    if fav_file.exists():
        with open(fav_file, encoding="utf-8") as f:
            fav = yaml.safe_load(f) or {}
        for pos, exp_id in enumerate(fav.get("pinned", []), 1):
            conn.execute("INSERT OR IGNORE INTO pinned (exp_id, position) VALUES (?,?)",
                        (exp_id, pos))
        for col_name, ids in fav.get("collections", {}).items():
            for exp_id in ids:
                conn.execute("INSERT OR IGNORE INTO collection_items (collection_name,exp_id) VALUES (?,?)",
                            (col_name, exp_id))
        print(f"  收藏夹: {len(fav.get('pinned',[]))} 置顶, "
              f"{sum(len(v) for v in fav.get('collections',{}).values())} 收藏")

    # ---- 分析报告 ----
    analysis_dir = experiments_dir / "_analysis_history"
    if analysis_dir.exists():
        count = 0
        for yaml_file in analysis_dir.glob("ANAL-*.yaml"):
            with open(yaml_file, encoding="utf-8") as f:
                a = yaml.safe_load(f) or {}
            conn.execute("""INSERT OR REPLACE INTO analyses
                (id,timestamp,question,selected_ids,analysis,created_at)
                VALUES (?,?,?,?,?,?)""",
                (a.get("id",""), a.get("timestamp",""), a.get("question",""),
                 json.dumps(a.get("selected_ids",[]), ensure_ascii=False),
                 a.get("analysis",""), now))
            count += 1
        print(f"  分析报告: {count} 条")

    # ---- 对话线程 ----
    threads_dir = experiments_dir / "_threads"
    if threads_dir.exists():
        count = 0
        for yaml_file in threads_dir.glob("THR-*.yaml"):
            with open(yaml_file, encoding="utf-8") as f:
                t = yaml.safe_load(f) or {}
            conn.execute("""INSERT OR REPLACE INTO threads
                (id,type,status,title,summary,exp_generated,anal_generated,
                 selected_exps,experiment_type,messages,branches,created,updated)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (t.get("id",""), t.get("type",""), t.get("status","active"),
                 t.get("title",""), t.get("summary",""),
                 t.get("exp_generated",""), t.get("anal_generated",""),
                 json.dumps(t.get("selected_exps",[]), ensure_ascii=False),
                 t.get("experiment_type","other"),
                 json.dumps(t.get("messages",[]), ensure_ascii=False),
                 json.dumps(t.get("branches",[]), ensure_ascii=False),
                 t.get("created",""), t.get("updated","")))
            count += 1
        print(f"  对话线程: {count} 条")

    # ---- kv_store（索引 + 状态文件）----
    kv_files = {
        "index": threads_dir / "index.yaml",
        "current_state": threads_dir / "_current_state.yaml",
        "global_context": threads_dir / "_global_context.yaml",
    }
    for key, fp in kv_files.items():
        if fp.exists():
            with open(fp, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            conn.execute("INSERT OR REPLACE INTO kv_store (key,value,updated_at) VALUES (?,?,?)",
                        (key, json.dumps(data, ensure_ascii=False), now))
    # 子 Agent 状态（*_child_state.yaml）
    if threads_dir.exists():
        for fp in threads_dir.glob("*_child_state.yaml"):
            thread_id = fp.stem.replace("_child_state", "")
            with open(fp, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            conn.execute("INSERT OR REPLACE INTO kv_store (key,value,updated_at) VALUES (?,?,?)",
                        (f"child_state:{thread_id}",
                         json.dumps(data, ensure_ascii=False), now))
    print(f"  KV 存储: 已迁移")

    # ---- 更新日志 ----
    update_dir = experiments_dir / "_update_logs"
    if update_dir.exists():
        count = 0
        for yaml_file in update_dir.glob("EXP-*.yaml"):
            with open(yaml_file, encoding="utf-8") as f:
                log = yaml.safe_load(f) or {}
            exp_id = log.get("experiment_id", yaml_file.stem)
            for entry in log.get("entries", []):
                conn.execute(
                    "INSERT OR IGNORE INTO update_logs "
                    "(exp_id,entry_id,timestamp,source,thread_id,context,changes) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (exp_id, entry.get("id",""),
                     entry.get("timestamp",""), entry.get("source",""),
                     entry.get("thread_id",""),
                     json.dumps(entry.get("context",{}), ensure_ascii=False),
                     json.dumps(entry.get("changes",[]), ensure_ascii=False)))
                count += 1
        print(f"  更新日志: {count} 条")

    conn.commit()
    print("迁移完成。")


if __name__ == "__main__":
    # 独立运行：python lib/repositories/migrate.py
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from lib.repositories.sqlite_connection import get_connection

    base = Path(__file__).parent.parent.parent / "experiments"
    conn = get_connection(str(base / "exdiary.db"))
    migrate_yaml_to_sqlite(conn, base)
    conn.close()
```

**迁移保证**：
- 使用 `INSERT OR REPLACE`/`INSERT OR IGNORE`——幂等，重复执行安全
- 迁移完成后 `conn.commit()` 一次性提交
- 错误不静默——异常直接抛出，调用方决定是否回滚

---

## 六、app.py 改造

```python
# config.yaml 新增
STORAGE: yaml    # yaml | sqlite

# app.py create_app() 中：

def create_app():
    ...
    storage_type = config.get("STORAGE", "yaml")

    if storage_type == "sqlite":
        from lib.repositories.sqlite_connection import get_connection, need_migration
        from lib.repositories.sqlite_experiment import SqliteExperimentRepository
        from lib.repositories.sqlite_analysis import SqliteAnalysisRepository
        from lib.repositories.sqlite_thread import SqliteThreadRepository
        from lib.repositories.sqlite_favorites import SqliteFavoritesRepository
        from lib.repositories.sqlite_update_log import SqliteUpdateLogRepository

        db_path = str(BASE_DIR / "experiments" / "exdiary.db")
        conn = get_connection(db_path)

        if need_migration(conn):
            from lib.repositories.migrate import migrate_yaml_to_sqlite
            migrate_yaml_to_sqlite(conn, BASE_DIR / "experiments")

        exp_repo = SqliteExperimentRepository(conn)
        analysis_repo = SqliteAnalysisRepository(conn)
        thread_repo = SqliteThreadRepository(conn)
        favorites_repo = SqliteFavoritesRepository(conn)
        update_log_repo = SqliteUpdateLogRepository(conn)

    else:  # yaml（现有逻辑不变）
        exp_repo = ExperimentStore(str(BASE_DIR / "experiments"))
        analysis_repo = AnalysisStore(str(BASE_DIR / "experiments/_analysis_history"))
        thread_repo = ThreadStore(str(BASE_DIR / "experiments/_threads"))
        favorites_repo = FavoritesStore(str(BASE_DIR / "experiments/_favorites.yaml"))
        update_log_repo = UpdateLogStore(str(BASE_DIR / "experiments/_update_logs"))

    # 之后的服务层代码完全不变——它们只依赖 Repository 接口
    experiment_svc = ExperimentService(exp_repo, update_log_repo, favorites_repo, BASE_DIR)
    # ...
```

**改动量**：app.py 增加约 20 行条件分支，服务层和路由层零改动。

---

## 七、实施阶段

| 阶段 | 内容 | 文件 | 工作量 | 风险 |
|------|------|------|--------|------|
| **1** | 连接管理 + DDL | `sqlite_connection.py` | 30 min | 低 |
| **2** | 实验仓库 | `sqlite_experiment.py` | 1 h | 中——方法最多 |
| **3** | 分析 + 收藏 + 日志仓库 | `sqlite_analysis.py`、`sqlite_favorites.py`、`sqlite_update_log.py` | 40 min | 低 |
| **4** | 线程仓库 | `sqlite_thread.py` | 1.5 h | 中——方法多、状态复杂 |
| **5** | 迁移脚本 | `migrate.py` | 40 min | 中——需逐类型测试 |
| **6** | app.py 条件注入 | `app.py` | 20 min | 低 |
| **7** | 全量回归测试 | — | 1 h | — |

**总计约 5-6 小时。**

---

## 八、验证清单

### 迁移验收
- [ ] `config.yaml` 中 `STORAGE: yaml` 时全功能不受影响
- [ ] `STORAGE: sqlite` 首次启动自动迁移所有 YAML 数据
- [ ] 迁移后 `SELECT COUNT(*) FROM experiments` = YAML 文件数
- [ ] 迁移后 YAML 文件未被删除或修改
- [ ] 重复启动不产生重复数据（幂等性）

### 数据一致性
- [ ] `load()` 返回的 dict 格式与 YAML 版完全一致（所有 JSON 字段正确反序列化）
- [ ] `list_all()` 返回量与 YAML 版一致
- [ ] `list_all_full()` 返回量与 YAML 版一致
- [ ] `summarize_all()` 输出与 YAML 版一致

### CRUD 操作
- [ ] 新建实验 `save()` → SQLite 写入 → `load()` 可读回
- [ ] `update()` 实验 → 字段正确更新 → `_row_to_dict()` 还原正确
- [ ] `delete()` 实验 → 行删除 + 外键级联删除 pinned/collection_items

### 收藏与置顶
- [ ] 置顶/取消置顶操作在 `pinned` 表中正确反映
- [ ] 收藏/取消收藏在 `collection_items` 表中正确反映
- [ ] `get_collections()` 返回结构与 YAML 版一致
- [ ] 删除已收藏的实验 → 外键级联清理 `collection_items`

### 线程与 Agent
- [ ] 线程 `create()` / `save()` / `load()` 完整闭环
- [ ] `get_index()` / `update_index()` 行为与 YAML 版一致
- [ ] Agent 对话：`save_current_state()` → `load_current_state()` 恢复正确
- [ ] 子 Agent 状态：`save_child_state()` → `load_child_state()` 恢复正确
- [ ] L0 摘要 `build_global_summary()` 输出与 YAML 版一致

### HTTP 路由
- [ ] 42 条路由全部 200（`python -c "from app import create_app; ..."`）
- [ ] `/api/agent/start` 正常启动 Agent
- [ ] `/api/agent/message` 正常对话
- [ ] `/api/exp/<id>/chat` 子 Agent 正常

### 回滚验证
- [ ] `STORAGE: yaml` 切回后全功能正常
- [ ] 删除 `exdiary.db` 后再次启动自动重建空库（不影响 YAML 数据）

---

## 九、不做的事

- **不删除 YAML 实现**：保留 `lib/repositories/yaml_*.py` + `lib/storage.py` 兼容层
- **不删除 YAML 文件**：迁移后原文件不删，双重保险
- **不引入 ORM**：`sqlite3` 标准库足够，不引入 SQLAlchemy
- **不拆子表**：嵌套结构用 JSON 列存储，避免复杂 JOIN
- **不加缓存层**：SQLite 在单用户本地场景下足够快
- **不在本次实施中完成**：查询优化（全文索引 FTS5）、增量迁移、SQLite 备份导出
