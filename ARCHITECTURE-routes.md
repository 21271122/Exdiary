# Exdiary 路由层与应用工厂

## 1. 应用工厂：`app.py`（230 行）

### 1.1 模块级常量与辅助变量

| 变量/函数 | 用途 |
|---|---|
| `BASE_DIR` | `Path(__file__).parent`——项目根目录 |
| `SETTINGS_PATH` | `BASE_DIR / "config.yaml"` |
| `DEFAULT_SETTINGS` | 默认配置字典：6 个键（API_KEY、MODEL、ANALYZE_MODEL、PORT、HOST、GUI） |
| `config` | 模块级字典，导入时通过 `load_settings()` 加载 |

### 1.2 配置函数

#### `_parse_dotenv(path: Path) -> dict`
逐行读取旧 `.env` 文件，解析 `KEY=VALUE` 对（跳过空行/注释，去除引号），返回字典。仅用于首次迁移。

#### `load_settings() -> dict`
- 若 `config.yaml` 存在：`yaml.safe_load` 读取，不存在时返回 `DEFAULT_SETTINGS`
- 若不存在：调用 `_parse_dotenv` 从 `.env` 迁移，合并到 `DEFAULT_SETTINGS`，写入 `config.yaml`

#### `save_settings(data: dict) -> None`
过滤保留 `DEFAULT_SETTINGS` 中的键，写入 `SETTINGS_PATH`（`allow_unicode=True`），更新模块级 `config`。

### 1.3 LLM 工厂函数（模块级）

| 函数 | 使用模型 | 用途 | 调用方 |
|---|---|---|---|
| `get_extract_llm()` | `DEEPSEEK_MODEL`（flash） | 自然语言→结构化实验提取 | `routes/api_experiment.py`、`routes/api_agent.py`、`routes/api_search.py`、`routes/experiment.py` |
| `get_analyze_llm()` | `DEEPSEEK_ANALYZE_MODEL`（pro） | 跨实验分析 | 注入 `AnalysisService` |
| `get_agent_llm()` | `DEEPSEEK_MODEL`（flash） | Agent 对话 | `routes/api_agent.py`、`routes/api_child.py` |

API Key 为空时三者均返回 `None`。

### 1.4 `create_app()` —— Flask 应用工厂

**第一步——仓储层**：5 个 Store 实例（通过 `lib/storage.py` 兼容层创建）。

| 变量 | 类 | 存储路径 |
|---|---|---|
| `exp_repo` | `ExperimentStore` | `BASE_DIR/"experiments"` |
| `analysis_repo` | `AnalysisStore` | `BASE_DIR/"experiments"/"_analysis_history"` |
| `thread_repo` | `ThreadStore` | `BASE_DIR/"experiments"/"_threads"` |
| `favorites_repo` | `FavoritesStore` | `BASE_DIR/"experiments"/"_favorites.yaml"` |
| `update_log_repo` | `UpdateLogStore` | `BASE_DIR/"experiments"/"_update_logs"` |

**第二步——服务层**：5 个 Service 实例。

| 变量 | 构造参数 |
|---|---|
| `experiment_svc` | `ExperimentService(exp_repo, update_log_repo, favorites_repo, BASE_DIR)` |
| `extraction_svc` | `ExtractionService(None)` —— LLM 在调用时注入 |
| `analysis_svc` | `AnalysisService(exp_repo, analysis_repo, None)` —— LLM 在调用时注入 |
| `template_svc` | `TemplateService(BASE_DIR/"experiment_templates")` |
| `agent_svc` | `AgentService(llm=None, exp_repo, thread_repo, update_log_repo, favorites_repo, analysis_repo, extraction_svc, experiment_svc, analysis_svc)` |

**第三步——`@app.before_request` 注入**：所有仓储、服务、`BASE_DIR`、三个 LLM 工厂函数注入 `flask.g`。

**第四步——蓝图注册**：

