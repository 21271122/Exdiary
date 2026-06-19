# lib/repositories/yaml_thread.py — 说明文档

## 文件作用摘要

线程持久化存储的 YAML 文件系统实现。经过 Phase 4 重构后采用 **Facade 模式**：1 个 Facade 类 `ThreadRepository` 委托给 5 个专职子类。路径必须指向 `experiments/_threads/` 目录。实现 `AbstractThreadRepository` 接口。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 子类 1: `ThreadCrud`
- **作用**: 线程文件 (`THR-YYYY-NNN.yaml`) 的 CRUD 操作
- **构造参数**: `path: str`
- **方法**:
  - `_thread_path(thread_id: str) -> Path`: 返回 `{thread_id}.yaml` 路径（内部方法）
  - `next_id() -> str`: 扫描目录 → 生成 `THR-YYYY-NNN` 增量 ID
  - `create(thread_type, messages, index_mgr) -> dict`: **创建新线程**。先写文件 (save) → 再通过 index_mgr 更新索引。非原子操作（若第二步失败，残留文件不导致数据损坏但需手动清理）
  - `save(thread_data: dict) -> None`: 覆盖写入线程 YAML 文件
  - `load(thread_id: str) -> dict | None`: 加载线程文件
  - `list_recent(n)`: 抛 `NotImplementedError` — 此方法依赖 index，应在 Facade 层调用

### 子类 2: `ThreadIndexManager`
- **作用**: 管理 `_threads/index.yaml` 的读写与内存缓存，以及反向映射
- **构造参数**: `path: str`
- **实例属性**: `self._cache: dict | None`（内存缓存，加载后生效）
- **方法**:
  - `_load() -> dict`: 读 index.yaml（优先缓存），初始化默认字段 (active_thread/threads/exp_to_thread/anal_to_thread/user_profile)
  - `_save() -> None`: 将缓存写回 index.yaml
  - `get_index() -> dict`: 返回 index 的 dict 副本
  - `update_index(thread_data)`: 更新线程索引条目 + `exp_to_thread`/`anal_to_thread` 反向映射
  - `_append_thread_entry(...)`: 追加新线程条目（内部方法，由 `ThreadCrud.create()` 调用）
  - `list_recent(n=5) -> list[dict]`: 返回最近 N 条索引条目
  - `get_active_id() -> str | None`: 返回当前活跃线程 ID
  - `set_active_id(thread_id)`: 设置活跃线程 ID
  - `get_user_profile() -> dict`: 获取用户画像 dict 副本
  - `get_raw_cache() -> dict`: 返回可修改的缓存引用（供 `UserProfileStore` 使用）

### 子类 3: `ThreadStateStore`
- **作用**: 管理活跃线程标记 + `_current_state.yaml` + `*_child_state.yaml`
- **构造参数**: `path: str`
- **方法**:
  - `get_active_thread(thread_crud, index_mgr) -> dict | None`: 读活跃线程 ID → 通过 ThreadCrud 加载完整数据
  - `set_active_thread(thread_id, thread_crud, index_mgr)`: 切换活跃线程。标记旧线程为 done + 写文件 + 更新 index
  - `save_current_state(agent_state) -> None`: 父 Agent 状态 → `_current_state.yaml`
  - `load_current_state() -> dict | None`: 加载 `_current_state.yaml`
  - `save_child_state(thread_id, agent_state) -> None`: 子 Agent 状态 → `{thread_id}_child_state.yaml`
  - `load_child_state(thread_id) -> dict | None`: 加载子 Agent 状态
  - `delete_child_state(thread_id) -> None`: 删除子 Agent 状态文件

### 子类 4: `GlobalContextStore`
- **作用**: L0 全局摘要（Python 确定性生成，不调 LLM）+ `_global_context.yaml` 压缩历史
- **构造参数**: `path: str`
- **方法**:
  - `build_global_summary(exp_repo, update_log_repo, recent_threads, user_profile) -> str`: 生成 L0 摘要。包含实验库统计（总数+各状态计数）、最近完成线程(最多3条)、常用标签(top6)、近期被修改的实验(最多3条)
  - `get_l0_generated_at() -> datetime | None`: 返回 L0 生成时间
  - `get_global_context() -> str`: 读取压缩后的对话历史
  - `update_global_context(compressed_text, ...) -> None`: 写入压缩上下文

