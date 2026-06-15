# Exdiary lib/ 模块架构参考

> 涵盖 `lib/` 下除 `agent_v2.py` 外的全部模块。

---

## Package overview

```
lib/
  core/              -- Pure-data constants: schema, tool defs, prompts, exceptions
    schema.py          Experiment JSON Schema, default context, tag vocabulary
    agent_tools.py     16 tool definitions in OpenAI format
    experiment_types.py 9 experiment types with P1/P2/P3 field priorities
    prompts.py         System prompts with dynamic priority injection
    exceptions.py      5 exception classes (ExdiaryError hierarchy)
  llm.py             -- LLMClient wrapper around OpenAI SDK
  storage.py         -- Compatibility re-export layer (to be removed post-Phase-5)
  repositories/      -- Abstract interfaces + YAML-backed implementations
    base.py            5 abstract repository interfaces
    yaml_experiment.py Experiment CRUD (EXP-*.yaml)
    yaml_analysis.py   Analysis CRUD (ANAL-*.yaml)
    yaml_thread.py     Thread CRUD + L0 summary + user profile + child state
    yaml_favorites.py  Pin/favorite management (_favorites.yaml)
    yaml_update_log.py Field-level change log (per-experiment YAML)
  services/          -- Business logic layer
    experiment.py      ExperimentService + compute_experiment_diff()
    extraction.py      ExtractionService (parse_notes, strip_html)
    analysis.py        AnalysisService (run_analysis, _analyze_experiments)
    agent.py           AgentService (create, run, child agents)
    template.py        TemplateService (list_all, load + builtin templates)
  parser.py          -- Legacy standalone parse_notes / strip_html (compat)
  analyzer.py        -- Legacy standalone analyze_experiments (compat)
  logger.py          -- ExdiaryLogger: 4 JSONL log files
  debug.py           -- DebugTracer: per-session LLM call trace
```

---

## `lib/core/schema.py`

Pure-data constants, zero dependencies.

### EXPERIMENT_SCHEMA

A JSON Schema dict defining 16 fields:

| # | Field                | Type                                      | Required |
|---|----------------------|-------------------------------------------|----------|
| 1 | `id`                 | `string`                                  | Yes      |
| 2 | `title`              | `string`                                  | Yes      |
| 3 | `date`               | `string`                                  | Yes      |
| 4 | `experimenter`       | `string`                                  | Yes      |
| 5 | `status`             | `string` `enum: planned/running/done/failed/repeated` | Yes |
| 6 | `tags`               | `array[string]`                           | No       |
| 7 | `purpose`            | `string`                                  | Yes      |
| 8 | `materials`          | `array[{name, purity, vendor, amount, notes}]` | No   |
| 9 | `equipment`          | `array[{device, model, location}]`        | No       |
| 10 | `experimental_plan`  | `array[{group, condition, expected}]`     | No       |
| 11 | `sop`                | `array[string]`                           | Yes      |
| 12 | `process_parameters` | `array[{step, parameter, setpoint, actual, deviation}]` | No |
| 13 | `observations`       | `object{no_anomalies: bool, items: array[string]}` | Yes |
| 14 | `characterization`   | `array[{method, sample_id, preparation, submission_date, data_path}]` | No |
| 15 | `results`            | `object{qualitative, key_data, figures}`  | No       |
| 16 | `conclusion`         | `string`                                  | Yes      |
|    | `next_steps`         | `array[string]`                           | No       |

6 required root fields: `id`, `title`, `purpose`, `sop`, `observations`, `conclusion`.

### DEFAULT_CONTEXT

A dict with default values for all 16 fields:

- Empty strings: `title`, `date`, `experimenter`, `purpose`, `conclusion`
- Status: `"planned"`
- Empty arrays: `tags`, `materials`, `equipment`, `experimental_plan`, `sop`, `process_parameters`, `characterization`, `next_steps`
- Objects with empty defaults: `observations` (`{"no_anomalies": True, "items": []}`), `results` (`{"qualitative": "", "key_data": [], "figures": []}`)

### TAG_VOCABULARY

27 controlled-vocabulary tags:

```
synthesis, characterization, photocatalysis, electrochemistry,
sintering, ball-milling, thin-film, XRD, SEM, TEM, mechanical-testing,
thermal-analysis, DFT, sol-gel, hydrothermal, co-precipitation,
calcination, doping, coating, corrosion, battery, ceramic, polymer,
composite, nano, perovskite-solar, spin-coating
```

