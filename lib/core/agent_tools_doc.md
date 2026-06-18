# lib/core/agent_tools.py — 说明文档

## 文件作用摘要

Agent 16 个工具函数的 JSON Schema 定义模块（OpenAI function calling 格式）。纯数据定义，不含执行逻辑。从 `lib/agent_v2.py` 迁出以减小单文件尺寸。被 `lib/agent_v2.py` 导入使用。

---

## 代码块详细说明

### 16 个工具定义常量

每个常量是一个 `{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}` 结构的 dict。

#### 父 Agent 通用工具 (general/record/analyze 三种父模式均可用)

- `TOOL_LOAD_REFERENCE` — 加载引用实验的完整数据。参数: `refs: array[string](required)`
- `TOOL_SEARCH_EXPERIMENTS` — 语义搜索历史实验。参数: `query: string(required)`
- `TOOL_QUERY_EXPERIMENT` — 回答实验参数查询。参数: `question: string(required)`, `refs: array[string](required)`
- `TOOL_LIST_EXPERIMENTS` — 按条件筛选实验列表。参数: `status: string(enum)`, `tags: array[string]`, `experimenter: string`, `since: string` (全部可选)
- `TOOL_MANAGE_COLLECTION` — 管理收藏和置顶。参数: `action: string(enum: pin/unpin/favorite/unfavorite)(required)`, `refs: array[string](required)`, `collection: string`
- `TOOL_READ_UPDATE_LOG` — 读取实验更新日志。参数: `exp_id: string(required)`, `since: string`(可选), `limit: integer`(可选)
- `TOOL_END_THREAD` — 结束当前线程。无参数

#### 父 Agent 按模式追加的工具

- **general 模式** 加: `TOOL_START_RECORD_THREAD`, `TOOL_START_ANALYZE_THREAD`, `TOOL_MODIFY_EXPERIMENT`
- **record 模式** 加: `TOOL_START_RECORD_THREAD`, `TOOL_UPDATE_SCHEMA`, `TOOL_ASK_USER`, `TOOL_GENERATE_RECORD`, `TOOL_MODIFY_EXPERIMENT`
- **analyze 模式** 加: `TOOL_START_ANALYZE_THREAD`, `TOOL_SELECT_EXPERIMENTS`, `TOOL_ASK_USER`, `TOOL_GENERATE_ANALYSIS`
  - **注意**: analyze 模式**不含** `TOOL_MODIFY_EXPERIMENT`（分析者不应修改实验），也**不含** `TOOL_MODIFY_ANALYSIS`

#### 子 Agent 角色专用工具

- **exp_editor** 角色: `TOOL_LOAD_REFERENCE`, `TOOL_SEARCH_EXPERIMENTS`, `TOOL_QUERY_EXPERIMENT`, `TOOL_LIST_EXPERIMENTS`, `TOOL_READ_UPDATE_LOG`, `TOOL_MODIFY_EXPERIMENT`, `TOOL_END_THREAD`
- **analysis_reviewer** 角色: `TOOL_LOAD_REFERENCE`, `TOOL_SEARCH_EXPERIMENTS`, `TOOL_QUERY_EXPERIMENT`, `TOOL_LIST_EXPERIMENTS`, `TOOL_READ_UPDATE_LOG`, `TOOL_MODIFY_ANALYSIS`, `TOOL_END_THREAD`

#### 各工具定义

- `TOOL_START_RECORD_THREAD` — 开启实验记录线程。无参数
- `TOOL_UPDATE_SCHEMA` — 增量写入 Schema。参数: `fields: object` (16 个子字段均可选, 空数组/空对象=清空), `round_summary: string`(可选)。description 中包含数组清空重写指引（先传 `[]` 再传完整列表实现插入修正）和嵌套对象完整性要求
- `TOOL_ASK_USER` — 向用户提问。参数: `questions: array[string](required, maxItems=3)`
- `TOOL_GENERATE_RECORD` — 生成实验记录草稿。无参数
- `TOOL_START_ANALYZE_THREAD` — 开启跨实验分析线程。无参数
- `TOOL_SELECT_EXPERIMENTS` — 展示实验选择面板。参数: `candidates: array[object](required)`, `preselected: array[string]`(可选), `title: string`(可选)
- `TOOL_GENERATE_ANALYSIS` — 执行分析并归档。参数: `query: string(required)`, `refs: array[string](required, 至少2个)`
- `TOOL_MODIFY_EXPERIMENT` — 修改实验字段。参数: `refs: array[string](required)`, `changes: object`(可选), `description: string`(可选——注意: `_modify_experiment()` 处理器实际只使用 `changes`，`description` 参数当前未被使用)
- `TOOL_MODIFY_ANALYSIS` — 修改分析报告。参数: `changes: string`, `additional_refs: array[string]`, `additional_query: string`（全部可选，处理器中按 changes→additional_refs→additional_query 优先级三选一）

### `TOOLS_OPENAI_FORMAT: list[dict]`
- **作用**: 上述 16 个工具定义组成的列表，用于传给 OpenAI API 的 `tools` 参数，也用于 `ToolExecutor._tool_schema()` 按 name 查找单个工具的 parameters schema
- **被调用情况**:
  - `lib/agent_v2.py:14-21` — `from lib.core.agent_tools import (TOOL_LOAD_REFERENCE, TOOL_SEARCH_EXPERIMENTS, ..., TOOLS_OPENAI_FORMAT)` — 导入全部 16 个工具定义 + TOOLS_OPENAI_FORMAT 列表
  - `lib/agent_v2.py` 中的使用:
    - `ToolExecutor._tool_schema()` (line 241-246): 遍历 `TOOLS_OPENAI_FORMAT` 按 name 查找单个 toolschema
    - `AgentLoop._get_active_tools()` (line 1046-1078): 从各个独立常量中按模式/角色动态组合工具子集（不直接使用 TOOLS_OPENAI_FORMAT 列表）
    - `AgentLoop.run()` (line 1128): `tools=self._get_active_tools()` → `self.llm.chat(tools=...)`
