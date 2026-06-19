# lib/repositories/yaml_update_log.py — 说明文档

## 文件作用摘要

实验更新日志的 YAML 文件系统存储。每个实验对应一个独立的更新日志文件 `experiments/_update_logs/{EXP-ID}.yaml`，记录该实验每次字段修改的 old→new diff。实现 `AbstractUpdateLogRepository` 接口。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 类

#### `YamlUpdateLogRepository` (AbstractUpdateLogRepository)
- **构造参数**: `path: str` — 日志目录路径（`experiments/_update_logs/`），自动创建
- **实例属性**: `self.path: Path`

##### 内部方法

- `_filepath(exp_id: str) -> Path`: 返回 `{exp_id}.yaml` 完整路径
- `_load(exp_id: str) -> dict`: 加载实验的更新日志 YAML 文件，不存在返回 `{}`
- `_save(exp_id: str, data: dict) -> None`: 将更新日志数据写回文件（覆盖写入）
- `_next_entry_id(exp_id: str) -> str`: 生成 `UPD-NNN-XXX` 格式的条目 ID。NNN 来自实验编号后三位（`EXP-YYYY-NNN`），XXX 递增

##### 公开方法

- `append(exp_id: str, source: str, changes: list[dict], context: dict | None = None, thread_id: str | None = None) -> str`:
  - **作用**: 追加一条更新日志条目
  - **参数**:
    - `exp_id` — 被修改的实验 ID
    - `source` — 修改来源: `"parent_agent"` / `"child_agent"` / `"manual_edit"` / `"system"`
    - `changes` — 差异列表，每项含 `{path, field, old, new}`（由 `compute_experiment_diff()` 生成）
    - `context` — 附加上下文 dict，如 `{"summary": "修改了N个字段"}`
    - `thread_id` — 关联的线程 ID（可选，Agent 操作时传入）
  - **输出**: 新条目的 `entry_id` 字符串
  - **被调用**:
    - `ExperimentService.save_with_log()` line 46: `self.update_log_repo.append(exp_id, source, diff, context=..., thread_id=...)`
    - `ExperimentService.delete_with_log()` line 53: `self.update_log_repo.append(exp_id=exp_id, source="system", changes=[...], context=...)`
    - `ToolExecutor._modify_experiment()` line 571: `self.update_log_store.append(exp_id=ref, source="parent_agent", changes=entries, thread_id=..., context=...)`

- `list_recent(exp_id: str, limit: int = 5) -> list[dict]`:
  - **作用**: 返回最近 N 条更新条目（按时间倒序，最新在前）
  - **被调用**:
    - `ToolExecutor._read_update_log()` line 532: `self.update_log_store.list_recent(exp_id, limit=limit)` — Agent 读取更新日志工具
    - `ToolExecutor._summarize_exp()` line 770: `self.update_log_store.list_recent(exp.get("id", ""), limit=3)` — 加载引用实验时附加最近更新摘要
    - `GlobalContextStore.build_global_summary()` line 351: `update_log_repo.list_recent(exp_id, limit=1)` — L0 摘要中显示近期修改

- `list_all(exp_id: str) -> list[dict]`:
  - **作用**: 返回全部更新条目（按时间倒序）
  - **被调用**: `self.get_entry()` 内部调用；无直接外部调用

- `get_entry(exp_id: str, entry_id: str) -> dict | None`:
  - **作用**: 获取单条更新条目（遍历全部条目按 entry_id 匹配）
  - **被调用**: 无直接外部调用（预留接口）
