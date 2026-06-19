# routes/pages.py — 说明文档

## 文件作用摘要

分析相关页面路由蓝图 `pages_bp`。处理分析主页和分析详情页的 HTML 渲染。

---

## 代码块详细说明

### 路由函数

- `analyze_page()` — GET `/analyze`: 分析页面入口 → 渲染 `analyze.html`（对话式跨实验分析界面）
- `view_analysis(anal_id)` — GET `/analysis/<anal_id>`: 分析报告详情页。`g.analysis_repo.load(anal_id)` 加载 → 渲染 `analysis_detail.html`（含 Markdown 渲染 + 审阅子 Agent 入口）
