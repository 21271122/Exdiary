# routes/experiment.py — 说明文档

## 文件作用摘要

实验记录页面与操作路由蓝图 `experiment_bp`，URL 前缀 `/experiments`。处理实验详情查看、YAML 原始查看/编辑、删除、JSON 保存、重新生成（re-parse）、打印视图。

---

## 代码块详细说明

### 路由函数

- `view_experiment(exp_id)` — GET `/experiments/<exp_id>`: 实验详情页。`g.exp_repo.load(exp_id)` → 渲染 `view.html`（含内联编辑 + 子 Agent 对话入口）

- `view_yaml(exp_id)` — GET `/experiments/<exp_id>/yaml`: YAML 原始文本查看。以 `text/plain; charset=utf-8` 返回 yaml.dump 结果

- `edit_experiment(exp_id)` — GET+POST `/experiments/<exp_id>/edit`:
  - GET: 加载实验 → yaml.dump → 渲染 `edit.html`（YAML 文本编辑器）
  - POST: `yaml.safe_load` 解析用户提交的 YAML → `g.exp_repo.update()` 保存 → `g.experiment_svc.save_with_log()` 写日志 → 重定向到详情页

- `delete_experiment(exp_id)` — DELETE `/experiments/<exp_id>/delete`: `g.experiment_svc.delete_with_log()` → 返回 200

- `save_experiment_json(exp_id)` — POST `/experiments/<exp_id>/save-json`: 前端 JS 提交 JSON 数据。`g.experiment_svc.save_and_update_refs()` 一站式保存 + 引用处理

- `regenerate_experiment(exp_id)` — POST `/experiments/<exp_id>/regenerate`: 用户修改 original_notes 文本后重新调 LLM 解析 (strip_html → parse_notes) → 更新实验 → 返回 JSON

- `print_experiment(exp_id)` — GET `/experiments/<exp_id>/print`: 实验打印视图 → 渲染 `print.html`
