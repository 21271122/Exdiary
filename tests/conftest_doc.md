# tests/conftest.py — 说明文档

## 文件作用摘要

pytest 共享 fixtures 和测试辅助函数。提供 Mock LLM 客户端 (`MockLLMClient`)、LLMResponse 工厂函数 (3个)、临时 Repository fixtures (5个)、样本实验数据 fixtures (2个)、以及 AgentLoop 构造辅助函数 `make_agent_loop()`。

---

## 代码块详细说明

### 类

#### `MockLLMClient` (AbstractLLMClient)
- **作用**: 返回预设 LLMResponse 序列的 Mock 客户端，隔离 LLM 依赖进行单元测试
- **继承**: `AbstractLLMClient` → 只需要实现 `chat()` 方法
- **方法**:
  - `set_responses(*responses: LLMResponse) -> None`: 设置预设响应序列，重置 call_count=0
  - `chat(messages, tools=None, temperature=0.3, reasoning_effort=None) -> LLMResponse`: 按调用顺序返回预设响应，超出预设数量抛 `RuntimeError`
- **被调用**: `make_agent_loop()` 中作为默认 LLM；`tests/test_agent_v2.py` 中所有 TestAgentLoopRun 测试

### LLMResponse 工厂函数

- `make_text_response(content: str) -> LLMResponse`: 纯文本（无 tool_calls），content 非空
- `make_tool_response(tool_name: str, arguments: dict) -> LLMResponse`: 只含 tool_calls（content=""），自动生成 `call_mock_{tool_name}` 的 tool_call_id
- `make_tool_with_text(text: str, tool_name: str, arguments: dict) -> LLMResponse`: 同时含文本和 tool_calls
- **被调用**: `tests/test_agent_v2.py` 中 TestAgentLoopRun 的 4 个测试用例

### pytest fixtures

- `tmp_exp_repo`, `tmp_analysis_repo`, `tmp_thread_repo`, `tmp_favorites_repo`, `tmp_update_log_repo` — 基于 `tempfile.TemporaryDirectory` 的临时仓储（yield 模式，测试后自动清理）
- `sample_exp_data()` — 标准 TiO2 光催化实验 dict（完整 17 字段含 materials/equipment/sop/params/observations/characterization/results）
- `sample_exp_data_list()` — 3 条实验数据列表（TiO2/ZnO/钙钛矿），用于搜索/过滤测试

### 辅助函数 (非 pytest fixture)

#### `make_agent_loop(llm=None, tool_executor=None, exp_repo=None, thread_repo=None, update_log_repo=None, favorites_repo=None, analysis_repo=None) -> AgentLoop`
- **作用**: 构造 AgentLoop 实例用于测试。所有 repo 默认 None 时自动创建临时版本；llm 默认 MockLLMClient
- **注意**: 这是普通函数而非 pytest fixture（避免 fixture 参数化问题），各测试在函数内部调用
- **被调用**: `tests/test_agent_v2.py` 中几乎全部测试类（TestMergeContext 除外，它不依赖 AgentLoop）
