# Exdiary 代码解耦与架构重构方案

## 一、当前问题诊断

### 1.1 app.py — 路由 + 业务逻辑 + 存储操作 混杂

```
当前:  HTTP请求 → Flask路由函数 → 直接调 store.load() + parse_notes() + 各种_开头的辅助函数
问题:  一个路由函数里同时做了参数校验、调用LLM、读写文件、构建diff、写日志
       没有中间层，想加缓存、想切换数据库、想做权限控制——全部需要改路由函数
```

具体症状：
- `save_experiment_json()` 里混合了：参数校验、文件读取（old_exp）、引用提取、diff 计算、更新日志写入、文件保存、引用关系清理 —— **7 种不同职责在一个函数里**
- `_extract_references()`、`_update_referenced_by()`、`_compute_diff()`、`_log_update()` 等私有函数全堆在 app.py 底部，和路由混在一个文件里
- LLM 客户端实例化逻辑（`get_extract_llm()`、`get_analyze_llm()`、`get_agent_llm()`）散落在路由文件中

### 1.2 agent_v2.py — 巨型单体类

```
当前:  agent_v2.py 一个文件 (~2430 行)，包含模块级定义和两个类:

       模块级定义 (~1544 行):
       - 16 个 TOOL_* dict 定义 + TOOLS_OPENAI_FORMAT 列表 (~395 行)
       - SYSTEM_PROMPT 字符串常量 (~196 行)
       - DEFAULT_CONTEXT 字典 (~18 行)
       - 5 个辅助函数: merge_context, _is_filled, _brief,
         _fallback_preview, _tool_log_summary (~119 行)
       - ToolExecutor 类: 工具注册 + 参数校验 + 16 个工具实现 (~775 行)

       AgentLoop 类 (~883 行):
       - 对话循环 (run() 方法)
       - Schema 状态管理
       - 线程生命周期管理
       - 上下文窗口压缩
       - 状态序列化/反序列化
       - 子 Agent 创建
       - 调试日志写入 + LLM 调用

       ToolExecutor 是模块级独立类，不在 AgentLoop 内部。两者通过
       AgentLoop.__init__ 中的 self.tools = ToolExecutor(...) 组合。
       几乎不可单元测试，耦合到具体 LLM API 行为。
```

### 1.3 agent.py — 遗留死代码

```
当前:  1866 行完整的 Agent v1 实现，包含:
       - PRIORITY_MAP (9 种实验类型 × 3 级优先级)
       - PARAM_ALIASES (参数名归一化映射)
       - 四阶段状态机 Prompt (PROMPT_INTENT/DETAIL/VERIFY/EXTRACT)
       - AgentState 数据类 + TurnController
       - ExperimentAgent 类
       
       app.py 完全不引用它。以下内容在 agent_v2.py 中重复存在:
       - DEFAULT_CONTEXT 字典 — 两处完全重复 (agent.py:345, agent_v2.py:619)
       - 9 种实验类型 × 3 级优先级清单 — agent.py 中是 PRIORITY_MAP
         数据结构，agent_v2.py 的 SYSTEM_PROMPT 中以内联自然语言
         散文形式重复 (agent_v2.py:546-576)
       - _PRIORITY_TO_SCHEMA_FIELD 及 get_schema_priority() 在 v2
         中无对应（v2 不做逐字段完整性评估，由 LLM 自主判断）
```

### 1.4 存储层 — 无抽象，直接文件 I/O

```
当前:  所有代码直接 import ExperimentStore, FavoritesStore, AnalysisStore 等
       然后调 store.load(id), store.save(data), store.list_all()
       
       没有接口层 → 将来想加 SQLite 缓存、想换数据库、想加 Redis 缓存 → 全部调用方都要改
```

### 1.5 前端 — JS 内联在模板中

```
当前:  14 个 HTML 模板各自内嵌 <script> 块
       view.html 中 ~1200 行 JS 内联
       new.html 中 ~1500 行 JS 内联
       
       同一个 fetch('/api/...') 调用模式在不同模板中重复实现
       跨页面共享的状态管理靠 sessionStorage 手写
```

---

## 二、目标架构

### 分层原则

```
┌─────────────────────────────────────────────────┐
│  表示层 (Presentation)                           │
│  templates/  +  static/js/  +  static/css/      │
│  - 模板只做渲染，不含业务逻辑                        │
│  - JS 抽到独立文件，按页面拆分                       │
│  - CSS 从模板中抽离                                │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  路由层 (Routes)                                  │
│  routes/                                         │
│  - 只做: 解析请求参数 → 调用服务 → 返回响应           │
│  - 不做: 文件读写、LLM调用、diff计算                  │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  服务层 (Services)                                │
│  services/                                       │
│  - ExperimentService: 实验CRUD + 引用管理 + 日志     │
│  - ExtractionService: 自然语言提取 + 解析           │
│  - AnalysisService: 跨实验分析 + 报告管理           │
│  - AgentService: Agent 生命周期 + 对话管理          │
│  - SyncService: 云同步 (新增)                      │
│  - AuthService: 用户认证 (新增)                     │
│  - 每个服务有自己的职责边界，通过 Repository 接口访问数据 │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  仓储层 (Repositories)                            │
│  repositories/                                   │
│  - ExperimentRepository (接口)                     │
│    ├─ YamlExperimentRepository (当前实现)          │
│    └─ SqliteExperimentRepository (未来实现)        │
│  - AnalysisRepository (接口)                       │
│  - ThreadRepository (接口)                         │
│  - FavoritesRepository (接口)                      │
│  - UpdateLogRepository (接口)                      │
│  - 所有数据访问通过接口，隔离存储实现                 │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  领域层 (Domain)                                  │
│  core/                                           │
│  - 实验 Schema (EXPERIMENT_SCHEMA)                 │
│  - Agent 工具定义 (TOOL_DEFINITIONS)               │
│  - 实验类型配置 (TAG_VOCABULARY, PRIORITY_MAP)     │
│  - Agent Prompt 模板                              │
│  - 异常定义                                       │
└─────────────────────────────────────────────────┘
```

### 新的目录结构

