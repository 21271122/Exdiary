# routes/templates.py — 说明文档

## 文件作用摘要

实验模板库路由蓝图 `templates_bp`。处理模板列表页（HTML）和模板内容 API（JSON）。

---

## 代码块详细说明

### 路由函数

- `template_library()` — GET `/templates`: 模板库列表页。`g.template_svc.list_all()` → 渲染 `templates.html`
- `api_get_template(template_id)` — GET `/api/templates/<template_id>`: 获取单个模板完整内容 (JSON API)。`g.template_svc.load(template_id)` → 返回 `{ok, title, content}`（content 为带【占位符】的 HTML 富文本，前端加载到 Quill 编辑器）
