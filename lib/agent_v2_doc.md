# lib/agent_v2.py — 说明文档

## 文件作用摘要

Exdiary Agent 核心引擎（~1902 行）。基于 OpenAI Tool Calling 的对话式实验记录系统。LLM 自主决策流程，Python 仅执行工具和注入 Schema 状态。包含四大类（AgentLoop / ToolExecutor / ChildContext / ThreadState）+ 7 个模块级辅助函数。被 `lib/agent_factory.py`、`routes/api_agent.py`、`routes/api_child.py` 导入使用。

---

## 代码块详细说明

### 模块级辅助类

#### `ChildContext`
- **字段** (__slots__): `is_child: bool`, `is_legacy: bool`, `exp_id: str | None`, `initial_history_len: int`, `agent_role: str | None`
- **被实例化**: `AgentLoop.__init__()` (line 1001: `self.child = ChildContext()`)
- **被读写**: `AgentLoop` 内部全部子 Agent 相关逻辑；`routes/api_child.py` 通过 `agent.child.exp_id` / `agent.child.agent_role` 等设置；`lib/agent_factory.py` 中 `build_analysis_child()` 和 `build_child_for_thread()` 设置 agent_role

#### `ThreadState`
- **字段** (__slots__): `id`, `type`, `pending_start`, `current_turn_user_idx`, `last_ended_id`
- **被实例化**: `AgentLoop.__init__()` (line 1000: `self.thread = ThreadState()`)

---

### 模块级函数 (7个)

#### `_cleanup_old_debug_dirs(debug_root: Path, max_age_days: int = 30) -> None`
- **作用**: 遍历 `_debug/` 下子目录，删除超过 max_age_days 天的旧 session
- **被调用**: 仅在 `AgentLoop.__init__()` (line 1027) 中调用

#### `merge_context(context: dict, fields: dict) -> dict`
- **作用**: 增量合并 fields 到 context。简单字段覆盖；数组去重追加（空数组清空）；嵌套对象递归合并（空dict清空）；不在 context 中的 key 被忽略
- **被调用**: 仅在 `ToolExecutor._update_schema()` (line 931) 中调用

#### `_is_filled(val) -> bool`
- **作用**: 判断字段是否有值。None→False, []/{}→False, {全falsy值}→False（dict 中只要存在任意 truthy 值即返回 True，源码: `any(v for v in val.values() if v)`）, "   "→False, False→False
- **被调用**: `AgentLoop._build_schema_status()` (line 1310), `AgentLoop._core_fields_filled()` (line 1415), `AgentLoop.from_dict()` (line 1869)

#### `_brief(val) -> str`
- **作用**: 字段值的简短描述。list→"N项"/"空", dict→"N子字段"/"空", str→前15字+..., bool→"有"/"空"
- **被调用**: 仅在 `AgentLoop._build_schema_status()` (line 1311) 中调用

#### `_build_preview(loop: "AgentLoop") -> dict`
- **作用**: 确定性构建预览。直接从 `loop._schema_context` 的 18 个字段（含 experimental_plan）映射为完整实验记录 dict，不调 LLM。`parse_notes` LLM 调用失败时的退化兜底——保证输出结构完整，但不做语义推断和补全
- **被调用**: 仅在 `ToolExecutor._generate_record()` 的 except 分支中调用

#### `_extract_thread_dialogue(loop: "AgentLoop") -> str`
- **作用**: 从当前线程 history 中提取用户与助手的纯文本对话。过滤系统消息、工具调用、工具结果——只保留自然语言往返。用于 `parse_notes` 增强 prompt 的 DIALOGUE 段
- **被调用**: 仅在 `ToolExecutor._generate_record()` (构建增强 prompt 时) 调用

#### `_tool_log_summary(name: str, args: dict, result: dict) -> dict`
- **作用**: 从工具调用中提取关键信息用于统一日志。按工具名分发（load_reference/search_experiments/update_schema/ask_user/generate_record/modify_experiment/manage_collection/query_experiment/analyze/list_experiments/read_update_log 共 11 种），error 时附加错误信息。注：其中 `analyze` 分支对应的工具名在 16 个注册工具中不存在，为死代码
- **被调用**: 仅在 `AgentLoop.run()` (line 1204) 中 `kw = _tool_log_summary(name, args, result)` 后传给 `log.tool()`

---

### 类 1: `ToolExecutor`

**作用**: 工具注册 (16个)、参数校验 (类型转换 + required 检查)、执行分发。所有工具方法签名均为 `(self, args: dict, loop: "AgentLoop") -> dict`，错误以 dict 形式返回（不抛异常）。

**构造参数**: `store` (必填), `update_log_store=None`, `favorites_store=None`, `analysis_store=None`

**被实例化**: `AgentLoop.__init__()` line 987-992 — 若未提供 tool_executor 则自动创建

#### 核心入口方法

- `execute(name: str, args: dict, loop: "AgentLoop") -> dict`: 参数校验 + 类型转换 + 执行 + try-except 兜底
  - **被调用**: `AgentLoop.run()` line 1195 — 每个 LLM tool_call 都通过此方法执行