### 子类 5: `UserProfileStore`
- **作用**: 用户画像与标签频率统计。通过 `ThreadIndexManager` 读写 index.yaml
- **方法**:
  - `get_user_profile(index_mgr) -> dict`: 获取画像 dict 副本
  - `update_user_profile(exp_data, index_mgr) -> None`: 统计实验者频次 + 更新默认实验者。需外部调 `index_mgr._save()` 持久化
  - `recalc_tag_counts(exp_repo, index_mgr) -> None`: 扫描全量实验 → 重新计算标签频次 top10

### Facade: `ThreadRepository` (AbstractThreadRepository)
- **构造**: `__init__(path)` → 创建 5 个子类实例: `self.crud`, `self.index`, `self.state`, `self.context`, `self.profile`
- **21 个公开方法** — 全部转发给对应子类，接口与旧的 `YamlThreadRepository` 完全一致:

| 类别 | 方法 | 转发到 | 被调用情况 |
|------|------|-------|-----------|
| 线程 CRUD | `next_id()` | self.crud | `AgentLoop._maybe_inject_thread_start()`, `routes/api_child.py:_migrate_legacy_analysis()` |
| | `create()` | self.crud | `ToolExecutor._start_record_thread()`, `_start_analyze_thread()`, `AgentLoop._maybe_inject_thread_start()` |
| | `save()` | self.crud | `AgentLoop._extract_and_save_thread()`, `routes/api_child.py:_migrate_legacy_analysis()` |
| | `load()` | self.crud | `AgentLoop.from_dict()`, `routes/api_child.py:api_analysis_chat()`, `api_exp_chat()`, `create_child_agent()` |
| 索引 | `get_index()` | self.index | `routes/api_child.py:api_analysis_chat()`, `api_exp_chat()` (查找反向映射) |
| | `update_index()` | self.index | `AgentLoop._extract_and_save_thread()`, `_migrate_legacy_analysis()` |
| 活跃线程 | `get_active_thread()` | self.state | **无外部调用** — ABC 预留接口，所有活跃线程检查均通过 `thread_store.load()` 直接读取 |
| | `set_active_thread()` | self.state | `ToolExecutor._start_record_thread()`, `_start_analyze_thread()`, `_maybe_inject_thread_start()`, `_maybe_inject_thread_end()`, `_update_schema()`（仅调用 `set_active_thread(None)` 结束 analyze 线程，不启动 record 线程） |
| | `list_recent()` | self.index | `GlobalContextStore.build_global_summary()` 通过 Facade 间接使用 |
| L0 摘要 | `build_global_summary()` | self.context | `AgentLoop.__init__()` (line 1010), `AgentLoop._refresh_l0()` (line 1434) |
| | `get_l0_generated_at()` | self.context | `AgentLoop.__init__()` (line 1012) |
| 压缩历史 | `get_global_context()` | self.context | `AgentLoop.run()` (line 1106), `AgentLoop._maybe_summarize()` (line 1826) |
| | `update_global_context()` | self.context | `AgentLoop._maybe_summarize()` (line 1827) |
| Agent 状态 | `save_current_state()` | self.state | `AgentLoop._save_runtime_state()` (line 1744) |
| | `load_current_state()` | self.state | `lib/agent_factory.py:get_or_create_agent()` (line 31) |
| 子 Agent | `save_child_state()` | self.state | `AgentLoop._save_runtime_state()` (line 1741), `routes/api_child.py` 多处 |
| | `load_child_state()` | self.state | `routes/api_child.py:api_analysis_chat()`, `api_exp_chat()` |
| | `delete_child_state()` | self.state | 当前无直接外部调用 |
| 用户画像 | `get_user_profile()` | self.profile | `GlobalContextStore.build_global_summary()` 通过 Facade 间接使用 |
| | `update_user_profile()` | self.profile | `AgentLoop._extract_and_save_thread()` (line 1579) |
| | `recalc_tag_counts()` | self.profile | `AgentLoop._extract_and_save_thread()` (line 1580) |