---

## `lib/core/agent_tools.py`

16 tool definitions in OpenAI Function Calling format. All assembled into `TOOLS_OPENAI_FORMAT` (list of 16 dicts).

| # | Constant                    | Tool Name                 | Purpose |
|---|-----------------------------|---------------------------|---------|
| 1 | `TOOL_LOAD_REFERENCE`       | `load_reference`          | Load full experiment data by EXP ID |
| 2 | `TOOL_SEARCH_EXPERIMENTS`   | `search_experiments`      | Semantic search over experiment history |
| 3 | `TOOL_UPDATE_SCHEMA`        | `update_schema`           | Write confirmed fields to the in-progress schema |
| 4 | `TOOL_ASK_USER`             | `ask_user`                | Ask the user 1-3 specific questions |
| 5 | `TOOL_GENERATE_RECORD`      | `generate_record`         | Generate structured experiment record draft |
| 6 | `TOOL_START_RECORD_THREAD`  | `start_record_thread`     | Enter record mode |
| 7 | `TOOL_END_THREAD`           | `end_thread`              | End current thread (record or analyze) |
| 8 | `TOOL_START_ANALYZE_THREAD` | `start_analyze_thread`    | Enter analyze mode |
| 9 | `TOOL_SELECT_EXPERIMENTS`   | `select_experiments`      | Show experiment selection panel |
| 10 | `TOOL_GENERATE_ANALYSIS`    | `generate_analysis`       | Execute cross-experiment analysis |
| 11 | `TOOL_MODIFY_ANALYSIS`      | `modify_analysis`        | Modify an existing analysis (3 modes) |
| 12 | `TOOL_READ_UPDATE_LOG`      | `read_update_log`         | Read update log for an experiment |
| 13 | `TOOL_MODIFY_EXPERIMENT`    | `modify_experiment`       | Modify existing experiment fields |
| 14 | `TOOL_MANAGE_COLLECTION`    | `manage_collection`       | Pin/unpin, favorite/unfavorite |
| 15 | `TOOL_QUERY_EXPERIMENT`     | `query_experiment`        | Answer parameter queries about experiments |
| 16 | `TOOL_LIST_EXPERIMENTS`     | `list_experiments`        | Filter experiment list by criteria |

**`TOOLS_OPENAI_FORMAT`**: A `list[dict]` of all 16 tools in OpenAI function-calling format. Passed directly as the `tools` argument to `LLMClient.chat()`. Imported by `agent_v2.py` to make tools available to the LLM.

---

## `lib/core/experiment_types.py`

### PRIORITY_MAP

A `dict[str, dict[str, list[str]]]` with 9 experiment types, each categorized into P1/P2/P3 field prompts.

| Type                     | P1 items                           | P2 items               | P3 items         |
|--------------------------|------------------------------------|------------------------|------------------|
| `photocatalysis`         | 催化剂名称和纯度, 目标污染物和浓度, 光源类型和功率 | 催化剂负载量, 降解时间, 表征手段 | 基板类型, 煅烧条件, 溶液pH |
| `hydrothermal`           | 前驱体名称和用量, 反应温度, 反应时间     | 溶剂类型和用量, 目标产物, 填充度 | 升温速率, pH值, 表面活性剂 |
| `sol-gel`                | 前驱体名称, 溶剂, 水解抑制剂          | 陈化温度和时间, 干燥条件, 煅烧温度 | 滴加速率, 催化剂用量, 研磨条件 |
| `spin-coating`           | 薄膜材料名称, 基底类型, 旋涂转速       | 前驱体浓度和溶剂, 退火温度和时间 | 旋涂层数, 预处理方式, 气氛 |
| `ball-milling`           | 原料名称和用量, 球料比, 球磨时间       | 转速, 球磨罐材质, 磨球尺寸 | 过程控制剂, 气氛保护, 停机间隔 |
| `electrochemistry`       | 活性材料名称, 电解液体系, 测试类型     | 电压窗口, 对电极/参比电极, 活性物负载量 | 导电剂和粘结剂配比, 测试温度, 扫速 |
| `xrd`                    | 样品名称和形态, 扫描范围, 靶材类型     | 管电压/管电流, 扫描步长, 物相检索数据库 | 仪器型号, 制样方式, 晶粒尺寸计算 |
| `perovskite-solar`       | 钙钛矿组分和配比, ETL/HTL材料, 退火温度和时间 | 旋涂参数, 反溶剂, 电极材料和厚度 | 有效面积, 测试光源条件, 器件结构 |
| `other`                  | 实验目的是什么, 使用了哪些关键材料      | 核心操作步骤, 主要参数有哪些 | 表征手段, 预期结果 |