| 蓝图变量 | url_prefix | 文件 |
|---|---|---|
| `dashboard_bp` | `/` | `routes/dashboard.py` |
| `experiment_bp` | `/experiments` | `routes/experiment.py` |
| `pages_bp` | `/` | `routes/pages.py` |
| `settings_bp` | `/` | `routes/settings.py` |
| `templates_bp` | `/` | `routes/templates.py` |
| `uploads_bp` | `/` | `routes/uploads.py` |
| `api_experiment_bp` | `/api` | `routes/api_experiment.py` |
| `api_agent_bp` | `/api/agent` | `routes/api_agent.py` |
| `api_child_bp` | `/api` | `routes/api_child.py` |
| `api_analysis_bp` | `/api` | `routes/api_analysis.py` |
| `api_search_bp` | `/api` | `routes/api_search.py` |
| `api_favorites_bp` | `/api` | `routes/api_favorites.py` |
| `api_upload_bp` | `/api` | `routes/api_upload.py` |

返回 `(app, config)` 元组。

### 1.5 `__main__` 启动流程

1. 调用 `create_app()` 获取 `app` 和 `config`
2. 读取 `PORT`（默认 5000）、`HOST`（默认 0.0.0.0）、`GUI`（默认 true）
3. 日志记录启动信息
4. Windows 下修复 `sys.stdout` 编码为 UTF-8
5. **检测 LAN IP**：UDP 套接字连接 `8.8.8.8:80`，读取 `getsockname()[0]`
6. 打印启动横幅（Local URL、Network URL、模型名）
7. **模式选择**：
   - `--headless` 或 `GUI != "true"`：直接 `app.run(host, port, debug=True)`
   - 否则尝试 `import webview`：
     - 未安装：降级为 Web 模式
     - 已安装：Flask 后台守护线程（`debug=False`）+ pywebview 原生窗口（1100×750）

---

## 2. 路由文件详细文档

### 2.1 `routes/dashboard.py` — 首页与列表路由

**蓝图**：`dashboard_bp`（名称 `"dashboard"`，无 url_prefix）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/` | `index()` | 首页仪表盘：列出实验（置顶优先），生成 Canvas 瓦片数据 | `g.exp_repo`、`g.favorites_repo` | `index.html` |
| GET | `/experiments` | `experiment_list()` | 实验卡片列表，置顶优先 | `g.exp_repo`、`g.favorites_repo` | `experiments.html` |
| GET | `/new` | `new_experiment()` | 新建实验表单页 | 无 | `new.html` |
| GET | `/timeline` | `timeline()` | 按日期排序的实验时间线 | `g.exp_repo` | `timeline.html` |
| GET | `/compare` | `compare_experiments()` | 对比 2-4 个实验（从 `?ids=` 读取），分配蓝/红/绿/紫色 | `g.exp_repo` | `compare.html` |
| GET | `/api/favorites` | `favorites_page()` | 收藏夹页面 | `g.favorites_repo` | `favorites.html` |

---

### 2.2 `routes/experiment.py` — 实验详情与编辑路由

**蓝图**：`experiment_bp`（名称 `"experiment"`，url_prefix `/experiments`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/<exp_id>` | `view_experiment(exp_id)` | 查看单个实验 | `g.exp_repo` | `view.html` 或 404 |
| GET | `/<exp_id>/yaml` | `view_yaml(exp_id)` | 实验原始 YAML 导出 | `g.exp_repo` | `text/plain; charset=utf-8` |
| GET/POST | `/<exp_id>/edit` | `edit_experiment(exp_id)` | GET=编辑表单，POST=提交 YAML 编辑、更新并写日志 | `g.exp_repo`、`g.experiment_svc` | `edit.html` 或重定向 |
| DELETE | `/<exp_id>/delete` | `delete_experiment(exp_id)` | 删除实验（含日志） | `g.experiment_svc` | 200 空 |
| POST | `/<exp_id>/save-json` | `save_experiment_json(exp_id)` | 从 JSON 保存，处理引用更新 | `g.exp_repo`、`g.experiment_svc` | `{"ok": true}` 或 400 |
| POST | `/<exp_id>/regenerate` | `regenerate_experiment(exp_id)` | LLM 重新解析原始笔记并原地更新 | `g.exp_repo`、`g.get_extract_llm`、`g.experiment_svc` | `{"ok": true}` 或错误 |
| GET | `/<exp_id>/print` | `print_experiment(exp_id)` | 打印友好视图 | `g.exp_repo` | `print.html` 或 404 |

