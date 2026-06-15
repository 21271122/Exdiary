# Exdiary Agent 系统架构手册

> 基于 `lib/agent_v2.py` (1816 行) 的完整函数/类/方法文档。

---

## 1. 数据类

### ChildContext (line 26)

子 Agent 标记。仅子 Agent 实例时有效。

```
__slots__:
  is_child           bool   — 是否为子 Agent（默认为 False）
  is_legacy          bool   — 是否为无线程关联的旧实验子 Agent（默认为 False）
  exp_id             str    — 子 Agent 关联的实验 ID 或分析报告 ID
  initial_history_len int   — 创建时 history 长度，前端只渲染此索引之后的消息
  agent_role         str    — 子 Agent 角色：'exp_editor' | 'analysis_reviewer' | None
```

生命期：创建于 `AgentLoop.__init__`，由 `create_child_agent` / `create_legacy_child_agent` 填充。

---

### ThreadState (line 37)

线程状态。一个 Agent 实例同时只持有一个活跃线程。

```
__slots__:
  id                   str    — 线程 ID（如 'THR-001'），None 表示无活跃线程
  type                 str    — 线程类型: 'record' | 'analyze' | None
  pending_start        str    — 待启动的线程类型（延迟注入用，analyze 工具触发时设置）
  current_turn_user_idx int   — 当前 turn 用户消息在 history 中的索引（用于线程标记插入定位）
  last_ended_id        str    — 最近结束的线程 ID（压缩时跳过此线程）
```

生命期：创建于 `AgentLoop.__init__`，由线程机制各方法读写。

---

## 2. 模块级辅助函数

### `merge_context(context, fields)`

| 项目 | 内容 |
|---|---|
| **签名** | `merge_context(context: dict, fields: dict) -> dict` |
| **用途** | 增量合并字段到 Schema 上下文。简单字段覆盖；数组追加去重；嵌套对象递归合并。 |
| **调用者** | `ToolExecutor._update_schema` |
| **依赖** | 纯 Python dict 操作，无外部依赖 |

合并规则：
- `list` + `list`: 数组追加，已有 str 元素跳过重复；空 list 清空
- `dict` + `dict`: 递归合并；空 dict 清空
- 其他类型: 直接覆盖

---

### `_is_filled(val)`

| 项目 | 内容 |
|---|---|
| **签名** | `_is_filled(val) -> bool` |
| **用途** | 检查单个字段是否"有值"（非空） |
| **调用者** | `AgentLoop._build_schema_status`, `AgentLoop._core_fields_filled`, `AgentLoop.from_dict` |
| **依赖** | 无 |

规则：
- `None` → `False`
- `list` → `len > 0`
- `dict` → `any(v for v in val.values() if v)`
- `str` → `val.strip() != ""`
- 其他 → `bool(val)`

---

### `_brief(val)`

| 项目 | 内容 |
|---|---|
| **签名** | `_brief(val) -> str` |
| **用途** | 字段值的简短描述，用于 Schema 状态摘要 |
| **调用者** | `AgentLoop._build_schema_status` |
| **依赖** | 无 |

规则：
- `list` → `"{n}项"` 或 `"空"`
- `dict` → `"{n}子字段"` 或 `"空"`
- `str` → 截取前 15 字符 + `"..."`（若超长）
- 其他 → `"有"` 或 `"空"`

---

### `_fallback_preview(loop)`

| 项目 | 内容 |
|---|---|
| **签名** | `_fallback_preview(loop: "AgentLoop") -> dict` |
| **用途** | 确定性回退：当 `generate_record` 中 `parse_notes` 调用 LLM 失败时，从 context 直接构造预览数据 |
| **调用者** | `ToolExecutor._generate_record`（except 分支） |
| **依赖** | `loop._schema_context`, `loop.store.next_id()`, `loop.references` |

返回完整 16 字段实验 dict，含 `original_notes: ""` 和 `references`。

---

### `_tool_log_summary(name, args, result)`

| 项目 | 内容 |
|---|---|
| **签名** | `_tool_log_summary(name: str, args: dict, result: dict) -> dict` |
| **用途** | 从工具名称、参数和结果中提取关键信息用于统一日志记录 |
| **调用者** | `AgentLoop.run()` 内循环 |
| **依赖** | 无 |

按工具名称提取不同字段：
- `load_reference` → refs, loaded_count
- `search_experiments` → query, hits
- `update_schema` → fields
- `ask_user` → questions
- `generate_record` → preview_id
- `modify_experiment` → refs, fields
- `manage_collection` → action, refs
- `query_experiment` → question, refs
- `analyze` → query
- `list_experiments` → 所有非空 args
- `read_update_log` → exp_id

在任何情况下，若 `result` 含 `error` 则提取错误消息。

---

## 3. ToolExecutor 类 (line 174-947)

### 整体结构

```python
class ToolExecutor:
    def __init__(self, store, update_log_store=None, favorites_store=None, analysis_store=None)
```

持有 4 个 Store 引用 + 一个 `self.registry` dict（16 个工具名 → handler 方法）。

初始化时注册所有 16 个工具。

---

### 工具注册表 (self.registry)

