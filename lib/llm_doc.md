# lib/llm.py — 说明文档

## 文件作用摘要

LLM 客户端核心模块，封装 OpenAI SDK 与 DeepSeek API 的通信。提供抽象基类 `AbstractLLMClient` + 生产级实现 `LLMClient`（含 3 次指数退避重试，区分 RateLimit/Timeout/Connection/通用 API 错误；支持流式 `chat_stream()` 和非流式 `chat()` 两种调用模式）。包含两个响应数据结构：`LLMResponse`（完整响应）和 `StreamEvent`（流式增量事件）。被 `app.py`、`lib/agent_v2.py`、`lib/services/analysis.py`、`lib/services/extraction.py`、`lib/parser.py`、`lib/analyzer.py` 等模块导入使用。

---

## 代码块详细说明

### 数据类

#### `LLMResponse` (dataclass)
- **作用**: LLM 单次调用的统一响应结构
- **字段**:
  - `content: str` — 文本回复内容（无 content 时为空字符串）
  - `reasoning: str = ""` — DeepSeek reasoning_content（思维链），由 `getattr(msg, "reasoning_content", "")` 获取
  - `tool_calls: list[dict] | None = None` — function calling 返回的工具调用列表，每条格式为 `{id, type: "function", function: {name, arguments}}`
  - `usage: dict[str, int] | None = None` — token 用量统计 `{prompt_tokens, completion_tokens}`
- **被调用情况**:
  - **被实例化**: `LLMClient.chat()` 成功返回时构造并返回
  - **被导入**: `lib/agent_v2.py:12` (`from lib.llm import LLMResponse`) - 在 `AgentLoop.run()` 中使用类型引用
  - **被导入**: `tests/conftest.py:12` (`from lib.llm import AbstractLLMClient, LLMResponse`) - 测试辅助函数 `make_text_response()`、`make_tool_response()`、`make_tool_with_text()` 中实例化
  - **被导入**: `tests/test_agent_v2.py` 通过 conftest 间接使用（MockLLMClient 返回 LLMResponse 序列）

---

### 抽象类

#### `AbstractLLMClient` (ABC)
- **作用**: LLM 客户端的抽象接口，定义统一入口。所有 LLM 调用必须通过此接口，方便单元测试替换为 Mock 实现
- **构造**: ABC 无 `__init__`，不同实现的构造参数差异大（真实客户端需要 api_key/model/base_url，Mock 不需要任何参数）

##### 抽象方法

- `chat(messages, tools=None, temperature=0.3, reasoning_effort=None) -> LLMResponse`:
  - **输入**: `messages: list[dict]` (对话历史), `tools: list[dict] | None` (可用工具定义), `temperature: float` (采样温度), `reasoning_effort: str | None` (DeepSeek 推理强度)
  - **输出**: `LLMResponse`
  - **被实现**: `LLMClient.chat()` (生产), `MockLLMClient.chat()` (测试)
  - **被调用**: `self.structured_extract()` 和 `self.analyze()` 内部通过 `self.chat()` 调用
  - **被直接调用**: `AgentLoop.run()` line 1126 直接调用 `self.llm.chat()`

##### 具体方法（委托给 `self.chat()`）

- `structured_extract(prompt: str, system_prompt: str, output_schema: dict) -> dict`:
  - **输入**: `prompt` (用户消息), `system_prompt` (系统指令), `output_schema` (OpenAI function calling 的 parameters schema)
  - **输出**: 解析 tool_calls 返回的 JSON dict
  - **实现**: 构建 `save_experiment` function 定义 → `self.chat(messages=[system, user], tools=[...])` → 解析 `resp.tool_calls[0]["function"]["arguments"]` 为 dict
  - **异常**: tool_calls 为空时抛出 `RuntimeError("Model did not call the function.")`
  - **被调用**:
    - `lib/services/extraction.py:27` — `ExtractionService.parse_notes()` 中 `self.extract_llm.structured_extract(prompt, EXTRACTION_SYSTEM_PROMPT, EXPERIMENT_SCHEMA)`
    - `lib/parser.py:33` — `parse_notes()` 中 `llm_client.structured_extract(prompt, SYSTEM_PROMPT, EXPERIMENT_SCHEMA)`

- `analyze(system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str`:
  - **输入**: `system_prompt` (系统指令), `user_prompt` (用户消息), `temperature` (采样温度)
  - **输出**: 纯文本分析结果字符串 (`resp.content`)
  - **实现**: `self.chat(messages=[system, user], temperature=temperature)` 不带 tools
  - **被调用**:
    - `lib/services/analysis.py:54` — `AnalysisService._analyze_experiments()` 中 `self.analyze_llm.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt)`
    - `lib/analyzer.py:16` — `analyze_experiments()` 中 `llm_client.analyze(ANALYSIS_SYSTEM_PROMPT, user_prompt)`
    - `lib/agent_v2.py:1800` — `AgentLoop._maybe_summarize()` 中 `self.llm.analyze(system_prompt=..., user_prompt=..., temperature=0.2)`
    - `lib/agent_v2.py:847` — `ToolExecutor._llm_semantic_search()` 中 `loop.llm.analyze(system_prompt=..., user_prompt=..., temperature=0.1)`
    - `lib/agent_v2.py:463` — `ToolExecutor._modify_analysis()` 的 additional_query 分支中 `loop.llm.analyze(system_prompt=..., user_prompt=..., temperature=0.3)`
    - `routes/api_search.py:61` — `api_resolve_reference()` 的 LLM 语义搜索回退中 `llm.analyze(system_prompt=..., user_prompt=..., temperature=0.1)`