---

## `lib/core/prompts.py`

### SYSTEM_PROMPT

**Length**: ~200 lines of Chinese instructions for the Exdiary experiment-recording assistant.

**Purpose**: Defines the agent's three working modes (free / record / analyze), tool availability per mode, the 16-field schema, priority lists per experiment type, contradiction detection rules, thread lifecycle, and behavior guidelines.

**`{priority_list}` placeholder**: A `{priority_list}` marker in the SYSTEM_PROMPT string is replaced at runtime by `build_system_prompt()` with the output of `_build_priority_prompt(PRIORITY_MAP)`. This injects the P1/P2/P3 field prompts for all 9 experiment types.

### `_build_priority_prompt(priority_map: dict) -> str`

- **Signature**: `_build_priority_prompt(priority_map: dict) -> str`
- **Purpose**: Formats `PRIORITY_MAP` into a natural-language paragraph per experiment type showing P1, P2, P3 items.

### `build_system_prompt() -> str`

- **Signature**: `build_system_prompt() -> str`
- **Purpose**: Calls `_build_priority_prompt(PRIORITY_MAP)` and substitutes the result into `SYSTEM_PROMPT.replace("{priority_list}", ...)`.
- **Callers**: `agent_v2.py` (on agent initialization).

### ANALYSIS_SYSTEM_PROMPT

- **Purpose**: System prompt for the analysis sub-agent. Instructs the LLM to act as a materials science research advisor. Mandates three output sections: `事实呈现` (factual presentation), `发现提示` (findings with confidence tags), `值得思考的问题` (3-5 thought-provoking questions). Response must be in Chinese with Markdown.

---

## `lib/core/exceptions.py`

5 exception classes in a single hierarchy:

```
Exception
  └── ExdiaryError
        ├── ExtractionError     — parse/extraction failures
        ├── StorageError        — repository/storage failures
        ├── AgentError          — agent loop / tool execution failures
        └── ConfigurationError  — invalid configuration
```

All inherit from `ExdiaryError` so callers can catch a single base exception.

---

## `lib/llm.py`

### LLMResponse dataclass

| Field          | Type                | Default | Purpose |
|----------------|---------------------|---------|---------|
| `content`      | `str`               | —       | Text response from the model |
| `reasoning`    | `str`               | `""`    | Reasoning/thinking content (if supported) |
| `tool_calls`   | `list[dict] \| None` | `None` | Parsed tool calls `[{id, type, function: {name, arguments}}]` |
| `usage`        | `dict \| None`      | `None`  | Token usage `{prompt_tokens, completion_tokens}` |

### LLMClient class

#### `__init__(api_key: str, model: str, base_url: str)`

Creates an `openai.OpenAI` client. Default model is `"deepseek-v4-pro"`, default base URL is `"https://api.deepseek.com"`.

#### `chat(messages, tools, temperature, reasoning_effort) -> LLMResponse`

**Unified entry point** for all LLM calls. Constructs kwargs from parameters, calls `self.client.chat.completions.create()`, and wraps the response in an `LLMResponse`. Handles tool call extraction, reasoning content extraction, and usage stats.

#### `structured_extract(prompt, system_prompt, output_schema) -> dict`

Delegates to `chat()` with a single-function tool call. The function's `parameters` is `output_schema`. Parses the first tool call's JSON arguments and returns the dict. Raises `RuntimeError` if the model didn't call the function.

#### `analyze(system_prompt, user_prompt, temperature) -> str`

Delegates to `chat()` with no tools (pure text generation). Returns `resp.content`.

---

## `lib/storage.py`

### Compatibility re-export layer

Re-exports YAML repository implementations under legacy `*Store` names:

| New name                          | Legacy alias |
|-----------------------------------|--------------|
| `YamlExperimentRepository`        | `ExperimentStore` |
| `YamlAnalysisRepository`          | `AnalysisStore` |
| `YamlThreadRepository`            | `ThreadStore` |
| `YamlFavoritesRepository`         | `FavoritesStore` |
| `YamlUpdateLogRepository`         | `UpdateLogStore` |

