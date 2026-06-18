# tests/test_agent_v2.py — 说明文档

## 文件作用摘要

AgentLoop 单元测试 + 模块级函数测试。覆盖 `merge_context` / `_is_filled` / `_brief` / `_build_preview` / `_extract_thread_dialogue` / `_tool_log_summary` 6 个辅助函数，以及 AgentLoop 的核心逻辑（`_core_fields_filled` / `_build_notes_from_context` / `_build_schema_status` / `_build_thread_status` / `mode` / `_get_active_tools` / `_l0_stale` / `_build_thread_guidance` / `state_to_dict`+`from_dict` / `run()` 集成测试）。

---

## 代码块详细说明

### 导入

- `from lib.agent_v2 import (AgentLoop, merge_context, _is_filled, _brief, _fallback_preview, _tool_log_summary, ChildContext, ThreadState)` — 顶层 import 被测函数和类
- `from lib.core.schema import DEFAULT_CONTEXT` — 测试数据基础模板
- `from tests.conftest import (MockLLMClient, make_text_response, make_tool_response, make_agent_loop)` — 测试辅助工具

### 测试类清单

| 测试类 | 测试数 | 覆盖内容 |
|--------|--------|---------|
| `TestMergeContext` | 7 | 简单覆盖 / 列表去重追加 / 空列表清空 / 嵌套dict合并 / 嵌套list追加 / 空dict清空 / 未知key忽略 |
| `TestIsFilled` | 11 | None / 空list / 有值list / 空dict / 含None值dict / 部分有值dict / 空str / 空白str / 有值str / True / False |
| `TestBrief` | 6 | 有值list→"N项" / 空list→"空" / dict→子字段计数 / 短str / 长str截断 / bool(有/空) |
| `TestBuildPreview` | 1 | 全部 18 字段生成 + experimental_plan/results 结构完整性 + references 继承 |
| `TestExtractThreadDialogue` | 1 | 过滤系统消息/工具调用/工具结果，仅保留用户和助手纯文本 |
| `TestToolLogSummary` | 4 | load_reference(loaded_count) / search_experiments(hits) / update_schema(fields) / tool_error |
| `TestAgentLoopCoreFields` | 3 | photocatalysis(purpose/materials/params/results) / hydrothermal(+sop) / other(默认4字段) |
| `TestAgentLoopBuildNotes` | 3 | 完整上下文(含 experimental_plan/tags/status 渲染) / 空上下文 / 部分上下文 |
| `TestAgentLoopSchemaStatus` | 2 | 部分填充状态 / ≥12/16填充→"可考虑结束收集" |
| `TestAgentLoopThreadStatus` | 5 | 自由模式 / record模式 / analyze模式 / exp_editor子Agent / analysis_reviewer子Agent |
| `TestAgentLoopMode` | 3 | 无线程=general / record线程 / analyze线程 |
| `TestAgentLoopActiveTools` | 4 | general工具集 / record工具集 / exp_editor子Agent工具集 / analysis_reviewer子Agent工具集 |
| `TestAgentLoopL0Stale` | 3 | None→过期 / 2h前→过期 / 刚刚→新鲜 |
| `TestAgentLoopThreadGuidance` | 2 | record引导(含"generate_record") / analyze引导(含"generate_analysis") |
| `TestAgentLoopStateRoundTrip` | 2 | 空状态(state_to_dict→from_dict) / 完整状态(含子Agent/线程/context/experiment_type/turn_count) |
| `TestAgentLoopRun` | 4 | 纯文本回复 / 搜索工具→文本回复循环 / LLM ConnectionError兜底(友好错误+history回退+turn_count重算) / 连续3次工具error |
| **总计** | **61** | **16 个测试类** |