| 工具名 | Handler | 行号 |
|---|---|---|
| `load_reference` | `_load_reference` | 686 |
| `search_experiments` | `_search_experiments` | 770 |
| `start_record_thread` | `_start_record_thread` | 234 |
| `update_schema` | `_update_schema` | 899 |
| `ask_user` | `_ask_user` | 465 |
| `generate_record` | `_generate_record` | 470 |
| `read_update_log` | `_read_update_log` | 511 |
| `modify_experiment` | `_modify_experiment` | 521 |
| `manage_collection` | `_manage_collection` | 577 |
| `query_experiment` | `_query_experiment` | 600 |
| `list_experiments` | `_list_experiments` | 652 |
| `end_thread` | `_end_thread` | 265 |
| `start_analyze_thread` | `_start_analyze_thread` | 277 |
| `select_experiments` | `_select_experiments` | 304 |
| `generate_analysis` | `_generate_analysis` | 316 |
| `modify_analysis` | `_modify_analysis` | 373 |

---

### `execute(name, args, loop)` — 参数校验入口 (line 203)

| 项目 | 内容 |
|---|---|
| **签名** | `execute(name: str, args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 校验参数 → 执行工具。错误以 dict 形式返回，不抛异常 |
| **参数** | `name` — 工具名; `args` — 参数 dict; `loop` — 当前 AgentLoop 实例 |
| **返回** | dict（成功含工具特定字段，失败含 `error` + `message`） |
| **访问 loop** | 无直接访问，透传给 handler |
| **调用者** | `AgentLoop.run()` 内循环 |

校验流：
1. 检查 `name in self.registry` → 未知工具报错
2. 通过 `_tool_schema` 获取参数 schema → 检查 required 字段
3. 类型修正：`expected=array` 但传入非 list → 包裹为 list；`expected=string` 但传入 int/float → 转 str
4. 调用 `self.registry[name](args, loop)` → 异常捕获返回 `{"error": "execution_failed", "message": ...}`

---

### `_tool_schema(name)` (line 225)

| 项目 | 内容 |
|---|---|
| **签名** | `_tool_schema(name: str) -> dict` |
| **用途** | 从 `TOOLS_OPENAI_FORMAT` 列表中查找指定工具的 parameters schema |
| **返回** | dict（OpenAI 工具格式的 parameters 部分）或空 dict |
| **依赖** | `lib.core.agent_tools.TOOLS_OPENAI_FORMAT` |

---

### `_start_record_thread(args, loop)` (line 234)

| 项目 | 内容 |
|---|---|
| **签名** | `_start_record_thread(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | LLM 判断要开始记录时调用，在当前 user 消息之后插入线程开始标记 |
| **参数** | 无特殊参数（args 未使用） |
| **返回** | `{"status": "started", "thread_id": ...}` 或错误/已存在状态 |
| **访问 loop** | `loop.thread_store`, `loop.thread.id/type`, `loop.history`, `loop.thread.current_turn_user_idx`, `loop.references`（被 `_enter_record_mode` 重置引用） |

流程：
1. 检查 `loop.thread_store` → 未配置则报错
2. 若已有线程：analyze 类型则自动结束它；record 类型则返回 `already_started`
3. 新线程：`next_id()` → 设置 `thread.id` / `thread.type` → `set_active_thread` → `_enter_record_mode`
4. 在 `current_turn_user_idx + 1` 处插入 `thread_begin` 和 guidance 消息
5. 写入 `thread_store.create("record", ...)`

---

### `_end_thread(args, loop)` (line 265)

| 项目 | 内容 |
|---|---|
| **签名** | `_end_thread(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 结束当前线程（record 或 analyze），归档并回到自由模式 |
| **参数** | 无特殊参数 |
| **返回** | `{"status": "ended", "thread_id": ...}` 或 `{"status": "no_active_thread"}` |
| **访问 loop** | `loop.thread.id`, `_maybe_inject_thread_end` |

直接委托 `_maybe_inject_thread_end("")`（空 product_id）。

---

### `_start_analyze_thread(args, loop)` (line 277)

| 项目 | 内容 |
|---|---|
| **签名** | `_start_analyze_thread(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 开启跨实验分析线程。与 `start_record_thread` 对称 |
| **参数** | 无特殊参数 |
| **返回** | `{"status": "started", "thread_id": ...}` 或错误 |
| **访问 loop** | `loop.thread_store`, `loop.thread.id/type`, `loop.history`, `loop.thread.current_turn_user_idx`, `_build_thread_guidance` |

与 `_start_record_thread` 不同：不调用 `_enter_record_mode`，使用 `_build_thread_guidance("analyze")` 生成引导消息。

---

### `_select_experiments(args, loop)` (line 304)

| 项目 | 内容 |
|---|---|
| **签名** | `_select_experiments(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 返回选择面板数据，由前端渲染为实验勾选卡片 |
| **参数** | `title` (str), `candidates` (list), `preselected` (list) |
| **返回** | `{"display": "selector", "pause": True, "title": ..., "items": ..., "preselected": ...}` |
| **副作用** | 设置 `pause: True` → 主循环暂停，等待前端反馈 |

---

### `_generate_analysis(args, loop)` (line 316)

| 项目 | 内容 |
|---|---|
| **签名** | `_generate_analysis(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 执行分析 → 写 AnalysisStore → 自动结束线程 → 返回标题+摘要 |
| **参数** | `query` (str, required), `refs` (list, 至少2个) |
| **返回** | `{"display": "analysis_done", "anal_id": ..., "title": ..., "summary": ..., "refs": ...}` |
| **访问 loop** | `loop.analysis_svc`, `loop.store`, `loop.llm`, `loop.thread.id`, `loop.child.is_child` |
| **依赖** | `lib.analyzer.analyze_experiments`（回退路径）、`AnalysisStore` |

两条路径：
1. `analysis_svc` 存在 → 调用 `analysis_svc.run_analysis(query, refs)`
2. 不存在 → `store.summarize_all` + `analyze_experiments` 直接调用，存入 `analysis_store`

结束时调用 `_maybe_inject_thread_end(anal_id)`（非子 Agent 时）。

---

### `_modify_analysis(args, loop)` (line 373)

| 项目 | 内容 |
|---|---|
| **签名** | `_modify_analysis(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 修改分析报告。支持 changes / additional_refs / additional_query 三种模式 |
| **参数** | `changes` (str, 完整替换), `additional_refs` (list, 合并实验重新分析), `additional_query` (str, 追加分析维度) |
| **返回** | `{"status": "modified", "mode": ...}` 或错误 |
| **访问 loop** | `loop.child.exp_id`, `loop.thread_store`, `loop.thread.id`, `loop.store`, `loop.llm` |
| **依赖** | `analysis_store`, `lib.analyzer.analyze_experiments`, `loop.llm.analyze` |

三种模式互斥：
1. **replace**: 直接覆盖 `a["analysis"] = changes`
2. **expand_refs**: 合并新 refs 后重新运行分析（通过 `analysis_svc` 或本地）
3. **expand_query**: 通过独立 LLM 调用融合新旧内容

---

### `_ask_user(args, loop)` (line 465)

| 项目 | 内容 |
|---|---|
| **签名** | `_ask_user(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 占位工具，实际由前端处理显示问题并等待用户回答 |
| **参数** | 无特殊参数（args 未使用于逻辑） |
| **返回** | `{"status": "asked", "pause": True}` |
| **副作用** | 设置 `pause: True` → 主循环暂停 |

---

### `_generate_record(args, loop)` (line 470)

| 项目 | 内容 |
|---|---|
| **签名** | `_generate_record(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 从 Schema context 生成实验记录预览。调用 `parse_notes` LLM 解析，回退到 `_fallback_preview` |
| **参数** | 无特殊参数 |
| **返回** | `{"status": "generated", "pause": True, "response_type": "generate", "include_state": True, "id": ..., "title": ..., "fields_count": ...}` |
| **访问 loop** | `loop.child.is_child`, `loop.child.exp_id`, `loop._schema_context`, `loop.store.next_id()`, `loop.references` |
| **依赖** | `lib.parser.parse_notes`（主路径）, `_fallback_preview`（回退路径） |

子 Agent 不允许使用此工具 → 返回 `use_modify_experiment` 错误。

---

### `_read_update_log(args, loop)` (line 511)

| 项目 | 内容 |
|---|---|
| **签名** | `_read_update_log(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 读取指定实验的修改历史日志 |
| **参数** | `exp_id` (str, required), `limit` (int, 默认 5) |
| **返回** | `{"status": "ok", "exp_id": ..., "entries": [...]}` |
| **访问 loop** | `loop.update_log_store` |

---

### `_modify_experiment(args, loop)` (line 521)

| 项目 | 内容 |
|---|---|
| **签名** | `_modify_experiment(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 直接修改已保存的实验记录字段，自动写更新日志并注入过期标记到 history |
| **参数** | `refs` (list, required), `changes` (dict, required) |
| **返回** | `{"modified": {ref: {"status": "modified", "display": "diff", "changes": [...]}}}` |
| **访问 loop** | `loop.store.load/save`, `loop.history.append`, `loop.thread.id` |
| **依赖** | `lib.services.experiment.compute_experiment_diff`, `update_log_store` |

特殊字段处理：
- `materials`, `equipment`, `experimental_plan`, `process_parameters`, `characterization` → 完整替换
- `results`, `observations` (dict) → `setdefault().update()` 合并
- `tags` → list 替换
- `sop`, `next_steps` → list 替换
- 其他 → 直接赋值

---

### `_manage_collection(args, loop)` (line 577)

| 项目 | 内容 |
|---|---|
| **签名** | `_manage_collection(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 管理实验收藏/置顶操作 |
| **参数** | `action` (str: 'pin'/'unpin'/'favorite'/'unfavorite'), `refs` (list), `collection` (str, 默认"默认收藏夹") |
| **返回** | `{"status": "ok", "display": "toast", "message": ..., "results": {...}}` |
| **访问 loop** | `loop.favorites_store` |

---

### `_query_experiment(args, loop)` (line 600)

| 项目 | 内容 |
|---|---|
| **签名** | `_query_experiment(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 快速查询实验关键信息（标题、状态、目的、结论），检查 memory 或磁盘 |
| **参数** | `question` (str, required), `refs` (list) |
| **返回** | `{"status": "ok", "display": "answer", "question": ..., "answer": ..., "exp_ids": ..., "source": ...}` |
| **访问 loop** | `loop.history`（检查是否已加载）, `loop.store.load` |

---

### `_list_experiments(args, loop)` (line 652)

| 项目 | 内容 |
|---|---|
| **签名** | `_list_experiments(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 列出实验，支持按 status/tags/experimenter/since 过滤 |
| **参数** | `status` (str), `tags` (list), `experimenter` (str), `since` (str, ISO date) |
| **返回** | `{"display": "list", "experiments": [...], "count": ...}`（最多 20 条） |
| **访问 loop** | `loop.store.list_all_full()` |

---

### `_load_reference(args, loop)` (line 686)

| 项目 | 内容 |
|---|---|
| **签名** | `_load_reference(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 加载引用实验数据到对话上下文。仅处理明确的 EXP ID 格式 |
| **参数** | `refs` (list of str) |
| **返回** | `{"loaded": {exp_id: summarized_dict, ...}}` |
| **访问 loop** | `loop.references.append`, `loop.store.load`, `loop.experiment_type` |

- 严格校验 ID 格式 `EXP-\d{4}-\d{3}`（可选前缀 `@`）
- 已加载的实验返回 `already_loaded`（避免重复加载）
- 首次加载时通过 tags 推断 `experiment_type`
- 依赖 `_summarize_exp` 提取摘要

---

### `_summarize_exp(exp)` — _load_reference 的 helper (line 728)

| 项目 | 内容 |
|---|---|
| **签名** | `_summarize_exp(exp: dict) -> dict` |
| **用途** | 提取实验的关键信息摘要。返回完整字段数据，不截断数组 |
| **依赖** | `update_log_store.list_recent`（追加最近更新日志摘要） |
| **调用者** | 仅 `_load_reference` |

---

### `_search_experiments(args, loop)` (line 770)

| 项目 | 内容 |
|---|---|
| **签名** | `_search_experiments(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 双层搜索：关键词粗筛 → LLM 语义搜索 |
| **参数** | `query` (str) |
| **返回** | `{"candidates": [{id, title, date, tags, score}, ...]}`（最多 5 条） |
| **访问 loop** | `loop.store.list_all_full()`, `loop.llm` |

流程：
1. `_fuzzy_search` 关键词粗筛
2. 若纯 ID/编号查询 → 直接返回关键词结果
3. 若关键词结果为空或最高分 < 0.3 → 调用 `_llm_semantic_search`

---

### `_llm_semantic_search(query, loop)` (line 793)

| 项目 | 内容 |
|---|---|
| **签名** | `_llm_semantic_search(query: str, loop: "AgentLoop") -> list[dict]` |
| **用途** | LLM 语义搜索：独立 API 调用，不污染 Agent 上下文。处理自然语言查询 |
| **参数** | `query` — 用户自然语言描述 |
| **返回** | `[{id, score, reason}, ...]`（JSON 解析后返回） |
| **依赖** | `loop.llm.analyze()` — 独立 API 调用 |

构造所有实验极简摘要（1-2 行/个）→ 调用 LLM 返回 JSON 数组。容错解析含正则回退。

---

### `_fuzzy_search(query, loop)` (line 849)

| 项目 | 内容 |
|---|---|
| **签名** | `_fuzzy_search(query: str, loop: "AgentLoop") -> list[dict]` |
| **用途** | 本地关键词搜索（含实验 ID），支持中文字符二元切分 |
| **参数** | `query` — 搜索字符串 |
| **返回** | `[{id, title, date, tags, score}, ...]`（最多 5 条，按 score 降序） |
| **访问 loop** | `loop.store.list_all_full()` |

得分规则：
- 每个 2+ 字符 token 匹配 `searchable` 文本 → +0.25
- 标签精确匹配 → +0.3
- 最低 0.2 分才返回
- 最高 0.99（软上限）

---

### `_update_schema(args, loop)` — 隐藏的 analyze→record 过渡 (line 899)

| 项目 | 内容 |
|---|---|
| **签名** | `_update_schema(args: dict, loop: "AgentLoop") -> dict` |
| **用途** | 纯写入：合并 fields → 生成 Schema 状态 → 注入 messages |
| **参数** | `fields` (dict, 需合并的字段) |
| **返回** | `{"status": "ok", "updated_fields": [...]}` |
| **访问 loop** | `loop._schema_context`, `loop.modified_values`, `loop.experiment_type`, `loop.thread.id/type`, `loop.history`, `loop.thread_store` |

关键特性：
1. **modified_values 追踪**：首次触及的字段记录旧值（deepcopy）
2. **experiment_type 推断**：从 tags 中识别实验类型
3. **analyze→record 过渡检测**：若当前在 analyze 线程中调用此工具 → 自动结束 analyze 线程（隐藏的行为！）
4. 合并后调用 `_build_schema_status` 注入系统消息

---

## 4. AgentLoop 类 (line 955-1816)

### 构造方法 (line 958)

```python
def __init__(self, llm_client, experiment_store,
             debug_dir: str | Path | None = None,
             thread_store=None, update_log_store=None,
             favorites_store=None, analysis_store=None,
             analysis_svc=None, extraction_svc=None)
```

初始化：
- 核心字段: `llm`, `store`, `history` (空), `references` (空), `turn_count` (0), `experiment_type` ("other")
- 模式: `_schema_context` (None), `_generated_preview` (None), `_generated_notes` (None)
- 工具: `self.tools = ToolExecutor(...)`
- 线程: `self.thread = ThreadState()`, `self.child = ChildContext()`
- 持久化: `_last_summarized_idx` (0), `_l0_generated_at` (None), `modified_values` ({})
- 服务: `analysis_svc`, `extraction_svc`
- 副作用: 若 `thread_store` 存在，注入 L0 全局摘要到 `history[0]`；设置 `debug_dir`

---

### 模式管理

#### `mode` (property, line 1007)

| 签名 | `mode(self) -> str` |
|---|---|
| **用途** | 返回当前对话模式 |
| **返回** | `'general'` | `'record'` | `'analyze'` |
| **逻辑** | `thread.id` 为 None → `general`；否则返回 `thread.type` |

#### `_enter_record_mode()` (line 1014)

| 签名 | `_enter_record_mode(self) -> None` |
|---|---|
| **用途** | 初始化 Schema 上下文（仅 record 模式） |
| **副作用** | `self._schema_context = deepcopy(DEFAULT_CONTEXT)` |

#### `_exit_record_mode()` (line 1018)

| 签名 | `_exit_record_mode(self) -> None` |
|---|---|
| **用途** | 清理 Schema 上下文 |
| **副作用** | `self._schema_context = None` |

#### `_get_active_tools()` (line 1022)

| 签名 | `_get_active_tools(self) -> list[dict]` |
|---|---|
| **用途** | 返回当前模式或子 Agent 角色可用的工具列表（OpenAI 格式） |
| **返回** | `TOOLS_OPENAI_FORMAT` 的子集 |
| **依赖** | `lib.core.agent_tools.TOOLS_*` 常量 |

过滤规则：
1. **子 Agent 角色优先**：
   - `analysis_reviewer`: `load_reference`, `search_experiments`, `query_experiment`, `list_experiments`, `read_update_log`, `modify_analysis`, `end_thread`
   - `exp_editor`: `load_reference`, `search_experiments`, `query_experiment`, `list_experiments`, `read_update_log`, `modify_experiment`, `end_thread`
2. **父 Agent — 按 mode**：
   - 通用工具（所有 mode）: `load_reference`, `search_experiments`, `query_experiment`, `list_experiments`, `manage_collection`, `read_update_log`, `end_thread`
   - `record` 模式追加: `start_record_thread`, `update_schema`, `ask_user`, `generate_record`, `modify_experiment`
   - `general` 模式追加: `start_record_thread`, `start_analyze_thread`, `modify_experiment`
   - `analyze` 模式追加: `start_analyze_thread`, `select_experiments`, `ask_user`, `generate_analysis`（不含 `modify_experiment`）

---

### 主循环: `run(user_message)` (line 1058)

**完整签名**: `def run(self, user_message: str = "") -> dict`

**返回 dict 格式**（根据出口不同）:

| type | 含有的键 |
|---|---|
| `"reply"` | `type`, `message`, `context` |
| `"generate"` | `type`, `message`, `state`, `preview`, `notes`, `context` |
| 其他暂停返回 | `type`, `message`, `context` |

#### 执行流程（文本流程图）

```
run(user_message)
│
├─ 1. 若有 user_message:
│     ├─ 设置 thread.current_turn_user_idx = len(history)
│     ├─ history.append({role:"user", content:message})
│     ├─ turn_count++
│     └─ 日志记录
│
├─ 2. 进入 while True 循环:
│     │
│     ├─ 2a. _maybe_inject_thread_start()  // 检查并注入延迟线程
│     │
│     ├─ 2b. 构建 LLM 消息:
│     │     ├─ system prompt (build_system_prompt())
│     │     ├─ 历史摘要 (若已压缩) + history[split:]
│     │     ├─ Schema 状态 (仅 record 模式)
│     │     └─ 线程状态 (_build_thread_status())
│     │
│     ├─ 2c. 调用 LLM: llm.chat(messages, tools, temperature=0.3)
│     │
│     ├─ 2d. 记录日志: _log_llm_request → _log_llm_response
│     │
│     ├─ 2e. 分支判断:
│     │     │
│     │     ├─ [纯文本 → 无 tool_calls]:
│     │     │     ├─ history.append({role:"assistant", content})
│     │     │     ├─ _maybe_inject_thread_start()
│     │     │     ├─ _check_thread_cancellation()
│     │     │     ├─ _save_runtime_state()
│     │     │     └─ return {"type":"reply", "message":..., "context":...}
│     │     │
│     │     └─ [有 tool_calls]:
│     │           │
│     │           ├─ 记录 assistant 文本（含 tool_calls）
│     │           │
│     │           ├─ 遍历每个 tool_call:
│     │           │     ├─ 解析 args (json.loads)
│     │           │     ├─ result = tools.execute(name, args, self)
│     │           │     ├─ 日志: _log_tool_call → _log_tool_result
│     │           │     ├─ 追加 {role:"assistant", tool_calls:[...]} 到 history
│     │           │     ├─ 追加 {role:"tool", content:json(result)} 到 history
│     │           │     │
│     │           │     ├─ 错误计数: 连续 3 次同一工具报错 → 回复错误信息并返回
│     │           │     │
│     │           │     └─ 暂停检查: result.get("pause"):
│     │           │           ├─ _maybe_inject_thread_start()
│     │           │           ├─ update_schema/analyze → 有进展; 否则 _no_progress++
│     │           │           ├─ _check_thread_cancellation()
│     │           │           │
│     │           │           ├─ [generate_record]:
│     │           │           │     ├─ _maybe_inject_thread_end(exp_id)
│     │           │           │     ├─ _save_runtime_state()
│     │           │           │     └─ return {"type":"generate", preview, state, ...}
│     │           │           │
│     │           │           └─ [其他 pause 工具]:
│     │           │                 ├─ _save_runtime_state()
│     │           │                 └─ return {"type":"reply", message, context}
│     │           │
│     │           └─ 循环继续（无 pause → while True）
```

#### 元数据驱动的暂停机制

暂停由工具返回的 `"pause": True` 控制。触发暂停的工具：

| 工具 | 暂停原因 |
|---|---|
| `generate_record` | 等待前端确认预览数据 |
| `ask_user` | 等待用户回答问题 |
| `select_experiments` | 等待用户在面板中勾选实验 |

暂停时保存运行时状态到 `thread_store`。

---

### 状态管理

#### `_build_schema_status()` (line 1246)

| 签名 | `_build_schema_status(self) -> str` |
|---|---|
| **用途** | 生成 Schema 状态摘要文本，注入到 LLM messages（不入 history） |
| **返回** | 多行字符串：`[Schema状态] 已填充 N/16 字段` + 已填列表 + 缺失列表 + 完成度提示 |
| **依赖** | `_is_filled`, `_brief` |
| **字段顺序** | title, date, experimenter, status, tags, purpose, materials, equipment, experimental_plan, sop, process_parameters, observations, characterization, results, conclusion, next_steps |

填充率 >= 70% 时提示"可考虑结束收集"。

#### `_build_notes_from_context()` (line 1279)

| 签名 | `_build_notes_from_context(self) -> str` |
|---|---|
| **用途** | 从 context 生成自然语言实验描述（纯 Python 模板，不调 LLM） |
| **依赖** | `self._schema_context` |
| **返回** | 多行自然语言文本，覆盖标题、日期、实验者、目的、材料、设备、步骤、参数、表征、结果、观察、结论、下一步 |

#### `_core_fields_filled()` (line 1353)

| 签名 | `_core_fields_filled(self) -> bool` |
|---|---|
| **用途** | 检查核心字段是否已填充（按实验类型定义不同的核心字段集） |
| **依赖** | `_is_filled`, `self.experiment_type`, `self._schema_context` |

各类型核心字段：

| 类型 | 核心字段 |
|---|---|
| photocatalysis | purpose, materials, process_parameters, results |
| hydrothermal | purpose, materials, sop, process_parameters, results |
| sol-gel | purpose, materials, sop, process_parameters, results |
| spin-coating | purpose, materials, sop, process_parameters, results |
| ball-milling | purpose, materials, sop, process_parameters, results |
| electrochemistry | purpose, materials, process_parameters, results |
| xrd | purpose, materials, process_parameters, results |
| perovskite-solar | purpose, materials, sop, process_parameters, results |
| 其他 (默认) | purpose, materials, sop, results |

---

### 线程系统

#### `_l0_stale()` (line 1371)

| 签名 | `_l0_stale(self) -> bool` |
|---|---|
| **用途** | L0 摘要是否过期（距上次生成超过 1 小时） |

#### `_refresh_l0()` (line 1382)

| 签名 | `_refresh_l0(self) -> None` |
|---|---|
| **用途** | 重新生成 L0 摘要并替换 `history[0]`（如果 `history[0]` 是 L0） |
| **依赖** | `thread_store.build_global_summary` |

#### `_build_thread_guidance(thread_type)` (line 1394)

| 签名 | `_build_thread_guidance(self, thread_type: str) -> dict` |
|---|---|
| **用途** | 生成线程模式引导消息（system role） |
| **返回** | `{"role": "system", "content": "..."}` |
| **类型** | `record` → 引导 LLM 收集实验字段；`analyze` → 引导 LLM 进行跨实验分析 |

#### `_build_thread_status()` (line 1404)

| 签名 | `_build_thread_status(self) -> str` |
|---|---|
| **用途** | 生成当前线程状态声明。每轮 LLM 请求注入，不入 history |
| **依赖** | `self.child.agent_role`, `self.thread.id`, `self.thread.type` |

子 Agent 角色覆盖返回特定约束文本。

#### `_maybe_inject_thread_start()` (line 1443)

| 签名 | `_maybe_inject_thread_start(self) -> None` |
|---|---|
| **用途** | 检查 `pending_start` 标记，若有则注入线程开始标记 |
| **触发条件** | `analyze` 工具执行时设置 `pending_start = "analyze"`（record 线程由 `start_record_thread` 工具直接处理，不走此路径） |
| **副作用** | 生成线程 ID、设置 `thread.id/type`、插入 begin 和 guidance 消息、写入 thread_store |

#### `_maybe_inject_thread_end(produced_id)` (line 1466)

| 签名 | `_maybe_inject_thread_end(self, produced_id: str) -> None` |
|---|---|
| **用途** | 注入线程结束标记 + 提取 messages → 写线程文件 + 更新索引 + 重置上下文 |
| **副作用** | 重置 `thread.id/type` → None、`_exit_record_mode()`、清空 `references`、`experiment_type = "other"`、`modified_values = {}` |

#### `_extract_and_save_thread(produced_id)` (line 1490)

| 签名 | `_extract_and_save_thread(self, produced_id: str) -> None` |
|---|---|
| **用途** | 提取 begin-end 标记间的 messages → 写入线程文件 + 更新索引 |
| **依赖** | `thread_store.load/save/update_index`, `store.load` |
| **副作用** | 更新用户画像 (`update_user_profile`)、重新计算标签计数、刷新 L0 |

#### `_check_thread_cancellation(consecutive_no_progress)` (line 1535)

| 签名 | `_check_thread_cancellation(self, consecutive_no_progress: int) -> None` |
|---|---|
| **用途** | 检测线程是否需要自动取消（连续 3 轮无进展） |
| **副作用** | 注入 `thread_cancelled` 标记、移除 begin 标记、重置 `thread.id/type`、`_exit_record_mode` |

"无进展"定义：此轮所有工具都不是 `update_schema` 或 `analyze`。

---

### 子 Agent

#### `create_child_agent(parent_loop, thread_id)` (classmethod, line 1566)

| 签名 | `create_child_agent(cls, parent_loop: "AgentLoop", thread_id: str) -> "AgentLoop"` |
|---|---|
| **用途** | 从父 Agent 创建子 Agent，用于续接历史线程（修改已完成的实验） |
| **参数** | `parent_loop` — 父 AgentLoop 实例；`thread_id` — 已有线程 ID |
| **返回** | 新的 AgentLoop 实例（child 标记） |
| **逻辑** | 加载线程消息 → 复制历史 → 设置 `child.is_child=True`, `agent_role="exp_editor"` |

#### `create_legacy_child_agent(llm_client, store, exp_data, ...)` (classmethod, line 1594)

| 签名 | `create_legacy_child_agent(cls, llm_client, store, exp_data: dict, thread_store=None, ...) -> "AgentLoop"` |
|---|---|
| **用途** | 为无线程关联的旧实验创建子 Agent |
| **参数** | `exp_data` — 实验数据 dict（注入为 system 消息） |
| **返回** | 新的 AgentLoop 实例（`child.is_child=True`, `child.is_legacy=True`, `agent_role="exp_editor"`） |

---

### 调试日志

#### `_log_llm_request(seq, messages)` (line 1617)

保存完整 messages（截断到 5000 chars/条）到 `{debug_dir}/call_{seq:03d}_request.json`。

#### `_log_llm_response(seq, response, reasoning)` (line 1642)

保存 LLM 原始响应（content 截断 3000 chars）到 `{debug_dir}/call_{seq:03d}_response.json`。

#### `_log_tool_call(seq, tool_name, raw_args)` (line 1661)

保存工具原始参数到 `{debug_dir}/call_{seq:03d}_tool_{name}_args.json`。

#### `_log_tool_result(seq, tool_name, result)` (line 1669)

保存工具返回结果（截断 10000 chars）到 `{debug_dir}/call_{seq:03d}_tool_{name}_result.json`。

所有日志方法都有 try/except，失败时仅 `print` 到 stderr，不抛异常。

---

### 持久化与上下文管理

#### `_save_runtime_state()` (line 1684)

| 签名 | `_save_runtime_state(self) -> None` |
|---|---|
| **用途** | 保存 AgentLoop 运行时状态。父 Agent 写 `_current_state.yaml`；子 Agent 写 `child_state.yaml` |
| **依赖** | `thread_store.save_current_state` / `save_child_state` |
| **副作用** | 父 Agent 写入后触发 `_maybe_summarize` |

#### `_maybe_summarize()` (line 1705)

| 签名 | `_maybe_summarize(self) -> None` |
|---|---|
| **用途** | 上次摘要后的新增消息超过 30 万 token（按字符/2 估算）时，生成新摘要 |
| **依赖** | `thread_store.get_global_context`, `thread_store.update_global_context`, `loop.llm.analyze` |

保留最近 10 万 token 完整，其余进摘要。摘要失败时降级为用户消息拼接。

---

### 状态序列化

#### `state_to_dict()` (line 1747)

```python
def state_to_dict(self) -> dict:
```

返回字典包含以下键：

| 键 | 类型 | 说明 |
|---|---|---|
| `context` | dict / None | Schema 上下文（record 模式时非 None） |
| `references` | list[str] | 已加载引用实验 ID |
| `experiment_type` | str | 当前实验类型 |
| `turn_count` | int | 对话轮次计数 |
| `llm_call_seq` | int | LLM 调用全局序号 |
| `history` | list[dict] | 完整对话历史（去除 None 值的条目） |
| `debug_dir` | str | 调试日志目录 |
| `thread_id` | str / None | 当前线程 ID |
| `_thread_type` | str / None | 当前线程类型 |
| `_pending_thread_start` | str / None | 待启动线程类型 |
| `_current_turn_user_idx` | int | 当前 turn 用户消息索引 |
| `modified_values` | dict | 字段修改追跟踪（记录旧值） |
| `_l0_generated_at` | str / None | L0 摘要生成时间（ISO 字符串） |
| `_last_summarized_idx` | int | 最后摘要的 history 索引 |
| `_is_child_agent` | bool | 子 Agent 标记 |
| `_is_legacy` | bool | 旧式子 Agent 标记 |
| `_child_exp_id` | str / None | 子 Agent 关联实验 ID |
| `_child_initial_history_len` | int | 子 Agent 初始 history 长度 |
| `_child_agent_role` | str / None | 子 Agent 角色 |

**向后兼容性保证**：
- 所有 `_` 前缀键为内部字段，读取方应容忍其缺失
- `history` 条目可能缺少 `tool_calls`、`reasoning_content`、`tool_call_id` 等字段
- `context` 空 dict 视为 None（在 `from_dict` 中转换）
- `debug_dir` 可能缺失 → `from_dict` 使用 `data.get("debug_dir") or None`

#### `from_dict(cls, ...)` (classmethod, line 1773）

```python
@classmethod
def from_dict(cls, llm_client, store, data: dict,
              thread_store=None, update_log_store=None,
              favorites_store=None, analysis_store=None,
              analysis_svc=None, extraction_svc=None) -> "AgentLoop":
```

反序列化逻辑：
1. 构造新 AgentLoop（会触发 L0 注入到 history[0]）
2. 恢复字段（所有字段使用 `.get()` 容忍缺失）：
   - `context`: 空 dict 或无效值 → `None`（按 None 处理）
   - `references`, `experiment_type`, `turn_count`, `llm_call_seq`, `history`
   - 线程状态: `thread_id`, `_thread_type`, `_pending_thread_start`, `_current_turn_user_idx`
   - 子 Agent: `_is_child_agent`, `_is_legacy`, `_child_exp_id`, `_child_initial_history_len`, `_child_agent_role`
   - `modified_values`, `_l0_generated_at`, `_last_summarized_idx`
3. **线程活跃性验证**：若 `thread.id` 存在且 `thread_store` 存在 → 加载线程文件检查 status 是否为 `"active"`，否则重置为 None
4. **L0 过期刷新**：若 L0 超过 1 小时，调用 `_refresh_l0()`

---

## 5. 调用关系图

### 外部模块导入 `agent_v2.py`

```
┌─────────────────────────────────────────────────────────────────┐
│                      routes/api_agent.py                        │
│  ┌─ AgentLoop.from_dict()         ── 恢复 Agent 状态            │
│  └─ AgentLoop(...) + .run()       ── 创建新 Agent + 处理消息    │
└───────────────────────┬─────────────────────────────────────────┘
                        │
┌─────────────────────────────────────────────────────────────────┐
│                      routes/api_child.py                        │
│  ┌─ AgentLoop.from_dict()         ── 恢复子 Agent 状态          │
│  ├─ AgentLoop(...)                ── 创建分析子 Agent           │
│  ├─ AgentLoop.create_child_agent() ── 创建实验编辑子 Agent      │
│  ├─ AgentLoop.create_legacy_child_agent() ── 旧实验子 Agent     │
│  └─ AgentLoop(...) + .run()       ── 处理子 Agent 消息          │
└───────────────────────┬─────────────────────────────────────────┘
                        │
┌─────────────────────────────────────────────────────────────────┐
│                     lib/services/agent.py                        │
│  AgentService 封装层:                                           │
│  ├─ create_or_resume_agent() → AgentLoop.from_dict()            │
│  ├─ create_or_resume_agent() → AgentLoop(...)                   │
│  ├─ run_message() → agent.run()                                 │
│  ├─ create_child_agent() → AgentLoop.create_child_agent()       │
│  ├─ create_legacy_child_agent() → AgentLoop.create_legacy_...() │
│  ├─ create_analysis_child_agent() → AgentLoop(...) 手动拼接     │
│  └─ save_runtime_state() → agent._save_runtime_state()          │
└───────────────────────┬─────────────────────────────────────────┘
                        │
┌─────────────────────────────────────────────────────────────────┐
│                    test_agent_v2.py                              │
│  测试文件:                                                      │
│  ├─ from lib.agent_v2 import AgentLoop, merge_context,          │
│  │                        _is_filled, TOOLS_OPENAI_FORMAT       │
│  └─ 集成测试使用                                                │
└─────────────────────────────────────────────────────────────────┘
```

### `agent_v2.py` 内部依赖

```
agent_v2.py
  ├── lib.logger.get_logger                    → 统一日志
  ├── lib.core.agent_tools.*                   → 工具定义常量（纯数据，不含执行逻辑）
  ├── lib.core.prompts.build_system_prompt     → 系统提示词
  ├── lib.core.schema.DEFAULT_CONTEXT          → 默认 Schema 上下文
  ├── lib.parser.parse_notes                   → (运行时 import) 解析笔记为实验数据
  ├── lib.analyzer.analyze_experiments          → (运行时 import) 执行跨实验分析
  └── lib.services.experiment.compute_experiment_diff → (运行时 import) 计算字段差异
```

---

## 6. 关键设计决策总结

| 决策 | 实现 |
|---|---|
| 工具执行失败处理 | 从不抛异常 → 返回 dict 含 `error` + `message` |
| 线程模型 | 一次一线程，支持 record/analyze 两种类型，通过标记注入实现 |
| 子 Agent | 独立 AgentLoop 实例，通过 `child.*` 标记角色，工具列表按角色过滤 |
| Schema 增删合并 | `merge_context` 函数，list 去重追加，dict 递归合并 |
| LLM 上下文管理 | 请求层（不入 history）+ history 持久层 + 静态 prompt |
| 暂停机制 | 工具返回 `pause: True` → 主循环 return |
| 自动取消线程 | 连续 3 轮无 `update_schema`/`analyze` 调用 → 取消 |
| 状态序列化 | 每轮结束时实时保存到 thread_store |
| 历史压缩 | 30 万 token 阈值 → LLM 摘要 + 保留最近 10 万 token 完整 |
| L0 摘要 | 启动时注入全局实验库概况，1 小时刷新一次 |
