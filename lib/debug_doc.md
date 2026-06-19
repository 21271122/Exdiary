# lib/debug.py — 说明文档

## 文件作用摘要

Exdiary Agent 调试追踪器模块。提供 `DebugTracer` 类和 `create_debug_tracer()` 工厂函数，按对话 session 组织 LLM 调用日志到 `experiments/_debug/<时间戳>/` 目录下的 Markdown 文件。

> **重要**: 此模块中的 `DebugTracer` 类和 `create_debug_tracer()` 函数**均未被项目中任何其他 Python 文件导入或调用**（全局 Grep 搜索 `lib.debug` 和 `create_debug_tracer` 仅命中此文件自身）。实际的调试日志写入由 `AgentLoop` 类内部的 `_log_llm_request()` / `_log_llm_response()` / `_log_tool_call()` / `_log_tool_result()` 方法完成（写入 JSON 文件到 `call_{seq:03d}_*.json`）。此模块可能在后续清理中移除（参见 ENGINEERING-PLAN.md Task 5.7）。

---

## 代码块详细说明

### 类

#### `DebugTracer`
- **作用**: 按 session 组织调试日志写入，将 LLM 调用、parse 错误、上下文数据、Agent 状态等记录到 Markdown 文件
- **构造参数**: `session_dir: Path` — session 目录路径（如 `experiments/_debug/20260617_143025/`）
- **实例属性**:
  - `self.dir: Path` — 日志输出目录
  - `self.index: int` — 日志序号计数器（每写入一个文件递增 1，初始 0）
  - `self.session_start: str` — session 开始时间字符串

##### 方法

- `_write(filename: str, content: str) -> Path` (私有):
  - **作用**: 写入单个 Markdown 日志文件，index 自增，返回文件路径
  - **输入**: `filename` — 文件名（含序号前缀如 `000_conversation_start.md`）, `content` — 文件内容
  - **输出**: 写入的文件 Path 对象
  - **被调用**: 本类所有 `log_*` 方法内部调用

- `log_conversation_start(user_message: str) -> None`:
  - **作用**: 记录对话开始，写入用户首条消息（截断至 2000 字符）
  - **输出文件**: `{index:03d}_conversation_start.md`

- `log_llm_call(stage: str, system_prompt: str, user_prompt: str, temperature: float, raw_response: str) -> Path`:
  - **作用**: 记录一次完整的 LLM 调用。超过 8000 字符的 system_prompt 保留首尾各 4000 字符；user_prompt 截断至 4000 字符；raw_response 截断至 6000 字符
  - **输出文件**: `{index:03d}_{stage}_call.md`

- `log_parse_error(stage: str, raw_response: str, error: str) -> Path`:
  - **作用**: 记录 JSON 解析失败。raw_response 截断至 6000 字符
  - **输出文件**: `{index:03d}_{stage}_parse_error.md`

- `log_context(label: str, content) -> Path`:
  - **作用**: 记录中间上下文数据。str 直接写入，dict/list 转 JSON 缩进写入。截断至 10000 字符
  - **输出文件**: `{index:03d}_context_{label}.md`

- `log_state(state_dict: dict) -> Path`:
  - **作用**: 记录 AgentState 快照。JSON 序列化后截断至 8000 字符
  - **输出文件**: `{index:03d}_state.md`

**被调用情况**: 无外部调用。`DebugTracer` 类在整个项目代码库中的唯一引用是其自身定义。

### 模块级函数

#### `create_debug_tracer(base_dir: str) -> DebugTracer`
- **作用**: 在 `{base_dir}/_debug/<时间戳>/` 下创建新的调试追踪器
- **输入**: `base_dir: str` — 通常为 `"experiments"`
- **输出**: `DebugTracer` 实例
- **被调用情况**: 无外部调用。与 `DebugTracer` 类一样，在整个项目代码库中没有被任何其他模块 import 或调用。
