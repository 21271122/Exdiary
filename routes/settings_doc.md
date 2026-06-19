# routes/settings.py — 说明文档

## 文件作用摘要

设置页面路由蓝图 `settings_bp`。处理设置页的查看（GET）和保存（POST）。

---

## 代码块详细说明

### 路由函数

- `settings_page()` — GET+POST `/settings`:
  - GET: 从 `g.config` 获取当前配置，API Key 遮罩处理（`key[:4] + "****" + key[-4:]`），渲染 `settings.html`
  - POST: 从表单获取 6 个字段 → `from app import save_settings; save_settings(data)` 写入 `config.yaml` → 渲染成功页面
  - POST 通过完整页面刷新应用新配置（非 AJAX）
  - 提示: 端口和 GUI 修改需重启生效
