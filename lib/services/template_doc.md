# lib/services/template.py — 说明文档

## 文件作用摘要

实验模板管理服务。从 `app.py` 的 `TemplateStore` 迁出。提供模板列表、加载、自动种子功能。内置 8 个材料科学实验模板（HTML 富文本含【占位符】），首次启动时自动种子到 `experiment_templates/` 目录。被 `app.py` 创建并注入到 `flask.g`。

---

## 代码块详细说明

### 类

#### `TemplateService`
- **构造参数**: `templates_dir: str` — 模板文件目录路径（`experiment_templates/`）
- **构造行为**: `mkdir` + 自动调用 `_seed_builtin()` — 仅在目录中无任何 `*.yaml` 文件时才写入内置模板
- **被注入**: `app.py:158` → `g.template_svc`

##### 方法

- `list_all() -> list[dict]`: 列出全部模板的摘要信息（id/title/category/description/tags）。扫描 `*.yaml` 文件
  - **被调用**: `routes/templates.py:8` — `template_library()` 渲染模板库页面

- `load(template_id: str) -> dict | None`: 加载指定模板的完整数据（含 `content` 字段 — 带 HTML 占位符的实验描述模板）
  - **被调用**: `routes/templates.py:14` — `api_get_template()` JSON API，前端选中模板后获取 HTML 内容填充到 Quill 编辑器

- `_seed_builtin() -> None`: 将 `_BUILTIN_TEMPLATES` 中的 8 个模板写入 `*.yaml` 文件。仅在目录为空时执行

### 模块级常量

#### `_BUILTIN_TEMPLATES: list[dict]`
- **作用**: 8 个内置实验模板的数据定义。每个模板含 id/title/category/description/tags/content(HTML 富文本含【】占位符)
- **8 个模板**: photocatalysis / hydrothermal / sol-gel / spin-coating / ball-milling / electrochemistry / xrd / perovskite-solar
- **被调用**: 仅在 `TemplateService._seed_builtin()` 内部使用