**Why it exists**: During the Phase-5 refactoring, repositories were renamed from `*Store` to `*Repository`. This file provides backward compatibility so that existing imports from `lib.storage` continue working. It will be removed after Phase 5 completes.

---

## `lib/repositories/base.py`

5 abstract base classes using Python `ABC`.

### `AbstractExperimentRepository`

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `next_id` | `() -> str` | Generate next experiment ID |
| `save` | `(experiment: dict) -> str` | Persist a new experiment |
| `load` | `(exp_id: str) -> dict \| None` | Load experiment by ID |
| `update` | `(exp_id: str, experiment: dict) -> bool` | Overwrite existing experiment |
| `delete` | `(exp_id: str) -> bool` | Delete experiment |
| `list_all` | `() -> list[dict]` | List summaries (id, title, date, experimenter, status, tags) |
| `list_all_full` | `() -> list[dict]` | List all experiments with full data |
| `summarize_all` | `(exp_ids: list[str] \| None) -> str` | Rich Markdown summary for analysis |
| `count` | `() -> int` | Total experiment count |

### `AbstractAnalysisRepository`

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `next_id` | `() -> str` | Generate next analysis ID |
| `save` | `(analysis: dict) -> str` | Persist analysis record |
| `load` | `(aid: str) -> dict \| None` | Load analysis by ID |
| `list_all` | `() -> list[dict]` | List all analyses |
| `delete` | `(aid: str) -> bool` | Delete analysis |

### `AbstractThreadRepository`

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `next_id` | `() -> str` | Generate next thread ID |
| `create` | `(thread_type: str, messages: list[dict]) -> dict` | Create new thread file |
| `save` | `(thread_data: dict) -> None` | Persist thread data |
| `load` | `(thread_id: str) -> dict \| None` | Load thread by ID |
| `get_index` | `() -> dict` | Load the index.yaml contents |
| `update_index` | `(thread_data: dict) -> None` | Update index + reverse mappings |
| `get_active_thread` | `() -> dict \| None` | Get currently active thread |
| `set_active_thread` | `(thread_id: str \| None) -> None` | Set active thread (auto-closes previous) |
| `list_recent` | `(n: int = 5) -> list[dict]` | List recent threads from index |
| `build_global_summary` | `(exp_repo, update_log_repo) -> str` | Generate L0 summary |
| `get_global_context` | `() -> str` | Load compressed history text |
| `update_global_context` | `(compressed_text, uncompressed_thread_ids, recently_modified_exps) -> None` | Save compressed history |
| `save_current_state` | `(agent_state: dict) -> None` | Persist agent runtime state |
| `load_current_state` | `() -> dict \| None` | Restore agent runtime state |
| `save_child_state` | `(thread_id: str, agent_state: dict) -> None` | Persist child agent state |
| `load_child_state` | `(thread_id: str) -> dict \| None` | Restore child agent state |
| `delete_child_state` | `(thread_id: str) -> None` | Remove child agent state file |
| `get_user_profile` | `() -> dict` | Get experimenter/tag profile |
| `update_user_profile` | `(exp_data: dict) -> None` | Update profile from completed record |
| `recalc_tag_counts` | `(exp_repo) -> None` | Full recalculation of tag frequencies |
| `get_l0_generated_at` | `() -> datetime \| None` | Timestamp of last L0 build |

### `AbstractFavoritesRepository`

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `is_pinned` | `(exp_id: str) -> bool` | Check if experiment is pinned |
| `toggle_pin` | `(exp_id: str) -> dict` | Toggle pin status (max 3) |
| `toggle_favorite` | `(exp_id: str, collection: str) -> dict` | Toggle favorite status |
| `get_pinned` | `() -> list[str]` | List pinned experiment IDs |
| `get_collections` | `() -> dict` | Get all collections |
| `create_collection` | `(name: str) -> dict` | Create a named collection |
| `delete_collection` | `(name: str) -> dict` | Delete a collection |

### `AbstractUpdateLogRepository`

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `append` | `(exp_id, source, changes, context, thread_id) -> str` | Append an update entry |
| `list_recent` | `(exp_id: str, limit: int) -> list[dict]` | Recent N entries |
| `list_all` | `(exp_id: str) -> list[dict]` | All entries for an experiment |
| `get_entry` | `(exp_id: str, entry_id: str) -> dict \| None` | Single entry by ID |