- `_tool_schema(name: str) -> dict`: 从 TOOLS_OPENAI_FORMAT 按 name 查找 parameters schema
  - **被调用**: `execute()` 内部

#### 16 个工具方法 — 全部由 deepseek LLM 通过 function calling 自主调用

- `_load_reference(args, loop)`: 加载 EXP 数据 + 推断 experiment_type
- `_search_experiments(args, loop)`: 关键词 (`_fuzzy_search`) + LLM 语义 (`_llm_semantic_search`) 双层搜索
- `_start_record_thread(args, loop)`: 注入 thread_begin + 引导 → enter_record_mode → 写线程文件
- `_end_thread(args, loop)`: 注入 thread_end → 归档
- `_start_analyze_thread(args, loop)`: 对称于 start_record_thread；拒绝在 record 线程中调用
- `_select_experiments(args, loop)`: 返回 `{display: "selector", pause: True}` → 前端渲染勾选卡片
- `_generate_analysis(args, loop)`: 主路径 `AnalysisService.run_analysis()`（使用专门的推理模型 `deepseek-v4-pro`，含摘要→LLM分析→持久化→关联回写）→ 若 `loop.analysis_svc` 为 None 则回退 `lib/analyzer.analyze_experiments()` + 手动持久化（使用 Agent 的 `loop.llm`）→ 自动结束线程
- `_modify_analysis(args, loop)`: 3 种模式 (changes 直接覆盖 / additional_refs 合并实验重新分析 优先级同 _generate_analysis / additional_query 直接调 `loop.llm.analyze()` 在原报告基础上追加维度)
- `_ask_user(args, loop)`: 返回 `{status: "asked", pause: True}`
- `_generate_record(args, loop)`: 构建四段式增强 prompt（RAW SCHEMA + DIALOGUE + NOTES + REFERENCES）→ 调 `parse_notes()` LLM 提取 → 生成预览；失败退化为 `_build_preview()`；子Agent 禁止此工具
- `_read_update_log(args, loop)`: 调 `self.update_log_store.list_recent()`
- `_modify_experiment(args, loop)`: 修改字段 + `compute_experiment_diff()` + 写日志 + 注入过期标记
- `_manage_collection(args, loop)`: toggle_pin/toggle_favorite → `self.favorites_store`
- `_query_experiment(args, loop)`: 从 history 已加载数据或磁盘读取
- `_list_experiments(args, loop)`: 按 status/tags/experimenter/since 过滤
- `_update_schema(args, loop)`: `merge_context()` → 推断 experiment_type → 自动结束 analyze 线程 → 注入 Schema 状态

#### 搜索辅助方法

- `_summarize_exp(exp: dict) -> dict`: 提取实验关键信息（含最近更新日志摘要）
  - **被调用**: `_load_reference()` (line 724)
- `_fuzzy_search(query: str, loop) -> list[dict]`: 本地关键词搜索（CJK 双字符切分 + 英文分词语）
  - **被调用**: `_search_experiments()` (line 792)
- `_llm_semantic_search(query: str, loop) -> list[dict]`: 独立 LLM 调用做语义搜索（不污染 Agent 上下文）
  - **被调用**: `_search_experiments()` (line 801)

---

### 类 2: `AgentLoop`

**作用**: 基于 tool calling 的对话循环引擎。管理对话历史、Schema 状态、线程生命周期、子 Agent、上下文窗口压缩。

**构造参数**: `llm_client, experiment_store, *, tool_executor=None, debug_dir=None, thread_store=None, update_log_store=None, favorites_store=None, analysis_store=None, analysis_svc=None, extraction_svc=None`

**构造行为** (line 974-1027):
1. 设置 `self.llm` / `self.store` / `self.history=[]` / `self.references=[]` 等核心属性
2. 若未提供 tool_executor → 自动创建 `ToolExecutor(experiment_store, ...)`
3. 初始化 `self.thread = ThreadState()` / `self.child = ChildContext()`
4. 如果有 thread_store → `thread_store.build_global_summary()` → 注入 L0 摘要到 `self.history[0]`
5. 创建 debug_dir（默认 `experiments/_debug/{timestamp}/`）
6. 调用 `_cleanup_old_debug_dirs()` 清理旧目录

**被实例化**: `lib/agent_factory.py` 的 `get_or_create_agent()` (新建路径)、`build_analysis_child()`、`routes/api_child.py:219` (直接构造 parent Agent 用于 create_child_agent)

#### 核心属性 (property)

- `mode -> str`: 只读，`self.thread.id` 为空 → "general"; 否则 `self.thread.type or "general"`

#### 核心方法

- `run(user_message: str = "") -> dict` (line 1082-1291): **主循环入口**
  - 追加 user 消息到 history → 循环: 构建 messages (SYSTEM_PROMPT + L0 + history + Schema状态 + 线程状态) → `self.llm.chat()` → 处理响应:
    - **纯文本**: 返回 `{type: "reply", message, context}`
    - **tool_calls**: 逐个执行 → 错误计数(连续3次→停止) → pause 检查 → 返回或继续循环
  - **LLM 异常兜底**: history 回退到最近 user 消息 + 清理残留 + 重算 turn_count
  - **无进展自动取消**: `_check_thread_cancellation()` 连续 3 轮无 update_schema/analyze → 取消线程
  - **被调用**: `routes/api_agent.py:20,50` (start/message), `routes/api_child.py:107,122,156,197,232` (子Agent对话)