---

### 实现类

#### `LLMClient` (AbstractLLMClient)
- **作用**: 基于 OpenAI SDK 的生产级 LLM 客户端实现，通信目标为 DeepSeek API
- **构造参数**: `api_key: str` (DeepSeek API Key), `model: str = "deepseek-v4-pro"`, `base_url: str = "https://api.deepseek.com"`
- **构造行为**: `OpenAI(api_key=..., base_url=..., max_retries=0, timeout=30.0)` — 禁用 SDK 内置重试（`max_retries=0`），全由自定义逻辑接管；连接超时 30 秒
- **实例属性**: `self.client: OpenAI`, `self.model: str`
- **被实例化**: `app.py:126` (`get_extract_llm()`), `app.py:133` (`get_analyze_llm()` — 仅注入到 g，未被实际调用), `app.py:139` (`get_agent_llm()`) — 三个工厂函数各创建一个 LLMClient 实例（通过 flask.g 注入后使用）。注意 `get_analyze_llm()` 工厂虽已定义但实际路由中未使用（`AnalysisService` 构造函数传入 `None`）

##### 方法

- `chat(messages, tools=None, temperature=0.3, reasoning_effort=None) -> LLMResponse`:
  - **输入**: 同 ABC 定义
  - **输出**: `LLMResponse` — 包含 content/reasoning/tool_calls/usage
  - **实现**:
    - 构建 kwargs dict（model/messages/temperature，条件添加 tools 和 reasoning_effort）
    - **3 次重试循环**（attempt=0,1,2），失败均抛异常:
      - `RateLimitError` (429): 优先读响应头 `Retry-After`（至少 5s），无头则用 `_backoff(attempt)` (2s/4s/8s)
      - `APITimeoutError / APIConnectionError`: `_backoff(attempt)` (2s/4s/8s)
      - `APIError` (通用): `_backoff(attempt)` (2s/4s/8s)
    - 成功: 构造 LLMResponse（解析 choices[0].message.content / reasoning_content / tool_calls / usage）
    - **3 次耗尽后**: 抛出最后一个异常（`raise last_exception`），由调用方 `AgentLoop.run()` try-except 兜底
  - **被调用**:
    - `AgentLoop.run():1204` — 直接 `self.llm.chat(messages, tools, temperature, reasoning_effort="max")`
    - 所有通过 `AbstractLLMClient.structured_extract()` 和 `AbstractLLMClient.analyze()` 的间接调用（见上述）

- `chat_stream(messages, tools=None, temperature=0.3, reasoning_effort=None) -> Generator[StreamEvent, None, LLMResponse]`:
  - **作用**: 流式 chat 调用，逐 token 产出 `StreamEvent`，流结束后返回完整 `LLMResponse`
  - **实现**: 设置 `stream=True, stream_options={"include_usage": True}` → 遍历 chunk 流 → 文本增量 → yield `StreamEvent(type="text")`；工具调用增量累积 → yield `StreamEvent(type="tool_call")`；流结束 → yield `StreamEvent(type="done", finished=resp)` → return `LLMResponse`
  - **重试**: 与 `chat()` 相同，3 次重试（RateLimit/Timeout/Connection/APIError），耗尽后抛异常
  - **被调用**: `AgentLoop.run_stream()` — 流式主循环中替代 `self.llm.chat()`
  - **注意**: 此方法不在 `AbstractLLMClient` ABC 中，为 `LLMClient` 独有

---

### 数据类

#### `StreamEvent` (dataclass)
- **作用**: 流式输出中的单个事件
- **字段**:
  - `type: str` — 事件类型: `"text"`（增量文本）、`"tool_call"`（工具调用开始）、`"done"`（流结束）
  - `content: str` — text 事件时的增量文本
  - `tool_name: str` — tool_call 事件时的工具名
  - `tool_args: str` — tool_call 事件时的参数 JSON（增量累积）
  - `finished: LLMResponse | None` — done 事件时携带的完整响应对象
- **被调用情况**: `LLMClient.chat_stream()` 内部 yield 产出；`AgentLoop.run_stream()` 消费

---

### 模块级私有函数

#### `_backoff(attempt: int) -> float`
- **作用**: 计算指数退避等待时间
- **输入**: `attempt` — 当前重试次数（0-based，取值 0/1/2）
- **输出**: 等待秒数 — `2.0 ** (attempt + 1)`, 即 2s / 4s / 8s
- **被调用情况**: 仅在 `LLMClient.chat()` 内部使用 — `RateLimitError` 无 Retry-After 头时、`APITimeoutError`、`APIConnectionError`、`APIError` 三个错误处理分支均调用此函数

#### `_parse_retry_after(exc: RateLimitError) -> float`
- **作用**: 从 429 响应头中解析 `Retry-After` 值
- **输入**: `exc: RateLimitError`
- **输出**: 解析出的等待秒数 (float)；解析失败或不存在时返回 `0.0`
- **实现**: 尝试 `exc.response.headers.get("Retry-After")` → `float(val)`；任何异常都静默返回 0.0
- **被调用情况**: 仅在 `LLMClient.chat()` 的 `RateLimitError` 异常处理分支中调用（line 135: `retry_after = _parse_retry_after(e)`）