---

## `lib/repositories/yaml_experiment.py` (YamlExperimentRepository)

Implements `AbstractExperimentRepository`. All experiments stored as individual YAML files.

**File path pattern**: `<path>/EXP-YYYY-NNN.yaml`

| Method | Purpose |
|--------|---------|
| `__init__(path: str)` | Initialize repo at directory; create if not exists |
| `next_id() -> str` | Scan EXP-\*.yaml, return max+1 as EXP-YYYY-NNN |
| `save(experiment: dict) -> str` | Write YAML file, return exp_id |
| `load(exp_id: str) -> dict \| None` | Read YAML file or return None |
| `list_all() -> list[dict]` | Glob EXP-\*.yaml sorted desc, return summary dicts |
| `update(exp_id: str, experiment: dict) -> bool` | Overwrite YAML file |
| `delete(exp_id: str) -> bool` | Delete YAML file |
| `list_all_full() -> list[dict]` | Like list_all but returns full experiment dicts |
| `count() -> int` | Count EXP-\*.yaml files |
| `summarize_all(exp_ids: list[str] \| None) -> str` | Rich Markdown: id, title, date, status, tags, purpose, conclusion, key results, observations |

---

## `lib/repositories/yaml_analysis.py` (YamlAnalysisRepository)

Implements `AbstractAnalysisRepository`.

**File path pattern**: `<path>/ANAL-YYYY-NNN.yaml`

| Method | Purpose |
|--------|---------|
| `__init__(path: str)` | Initialize at directory |
| `next_id() -> str` | Scan ANAL-\*.yaml, return max+1 as ANAL-YYYY-NNN |
| `save(analysis: dict) -> str` | Write YAML, return anal_id |
| `load(aid: str) -> dict \| None` | Load by anal_id |
| `list_all() -> list[dict]` | Glob ANAL-\*.yaml sorted desc |
| `delete(aid: str) -> bool` | Delete YAML file |

---

## `lib/repositories/yaml_thread.py` (YamlThreadRepository)

Implements `AbstractThreadRepository`. Manages thread persistence, L0 summary, user profile, child agent state, and global context.

**File path pattern**: `<path>/THR-YYYY-NNN.yaml` for individual threads.

### index.yaml structure

A central index file at `<path>/index.yaml`:

```yaml
active_thread: THR-2026-001          # currently active thread (or null)
threads:
  - id, type, status, title, summary, exp_generated, created, updated
exp_to_thread:
  EXP-2026-001: THR-2026-001        # reverse mapping
anal_to_thread:
  ANAL-2026-001: THR-2026-002       # reverse mapping
user_profile:
  experimenter_counts: { "张三": 5 }
  default_experimenter: "张三"
  tag_counts: { "synthesis": 3, "XRD": 2 }
  frequent_tags: [ "synthesis", "XRD" ]
  last_updated: "2026-06-15"
```

### L0 mechanism

`build_global_summary()` generates a concise plain-text summary ("L0 context") from:
1. Experiment library statistics (total count + status breakdown)
2. Recent completed threads (up to 3)
3. User profile: frequent tags with counts
4. Recently modified experiments (from update log)

`get_l0_generated_at()` returns the `datetime` of the last L0 build (or `None`).

### 22 methods

