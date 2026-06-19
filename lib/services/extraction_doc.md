# lib/services/extraction.py — 说明文档

## 文件作用摘要

自然语言实验记录的结构化提取服务。吸收旧版 `lib/parser.py` 的 `parse_notes()` 和 `strip_html()` 功能。包含 HTML 清洗 + LLM function calling 提取 + 13 条英文提取规则（`EXTRACTION_SYSTEM_PROMPT`）。该 prompt 也是 `lib/parser.py` 的 `SYSTEM_PROMPT` 常量的唯一维护源。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 类

#### `ExtractionService`
- **作用**: 自然语言 → 结构化实验记录的提取服务
- **构造参数**: `extract_llm: Any` — 用于提取的 LLM 客户端实例（通常为 deepseek-v4-flash）
  - **注意**: 在 `app.py:156` 创建时传入 `None`，因为 LLM 实例按需通过 `g.get_extract_llm()` 动态获取。当前代码中 `ExtractionService` 的实例方法 `parse_notes()` 并未被直接调用——提取实际走 `lib/parser.py` 的 `parse_notes()` (runtime import) 路径。此外，`app.py:156` 创建 `ExtractionService(None)` 后将其注入 `g.extraction_svc`，并传入 `AgentLoop(extraction_svc=...)`，但 **`AgentLoop` 内部从未使用 `self.extraction_svc`**——Agent 始终通过 runtime import `from lib.parser import parse_notes` 调用提取功能

##### 方法

- `parse_notes(notes: str) -> dict[str, Any]`:
  - **作用**: 自然语言实验描述 → 结构化 dict（LLM function calling）
  - **输入**: `notes` — 纯文本实验描述
  - **输出**: 17 字段实验 dict，date 默认为当天，original_notes 为原始输入
  - **实现**: `self.extract_llm.structured_extract(prompt, EXTRACTION_SYSTEM_PROMPT, EXPERIMENT_SCHEMA)` → 补全 date/original_notes
  - **被调用情况**: **当前无直接外部调用**。提取功能实际走 `lib/parser.py` 的 `parse_notes()` 路径（runtime import），而非 `ExtractionService.parse_notes()`。注：`app.py:156` 创建 `ExtractionService(None)` 并将其注入 `g.extraction_svc` 并传入 `AgentLoop(extraction_svc=...)` ——但 `AgentLoop` 内部也未使用 `self.extraction_svc`，始终通过 runtime import 调用 `lib.parser.parse_notes()`。此方法为未来统一入口预留

- `strip_html(html_text: str) -> str` (staticmethod):
  - **作用**: 将 Quill 富文本 HTML 清洗为纯文本（4 步正则替换）
  - **被调用情况**: **当前无直接外部调用**。HTML 清洗实际走 `lib/parser.py` 的 `strip_html()` 路径。此方法与 `lib/parser.strip_html` 功能完全相同

### 模块级常量

#### `EXTRACTION_SYSTEM_PROMPT: str`
- **作用**: 结构化提取的 13 条英文规则。与 OpenAI function calling 的 `output_schema` (EXPERIMENT_SCHEMA) 配合使用
- **13 条规则摘要**: 不编造 / ID 占位 / 状态推断 / 受控标签 / 材料精确保留 / SOP 步骤重建 / 参数明确 / 观察异常 / 结论回答 purpose / next_steps 推断 / 中文内容保留 / 样品ID保留 / date 默认当天
- **被调用情况**:
  - `lib/services/extraction.py:29` — 自身的 `parse_notes()` 方法中使用
  - `lib/parser.py:6` — `from lib.services.extraction import EXTRACTION_SYSTEM_PROMPT`, 赋值给 `SYSTEM_PROMPT`，在 `lib/parser.parse_notes()` 中使用
