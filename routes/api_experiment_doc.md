# routes/api_experiment.py — 说明文档

## 文件作用摘要

实验解析与保存 API 蓝图 `api_experiment_bp`，URL 前缀 `/api`。处理传统自然语言解析（`/api/parse`）和解析结果确认（`/api/parse/confirm`）。支持 JSON 和 Form 两种请求格式。

---

## 代码块详细说明

### 导入

- `from lib.parser import parse_notes, strip_html` — 顶层 import, 使用旧版解析模块

### 路由函数

- `api_parse()` — POST `/api/parse`: 传统自然语言 → AI 结构化提取
  - 请求: Form (`request.form.get("notes")`) 或 JSON (`request.json.get("notes")`)
  - 自动判断返回格式: `request.is_json or Accept: application/json`
  - 流程: HTML 清洗(`strip_html`) → 长度校验(≥10) → `g.get_extract_llm()` → `parse_notes(notes_plain, llm)` → JSON 返回提取结果 / Form 返回重定向
  - 返回: JSON `{ok, data}` / 或 Form 重定向到详情页

- `api_parse_confirm()` — POST `/api/parse/confirm`: 确认并保存解析后的实验数据
  - 请求体: JSON `{id, ...字段, original_notes}`
  - 流程: `g.experiment_svc.extract_references()` → `g.exp_repo.save()` → `g.experiment_svc.update_referenced_by()` → `g.experiment_svc.move_draft_images()`
  - **同时被**: `routes/api_agent.py:api_agent_confirm()` 直接委托给此函数