| Method | Purpose |
|--------|---------|
| `__init__(path: str)` | Initialize repo; set up index cache |
| `_index_path()` | Return `<path>/index.yaml` |
| `_thread_path(thread_id)` | Return `<path>/<thread_id>.yaml` |
| `_global_context_path()` | Return `<path>/_global_context.yaml` |
| `_current_state_path()` | Return `<path>/_current_state.yaml` |
| `_child_state_path(thread_id)` | Return `<path>/<thread_id>_child_state.yaml` |
| `next_id()` | Generate THR-YYYY-NNN |
| `_load_index()` | Lazy-load index.yaml into `_index_cache` |
| `_save_index()` | Write `_index_cache` to index.yaml |
| `get_index()` | Return copy of index dict |
| `update_index(thread_data)` | Upsert thread summary + reverse mappings |
| `get_active_thread()` | Load the active thread from disk |
| `set_active_thread(thread_id\|None)` | Set active (auto-close previous) |
| `list_recent(n=5)` | Return N most recent thread summaries |
| `create(thread_type, messages)` | Create thread file + update index |
| `save(thread_data)` | Write thread YAML |
| `load(thread_id)` | Read thread YAML |
| `build_global_summary(exp_repo, update_log_repo)` | Generate L0 summary |
| `get_l0_generated_at()` | Return L0 timestamp |
| `get_global_context()` | Read `_global_context.yaml` compressed text |
| `update_global_context(compressed, ...)` | Write `_global_context.yaml` |
| `save_current_state(agent_state)` | Write `_current_state.yaml` |
| `load_current_state()` | Read `_current_state.yaml` |
| `save_child_state(thread_id, agent_state)` | Write `<thread_id>_child_state.yaml` |
| `load_child_state(thread_id)` | Read child state |
| `delete_child_state(thread_id)` | Remove child state file |
| `get_user_profile()` | Return `user_profile` from index |
| `update_user_profile(exp_data)` | Update experimenter counts in profile |
| `recalc_tag_counts(exp_repo)` | Full tag frequency recalculation |

---

## `lib/repositories/yaml_favorites.py` (YamlFavoritesRepository)

Implements `AbstractFavoritesRepository`.

**`_favorites.yaml` structure**:

```yaml
pinned:
  - EXP-2026-001
  - EXP-2026-003
collections:
  默认收藏夹:
    - EXP-2026-001
    - EXP-2026-002
  重要实验:
    - EXP-2026-005
```

### 12 methods

| Method | Purpose |
|--------|---------|
| `__init__(path: str)` | Initialize with path to `_favorites.yaml` |
| `_load()` | Lazy-load the YAML file into `_data` cache |
| `_save()` | Write `_data` cache to disk |
| `is_pinned(exp_id) -> bool` | Check if experiment is pinned |
| `is_favorited(exp_id, collection) -> bool` | Check favorite status in a collection |
| `toggle_pin(exp_id) -> dict` | Toggle pin (max 3 limit enforced) |
| `toggle_favorite(exp_id, collection) -> dict` | Toggle favorite in a collection |
| `get_pinned() -> list[str]` | List all pinned EXP IDs |
| `get_collections() -> dict` | Get all collections with their EXP lists |
| `create_collection(name) -> dict` | Create new collection |
| `delete_collection(name) -> dict` | Delete collection (except "默认收藏夹") |
| `add_to_collection(exp_id, collection) -> dict` | Add experiment to a collection |
| `remove_from_collection(exp_id, collection) -> dict` | Remove experiment from a collection |

---

## `lib/repositories/yaml_update_log.py` (YamlUpdateLogRepository)

Implements `AbstractUpdateLogRepository`.

**File path pattern**: `<path>/EXP-YYYY-NNN.yaml` (same base path, per-experiment YAML)

### Entry ID format

`UPD-NNN-XXX` where:
- `NNN` = the 3-digit number from the experiment ID (e.g., `001` from `EXP-2026-001`)
- `XXX` = sequential 3-digit counter within that experiment's log

Example: `UPD-001-000`, `UPD-001-001`, ...

### 8 methods

| Method | Purpose |
|--------|---------|
| `__init__(path: str)` | Initialize at directory |
| `_filepath(exp_id)` | Return `<path>/<exp_id>.yaml` |
| `_load(exp_id)` | Load YAML or return empty dict |
| `_save(exp_id, data)` | Write YAML |
| `_next_entry_id(exp_id)` | Generate UPD-NNN-XXX |
| `append(exp_id, source, changes, context, thread_id) -> str` | Append log entry; each change: `{path, field, old, new}` |
| `list_recent(exp_id, limit) -> list[dict]` | Recent entries (newest first) |
| `list_all(exp_id) -> list[dict]` | All entries (newest first) |
| `get_entry(exp_id, entry_id) -> dict \| None` | Single entry lookup |

---

## `lib/services/experiment.py`

