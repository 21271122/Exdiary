# routes/dashboard.py — 说明文档

## 文件作用摘要

主页与导航路由蓝图 `dashboard_bp`。处理仪表盘、实验列表、新建实验、时间线、多实验对比、收藏页等页面级别的 HTML 渲染请求。

---

## 代码块详细说明

### 路由函数 (全部注册在 `/` 路径前缀下)

- `index()` — GET `/`: 仪表盘主页。
  - 获取置顶优先排序的实验列表 + 最近 8 条实验摘要 JSON → 渲染 `index.html`（Canvas 蒙德里安仪表盘）
  - 通过 `g.exp_repo.list_all()`, `g.favorites_repo.get_pinned()`, `g.exp_repo.list_all_full()[:8]`

- `experiment_list()` — GET `/experiments`: 传统卡片视图的实验列表页。置顶优先排序 → 渲染 `experiments.html`

- `new_experiment()` — GET `/new`: 新建实验表单页（Quill 编辑器 + AI 解析按钮）→ 渲染 `new.html`

- `timeline()` — GET `/timeline`: 实验时间线页。按 date 排序全部实验 → 渲染 `timeline.html`

- `compare_experiments()` — GET `/compare?ids=EXP-001,EXP-002`: 多实验对比页。从 query string 解析 ids（逗号分隔，最多 4 个），分配颜色（蓝/红/绿/紫）→ 渲染 `compare.html`。不足 2 个重定向到首页

- `favorites_page()` — GET `/api/favorites`: 收藏夹页面 → 渲染 `favorites.html`。注意此 URL 以 `/api/` 开头但返回 HTML（非 JSON API）
