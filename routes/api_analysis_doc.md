# routes/api_analysis.py — 说明文档

## 文件作用摘要

分析历史 API 蓝图 `api_analysis_bp`，URL 前缀 `/api`。提供分析报告的列表、详情查看和删除。纯 JSON REST API。

---

## 代码块详细说明

### 路由函数

- `api_analysis_history()` — GET `/api/analysis-history`: 列出全部分析报告
  - 返回: `g.analysis_repo.list_all()` JSON 数组

- `api_analysis_detail(anal_id)` — GET `/api/analysis-history/<anal_id>`: 单个分析报告详情
  - 返回: `{ok: True, data: {...}}` 或 404

- `api_analysis_delete(anal_id)` — DELETE `/api/analysis-history/<anal_id>`: 删除分析报告
  - 返回: `{ok: True/False}`