### ExperimentService

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `__init__` | `(exp_repo, update_log_repo, favorites_repo, base_dir: Path \| None)` | Initialize with repository dependencies |
| `save_with_log` | `(exp_id, data, source, thread_id=None)` | Save experiment + auto-compute diff + write update log |
| `delete_with_log` | `(exp_id)` | Delete experiment + write system log |
| `extract_references` | `(text: str) -> list[str]` | Regex-extract `@EXP-YYYY-NNN` references (deterministic, no LLM) |
| `update_referenced_by` | `(exp_id, refs, old_refs=None)` | Maintain bidirectional reference relationships |
| `save_and_update_refs` | `(exp_id, data, source, old_refs=None, thread_id=None)` | Combined: save + extract refs + update referrers |
| `move_draft_images` | `(exp_id: str)` | Migrate images from `uploads/_draft/` to `uploads/<exp_id>/` |
| `get_pinned_and_others` | `() -> tuple` | Get pinned + non-pinned experiment lists |

### `compute_experiment_diff(old: dict | None, new: dict) -> list[dict]`

Standalone function. Compares two experiment dicts and returns `[{path, field, old, new}]`.

**4 field categories compared:**

| Category | Fields | Diff Method |
|----------|--------|-------------|
| `simple_fields` | `title, date, experimenter, status, purpose, conclusion, original_notes` | Direct string comparison (coerced to empty string) |
| `array_fields` | `tags, sop, next_steps` | Direct list equality |
| `complex_fields` | `materials, equipment, experimental_plan, process_parameters, characterization` | JSON-serialized comparison (sorted keys); summary: "N entries" vs "N entries" |
| `nested_fields` | `observations, results` | Dict equality; summary: "filled" vs "empty" |

---

## `lib/services/extraction.py`

### ExtractionService

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `__init__` | `(extract_llm: LLMClient)` | Store the LLM client |
| `parse_notes` | `(notes: str) -> dict` | Natural language extraction via `structured_extract()`; defaults date to today; preserves `original_notes` |
| `strip_html` | `(html_text: str) -> str` (static) | Strip HTML tags, convert structural tags to newlines |

The extraction system prompt (`_EXTRACTION_SYSTEM_PROMPT`, module-private) is a 13-rule prompt for the LLM covering data fabrication prohibition, tag vocabulary, material extraction, SOP reconstruction, observations, and more.

---

## `lib/services/analysis.py`

### AnalysisService

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `__init__` | `(exp_repo, analysis_repo, analyze_llm)` | Initialize with repositories and LLM |
| `run_analysis` | `(query: str, refs: list[str]) -> dict` | Full pipeline: summarize experiments -> analyze -> save to repo -> update experiment's `analyzed_in` -> return `{anal_id, title, refs, analysis}` |
| `_analyze_experiments` | `(summary_text: str, question: str) -> str` | Private: calls `analyze_llm.analyze()` with `ANALYSIS_SYSTEM_PROMPT` and a formatted user prompt |

---

## `lib/services/agent.py`

### AgentService

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `__init__` | `(llm_client, exp_repo, thread_repo, update_log_repo, favorites_repo, analysis_repo, extraction_svc, experiment_svc, analysis_svc)` | Inject all dependencies |
| `create_or_resume_agent` | `(state_dict=None) -> AgentLoop` | Create new agent or restore from state_dict / disk (`_current_state.yaml`) |
| `run_message` | `(agent: AgentLoop, message: str) -> dict` | Process user message through agent loop |
| `create_child_agent` | `(parent: AgentLoop, thread_id: str, role: str) -> AgentLoop` | Create child/editing agent with role `"exp_editor"` or `"analysis_reviewer"` |
| `create_legacy_child_agent` | `(exp_data: dict) -> AgentLoop` | Create child agent for a threadless old experiment |
| `create_analysis_child_agent` | `(llm_client, store, thread, anal_id) -> AgentLoop` | Create analysis-review child from an existing thread file |
| `save_runtime_state` | `(agent: AgentLoop)` | Persist agent runtime state to `_current_state.yaml` |

---

## `lib/services/template.py`

### TemplateService

| 方法 | 签名 | 用途 |
|--------|-----------|---------|
| `__init__` | `(templates_dir: str)` | Initialize at directory; auto-seed builtin templates if empty |
| `list_all` | `() -> list[dict]` | List all `.yaml` templates with `{id, title, category, description, tags}` |
| `load` | `(template_id: str) -> dict \| None` | Load full template content from YAML |
| `_seed_builtin` | (private) | Write 7 built-in templates if no `.yaml` files exist |