```
lib/
  core/              # 领域层：纯数据定义，零依赖
    __init__.py
    schema.py        # EXPERIMENT_SCHEMA, DEFAULT_CONTEXT, TAG_VOCABULARY
    agent_tools.py   # 16 个工具的 JSON Schema 定义（从 agent_v2.py 迁出）
    prompts.py       # SYSTEM_PROMPT, ANALYSIS_SYSTEM_PROMPT（从 agent_v2.py 和 analyzer.py 迁出）
                     #   含 _build_priority_prompt(PRIORITY_MAP) 辅助函数，
                     #   运行时将 PRIORITY_MAP 数据结构格式化为 prompt 中的自然语言段落
    experiment_types.py  # PRIORITY_MAP（从 agent.py 迁出）
    exceptions.py    # ExdiaryError, ExtractionError, StorageError, etc.

  repositories/      # 仓储层：数据访问接口 + 实现
    __init__.py
    base.py          # 5 个抽象接口：AbstractExperimentRepository,
                     #   AbstractAnalysisRepository, AbstractThreadRepository,
                     #   AbstractFavoritesRepository, AbstractUpdateLogRepository
    yaml_experiment.py    # ← ExperimentStore 实现接口
    yaml_analysis.py      # ← AnalysisStore 实现接口
    yaml_thread.py        # ← ThreadStore 实现接口（_index_cache 作为实现内部细节保留）
    yaml_favorites.py     # ← FavoritesStore 实现接口
    yaml_update_log.py    # ← UpdateLogStore 实现接口

  services/          # 服务层：业务逻辑
    __init__.py
    experiment.py    # ExperimentService — 实验 CRUD + 引用管理 + 更新日志
                     #   含 _compute_diff（私有）、_extract_references（私有）、
                     #   _update_referenced_by（私有）、_move_draft_images（私有）
    extraction.py    # ExtractionService — 自然语言提取
                     #   吸收原 lib/parser.py 的 parse_notes() 和 strip_html()
    analysis.py      # AnalysisService — 跨实验分析 + 报告管理
                     #   吸收原 lib/analyzer.py 的 analyze_experiments()
    agent.py         # AgentService — Agent 生命周期 + 对话管理
                     #   整合 app.py 中的 _create_analysis_child_agent() 和
                     #   agent_v2.py 中的 create_child_agent/from_dict
    template.py      # TemplateService — 实验模板管理
                     #   吸收 app.py 中的 TemplateStore 类和 _BUILTIN_TEMPLATES

  agent/             # Agent 引擎：从 agent_v2.py 拆分
    __init__.py
    loop.py          # AgentLoop 类 (主循环 + 状态管理)
    executor.py      # ToolExecutor 类 (工具注册 + 参数校验 + 分发)
    tools/           # 各工具的实现 (每个工具一个文件)
      __init__.py
      load_reference.py
      search_experiments.py     # 含 _fuzzy_search + _llm_semantic_search
      update_schema.py
      ask_user.py
      generate_record.py
      modify_experiment.py
      query_experiment.py
      list_experiments.py
      manage_collection.py
      read_update_log.py
      thread_control.py         # start_record_thread, end_thread
      analyze.py                # start_analyze_thread, select_experiments,
                                #   generate_analysis, modify_analysis
    state.py         # AgentState 序列化/反序列化
    summarizer.py    # 上下文窗口压缩（_maybe_summarize 独立为函数）

  llm.py             # LLM 客户端 (重构后：统一封装，含 reasoning_content 回传)
                     #   原有 structured_extract() 和 analyze() 委托给 chat()

  logger.py          # 统一日志系统 (保持不变，纯基础设施)
  debug.py           # 调试追踪器 (保持不变，纯基础设施)
                     #   注: logger.py 和 debug.py 是横切关注点，不参与分层架构。
                     #   各层可以直接 import 使用，不通过依赖注入。

routes/              # 路由层：从 app.py 拆分（Phase 4 — 见下方）
  __init__.py
  dashboard.py       # /, /experiments, /timeline, /compare, /favorites
  experiment.py      # /experiments/<id> 及其子路由 (view/edit/delete/print/yaml)
  api_experiment.py  # /api/parse, /api/parse/confirm
  api_agent.py       # /api/agent/start, /api/agent/message, /api/agent/confirm
  api_child.py       # /api/exp/<id>/chat, /api/exp/<id>/confirm,
                     #   /api/analysis/<id>/chat
  api_analysis.py    # /api/analysis-history, /api/analysis-history/<id>
  api_search.py      # /api/experiments/search, /api/resolve-reference
  api_favorites.py   # /api/experiments/<id>/pin, /api/experiments/<id>/favorite,
                     #   /api/list-collections, /api/collections
  api_upload.py      # /api/upload-image
  settings.py        # /settings
  templates.py       # /templates, /api/templates/<id>
  uploads.py         # /uploads/<path:filepath>

app.py               # 精简为: 创建 Flask app → 注册所有蓝图 → 启动

---

## 三、分阶段实施

### Phase 1: 抽离领域层 + 清理死代码（影响最小，收益明确）

**目标**：把纯数据定义迁出，删掉 agent.py

**改动**：

1. **新建 `lib/core/schema.py`** — 从 `parser.py` 迁出 `EXPERIMENT_SCHEMA`，从 `agent_v2.py` 迁出 `DEFAULT_CONTEXT`（只迁常量，不改逻辑）

2. **新建 `lib/core/agent_tools.py`** — 从 `agent_v2.py` 迁出全部 16 个 `TOOL_*` 的 dict 定义和 `TOOLS_OPENAI_FORMAT` 列表（约 400 行纯 JSON 定义，不含执行逻辑）

3. **新建 `lib/core/experiment_types.py` 和 `lib/core/prompts.py`** — 从 `agent.py` 迁出 `PRIORITY_MAP` 到 `experiment_types.py`；从 `agent_v2.py` 迁出 `SYSTEM_PROMPT` 到 `prompts.py`。`PRIORITY_MAP` 目前在两处存在：(a) agent.py 中是 Python 数据结构，(b) agent_v2.py 的 SYSTEM_PROMPT 中是内联的自然语言散文。统一后，SYSTEM_PROMPT 中的优先级清单在运行时由 `prompts.py` 中的 `_build_priority_prompt(PRIORITY_MAP)` 动态生成，不再手动维护两份。`PARAM_ALIASES`、`_PRIORITY_TO_SCHEMA_FIELD`、`get_schema_priority()`、`normalize_param_name()` 是 v1 专属实现（v2 的矛盾检测和参数归一化完全由 LLM 自主判断），随 agent.py 一起删除，不迁出。

   在 `lib/core/prompts.py` 中新增辅助函数：
   ```python
   def _build_priority_prompt(priority_map: dict) -> str:
       """将 PRIORITY_MAP 数据结构格式化为 SYSTEM_PROMPT 中的自然语言段落。"""
       lines = []
       for exp_type, levels in priority_map.items():
           lines.append(f"{exp_type}: P1 {', '.join(levels['priority_1'])}")
           lines.append(f"          P2 {', '.join(levels['priority_2'])}")
           lines.append(f"          P3 {', '.join(levels['priority_3'])}")
       return "\n".join(lines)
   ```
   SYSTEM_PROMPT 中对应的内联段落改为 `{priority_list}` 占位符，在 AgentLoop 初始化时填入。

4. **新建 `lib/core/exceptions.py`** — 统一定义异常类：
   ```python
   class ExdiaryError(Exception): pass
   class ExtractionError(ExdiaryError): pass
   class StorageError(ExdiaryError): pass
   class AgentError(ExdiaryError): pass
   class ConfigurationError(ExdiaryError): pass
   ```

5. **删除 `lib/agent.py` 和 `lib/resolver.py`** — agent.py（1866 行）和 resolver.py（104 行）均为死代码。全项目无任何文件 import 它们。agent.py 的有用数据（`PRIORITY_MAP`）已在第 3 步迁出；resolver.py 的 `resolve_refs()` 功能由 agent_v2.py 的 `ToolExecutor._load_reference` 和 `_search_experiments` 独立覆盖，不共享任何代码路径

6. **更新所有 import**。关键变更路径：

   | 旧 import | 新 import |
   |-----------|-----------|
   | `from lib.parser import EXPERIMENT_SCHEMA` (或局部引用) | `from lib.core.schema import EXPERIMENT_SCHEMA` |
   | `from lib.agent_v2 import DEFAULT_CONTEXT` | `from lib.core.schema import DEFAULT_CONTEXT` |
   | `from lib.agent_v2 import SYSTEM_PROMPT` | `from lib.core.prompts import SYSTEM_PROMPT` |
   | `from lib.agent_v2 import TOOL_LOAD_REFERENCE, TOOLS_OPENAI_FORMAT, ...` | `from lib.core.agent_tools import TOOL_LOAD_REFERENCE, TOOLS_OPENAI_FORMAT, ...` |
   | agent_v2.py 中 `PRIORITY_MAP` 的局部使用 | `from lib.core.experiment_types import PRIORITY_MAP` |

   注意：`parser.py` 中当前直接定义了 `EXPERIMENT_SCHEMA`（第 44 行模块级变量），Phase 1 后改为 `from lib.core.schema import EXPERIMENT_SCHEMA`。`parser.py` 中的 `SYSTEM_PROMPT`（提取专用）和 `strip_html()` 在 Phase 3 迁入 `ExtractionService`，PROMPT 作为其私有类常量。`parser.py` 和 `analyzer.py` 的删除时机见 Phase 3 第 8 点——必须等到 Phase 5（agent_v2.py 拆分，_generate_record / _generate_analysis 改为调用 Service）后才能安全删除，因为 agent_v2.py 直到 Phase 5 才不再直接 import 这两个文件。

**验证**：启动 Exdiary，手动测试一条完整流程（记录 → 提取 → 查看 → 分析）

**预计改动**：~150 行新增 + ~100 行 import 调整 + ~1970 行删除（agent.py 1866 行 + resolver.py 104 行）。总 diff 约 -1700 行。

---

### Phase 2: 仓储层接口化

**目标**：在所有存储类上抽象出接口，让服务层通过接口访问数据

**改动**：

1. **新建 `lib/repositories/base.py`** — 定义抽象接口（用 Python ABC）：
   ```python
   from abc import ABC, abstractmethod

   class AbstractExperimentRepository(ABC):
       @abstractmethod
       def next_id(self) -> str: ...
       @abstractmethod
       def save(self, experiment: dict) -> str: ...
       @abstractmethod
       def load(self, exp_id: str) -> dict | None: ...
       @abstractmethod
       def update(self, exp_id: str, experiment: dict) -> bool: ...
       @abstractmethod
       def delete(self, exp_id: str) -> bool: ...
       @abstractmethod
       def list_all(self) -> list[dict]: ...
       @abstractmethod
       def list_all_full(self) -> list[dict]: ...
       @abstractmethod
       def summarize_all(self, exp_ids: list[str] | None = None) -> str: ...
       @abstractmethod
       def count(self) -> int: ...

   class AbstractAnalysisRepository(ABC):
       @abstractmethod
       def next_id(self) -> str: ...
       @abstractmethod
       def save(self, analysis: dict) -> str: ...
       @abstractmethod
       def load(self, aid: str) -> dict | None: ...
       @abstractmethod
       def list_all(self) -> list[dict]: ...
       @abstractmethod
       def delete(self, aid: str) -> bool: ...

   class AbstractThreadRepository(ABC):
       @abstractmethod
       def next_id(self) -> str: ...
       @abstractmethod
       def create(self, thread_type: str, messages: list[dict]) -> dict: ...
       @abstractmethod
       def save(self, thread_data: dict) -> None: ...
       @abstractmethod
       def load(self, thread_id: str) -> dict | None: ...
       @abstractmethod
       def get_index(self) -> dict: ...
       @abstractmethod
       def update_index(self, thread_data: dict) -> None: ...
       @abstractmethod
       def get_active_thread(self) -> dict | None: ...
       @abstractmethod
       def set_active_thread(self, thread_id: str | None) -> None: ...
       @abstractmethod
       def list_recent(self, n: int = 5) -> list[dict]: ...
       @abstractmethod
       def build_global_summary(self, exp_repo: AbstractExperimentRepository,
                                 update_log_repo: AbstractUpdateLogRepository) -> str: ...
       @abstractmethod
       def get_global_context(self) -> str: ...
       @abstractmethod
       def update_global_context(self, compressed_text: str,
                                 uncompressed_thread_ids: list[str] | None = None,
                                 recently_modified_exps: list[str] | None = None) -> None: ...
       @abstractmethod
       def save_current_state(self, agent_state: dict) -> None: ...
       @abstractmethod
       def load_current_state(self) -> dict | None: ...
       @abstractmethod
       def save_child_state(self, thread_id: str, agent_state: dict) -> None: ...
       @abstractmethod
       def load_child_state(self, thread_id: str) -> dict | None: ...
       @abstractmethod
       def get_user_profile(self) -> dict: ...
       @abstractmethod
       def update_user_profile(self, exp_data: dict) -> None: ...
       @abstractmethod
       def recalc_tag_counts(self, exp_repo: AbstractExperimentRepository) -> None: ...
       @abstractmethod
       def delete_child_state(self, thread_id: str) -> None: ...
       @abstractmethod
       def get_l0_generated_at(self) -> datetime | None: ...
       """返回 L0 摘要的最后生成时间。AgentLoop 用它判断是否过期。
       注意：当前 YAML 实现用 @property 暴露此值，实施时需统一为方法调用
       并将 AgentLoop 中 getattr(thread_store, 'l0_generated_at', None) 改为
       thread_repo.get_l0_generated_at()。"""

   class AbstractFavoritesRepository(ABC):
       @abstractmethod
       def is_pinned(self, exp_id: str) -> bool: ...
       @abstractmethod
       def toggle_pin(self, exp_id: str) -> dict: ...
       @abstractmethod
       def toggle_favorite(self, exp_id: str, collection: str = "默认收藏夹") -> dict: ...
       @abstractmethod
       def get_pinned(self) -> list[str]: ...
       @abstractmethod
       def get_collections(self) -> dict: ...
       @abstractmethod
       def create_collection(self, name: str) -> dict: ...
       @abstractmethod
       def delete_collection(self, name: str) -> dict: ...

   class AbstractUpdateLogRepository(ABC):
       @abstractmethod
       def append(self, exp_id: str, source: str, changes: list[dict],
                  context: dict | None = None, thread_id: str | None = None) -> str: ...
       @abstractmethod
       def list_recent(self, exp_id: str, limit: int = 5) -> list[dict]: ...
       @abstractmethod
       def list_all(self, exp_id: str) -> list[dict]: ...
       @abstractmethod
       def get_entry(self, exp_id: str, entry_id: str) -> dict | None: ...
   ```

   注意：
   - `_index_cache` 是 YAML 实现的**内部优化细节**，不暴露在接口上。调用者不感知。
   - `_l0_generated_at` 通过 `get_l0_generated_at()` 暴露到接口（AgentLoop 用它判断 L0 是否过期），但各实现可自由选择缓存或实时返回。
   - `build_global_summary()` 和 `recalc_tag_counts()` 的参数在接口中声明为抽象类型 `AbstractExperimentRepository` / `AbstractUpdateLogRepository`，而非具体 YAML 实现。这不会形成循环依赖（ThreadRepository → ExperimentRepository 是单向的）。

2. **重构 `lib/storage.py`** — 将现有的 5 个 Store 类拆分为独立文件，让它们实现对应的抽象接口：
   - `lib/repositories/yaml_experiment.py` ← `ExperimentStore`（代码几乎不变，只是加 `(AbstractExperimentRepository)` 继承）
   - `lib/repositories/yaml_analysis.py` ← `AnalysisStore`
   - 以此类推

3. **旧的 `lib/storage.py` 保留为兼容层**（仅 Phase 2 期间）：重导出新类为旧名称 —— `from lib.repositories.yaml_experiment import YamlExperimentRepository as ExperimentStore` —— 这样 app.py 和 agent_v2.py 暂时不需要改 import。Phase 5（Agent 拆分）完成后删除该兼容文件（此时所有调用方已迁移到 Repository 接口）。

4. **在 app.py 的工厂函数中注入实现**：
   ```python
   # app.py
   from lib.repositories.yaml_experiment import YamlExperimentRepository
   from lib.repositories.yaml_analysis import YamlAnalysisRepository
   
   exp_repo = YamlExperimentRepository(str(BASE_DIR / "experiments"))
   analysis_repo = YamlAnalysisRepository(str(BASE_DIR / "experiments" / "_analysis_history"))
   # ...
   ```

**验证**：所有现有功能不受影响。YAML 文件的读写行为完全不变。

**预计改动**：~300 行接口定义 + ~200 行拆分/重组。对外部调用者透明。

---

### Phase 3: 服务层引入

**目标**：把 app.py 和 lib/ 中的业务逻辑函数迁移到 Service 类中。路由仍在 app.py 中（仅身体变薄），路由拆分为蓝图文件在 Phase 4。

**改动**：

1. **新建 `lib/services/experiment.py`** — `ExperimentService` 封装实验的核心业务逻辑：
   ```python
   class ExperimentService:
       def __init__(self, exp_repo, update_log_repo, favorites_repo):
           self.exp_repo = exp_repo
           self.update_log_repo = update_log_repo
           self.favorites_repo = favorites_repo

       def save_with_log(self, exp_id, data, source, thread_id=None):
           """保存实验 + 自动计算 diff + 写更新日志。
           自动判断新建（调 save()）还是修改（调 update()）。
           调用方自行维护引用关系（先调 extract_references，再调 save_with_log，
           然后调 update_referenced_by）。"""
           old = self.exp_repo.load(exp_id)
           if old is None:
               # 新建实验
               self.exp_repo.save(data)
               return
           # 修改已有实验
           ok = self.exp_repo.update(exp_id, data)
           if not ok:
               return  # 实验在 load 后被删除，不写日志
           diff = self._compute_diff(old, data)
           if diff:
               self.update_log_repo.append(exp_id, source, diff,
                                           context={"summary": f"修改了 {len(diff)} 个字段"},
                                           thread_id=thread_id)

       def delete_with_log(self, exp_id):
           """删除实验 + 写系统日志"""
           self.update_log_repo.append(
               exp_id=exp_id, source="system",
               changes=[{"path": "_deleted", "field": "实验记录",
                         "old": exp_id, "new": "[已删除]"}],
               context={"summary": f"实验记录 {exp_id} 已被删除"})
           self.exp_repo.delete(exp_id)

       def extract_references(self, text: str) -> list[str]:
           """从文本提取 @EXP-xxx 引用（正则匹配，确定性，不调 LLM）。"""

       def update_referenced_by(self, exp_id, refs, old_refs=None):
           """维护双向引用关系。
           1. 对 refs 中每个 rid → 加载其实验 → 在 referenced_by 中追加 exp_id
           2. 对 old_refs 中不在 refs 中的 rid → 从其 referenced_by 中移除 exp_id
           old_refs 为 None 时视为 []，即只做新增不做清理。"""

       def save_and_update_refs(self, exp_id, data, source, old_refs=None, thread_id=None):
           """保存实验 + 自动处理引用关系（save_with_log + extract_references + update_referenced_by 的组合便捷方法）。
           大多数路由直接调此方法即可。"""
           text = data.get("original_notes", "")
           refs = self.extract_references(text)
           data["references"] = refs
           self.save_with_log(exp_id, data, source, thread_id=thread_id)
           self.update_referenced_by(exp_id, refs, old_refs=old_refs)

       def move_draft_images(self, exp_id: str):
           """将 uploads/_draft/ 中的图片迁移到 uploads/<exp_id>/"""

       def get_pinned_and_others(self):
           """获取置顶 + 其余实验列表"""

       # -- 以下为私有方法 --
       def _compute_diff(self, old: dict | None, new: dict) -> list[dict]:
           """比较两个实验 dict，返回 [{path, field, old, new}] 差异列表。
           从 app.py 的 _compute_diff() 原样迁入。"""
   ```

2. **新建 `lib/services/extraction.py`** — `ExtractionService` 封装自然语言提取：
   ```python
   class ExtractionService:
       def __init__(self, extract_llm):
           self.extract_llm = extract_llm    # flash 模型, 用于结构化提取

       def parse_notes(self, notes: str) -> dict:
           """自然语言 → 结构化 dict。
           吸收原 lib/parser.py 的 parse_notes() 和 strip_html()。"""
           ...

       def regenerate(self, exp_id: str, notes: str) -> dict:
           """从修改后的笔记重新提取，保留 exp_id。"""
           ...
   ```

3. **新建 `lib/services/analysis.py`** — `AnalysisService` 封装跨实验分析：
   ```python
   class AnalysisService:
       def __init__(self, exp_repo, analysis_repo, analyze_llm):
           self.exp_repo = exp_repo
           self.analysis_repo = analysis_repo
           self.analyze_llm = analyze_llm

       def run_analysis(self, query: str, refs: list[str]) -> dict:
           """执行分析 → 写 AnalysisStore → 更新实验关联 → 返回报告。
           吸收原 lib/analyzer.py 的 analyze_experiments()。"""
           ...
   ```

4. **新建 `lib/services/agent.py`** — `AgentService` 封装 Agent 生命周期。
   按功能拆分 Agent，各服务间通过显式参数传递，不使用泛化的 dict/service locator 以避免循环依赖：
   ```python
   class AgentService:
       def __init__(self, llm_client, exp_repo, thread_repo, update_log_repo,
                    favorites_repo, analysis_repo, extraction_svc,
                    experiment_svc, analysis_svc):
           self.llm_client = llm_client
           self.exp_repo = exp_repo
           self.thread_repo = thread_repo
           self.update_log_repo = update_log_repo
           self.favorites_repo = favorites_repo
           self.analysis_repo = analysis_repo
           self.extraction_svc = extraction_svc   # 传给 AgentLoop（generate_record 需要）
           self.experiment_svc = experiment_svc   # 用于自动保存
           self.analysis_svc = analysis_svc       # 用于自动生成分析

       def create_or_resume_agent(self, state_dict=None) -> AgentLoop:
           """创建新 Agent 或从已保存状态恢复。
           
           恢复路径：state_dict 非空 → AgentLoop.from_dict(llm, exp_repo, state_dict, ...)
           或 thread_store.load_current_state() 有值 → 同上。
           
           新建路径：AgentLoop(llm, exp_repo, thread_repo=..., ...) →
           agent.run("")（空消息触发 greeting 和 L0 摘要注入）。
           
           整合 app.py 中 api_agent_start() 的分支逻辑。"""
           ...

       def run_message(self, agent: AgentLoop, message: str) -> dict:
           """处理用户消息 → 返回 {type, message, state?, preview?}。
           封装 agent.run() + 后处理（自动保存 generate 结果等）。"""
           ...

       def create_child_agent(self, parent: AgentLoop, thread_id: str,
                              role: str) -> AgentLoop:
           """创建子 Agent。role 为 "exp_editor" 或 "analysis_reviewer"。

           角色差异（实施关键）：
           - exp_editor: _child_agent_role="exp_editor", _child_exp_id=目标EXP_ID。
             系统注入 "[修改模式] 你正在修改已完成的实验..." system prompt。
             可用工具：load_reference, search_experiments, query_experiment,
             list_experiments, read_update_log, modify_experiment, end_thread。
           - analysis_reviewer: _child_agent_role="analysis_reviewer",
             _child_exp_id=目标ANAL_ID（复用此字段）。
             系统注入 "[系统状态] 你正在审阅/修改一份已完成的分析报告..." system prompt。
             可用工具：load_reference, search_experiments, query_experiment,
             list_experiments, read_update_log, modify_analysis, end_thread。

           整合 app.py 中的 _create_analysis_child_agent()
           和 agent_v2.py 中的 AgentLoop.create_child_agent() /
           create_legacy_child_agent()。

           **legacy 路径**（无线程的旧实验，api_exp_chat 第 1211–1253 行）：
           提供独立的 create_legacy_child_agent(exp_data) 方法。内部创建 AgentLoop 后注入
           EXP 结构化数据作为 system 消息，设置 _is_legacy=True, _child_agent_role="exp_editor",
           _child_exp_id=exp_id。此路径不依赖 ThreadStore。"""
           ...

       def save_runtime_state(self, agent: AgentLoop):
           """持久化 Agent 运行时状态到 ThreadStore。"""
           ...
   ```

5. **新建 `lib/services/template.py`** — `TemplateService` 管理实验模板：
   ```python
   class TemplateService:
       def __init__(self, templates_dir: str):
           """templates_dir 指向 experiment_templates/ 目录。"""
           ...

       def list_all(self) -> list[dict]: ...
       def load(self, template_id: str) -> dict | None: ...
       # _BUILTIN_TEMPLATES 从 app.py 迁入，作为此文件的私有模块级常量。
       # _seed_builtin() 逻辑保留在 TemplateService.__init__ 中：模板目录为空时自动写入内置模板。
       # 触发时机与当前行为完全一致（TemplateService 实例化时）。
   ```

6. **app.py 中注入服务**：
   ```python
   # 服务层实例化（在仓储层之后）
   experiment_svc = ExperimentService(exp_repo, update_log_repo, favorites_repo)
   extraction_svc = ExtractionService(extract_llm)
   analysis_svc = AnalysisService(exp_repo, analysis_repo, analyze_llm)
   template_svc = TemplateService(str(BASE_DIR / "experiment_templates"))
   agent_svc = AgentService(
       llm_client=agent_llm,
       exp_repo=exp_repo,
       thread_repo=thread_repo,
       update_log_repo=update_log_repo,
       favorites_repo=favorites_repo,
       analysis_repo=analysis_repo,
       experiment_svc=experiment_svc,
       analysis_svc=analysis_svc,
   )
   ```

7. **逐步迁移路由函数** — 每个路由改为调用 Service 方法：
   ```python
   # 之前
   @app.route("/experiments/<exp_id>/save-json", methods=["POST"])
   def save_experiment_json(exp_id):
       data = request.get_json()
       old_exp = store.load(exp_id)
       # ... 30 行混合逻辑 ...
       store.update(exp_id, data)
       _log_update(...)
       _update_referenced_by(...)

   # 之后
   @app.route("/experiments/<exp_id>/save-json", methods=["POST"])
   def save_experiment_json(exp_id):
       data = request.get_json()
       if not data: return jsonify({"ok": False}), 400
       old_refs = (experiment_svc.exp_repo.load(exp_id) or {}).get("references", [])
       experiment_svc.save_and_update_refs(exp_id, data, source="manual_edit", old_refs=old_refs)
       return jsonify({"ok": True})
   ```

   注意：此 Phase 中路由仍在 app.py 中。路由函数身体变薄（~10 行），文件总长从 ~1700 行降至 ~500 行。

8. **lib/parser.py 和 lib/analyzer.py 的处理**：
   - `parser.py` 中的 `parse_notes()` → 迁入 `ExtractionService.parse_notes()`；`strip_html()` 作为 `ExtractionService` 的私有方法
   - `parser.py` 中的 `EXPERIMENT_SCHEMA` → 已在 Phase 1 迁至 `core/schema.py`
   - `analyzer.py` 中的 `analyze_experiments()` → 迁入 `AnalysisService.run_analysis()`
   - `ANALYSIS_SYSTEM_PROMPT` → 已在 Phase 1 迁至 `core/prompts.py`
   - 原文件 `lib/parser.py` 和 `lib/analyzer.py` 在 Phase 5（Agent 拆分）完成后删除。此时 `agent_v2.py` 的 `_generate_record` 已改为调用 `ExtractionService`，`_generate_analysis` / `_modify_analysis` 已改为调用 `AnalysisService`，不再直接 import 这两个旧模块。

**验证**：完整回归测试——新建实验、编辑保存、删除、分析、Agent 对话。运行 `test_agent_v2.py`。

**预计改动**：~600 行新增 Service 类 + ~600 行路由精简。app.py 从 ~1700 行降到 ~500 行。

**Service 间依赖方向**（实施时用于避免循环 import）：
- `AgentService` → `ExperimentService`（自动保存生成结果）
- `AgentService` → `AnalysisService`（自动生成分析报告）
- `ExperimentService` 和 `AnalysisService` 均不依赖任何其他 Service。依赖图严格单向无环。

---

### Phase 4: 路由层拆分

**目标**：将 app.py 中已变薄的路由函数迁移到 `routes/` 蓝图文件。此 Phase 完成后 app.py 降至 ~120 行（仅剩工厂函数和启动逻辑）。

**改动**：

1. **创建 `routes/` 包** — 从 app.py 拆分出 12 个蓝图文件：

   | 蓝图文件 | 注册 url_prefix | 路由 |
   |---------|----------------|------|
   | `routes/dashboard.py` | `/` | `/`, `/experiments`, `/timeline`, `/compare`, `/favorites` |
   | `routes/experiment.py` | `/experiments` | `/<id>`, `/<id>/edit`, `/<id>/delete`, `/<id>/print`, `/<id>/yaml`, `/<id>/save-json`, `/<id>/regenerate` |
   | `routes/api_experiment.py` | `/api` | `/parse`, `/parse/confirm` |
   | `routes/api_agent.py` | `/api/agent` | `/start`, `/message`, `/confirm` |
   | `routes/api_child.py` | `/api` | `/exp/<id>/chat`, `/exp/<id>/confirm`, `/analysis/<id>/chat` |
   | `routes/api_analysis.py` | `/api` | `/analysis-history`, `/analysis-history/<id>` (GET + DELETE) |
   | `routes/api_search.py` | `/api` | `/experiments/search`, `/resolve-reference` |
   | `routes/api_favorites.py` | `/api` | `/experiments/<id>/pin`, `/experiments/<id>/favorite`, `/list-collections`, `/collections` |
   | `routes/api_upload.py` | `/api` | `/upload-image` |
   | `routes/settings.py` | (无) | `/settings` |
   | `routes/templates.py` | (无) | `/templates`, `/api/templates/<id>` |
   | `routes/uploads.py` | (无) | `/uploads/<path:filepath>` |

   注意：`api_experiment.py`、`api_child.py`、`api_analysis.py`、`api_search.py`、`api_favorites.py`、`api_upload.py` 都注册在 `/api` 下，Flask 会自动合并同一 url_prefix 的蓝图，不存在路由冲突。

   **URL 变更标注**：
   - `save-json` 和 `regenerate` 在当前 app.py 中无 `/api` 前缀（`@app.route("/experiments/<id>/save-json")`），现已归入 `routes/experiment.py`（`url_prefix="/experiments"`），URL 保持不变。
   - `/api/favorites` 页面路由（当前返回 HTML）从 `/api` 下移至 `/favorites`——这是 URL 规范化修正（页面路由不应在 `/api` 下）。**前端模板中指向 `/api/favorites` 的链接需同步改为 `/favorites`。**

2. **每个蓝图文件只做薄路由**（参数解析 → 调 Service → 返回响应），典型约 30-60 行：
   ```python
   # routes/experiment.py
   from flask import Blueprint, request, redirect, url_for, jsonify
   from flask import g

   experiment_bp = Blueprint("experiment", __name__)

   @experiment_bp.route("/<exp_id>")
   def view(exp_id):
       exp = g.exp_repo.load(exp_id)
       if not exp:
           return "Experiment not found", 404
       return render_template("view.html", exp=exp)
   ```

3. **完整路由→蓝图映射表**（便于维护时快速定位）：

   | 蓝图文件 | Flask endpoint 前缀 | 路由 |
   |---------|---------------------|------|
   | `routes/dashboard.py` | `dashboard.` | `GET /` (index), `GET /experiments` (list), `GET /timeline`, `GET /compare`, `GET /favorites`（原 `/api/favorites`，此 URL 作为页面路由本不应在 /api 下，Phase 4 统一为 `/favorites`；前端模板中指向此页面的链接需同步更新） |
   | `routes/experiment.py` | `experiment.` | `GET /experiments/<id>` (view), `GET/POST /experiments/<id>/edit`, `DELETE /experiments/<id>/delete`, `GET /experiments/<id>/print`, `GET /experiments/<id>/yaml`, `POST /experiments/<id>/save-json`, `POST /experiments/<id>/regenerate` |
   | `routes/api_experiment.py` | `api_experiment.` | `POST /api/parse`, `POST /api/parse/confirm` |
   | `routes/api_agent.py` | `api_agent.` | `POST /api/agent/start`, `POST /api/agent/message`, `POST /api/agent/confirm` |
   | `routes/api_child.py` | `api_child.` | `POST /api/exp/<id>/chat`, `POST /api/exp/<id>/confirm`, `POST /api/analysis/<id>/chat` |
   | `routes/api_analysis.py` | `api_analysis.` | `GET /api/analysis-history`, `GET /api/analysis-history/<id>`, `DELETE /api/analysis-history/<id>` |
   | `routes/api_search.py` | `api_search.` | `GET /api/experiments/search`, `POST /api/resolve-reference` |
   | `routes/api_favorites.py` | `api_favorites.` | `POST /api/experiments/<id>/pin`, `POST /api/experiments/<id>/favorite`, `GET /api/list-collections`, `POST /api/collections`, `DELETE /api/collections/<name>` |
   | `routes/api_upload.py` | `api_upload.` | `POST /api/upload-image` |
   | `routes/settings.py` | `settings.` | `GET/POST /settings`（POST 调用 `app.py` 中保留的模块级 `save_settings()` 函数，该函数写 `config.yaml` 并更新运行时 `config` dict） |
   | `routes/templates.py` | `templates.` | `GET /templates`, `GET /api/templates/<id>` |
   | `routes/uploads.py` | `uploads.` | `GET /uploads/<path:filepath>` |

   注意：多个蓝图共用 `/api` 前缀，Flask 按路由具体 pattern 匹配，不按蓝图注册顺序。只要路由 path 不重复就不会冲突。

4. **Flask g 注入的前置条件**：
   `@app.before_request` 钩子在 `create_app()` 中注册，位于所有 `app.register_blueprint()` 调用**之前**。这样每个请求到达任何蓝图路由时，`g` 上已有 `exp_repo`、`experiment_svc` 等属性。蓝图文件中只需 `from flask import g` 即可访问。

5. **app.py 注册所有蓝图**：
   ```python
   from routes.dashboard import dashboard_bp
   from routes.experiment import experiment_bp
   # ... 等 12 个蓝图

   # before_request 在蓝图注册之前
   @app.before_request
   def inject_services():
       from flask import g
       g.exp_repo = exp_repo
       # ... 等

   app.register_blueprint(dashboard_bp)
   app.register_blueprint(experiment_bp, url_prefix="/experiments")
   # ...
   ```

6. **清理 app.py**：删除已迁出的路由函数、删除私有辅助函数（`_extract_references`、`_compute_diff` 等已迁入 Service）。

7. **更新前端模板中的链接**：`/api/favorites` → `/favorites`（此 URL 从 API 命名空间移出，模板中所有导航链接需同步更新。在模板目录中 grep `/api/favorites` 即可找到所有引用点。）

**验证**：所有 23 个路由功能不变。`python app.py` 启动无 import / 蓝图注册错误。

**预计改动**：~500 行新增蓝图文件 + ~380 行 app.py 删除。app.py 从 ~500 行降至 ~120 行。

---

### Phase 5: Agent 系统拆分

**目标**：把 agent_v2.py 拆成可独立理解和测试的模块

**改动**：

1. **`lib/agent/tools/`** — 每个工具一个文件。每个文件导出 `execute(args, loop)` 函数，工具定义 dict 从 `core/agent_tools.py` import：

   ```python
   # lib/agent/tools/load_reference.py
   from lib.core.agent_tools import TOOL_LOAD_REFERENCE as TOOL_DEF

   def execute(args: dict, loop: "AgentLoop") -> dict:
       """加载引用实验。通过 loop 访问 Repository 和 Service。"""
       ...
       return result
   ```

   工具函数通过 `loop` 参数间接获取所有依赖——loop 上挂载了 `exp_repo`、`update_log_repo`、`favorites_repo`、`analysis_repo` 等 Repository 引用（在 Phase 5 拆分时 AgentLoop.__init__ 接收这些依赖并设为实例属性）。**不**通过闭包或偏函数注入，不增加额外参数。

   ToolExecutor 的构造方式变为显式接收依赖，注册表只做函数引用映射：

   ```python
   # lib/agent/executor.py
   from lib.agent.tools import (
       load_reference, search_experiments, update_schema, ...
   )

   class ToolExecutor:
       """工具注册 + 参数校验 + 分发。不持有 Repository/Service 引用（它们通过 loop 访问）。"""
       
       def __init__(self):
           self.registry = {
               "load_reference": load_reference.execute,
               "search_experiments": search_experiments.execute,
               "update_schema": update_schema.execute,
               ...
           }
       
       def execute(self, name: str, args: dict, loop: "AgentLoop") -> dict:
           """校验参数 → 分发到注册的函数。loop 参数透传给工具函数。"""
           ...
           return self.registry[name](args, loop)
   ```

   当前 `ToolExecutor.__init__` 接收的 4 个参数 `(store, update_log_store, favorites_store, analysis_store)` 被移除。每个工具函数原本通过 `self.store.load(...)` 访问的数据现在通过 `loop.exp_repo.load(...)` 访问。

2. **`lib/agent/loop.py`** — `AgentLoop` 类精简为：

   `__init__` 更新为显式接收 Repository 接口（工具函数通过 `loop.exp_repo` 等访问数据）：
   ```python
   class AgentLoop:
       def __init__(self, llm_client, exp_repo, debug_dir=None,
                    thread_repo=None, update_log_repo=None,
                    favorites_repo=None, analysis_repo=None,
                    extraction_svc=None):
           self.llm = llm_client
           self.exp_repo = exp_repo
           self.thread_repo = thread_repo
           self.update_log_repo = update_log_repo
           self.favorites_repo = favorites_repo
           self.analysis_repo = analysis_repo
           self.extraction_svc = extraction_svc  # generate_record 工具需要
           self.tools = ToolExecutor()  # 不再传参
           # ... 其余初始化不变 ...
   ```
   注：原 `experiment_store` 参数改名为 `exp_repo`（Repository 接口），原 `thread_store` 改名为 `thread_repo`。原 `update_log_store` / `favorites_store` / `analysis_store` 同样改为 `_repo` 后缀，类型从具体类改为抽象接口。
   
   注：`extraction_svc` 是 AgentLoop 唯一持有的 Service 引用。`generate_record` 工具在生成记录时需要调 `loop.extraction_svc.parse_notes()` 做结构化提取——此调用路径不经过 ToolExecutor，因为 `parse_notes` 不是工具而是确定性函数。`ExtractionService` 不依赖任何 Repository，不会形成循环。`AgentService` 创建 AgentLoop 时将自己持有的 `extraction_svc` 传进去即可。

   注：`from_dict` 类方法也需要 `extraction_svc` 参数——它不可序列化（含 LLM client），恢复状态时由调用方传入。`state_to_dict` 不序列化 `extraction_svc`（跳过此属性）。

   - `run()` 主循环（~150 行，当前约 193 行，提取日志/压缩逻辑后缩减）
   - 状态管理（`_build_schema_status`, `_build_thread_status` 等 ~80 行）
   - 线程管理（`_maybe_inject_thread_start/end` 等 ~80 行）
   - 序列化（`state_to_dict` / `from_dict` ~60 行）

3. **`lib/agent/summarizer.py`** — `_maybe_summarize()` 逻辑独立出来

4. **`lib/agent/state.py`** — AgentState 的序列化/反序列化从 `AgentLoop` 中独立

5. **`lib/agent/tools/` 中每个工具通过接口访问数据** — 不再直接 `self.store.load()`，而是通过传入的 Repository 接口：
   ```python
   def execute(args, loop):
       exp = loop.exp_repo.load(exp_id)  # 通过接口，不写死实现
   ```

6. **`lib/agent/executor.py`** — 从 agent_v2.py 的模块级类提升为独立文件。`ToolExecutor` 本身不持有 Repository/Service 引用（依赖通过 `loop` 参数透传给工具函数，详见第 1 点），此处是物理隔离而非重构类关系。

7. **工具文件分组原则**：
   - 每个工具自成独立文件（一个 `execute()` 函数 + 对 `core/agent_tools.py` 中 TOOL_DEF 的引用）
   - 仅两个例外合并：`thread_control.py`（`start_record_thread` + `end_thread`，共享线程生命周期逻辑）；`analyze.py`（`start_analyze_thread` + `select_experiments` + `generate_analysis` + `modify_analysis`，共享 AnalysisStore 操作和 analyze_experiments 调用）
   - 这一分组反映的是"共享同一个 Service 依赖"而非功能相似性。将来如分析工具膨胀，可进一步拆分

8. **循环 import 处理**：工具文件（`lib/agent/tools/*.py`）的 `execute(args, loop: "AgentLoop")` 签名需要 AgentLoop 类型标注。正确做法是使用 `TYPE_CHECKING`：
   ```python
   from __future__ import annotations
   from typing import TYPE_CHECKING
   if TYPE_CHECKING:
       from lib.agent.loop import AgentLoop
   ```
   运行时不会真正 import AgentLoop，避免 `tools/ → loop.py → executor.py → tools/` 循环。

9. **`update_schema` 工具的隐蔽行为**（agent_v2.py L1519–1528）：当在 analyze 线程中调用 `update_schema` 时，工具会自动注入 `thread_end`、清除 `thread_id` 和 `_thread_type`，即**自动将 analyze 线程切换为 record 线程**。拆分 `lib/agent/tools/update_schema.py` 时必须保留此逻辑。

10. **`_parent_thread_store` 属性**（agent_v2.py L2198, L2220）：只在 `create_child_agent()` 和 `create_legacy_child_agent()` 中被写入，全项目无任何读取。`state_to_dict` 不序列化，`from_dict` 不恢复。**确认为死代码，Phase 5 删除这两行赋值。**

**验证**：运行 `test_agent_v2.py` 中的所有测试。手动测试 Agent 对话全流程。

**预计改动**：agent_v2.py 从 ~2430 行拆分为 ~15 个文件（每个 50-300 行），总量不变但可维护性大幅提升。

**建议实施顺序**（降低调试难度）：
Phase 5 同时做了两件独立的事：(a) AgentLoop 参数从具体 Store 类改为 Repository 接口，(b) ToolExecutor 参数清空改为通过 loop 访问。建议分两步验证：
1. 先做 (a)：AgentLoop 挂 `exp_repo`/`thread_repo` 等属性，ToolExecutor 暂时保持原样（仍接收旧参数、通过 `self.store` 访问）。验证 Agent 对话全流程正常。
2. 再做 (b)：删除 ToolExecutor 的 Store 引用，改为 `execute(args, loop)` 透传。工具函数通过 `loop.exp_repo` 访问数据。再次验证全流程。
分步好处：如果第 2 步出问题，git bisect 可以直接定位到 ToolExecutor 改造，不用在 AgentLoop 和 ToolExecutor 的改动之间排查。

---

### Phase 6: LLM 客户端重构

**目标**：统一所有 LLM 调用路径，封装 reasoning_content 回传

**改动**：

```python
# lib/llm.py (重构后)
from dataclasses import dataclass

@dataclass
class LLMResponse:
    content: str
    reasoning: str = ""
    tool_calls: list[dict] | None = None
    usage: dict | None = None  # token 统计

class LLMClient:
    def __init__(self, api_key, model, base_url="https://api.deepseek.com"):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, messages, tools=None, temperature=0.3, 
             reasoning_effort=None) -> LLMResponse:
        """统一的聊天接口，返回 LLMResponse 包含 reasoning_content"""
        kwargs = {"model": self.model, "messages": messages, "temperature": temperature}
        if tools:
            kwargs["tools"] = tools
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return LLMResponse(
            content=msg.content or "",
            reasoning=getattr(msg, "reasoning_content", "") or "",
            tool_calls=[{
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments}
            } for tc in (msg.tool_calls or [])],
            usage={"prompt_tokens": resp.usage.prompt_tokens, 
                   "completion_tokens": resp.usage.completion_tokens}
            if resp.usage else None
        )

    def structured_extract(self, prompt, system_prompt, output_schema) -> dict:
        """使用 function calling 的结构化提取"""
        resp = self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            tools=[{"type": "function", "function": {
                "name": "save_experiment", "parameters": output_schema
            }}]
        )
        if not resp.tool_calls:
            raise RuntimeError(f"模型未调用函数: {resp.content[:200]}")
        return json.loads(resp.tool_calls[0]["function"]["arguments"])

    def analyze(self, system_prompt, user_prompt, temperature=0.3) -> str:
        """纯文本分析调用"""
        resp = self.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=temperature
        )
        return resp.content
```

**改动点**：
- `LLMClient.chat()` 成为唯一直接调用 OpenAI API 的方法
- 当前 `agent_v2.py` 中只有 **一处** 直接调用 `self.llm.client.chat.completions.create(...)`（`AgentLoop.run()` 方法，第 1698 行）。该处改为 `self.llm.chat(messages, tools=..., reasoning_effort="max")`。`reasoning_effort` 由**调用方决定**（AgentLoop 传 `"max"`，ExtractionService/AnalysisService 不传），不在 LLMClient 内部硬编码。
- `ToolExecutor` 中的 `_search_experiments`、`_generate_analysis`、`_modify_analysis` 等通过 `loop.llm.analyze(...)` 包装方法调用，无需改动——它们会在 Phase 6 中随 `analyze()` 重构为委托 `chat()`。
- `structured_extract()` 和 `analyze()` 都委托给 `chat()`，不再各自调 API
- 增加了 `LLMResponse` 数据类，返回类型安全的数据而非裸 SDK 对象
- `LLMClient` 的消费者不只有 AgentLoop——`ExtractionService`（调 `structured_extract()`）和 `AnalysisService`（调 `analyze()`）也依赖它。Phase 6 统一 `chat()` 后，所有消费者受益于统一的错误处理和 token 统计。
- 将来切换模型供应商：只需改 `LLMClient.__init__` 和 `chat()`，其他代码不受影响

**预计改动**：~60 行新增 + ~40 行改动。

---

### Phase 7: 前端解耦（可选，可延后）

**目标**：把模板中的 JS 抽到独立文件

**改动**：

1. **新建 `static/js/` 目录**：
   ```
   static/
     js/
       api.js          # 通用 API 调用封装 (fetch 包装)
       sync.js         # 云同步状态管理
       editor.js       # Quill 编辑器相关
       chat.js         # Agent 聊天面板
       view.js         # 实验详情页交互
       new.js          # 新建实验页交互
       selector.js     # 实验选择面板
     css/
       de-stijl.css    # 从 base.html 迁出核心样式
       components.css  # 组件样式
   ```

2. **模板中使用 `<script src>` 引用**，而非内联。每个页面只引用需要的 JS。

3. **`api.js` 提供统一的请求封装**，消除各模板里重复的 `fetch(...)` 模式：
   ```javascript
   // api.js
   const API = {
       async get(url) { ... },
       async post(url, data) { ... },
       async delete(url) { ... },
       // 自动处理 401、网络错误、JSON 解析
   };
   ```

4. **`_selector_scripts.html` 和 `_selector_styles.html` 的处理**：
   - 样式部分迁入 `static/css/components.css`
   - 脚本部分迁入 `static/js/selector.js`
   - 原 partial 模板删除。其他模板中 `{% include '_selector_scripts.html' %}` 替换为 `<script src="/static/js/selector.js"></script>`

**验证**：所有页面功能正常，浏览器 DevTools 无 404。首次加载 JS 资源正常。

**预计改动**：~200 行新增 JS 文件 + ~800 行模板精简（script 标签迁移）。

---

## 四、重构后的 app.py 预览

```python
import os, sys, socket, threading
from pathlib import Path
from flask import Flask, g

from lib.logger import init_logger, get_logger
from lib.llm import LLMClient

# 仓储层
from lib.repositories.yaml_experiment import YamlExperimentRepository
from lib.repositories.yaml_analysis import YamlAnalysisRepository
from lib.repositories.yaml_thread import YamlThreadRepository
from lib.repositories.yaml_favorites import YamlFavoritesRepository
from lib.repositories.yaml_update_log import YamlUpdateLogRepository

# 服务层
from lib.services.experiment import ExperimentService
from lib.services.extraction import ExtractionService
from lib.services.analysis import AnalysisService
from lib.services.agent import AgentService
from lib.services.template import TemplateService

# 路由层
from routes.dashboard import dashboard_bp
from routes.experiment import experiment_bp
from routes.api_experiment import api_experiment_bp
from routes.api_agent import api_agent_bp
from routes.api_child import api_child_bp
from routes.api_analysis import api_analysis_bp
from routes.api_search import api_search_bp
from routes.api_favorites import api_favorites_bp
from routes.api_upload import api_upload_bp
from routes.settings import settings_bp
from routes.templates import templates_bp
from routes.uploads import uploads_bp

# ---- 配置 ----
BASE_DIR = Path(__file__).parent
SETTINGS_PATH = BASE_DIR / "config.yaml"

def load_settings():
    # ... 不变 ...

# ---- 应用工厂 ----
def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    config = load_settings()

    # ----- 日志系统 -----
    init_logger(BASE_DIR / "experiments")

    # ----- 仓储层 -----
    exp_repo = YamlExperimentRepository(str(BASE_DIR / "experiments"))
    analysis_repo = YamlAnalysisRepository(str(BASE_DIR / "experiments" / "_analysis_history"))
    thread_repo = YamlThreadRepository(str(BASE_DIR / "experiments" / "_threads"))
    favorites_repo = YamlFavoritesRepository(str(BASE_DIR / "experiments" / "_favorites.yaml"))
    update_log_repo = YamlUpdateLogRepository(str(BASE_DIR / "experiments" / "_update_logs"))

    # ----- LLM 客户端 -----
    api_key = config.get("DEEPSEEK_API_KEY", "")
    extract_llm = LLMClient(api_key, config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")) if api_key else None
    analyze_llm = LLMClient(api_key, config.get("DEEPSEEK_ANALYZE_MODEL", "deepseek-v4-pro")) if api_key else None
    agent_llm = LLMClient(api_key, config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")) if api_key else None

    # ----- 服务层 -----
    experiment_svc = ExperimentService(exp_repo, update_log_repo, favorites_repo)
    extraction_svc = ExtractionService(extract_llm)
    analysis_svc = AnalysisService(exp_repo, analysis_repo, analyze_llm)
    template_svc = TemplateService(str(BASE_DIR / "experiment_templates"))
    agent_svc = AgentService(
        llm_client=agent_llm,
        exp_repo=exp_repo,
        thread_repo=thread_repo,
        update_log_repo=update_log_repo,
        favorites_repo=favorites_repo,
        analysis_repo=analysis_repo,
        extraction_svc=extraction_svc,
        experiment_svc=experiment_svc,
        analysis_svc=analysis_svc,
    )

    # ----- 将服务注入到 app 上下文 (flask.g) -----
    # 规则：路由层只读查询可用 exp_repo（load / list_all），所有写操作必须走 Service。
    # 其他 Repository（analysis / thread / update_log / favorites）仅供内部 Service/Agent 使用，
    # 不注入 g——路由层通过 Service 访问它们的数据。
    @app.before_request
    def inject_services():
        g.config = config
        g.exp_repo = exp_repo           # 只读查询快捷访问
        g.experiment_svc = experiment_svc
        g.extraction_svc = extraction_svc
        g.analysis_svc = analysis_svc
        g.template_svc = template_svc
        g.agent_svc = agent_svc

    # ----- 注册路由蓝图 -----
    # 页面路由
    app.register_blueprint(dashboard_bp)                         # /, /experiments, /timeline, /compare, /favorites
    app.register_blueprint(experiment_bp, url_prefix="/experiments")  # /<id>/view, /<id>/edit, ...
    app.register_blueprint(settings_bp)                          # /settings
    app.register_blueprint(templates_bp)                         # /templates, /api/templates/<id>
    app.register_blueprint(uploads_bp)                           # /uploads/<path:filepath>

    # API 路由 — 多个蓝图共用 /api 前缀，Flask 自动合并
    app.register_blueprint(api_experiment_bp, url_prefix="/api")      # /parse, /parse/confirm
    app.register_blueprint(api_agent_bp, url_prefix="/api/agent")     # /start, /message, /confirm
    app.register_blueprint(api_child_bp, url_prefix="/api")           # /exp/<id>/chat, /exp/<id>/confirm, /analysis/<id>/chat
    app.register_blueprint(api_analysis_bp, url_prefix="/api")        # /analysis-history, /analysis-history/<id>
    app.register_blueprint(api_search_bp, url_prefix="/api")          # /experiments/search, /resolve-reference
    app.register_blueprint(api_favorites_bp, url_prefix="/api")       # /experiments/<id>/pin, /experiments/<id>/favorite, /list-collections, /collections
    app.register_blueprint(api_upload_bp, url_prefix="/api")          # /upload-image

    return app, config

# ---- 启动 ----
if __name__ == "__main__":
    app, config = create_app()
    port = int(config.get("PORT", 5000))
    host = config.get("HOST", "0.0.0.0")
    use_gui = config.get("GUI", "true").lower() in ("true", "1", "yes")

    log = get_logger()
    if log:
        log.system("info", "startup", port=port, gui=config.get("GUI", "true"))

    model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    analyze_model = config.get("DEEPSEEK_ANALYZE_MODEL", "deepseek-v4-pro")

    # 修复 Windows 控制台中文乱码
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    # 检测 LAN IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    print(f"  Exdiary")
    print(f"  Local:    http://127.0.0.1:{port}")
    print(f"  Network:  http://{lan_ip}:{port}")
    print(f"  Extract:  {model}")
    print(f"  Analyze:  {analyze_model}")

    if "--headless" in sys.argv or not use_gui:
        print(f"  Mode:     headless (web only)")
        app.run(host=host, port=port, debug=True)
    else:
        try:
            import webview
        except ImportError:
            print("  pywebview not installed. Run: pip install pywebview")
            print("  Falling back to web mode...")
            app.run(host=host, port=port, debug=True)
            sys.exit()

        def run_flask():
            app.run(host=host, port=port, debug=False, use_reloader=False)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        print(f"  Mode:     native desktop window")
        webview.create_window(
            title="Exdiary — 实验记录",
            url=f"http://127.0.0.1:{port}",
            width=1100, height=750, min_size=(800, 500),
            text_select=True,
        )
        webview.start()
        sys.exit(0)
```

**与当前 app.py 的对比**：

| 指标 | 当前 | 重构后 |
|------|------|--------|
| app.py 行数 | ~1734 | ~120 |
| 路由数量 | 30+ (全在 app.py) | 30+ (分在 12 个蓝图文件) |
| 业务逻辑 | 混在路由函数和私有函数中 | 在 Service 层 |
| 存储访问 | 直接调 store.load() | 通过 Repository 接口 |
| 添加新路由 | 在 app.py 中加函数 | 新建蓝图文件 + 注册 |
| 切换数据库 | 修改所有调用方 | 实现新的 Repository |
| 单元测试 | 几乎不可测试（依赖 Flask + 文件系统） | 可 mock Repository 接口独立测试 Service |

---

## 五、迁移策略

### 原则

1. **每 Phase 结束后，所有现有功能必须完整可用**。不允许"先破坏再重建"。
2. **改动只涉及内部结构，不改变外部行为**。API 响应格式、模板渲染结果、文件存储格式完全不变。
3. **每个 Phase 独立可回滚**。Phase 2 的改动不依赖 Phase 3 是否完成。

### 执行顺序

```
Phase 1 (领域层抽离)        ← 先做，风险最低，收益明确
    ↓
Phase 2 (仓储接口化)        ← 对调用者透明，不改行为
    ↓
Phase 3 (服务层引入)        ← 核心，工作量最大
    ↓
Phase 4 (路由层拆分)        ← 依赖 Phase 3 完成后路由函数已变薄
    ↓
Phase 5 (Agent 拆分)        ← 依赖 Phase 2+3+4：此时 agent_v2.py 可通过
    ↓                           Repository 接口和 Service 层访问数据
Phase 6 (LLM 客户端重构)    ← 依赖 Phase 5。此时所有三个 LLMClient 消费者——
    ↓                           ExtractionService、AnalysisService（Phase 3 就位）、
                                AgentLoop（Phase 5 拆分就位）——均已稳定在各自模块中
    ↓
Phase 7 (前端解耦)          ← 可延后，优先级最低
```

Phase 3 和 Phase 6 不可并行（Phase 6 修改 LLMClient API，而 Phase 3 的 AgentService/ExtractionService/AnalysisService 都是该 API 的调用方）。Phase 6 在 Phase 5 之后执行，此时 LLMClient 的消费者都已迁至 lib/ 下各自的模块，改动面清晰可控。

### 每 Phase 的验证清单

测试环境准备：在每个 Phase 开始前，将当前 `experiments/` 目录完整备份。测试在真实数据上运行，不额外创建隔离环境。如果某个 Phase 验证失败，从备份恢复后修复。

**`test_agent_v2.py` 的适配**：测试文件当前直接 import `lib.agent_v2`（`AgentLoop`, `merge_context`, `_is_filled`, `TOOLS_OPENAI_FORMAT`）。各 Phase 改变了 import 路径和类签名后，测试文件必然 break。每个 Phase 验证前，先更新 `test_agent_v2.py` 的 import 路径和调用签名以匹配该 Phase 的模块结构。Phase 1 改 `TOOLS_OPENAI_FORMAT` 的 import 源；Phase 5 改 `AgentLoop`、`merge_context`、`_is_filled` 的 import 源。

- [ ] `python app.py` 启动无 import / 注册错误（含 Flask endpoint 冲突）
- [ ] 仪表盘页面正常加载
- [ ] 新建实验：Chat Mode 一次完整对话 → 生成记录
- [ ] 新建实验：Free Writing 一次提取 → 预览 → 确认保存
- [ ] 查看实验详情 → 编辑模式保存
- [ ] 查看实验：子 Agent 对话 → 修改字段 → 确认保存
- [ ] 删除实验
- [ ] 跨实验分析：Agent 对话中触发分析
- [ ] 分析历史列表 → 查看分析详情
- [ ] 对比视图（勾选 → 对比）
- [ ] 时间线 → 收藏夹 → 模板库 → 设置页面
- [ ] 运行 `test_agent_v2.py` 全部通过
- [ ] 通过手动将 `_l0_generated_at` 时间戳回拨到 2 小时前，触发下一轮 Agent 对话，验证 L0 摘要自动刷新

---

## 六、预期效果

| 指标 | 重构前 | 重构后 |
|------|--------|--------|
| app.py 行数 | ~1734 | ~120 |
| agent_v2.py 行数 | ~2430 | 拆为 ~15 个文件，每个 <300 行 |
| agent.py (死代码) | ~1866 | 0（删除，PRIORITY_MAP 等迁入 core/） |
| 路由函数平均行数 | ~50 | ~10 (薄层，只做参数→服务→响应) |
| Service 类数量 | 0 | 5 (Experiment, Extraction, Analysis, Agent, Template) |
| Repository 接口数量 | 0 | 5 |
| 能否 mock 测试 | 否 | 是 |
| 能否替换存储实现 | 否（需改所有调用方） | 是（实现新 Repository） |
| 能否替换 LLM 供应商 | 需改 agent_v2.py 内部 | 只需改 lib/llm.py |
| 路由文件数量 | 1 (app.py) | 12 个蓝图文件 |

---

## 七、不做的事

以下是在重构中**刻意不做**的，避免范围蔓延：

1. **不改数据库**。YAML 文件存储保持不变。Repository 接口的存在使得"将来可以换 SQLite"，但本次重构不实现 SQLite 版本。
2. **不引入 ORM/第三方框架**。不引入 SQLAlchemy、Pydantic、FastAPI、Django。保持最小依赖原则。
3. **不改 Agent 的行为**。SYSTEM_PROMPT、工具定义、对话循环逻辑照旧。重构只改代码组织，不改功能行为。
4. **不做前后端分离 SPA**。保留 Flask + Jinja2 模板方案。JS 抽到独立文件但仍是传统多页面架构。
5. **不引入构建工具链**。无 Webpack、无 npm、无 TypeScript。保持"一个 Python 文件即一个应用"的部署简单性。
6. **不删除任何用户可见功能**。
