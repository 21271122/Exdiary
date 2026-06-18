# lib/parser.py — 说明文档

## 文件作用摘要

旧版自由文本解析模块。功能已迁入 `ExtractionService` (`lib/services/extraction.py`)，本文件作为 fallback 保留。提供 HTML 清洗 `strip_html()` 和 LLM 结构化提取 `parse_notes()` 两个函数。同时通过 `from lib.services.extraction import EXTRACTION_SYSTEM_PROMPT` 统一了 SYSTEM_PROMPT 的维护源（避免两份独立维护的提取规则）。

被 `lib/agent_v2.py` (runtime import)、`routes/api_agent.py` (runtime import)、`routes/api_experiment.py` (顶层 import)、`routes/experiment.py` (顶层 import) 使用。

---

## 代码块详细说明

### 模块级导入

- `from lib.core.schema import EXPERIMENT_SCHEMA` — JSON Schema（传给 structured_extract 的 output_schema）
- `from lib.services.extraction import EXTRACTION_SYSTEM_PROMPT` — 13 条提取规则（唯一维护源在 extraction.py）

### 模块级常量

#### `SYSTEM_PROMPT: str`
- **作用**: 公共别名，值等于 `EXTRACTION_SYSTEM_PROMPT`。保留此常量用于向后兼容
- **赋值**: `SYSTEM_PROMPT = EXTRACTION_SYSTEM_PROMPT`
- **被调用情况**: 在本模块的 `parse_notes()` 中实际使用的是 `SYSTEM_PROMPT` (line 36: `system_prompt=SYSTEM_PROMPT`)。但本模块的 `SYSTEM_PROMPT` 常量未被其他模块直接 import 使用——其他模块直接 import `EXTRACTION_SYSTEM_PROMPT`

### 模块级函数

#### `strip_html(html_text: str) -> str`
- **作用**: 将 Quill 富文本 HTML 清洗为纯文本，供 AI 提取使用
- **输入**: `html_text` — 含 HTML 标签的富文本
- **输出**: 去除了格式标签后替换为换行的纯文本
- **实现**: 4 步正则替换:
  1. 移除 `<img>` 标签
  2. `</?(p|div|br|li|h\d|tr)[^>]*>` → 替换为 `\n`
  3. `<[^>]+>` → 移除其余 HTML 标签
  4. 压缩 3+ 连续空行为双空行
- **被调用情况**:
  - `routes/api_experiment.py:2` — `from lib.parser import parse_notes, strip_html`, 在 `api_parse()` 中 line 15: `notes_plain = strip_html(notes_raw)`
  - `routes/experiment.py:3` — `from lib.parser import parse_notes, strip_html`, 在 `regenerate_experiment()` 中 line 75: `notes_plain = strip_html(notes_raw)`

#### `parse_notes(notes: str, llm_client) -> dict`
- **作用**: 将自由文本实验描述转换为结构化 dict
- **输入**:
  - `notes: str` — 纯文本实验描述
  - `llm_client` — LLM 客户端实例（必须有 `structured_extract()` 方法）
- **输出**: 17 字段实验 dict（date 默认为当天 `datetime.now().strftime("%Y-%m-%d")`，`original_notes` 为原始输入 .strip()）
- **实现**: 构建英文提取 prompt（含 BEGIN/END NOTES 标记）→ `llm_client.structured_extract(prompt, system_prompt=SYSTEM_PROMPT, output_schema=EXPERIMENT_SCHEMA)` → 补全 date/original_notes
- **被调用情况**:
  - `lib/agent_v2.py` — `ToolExecutor._generate_record()` 中 runtime import。接收增强 prompt（四段式：RAW SCHEMA + DIALOGUE + NOTES + REFERENCES），而非纯 notes 文本
  - `routes/api_experiment.py:2` — `from lib.parser import parse_notes, strip_html`, 在 `api_parse()` 中 line 32: `result = parse_notes(notes_plain, llm)`
  - `routes/experiment.py:3` — `from lib.parser import parse_notes, strip_html`, 在 `regenerate_experiment()` 中 line 86: `result = parse_notes(notes_plain, llm)`