- `_build_schema_status() -> str` (line 1294): 生成 "已填 N/16 字段" + 具体列表 + 缺失提示
- `_core_fields_filled() -> bool` (line 1401): 按 8 种类型的 CORE_BY_TYPE 检查核心字段
- `_build_notes_from_context() -> str` (line 1327): 从 Schema 生成自然语言实验描述（Python 模板）
  - **被调用**: `ToolExecutor._generate_record()` (line 494: `loop._build_notes_from_context()`); `routes/api_agent.py:53` (外部调用 `agent._build_notes_from_context()`)

#### 模式管理

- `_enter_record_mode()`: `self._schema_context = deepcopy(DEFAULT_CONTEXT)` — 被 `_start_record_thread()` / `_maybe_inject_thread_start()` 调用
- `_exit_record_mode()`: `self._schema_context = None` — 被 `_maybe_inject_thread_end()` / `_check_thread_cancellation()` 调用
- `_get_active_tools() -> list[dict]`: 按当前模式/子Agent角色返回工具子集 — **每轮 LLM 调用前执行**

#### 线程系统 (8 个方法)

- `_build_thread_status() -> str`: 生成 `[系统状态]` 消息 — **每轮 LLM 请求注入** (不入 history)
- `_build_thread_guidance(thread_type) -> dict`: record/analyze 引导消息
- `_maybe_inject_thread_start()`: pending_start flag 触发线程创建。当前实际仅 analyze 线程走此路径（record 线程由 `_start_record_thread` 工具直接处理），但代码包含对 `thread_type == "record"` 的防御性处理（调用 `_enter_record_mode()`）
- `_maybe_inject_thread_end(produced_id)`: 结束线程 + 提取保存 + 重置状态
- `_extract_and_save_thread(produced_id)`: 提取 begin-end 间 messages → 写线程文件 + 更新索引 + 更新用户画像 + refresh L0
- `_check_thread_cancellation(consecutive_no_progress)`: ≥3 → 取消
- `_l0_stale() -> bool`: 距上次生成 > 3600s → True
- `_refresh_l0()`: 重新生成 L0 并替换/插入到 history[0]

#### 子 Agent (classmethod, 2 个)

- `create_child_agent(parent_loop, thread_id) -> AgentLoop` (line 1614): 从父 Agent + 线程文件创建子 Agent。复制 L0 + 线程 messages → `child.is_child=True / agent_role="exp_editor"`
  - **被调用**: `lib/agent_factory.py:59` — `build_child_for_thread()`
- `create_legacy_child_agent(llm_client, store, exp_data, ...) -> AgentLoop` (line 1642): 无线程旧实验子 Agent。注入 EXP JSON 数据 → `child.is_child=True / is_legacy=True / agent_role="exp_editor"`
  - **被调用**: `lib/agent_factory.py:115` — `build_legacy_child()`

#### 调试日志 (4 个方法)

- `_log_llm_request(seq, messages)`: `call_{seq:03d}_request.json` (截断 5000 字符)
- `_log_llm_response(seq, response, reasoning)`: `call_{seq:03d}_response.json` (截断 3000 字符)
- `_log_tool_call(seq, tool_name, raw_args)`: `call_{seq:03d}_tool_{name}_args.json`
- `_log_tool_result(seq, tool_name, result)`: `call_{seq:03d}_tool_{name}_result.json` (截断 10000 字符)
- **均被调用**: `AgentLoop.run()` 主循环中 (lines 1123, 1160, 1192, 1198)

#### 持久化

- `_save_runtime_state()` (line 1732): 每轮结束实时保存。父 Agent → `save_current_state()`；子 Agent → `save_child_state()`；父 Agent 触发 `_maybe_summarize()`
  - **被调用**: `run()` 中全部 return/pause 路径 (lines 1150, 1173, 1242, 1265, 1273)
- `state_to_dict() -> dict` (line 1832): 完整序列化（19 个字段含 context/history/thread/child/modified_values/summary 相关）
- `from_dict(llm_client, store, data, ...) -> AgentLoop` (classmethod, line 1858): 从 dict 恢复。验证磁盘线程是否仍活跃；L0 过期自动刷新；向后兼容旧的空 context（按 None 处理）
  - **被调用**: `lib/agent_factory.py:22,33` — `get_or_create_agent()` 的步骤 1 和 2

#### 上下文窗口管理

- `_estimate_tokens(text: str) -> int` (staticmethod, line 1753): 基于 Unicode 范围估算。CJK≈1.2, ASCII≈0.25, other≈0.8
- `_maybe_summarize()` (line 1776): 新增 > 300K token → LLM 压缩旧消息；保留最近 100K token；失败回退确定性摘要（保留 system 标记 + tool 状态）；追加到已有摘要后面