---

### 2.3 `routes/api_experiment.py` — 解析 API

**蓝图**：`api_experiment_bp`（名称 `"api_experiment"`，url_prefix `/api`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| POST | `/parse` | `api_parse()` | LLM 解析自然语言笔记；支持表单和 JSON 输入；表单 POST 直接保存并重定向 | `g.get_extract_llm`、`g.exp_repo`、`g.experiment_svc` | JSON 或 `new.html` 错误或重定向 |
| POST | `/parse/confirm` | `api_parse_confirm()` | 确认并保存解析结果（JSON body），提取引用，迁移草稿图片 | `g.exp_repo`、`g.experiment_svc` | `{"ok": true, "exp_id": "..."}` |

---

### 2.4 `routes/api_agent.py` — 父 Agent API

**蓝图**：`api_agent_bp`（名称 `"api_agent"`，url_prefix `/api/agent`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| POST | `/start` | `api_agent_start()` | 启动新 Agent 或从 `_current_state.yaml` 恢复；空消息触发问候语 | 全部仓储 + LLM 工厂 + `g.analysis_svc`、`g.extraction_svc` | `{"ok": true, "state": ..., "type": ..., "message": ..., "greeting": ...}` |
| POST | `/message` | `api_agent_message()` | 发送消息给 Agent；若返回 `extract`/`generate` 类型，自动保存实验 | 同上 | `{"ok": true, "state": ..., "type": ..., "message": ...}` 或 `{"ok": true, "type": "saved", "exp_id": ...}` |
| POST | `/confirm` | `api_agent_confirm()` | 委托给 `api_parse_confirm()` | 同 `/parse/confirm` | `{"ok": true, "exp_id": "..."}` |

辅助函数：
- `_build_notes_from_context(context)` —— 从 Agent 上下文字典重建纯文本笔记
- `_extract_or_fallback(notes, context, agent)` —— 尝试 LLM 解析，失败则从上下文字段构造最小实验数据

---

### 2.5 `routes/api_child.py` — 子 Agent API

**蓝图**：`api_child_bp`（名称 `"api_child"`，url_prefix `/api`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| POST | `/analysis/<anal_id>/chat` | `api_analysis_chat(anal_id)` | 与分析报告关联的子 Agent 对话；处理旧记录迁移、状态恢复、新 Agent 创建（`analysis_reviewer` 角色） | 全部仓储 + LLM 工厂 | JSON：`state`、`type`、`message`、`is_legacy`、`anal_data` |
| POST | `/exp/<exp_id>/chat` | `api_exp_chat(exp_id)` | 与实验关联的子 Agent 对话；处理状态恢复、旧实验 Agent 创建、新子 Agent 创建（注入修改模式指令） | 同上 | JSON：`state`、`type`、`message`、`preview`、`is_legacy`、`exp_data` |
| POST | `/exp/<exp_id>/confirm` | `api_exp_confirm(exp_id)` | 确认保存子 Agent 修改；提取引用、保存、写日志、更新 `referenced_by` | `g.exp_repo`、`g.experiment_svc` | `{"ok": true, "exp_id": "..."}` |

辅助函数：
- `_migrate_legacy_analysis(anal_id, analysis_data)` —— 为无线程的旧分析记录创建线程
- `_make_analysis_chat_response(agent, result, thread_id)` —— 构造分析聊天的 JSON 响应并保存子状态
- `_make_chat_response(agent, result, thread_id)` —— 构造实验聊天的 JSON 响应，`extract`/`generate` 时含预览
- `_create_analysis_child_agent(llm_client, thread, anal_id)` —— 构造配置为分析审阅的 AgentLoop

---

### 2.6 `routes/api_analysis.py` — 分析历史 API

