# lib/core/prompts.py — 说明文档

## 文件作用摘要

Agent Prompt 模板定义模块。包含完整的 `SYSTEM_PROMPT`（Agent 的行为准则、工具清单、对话模式说明）、跨实验分析的 `ANALYSIS_SYSTEM_PROMPT`、以及动态生成实验类型优先级清单的函数。被 `lib/agent_v2.py`、`lib/analyzer.py`、`lib/services/analysis.py` 导入使用。

---

## 代码块详细说明

### 模块级私有函数

#### `_build_priority_prompt(priority_map: dict[str, Any]) -> str`
- **作用**: 将 PRIORITY_MAP 数据结构格式化为 SYSTEM_PROMPT 中的自然语言段落
- **输入**: `priority_map` — 从 `lib/core/experiment_types.py` 导入的 `PRIORITY_MAP`
- **输出**: 格式化的多行字符串，每行为 `实验类型key: P1 字段1, 字段2\n          P2 字段1, 字段2\n          P3 字段1, 字段2`
- **被调用情况**: 仅在 `build_system_prompt()` 函数内部调用（本文件内 line 190）

### 模块级函数

#### `build_system_prompt() -> str`
- **作用**: 生成完整的 SYSTEM_PROMPT，动态填充 `{priority_list}` 占位符
- **输入**: 无（从 `lib/core/experiment_types.py` 导入 `PRIORITY_MAP`，调用 `_build_priority_prompt(PRIORITY_MAP)`）
- **输出**: 替换了占位符的完整 prompt 字符串（约 200 行）
- **被调用情况**:
  - `lib/agent_v2.py:22` — `from lib.core.prompts import build_system_prompt`, 在 `AgentLoop.run()` 的 line 1102: `{"role": "system", "content": build_system_prompt()}` — 每轮 LLM 调用时构建 messages 列表的头部

### 模块级常量

#### `SYSTEM_PROMPT: str`
- **作用**: Agent 的完整 system prompt 模板（约 185 行，替换 priority_list 后约 212 行）。包含 `{priority_list}` 占位符，由 `build_system_prompt()` 在运行时替换为 PRIORITY_MAP (9 种类型 × 3 级) 的自然语言清单
- **内容结构**:
  1. **对话模式** (3 种): 自由模式（查询/收藏/闲聊 → start_record_thread / start_analyze_thread）/ record 模式（收集信息 → generate_record）/ analyze 模式（搜索 → select → load → discuss → generate_analysis）
  2. **工作方式**: 加载引用(load_reference) → 写入Schema(update_schema) → 追问(ask_user) → 生成(generate_record) 的完整流程；含 4a 规则（用户说"够了"）
  3. **消息格式说明**: `[系统内部]` vs `[系统状态]` 的区别
  4. **工具清单**: 通用工具(8个) / record 专用(4个: start_record_thread, update_schema, ask_user, generate_record) / analyze 专用(3个: start_analyze_thread, select_experiments, generate_analysis)
     - **注意**: `modify_analysis` 工具**不在 SYSTEM_PROMPT 中**，它仅供 analysis_reviewer 子 Agent 角色使用（由 `_get_active_tools()` 按角色注入）
     - **注意**: `ask_user` 在 SYSTEM_PROMPT 中归类为 record 专用工具，但 `AgentLoop._get_active_tools()` 对 analyze 模式也会添加 `TOOL_ASK_USER`——analyze 工作流中确实需要向用户了解分析需求
  5. **实验 Schema**: 16 字段说明 + 受控标签词汇表
  6. **各实验类型关键参数优先级**: 由 `{priority_list}` 占位符动态填充
  7. **矛盾检测**: 写入前比对已有数据与引用数据的规则
  8. **取消与结束线程**: end_thread 调用规则
  9. **事实获取规则**: 3 层优先级（过期标记 > 本轮修改 > disk 加载）。已加载的实验可能在对话中被修改、被历史压缩或跨线程重启后磁盘数据变化，涉及关键决策或修改前应用 `load_reference` 重新加载确认磁盘最新状态，不依赖对话历史数据
  10. **行为准则**: 中文回复、不编造、一次追问≤3 项
- **被调用情况**: 通过 `build_system_prompt()` 间接使用（替换 `{priority_list}` 后传给 Agent）

#### `ANALYSIS_SYSTEM_PROMPT: str`
- **作用**: 跨实验分析的 system prompt（英文），定义材料科学研究顾问角色 + 三区域强制输出格式
- **内容要点**: 角色定位(帮助研究者思考而非替他们思考) / 分析准则(直接回答问题/灵活结构/无内容则省略/具体引用ID) / 三区域强制格式:
  - **事实呈现**: 客观数据摘录，每条标注来源 EXP ID，不做解读
  - **发现提示**: 模式/异常/趋势，每条标注置信度 [高/中/低]，用"数据显示"而非"导致"
  - **值得思考的问题**: 3-5 个引导性疑问句，不嵌入答案，不用"你应该"句式
- **被调用情况**:
  - `lib/analyzer.py:1` — `from lib.core.prompts import ANALYSIS_SYSTEM_PROMPT`, 在 `analyze_experiments()` line 16 中 `llm_client.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt)`
  - `lib/services/analysis.py:7` — `from lib.core.prompts import ANALYSIS_SYSTEM_PROMPT`, 在 `AnalysisService._analyze_experiments()` line 54 中 `self.analyze_llm.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt)`
