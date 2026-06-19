# routes/api_search.py — 说明文档

## 文件作用摘要

实验搜索与引用解析 API 蓝图 `api_search_bp`，URL 前缀 `/api`。提供全文搜索和引用解析两个端点。搜索流程包含三层：精确 ID 匹配 → 关键词打分 → LLM 语义搜索回退。

---

## 代码块详细说明

### 路由函数

- `api_experiments_search()` — GET `/api/experiments/search`: 返回全部实验的完整数据
  - 返回: `g.exp_repo.list_all_full()` 的 JSON 数组
  - 注意: 此端点不执行搜索过滤，真正的搜索在 `api_resolve_reference()` 中

- `api_resolve_reference()` — POST `/api/resolve-reference`: 实验引用解析
  - 请求体: `{text: str}`
  - **三层搜索**:
    1. **精确 ID**: text 匹配 `EXP-YYYY-NNN` 格式 → `g.exp_repo.load()` 返回 score=1.0
    2. **关键词**: 在 title/tags/purpose/materials 中分词打分（英文按空格切分，tag 匹配额外加权 0.3）
    3. **LLM 语义**: 关键词 top1 score < 0.3 且非 EXP ID 格式 → `g.get_extract_llm().analyze()` 做语义匹配
  - 返回: `{ok, results: [{id, title, date, tags, score}]}` (最多 5 条)
  - 注意: API 路由中的关键词搜索实现与 `ToolExecutor._fuzzy_search()` 独立维护（存在功能重叠）