**蓝图**：`api_analysis_bp`（名称 `"api_analysis"`，url_prefix `/api`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/analysis-history` | `api_analysis_history()` | 列出全部分析历史 | `g.analysis_repo` | JSON 数组 |
| GET | `/analysis-history/<anal_id>` | `api_analysis_detail(anal_id)` | 获取单条分析记录 | `g.analysis_repo` | `{"ok": true, "data": ...}` 或 404 |
| DELETE | `/analysis-history/<anal_id>` | `api_analysis_delete(anal_id)` | 删除分析记录 | `g.analysis_repo` | `{"ok": true/false}` |

---

### 2.7 `routes/api_search.py` — 搜索 API

**蓝图**：`api_search_bp`（名称 `"api_search"`，url_prefix `/api`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/experiments/search` | `api_experiments_search()` | 返回全部实验（完整数据）供客户端筛选 | `g.exp_repo` | JSON 数组 |
| POST | `/resolve-reference` | `api_resolve_reference()` | 智能引用解析：先精确匹配 EXP-ID，再关键词评分（标题/标签/目的/材料），结果差时回退 LLM 语义搜索 | `g.exp_repo`、`g.get_extract_llm` | `{"ok": true, "results": [...]}` 最多 5 条 |

---

### 2.8 `routes/api_favorites.py` — 收藏夹 API

**蓝图**：`api_favorites_bp`（名称 `"api_favorites"`，url_prefix `/api`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| POST | `/experiments/<exp_id>/pin` | `api_toggle_pin(exp_id)` | 切换置顶状态 | `g.favorites_repo` | JSON |
| POST | `/experiments/<exp_id>/favorite` | `api_toggle_favorite(exp_id)` | 切换收藏状态（默认收藏夹） | `g.favorites_repo` | JSON |
| GET | `/list-collections` | `api_list_collections()` | 列出全部收藏夹 | `g.favorites_repo` | JSON |
| POST | `/collections` | `api_create_collection()` | 创建新收藏夹 | `g.favorites_repo` | JSON |
| DELETE | `/collections/<name>` | `api_delete_collection(name)` | 删除收藏夹 | `g.favorites_repo` | JSON |

---

### 2.9 `routes/api_upload.py` — 文件上传 API

**蓝图**：`api_upload_bp`（名称 `"api_upload"`，url_prefix `/api`）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| POST | `/upload-image` | `api_upload_image()` | 上传图片到 `BASE_DIR/uploads/<exp_id>/<uuid><ext>`；允许 png/jpg/jpeg/gif/webp/bmp | `g.base_dir` | `{"ok": true, "url": "/uploads/..."}` |

---

### 2.10 `routes/settings.py` — 设置路由

**蓝图**：`settings_bp`（名称 `"settings"`，无 url_prefix）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET/POST | `/settings` | `settings_page()` | GET=显示带掩码 API Key 的设置表单；POST=保存设置（调用 `app.save_settings()`） | `g.config` | `settings.html` |

---

### 2.11 `routes/templates.py` — 模板路由

**蓝图**：`templates_bp`（名称 `"templates"`，无 url_prefix）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/templates` | `template_library()` | 实验模板列表 | `g.template_svc` | `templates.html` |
| GET | `/api/templates/<template_id>` | `api_get_template(template_id)` | 获取单个模板的标题和内容 | `g.template_svc` | `{"ok": true, "title": ..., "content": ...}` 或 404 |

---

### 2.12 `routes/uploads.py` — 文件服务路由

**蓝图**：`uploads_bp`（名称 `"uploads"`，无 url_prefix）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/uploads/<path:filepath>` | `serve_upload(filepath)` | 提供上传文件（图片等）的静态服务 | `g.base_dir` | 文件内容（`send_from_directory`） |

---

### 2.13 `routes/pages.py` — 分析页面路由

**蓝图**：`pages_bp`（名称 `"pages"`，无 url_prefix）

| 方法 | 路径 | 处理函数 | 用途 | 访问 `g.*` | 返回 |
|---|---|---|---|---|---|
| GET | `/analyze` | `analyze_page()` | 跨实验分析工具页面 | 无 | `analyze.html` |
| GET | `/analysis/<anal_id>` | `view_analysis(anal_id)` | 分析报告详情页 | `g.analysis_repo` | `analysis_detail.html` 或 404 |