**Built-in templates**: photocatalysis (光催化降解), hydrothermal (水热/溶剂热), sol-gel (溶胶-凝胶), spin-coating (旋涂), ball-milling (球磨), electrochemistry (电化学), xrd (XRD物相), perovskite-solar (钙钛矿太阳能电池). Each is a YAML file with `id`, `title`, `category`, `description`, `tags`, and `content` (rich HTML).

---

## `lib/parser.py`

Legacy module -- functionality has been absorbed by `services/extraction.py` but kept for backward compatibility.

| Symbol | Purpose |
|--------|---------|
| `strip_html(html_text: str) -> str` | Strip HTML tags from rich text |
| `parse_notes(notes: str, llm_client) -> dict` | Convert free-form notes to structured dict via `llm_client.structured_extract()` |
| `SYSTEM_PROMPT` | The 13-rule extraction system prompt (duplicated from extraction.py) |

---

## `lib/analyzer.py`

Legacy module -- functionality has been absorbed by `services/analysis.py`.

| Symbol | Purpose |
|--------|---------|
| `analyze_experiments(summary_text: str, question: str, llm_client) -> str` | Format summary + question into a user prompt, call `llm_client.analyze()` with `ANALYSIS_SYSTEM_PROMPT`, return analysis text |

---

## `lib/logger.py`

### Module-level functions

| 函数 | 用途 |
|----------|---------|
| `init_logger(base_dir: str \| Path) -> ExdiaryLogger` | Initialize global logger; stores logs in `<base_dir>/_logs/` |
| `get_logger() -> ExdiaryLogger \| None` | Get the global logger instance |

### ExdiaryLogger class

**4 JSONL output files** at `<base_dir>/_logs/`:

| File | Content | Key methods |
|------|---------|-------------|
| `agent.log` | All conversation messages (parent/child mixed, `agent` field distinguishes) | `agent()`, `agent_user()`, `agent_assistant()` |
| `tools.log` | All tool calls with success status | `tool()`, `tool_from_loop()` |
| `operations.log` | File/state changes (exp saved, thread done, etc.) | `operation()`, `op_from_loop()` |
| `system.log` | Startup, errors, exceptions | `system()`, `exception()` |

Each entry is JSONL with an auto-generated `ts` field.

| Method | Purpose |
|--------|---------|
| `__init__(base_dir)` | Create `_logs/` directory |
| `_write(filename, entry)` | Append JSON line to log file |
| `_agent_type(loop)` | Infer "parent" or "child" from AgentLoop |
| `_agent_exp(loop)` | Get the exp_id if agent is a child |
| `agent(agent, role, content, tool_calls, exp)` | Log a conversation message |
| `agent_user(loop, content)` | Log a user message (auto-detects agent type) |
| `agent_assistant(loop, content, tool_calls)` | Log an assistant message |
| `tool(agent, tool_name, ok, exp, **summary)` | Log a tool call |
| `tool_from_loop(loop, tool_name, ok, **summary)` | Log a tool call from AgentLoop |
| `operation(op, agent, **kwargs)` | Log an operation event |
| `op_from_loop(loop, op, **kwargs)` | Log an operation from AgentLoop |
| `system(level, event, **kwargs)` | Log a system event |
| `exception(event, **kwargs)` | Log an exception with auto-captured traceback |

---

## `lib/debug.py`

### DebugTracer class

Per-conversation-session LLM call debug logger. Creates sequential Markdown files in `<base_dir>/_debug/<timestamp>/`.

| Method | Purpose | File pattern |
|--------|---------|--------------|
| `__init__(session_dir: Path)` | Create session directory |
| `_write(filename, content) -> Path` | Write content to `<dir>/<filename>` |
| `log_conversation_start(user_message)` | Log the user's first message | `000_conversation_start.md` |
| `log_llm_call(stage, system_prompt, user_prompt, temperature, raw_response) -> Path` | Full LLM call trace (prompts truncated if >8k/4k/6k chars) | `NNN_<stage>_call.md` |
| `log_parse_error(stage, raw_response, error) -> Path` | JSON parse failure details | `NNN_<stage>_parse_error.md` |
| `log_context(label, content)` | Intermediate context data (truncated at 10k chars) | `NNN_context_<label>.md` |
| `log_state(state_dict)` | AgentState snapshot (truncated at 8k chars) | `NNN_state.md` |

**Factory function**: `create_debug_tracer(base_dir: str) -> DebugTracer` creates a new session at `<base_dir>/_debug/<YYYYMMDD_HHMMSS>/`.