---

## 3. 完整 URL 路由表

| URL | 方法 | 蓝图 | 处理函数 | 用途 |
|---|---|---|---|---|
| `/` | GET | dashboard | `index()` | 首页仪表盘 |
| `/experiments` | GET | dashboard | `experiment_list()` | 实验卡片列表 |
| `/new` | GET | dashboard | `new_experiment()` | 新建实验表单 |
| `/timeline` | GET | dashboard | `timeline()` | 时间线视图 |
| `/compare` | GET | dashboard | `compare_experiments()` | 多实验对比 |
| `/api/favorites` | GET | dashboard | `favorites_page()` | 收藏夹页面 |
| `/experiments/<id>` | GET | experiment | `view_experiment(id)` | 实验详情 |
| `/experiments/<id>/yaml` | GET | experiment | `view_yaml(id)` | 原始 YAML |
| `/experiments/<id>/edit` | GET/POST | experiment | `edit_experiment(id)` | 编辑实验 |
| `/experiments/<id>/delete` | DELETE | experiment | `delete_experiment(id)` | 删除实验 |
| `/experiments/<id>/save-json` | POST | experiment | `save_experiment_json(id)` | JSON 保存 |
| `/experiments/<id>/regenerate` | POST | experiment | `regenerate_experiment(id)` | LLM 重新解析 |
| `/experiments/<id>/print` | GET | experiment | `print_experiment(id)` | 打印视图 |
| `/analyze` | GET | pages | `analyze_page()` | 分析工具页 |
| `/analysis/<id>` | GET | pages | `view_analysis(id)` | 分析报告详情 |
| `/settings` | GET/POST | settings | `settings_page()` | 设置页 |
| `/templates` | GET | templates | `template_library()` | 模板库 |
| `/api/templates/<id>` | GET | templates | `api_get_template(id)` | 模板 API |
| `/uploads/<path>` | GET | uploads | `serve_upload(path)` | 文件服务 |
| `/api/parse` | POST | api_experiment | `api_parse()` | 解析笔记 |
| `/api/parse/confirm` | POST | api_experiment | `api_parse_confirm()` | 确认解析 |
| `/api/agent/start` | POST | api_agent | `api_agent_start()` | 启动 Agent |
| `/api/agent/message` | POST | api_agent | `api_agent_message()` | Agent 消息 |
| `/api/agent/confirm` | POST | api_agent | `api_agent_confirm()` | Agent 确认 |
| `/api/analysis/<id>/chat` | POST | api_child | `api_analysis_chat(id)` | 分析子 Agent |
| `/api/exp/<id>/chat` | POST | api_child | `api_exp_chat(id)` | 实验子 Agent |
| `/api/exp/<id>/confirm` | POST | api_child | `api_exp_confirm(id)` | 子 Agent 确认 |
| `/api/analysis-history` | GET | api_analysis | `api_analysis_history()` | 分析历史列表 |
| `/api/analysis-history/<id>` | GET/DELETE | api_analysis | `api_analysis_detail(id)` / `api_analysis_delete(id)` | 分析详情/删除 |
| `/api/experiments/search` | GET | api_search | `api_experiments_search()` | 全量搜索 |
| `/api/resolve-reference` | POST | api_search | `api_resolve_reference()` | 引用解析 |
| `/api/experiments/<id>/pin` | POST | api_favorites | `api_toggle_pin(id)` | 切换置顶 |
| `/api/experiments/<id>/favorite` | POST | api_favorites | `api_toggle_favorite(id)` | 切换收藏 |
| `/api/list-collections` | GET | api_favorites | `api_list_collections()` | 列出收藏夹 |
| `/api/collections` | POST | api_favorites | `api_create_collection()` | 创建收藏夹 |
| `/api/collections/<name>` | DELETE | api_favorites | `api_delete_collection(name)` | 删除收藏夹 |
| `/api/upload-image` | POST | api_upload | `api_upload_image()` | 上传图片 |

**统计**：13 个蓝图，42 条路由，12 个模板。
