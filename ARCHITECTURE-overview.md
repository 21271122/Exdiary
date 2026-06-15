# Exdiary v1.2 架构总览

> 基于 Flask 的材料科学实验记录管理系统，集成 DeepSeek LLM 对话式交互。

---

## 1. 项目概览

| 维度 | 说明 |
|---|---|
| **用途** | 通过 LLM Agent 自然语言对话，完成材料科学实验的结构化记录、跨实验分析与检索 |
| **后端** | Python 3.12+，Flask 框架，无数据库——纯 YAML 文件持久化 |
| **LLM** | DeepSeek API，通过 OpenAI Python SDK 调用。三个模型实例分管不同任务 |
| **存储** | 磁盘 YAML 文件——每个实验/分析报告/对话线程一个文件 |
| **前端** | Jinja2 服务端渲染 + HTMX 局部更新 + Quill.js 富文本编辑器。De Stijl（蒙德里安）CSS 设计系统。Canvas 仪表盘 |
| **桌面** | 可选 pywebview 原生窗口（Windows/Mac/Linux） |

---

## 2. 完整目录结构

```
Exdiary-v1.2/
│
├── app.py                          # Flask 应用工厂、配置加载、LLM 工厂函数、启动入口
├── config.yaml                     # 运行时配置（API Key、模型名、端口、GUI 开关）
├── CLAUDE.md                       # AI 编程助手的项目行为规范
├── run.bat                         # Windows 启动脚本
│
├── routes/                         # 13 个 Flask 蓝图文件（HTTP 层）
│   ├── dashboard.py                #   首页仪表盘、实验列表页、时间线、对比、收藏页
│   ├── experiment.py               #   实验详情/编辑/删除/打印/YAML 视图
│   ├── pages.py                    #   分析中心页、分析报告详情页
│   ├── settings.py                 #   设置页面
│   ├── templates.py                #   实验模板库页 + 模板 API
│   ├── uploads.py                  #   文件上传静态服务
│   ├── api_experiment.py           #   自然语言解析 API、解析确认 API
│   ├── api_agent.py                #   父 Agent：启动对话、发送消息、确认保存
│   ├── api_child.py                #   子 Agent：实验修改对话、分析报告审阅对话、确认保存
│   ├── api_analysis.py             #   分析历史列表/详情/删除 API
│   ├── api_search.py               #   全量实验搜索、智能引用解析 API
│   ├── api_favorites.py            #   置顶/收藏切换、收藏夹管理 API
│   └── api_upload.py               #   图片上传 API
│
├── lib/                            # Python 库（业务逻辑、数据访问、领域定义）
│   ├── agent_v2.py                 #   Agent 引擎：AgentLoop（对话主循环）、ToolExecutor（工具执行器）、ChildContext、ThreadState
│   ├── llm.py                      #   LLMClient 封装——chat()、structured_extract()、analyze()
│   ├── parser.py                   #   自由文本 → 结构化实验 dict（旧版，仍被部分路由引用）
│   ├── analyzer.py                 #   跨实验分析函数（旧版，仍被部分代码引用）
│   ├── logger.py                   #   ExdiaryLogger——4 个 JSONL 日志文件
│   ├── storage.py                  #   兼容层——将 Repository 类重导出为旧 Store 名称
│   ├── debug.py                    #   DebugTracer——按对话 session 记录 LLM 调用详情
│   │
│   ├── core/                       # 领域层（纯数据定义，零 I/O 依赖）
│   │   ├── schema.py               #   实验 JSON Schema、DEFAULT_CONTEXT、TAG_VOCABULARY
│   │   ├── agent_tools.py          #   16 个工具的 OpenAI function-calling 格式定义
│   │   ├── prompts.py              #   SYSTEM_PROMPT 构建器、分析提示词
│   │   ├── experiment_types.py     #   PRIORITY_MAP——9 种实验类型的字段优先级
│   │   └── exceptions.py           #   自定义异常类（ExdiaryError 等 5 个）
│   │
│   ├── repositories/               # 数据访问层（5 个抽象接口 + 5 个 YAML 实现）
│   │   ├── base.py                 #   Abstract*Repository 抽象接口
│   │   ├── yaml_experiment.py      #   YamlExperimentRepository——EXP-*.yaml CRUD
│   │   ├── yaml_analysis.py        #   YamlAnalysisRepository——ANAL-*.yaml CRUD
│   │   ├── yaml_thread.py          #   YamlThreadRepository——线程生命周期、状态、全局上下文
│   │   ├── yaml_favorites.py       #   YamlFavoritesRepository——_favorites.yaml 管理
│   │   └── yaml_update_log.py      #   YamlUpdateLogRepository——按实验的更新日志
│   │
│   └── services/                   # 业务逻辑层（5 个 Service 类）
│       ├── experiment.py           #   ExperimentService——保存+日志、删除+日志、引用管理、diff 计算
│       ├── extraction.py           #   ExtractionService——LLM 结构化提取实验记录
│       ├── analysis.py             #   AnalysisService——跨实验分析（汇总→LLM→保存）
│       ├── agent.py                #   AgentService——Agent 生命周期管理
│       └── template.py             #   TemplateService——实验模板 CRUD + 内置模板初始化
│
├── templates/                      # Jinja2 HTML 模板（17 个文件）
│   ├── base.html                   #   全局布局（De Stijl 外壳、导航、Quill/HTMX/Marked CDN）
│   ├── index.html                  #   Canvas 蒙德里安仪表盘
│   ├── experiments.html            #   实验卡片列表
│   ├── view.html                   #   实验详情 + 子 Agent 聊天面板
│   ├── edit.html                   #   YAML 原始编辑
│   ├── new.html                    #   新建实验（Quill 富文本 + AI 预览）
│   ├── analyze.html                #   分析中心
│   ├── analysis_detail.html        #   分析报告详情
│   ├── compare.html                #   多实验对比视图
│   ├── timeline.html               #   时间线视图
│   ├── favorites.html              #   收藏夹管理
│   ├── settings.html               #   设置页
│   ├── templates.html              #   实验模板库
│   ├── print.html                  #   打印友好版
│   ├── _selector_scripts.html      #   共享：实验选择面板 JS
│   └── _selector_styles.html       #   共享：实验选择面板 CSS
│
├── static/                         # 静态资源
│   ├── css/
│   │   ├── de-stijl.css            #   De Stijl（蒙德里安）设计系统——配色、布局、字体
│   │   └── components.css          #   组件样式（选择面板等）
│   └── js/
│       ├── dashboard.js            #   Canvas 蒙德里安仪表盘
│       ├── nav.js                  #   导航历史系统（sessionStorage）
│       └── selector.js             #   实验选择面板交互
│
├── experiments/                    # 运行时数据目录
│   ├── EXP-YYYY-NNN.yaml           #   实验记录（每年递增编号）
│   ├── _favorites.yaml             #   置顶列表 + 收藏夹
│   ├── _analysis_history/          #   分析报告（ANAL-YYYY-NNN.yaml）
│   ├── _threads/                   #   对话线程（THR-*.yaml + 状态文件 + 索引）
│   ├── _update_logs/               #   按实验的更新日志
│   ├── _logs/                      #   4 个 JSONL 日志文件
│   └── _debug/                     #   按对话 session 的 LLM 调用追踪
│
├── experiment_templates/           # 8 个内置实验模板（YAML，首次运行时自动初始化）
├── uploads/                        # 图片上传目录（按实验 ID 分子目录）
├── design-explorations/            # 设计原型 HTML 文件（不参与应用运行）
└── test_agent_v2.py                # Agent 集成测试
```

---

## 3. 运行方式

```bash
conda activate exdiary
python app.py
```

| 模式 | 配置/参数 | 行为 |
|---|---|---|
| **桌面 GUI** | `GUI: true`（默认） | Flask 后台线程 + pywebview 原生窗口（1100×750） |
| **Web 模式** | `GUI: false` 或 `--headless` | Flask 监听 `0.0.0.0:5000`，debug 模式 |
| **局域网访问** | 启动时打印 LAN IP | 手机/其他设备可通过局域网 IP 访问 |
| **依赖** | pywebview 可选 | 未安装时自动降级为 Web 模式 |

---

## 4. 配置

文件：`config.yaml`（首次运行时自动从 `.env` 迁移创建）。

```yaml
DEEPSEEK_API_KEY: sk-...              # DeepSeek API 密钥
DEEPSEEK_MODEL: deepseek-v4-flash      # 默认模型（提取 + Agent 对话）
DEEPSEEK_ANALYZE_MODEL: deepseek-v4-pro # 分析专用模型（推理增强）
PORT: '5000'
HOST: 0.0.0.0
GUI: 'true'
```

配置由 `app.py:load_settings()` 加载，每次请求通过 `flask.g.config` 注入到路由上下文。

---

## 5. 分层架构

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  templates/  +  static/                (表示层)                       │
  │  Jinja2 + HTMX + Quill.js + De Stijl CSS + Canvas 仪表盘             │
  └────────────────────────────────┬─────────────────────────────────────┘
                                   │ HTTP 请求/响应
  ┌────────────────────────────────┼─────────────────────────────────────┐
  │  routes/（13 蓝图）             │  (路由层)                           │
  │  · 页面路由（5）: dashboard, experiment, pages, settings,            │
  │    templates, uploads                                                │
  │  · API 路由（7）: api_experiment, api_agent, api_child,              │
  │    api_analysis, api_search, api_favorites, api_upload               │
  └────────────────────────────────┬─────────────────────────────────────┘
                                   │ 调用
  ┌────────────────────────────────┼─────────────────────────────────────┐
  │  lib/services/（5 个类）        │  (业务逻辑层)                       │
  │  ExperimentService             │  CRUD + diff + 引用管理              │
  │  ExtractionService             │  LLM 结构化提取                     │
  │  AnalysisService               │  跨实验分析                         │
  │  AgentService                  │  Agent 生命周期                     │
  │  TemplateService               │  模板 CRUD                          │
  └────────────────────────────────┬─────────────────────────────────────┘
                                   │ 调用
  ┌────────────────────────────────┼─────────────────────────────────────┐
  │  lib/repositories/（5+5）       │  (数据访问层)                       │
  │  Abstract*Repository 接口       │  (base.py 中的契约)                 │
  │  Yaml*Repository 实现           │  (读写 YAML 文件)                  │
  └────────────────────────────────┬─────────────────────────────────────┘
                                   │ 读写
  ┌────────────────────────────────┼─────────────────────────────────────┐
  │  experiments/*.yaml            │  (存储——磁盘 YAML 文件)              │
  │  + _analysis_history/ + _threads/ + _update_logs/ + _favorites.yaml  │
  └──────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │  lib/core/                      (领域层——纯数据，零I/O)              │
  │  schema.py, agent_tools.py, prompts.py, experiment_types.py          │
  └──────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │  lib/agent_v2.py                (LLM Agent 引擎)                     │
  │  AgentLoop.run() → LLM chat → 工具调用 → 执行 → 循环/返回           │
  └──────────────────────────────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────────────────────────────┐
  │  lib/llm.py                     (LLM 客户端——OpenAI SDK 封装)        │
  │  chat(), structured_extract(), analyze()                             │
  └──────────────────────────────────────────────────────────────────────┘
```

---

## 6. 数据流

### HTTP 请求流（标准 CRUD）

```
浏览器 HTTP 请求
  → Flask 路由处理函数（routes/*.py）
    → Service 方法（lib/services/*.py）
      → Repository 方法（lib/repositories/yaml_*.py）
        → YAML 文件读写（experiments/*.yaml）
```

### Agent 对话流

```
用户发送消息
  → POST /api/agent/message
    → AgentService.run_message()
      → AgentLoop.run(user_message)
        → 构建消息列表: [system prompt, history, Schema 状态, 线程状态]
        → LLMClient.chat(messages, tools=[16 工具], reasoning_effort="max")
        → LLM 响应: 纯文本（返回给用户）或 tool_calls
        → ToolExecutor.execute(工具名, 参数)
          → 磁盘 I/O（加载/保存实验、线程等）
          → 返回结果 dict 给 AgentLoop
        → AgentLoop 将工具结果追加到 history
        → 循环继续直到 LLM 返回纯文本或工具返回 pause=True
      → 返回 {type, message, context, state, preview?}
    → 序列化响应 + AgentLoop.state_to_dict() 返回前端
  → 前端重新渲染聊天界面 + 状态
```

### 父子 Agent 架构

```
┌────────────────────────────────────────────────────────────┐
│ 父 AgentLoop（自由模式 / record / analyze）                  │
│   使用: agent_llm (flash, reasoning_effort=max)             │
│   线程状态通过 ThreadStore 管理                              │
│   可创建子 Agent:                                           │
│     · exp_editor       —— 修改已有实验字段                   │
│     · analysis_reviewer —— 审阅/修改分析报告                │
└────────────────────────────────────────────────────────────┘
```

---

## 7. LLM 模型配置

`app.py` 中的工厂函数创建三个 LLM 客户端实例：

| 实例 | 工厂函数 | 模型配置 | 用途 |
|---|---|---|---|
| `extract_llm` | `get_extract_llm()` | `DEEPSEEK_MODEL`（flash） | 从自由文本中结构化提取实验数据（function calling） |
| `analyze_llm` | `get_analyze_llm()` | `DEEPSEEK_ANALYZE_MODEL`（pro） | 跨实验分析（高推理能力） |
| `agent_llm` | `get_agent_llm()` | `DEEPSEEK_MODEL`（flash） | AgentLoop 主对话（tool calling + reasoning_effort="max"） |

三者共享同一个 `LLMClient` 类（`lib/llm.py`），底层封装 `openai.OpenAI`，`base_url="https://api.deepseek.com"`。

---

## 8. 数据持久化（YAML 文件格式）

### 实验记录 —— `experiments/EXP-YYYY-NNN.yaml`

每个实验一个 YAML 文件，遵循 `lib/core/schema.py` 定义的 16 字段 Schema。字段包括：`id`、`title`、`date`、`experimenter`、`status`（planned/running/done/failed/repeated）、`tags`、`purpose`、`materials`（含 name/purity/vendor/amount/notes）、`equipment`、`experimental_plan`、`sop`、`process_parameters`、`observations`、`characterization`、`results`（含 qualitative/key_data/figures）、`conclusion`、`next_steps`。

### 分析报告 —— `experiments/_analysis_history/ANAL-YYYY-NNN.yaml`

```yaml
timestamp: 2026-05-26 15:40:00
question: "对比钙钛矿PCE趋势"
selected_ids: ["EXP-2026-005", "EXP-2026-023"]
analysis: "## 事实呈现\n..."
```

### 对话线程 —— `experiments/_threads/THR-YYYY-NNN.yaml`

OpenAI 消息格式的完整对话历史，附带线程元数据（type、status、title、summary、branches）。辅助文件：`_current_state.yaml`（活跃 Agent 状态）、`_global_context.yaml`（压缩历史摘要）、`index.yaml`（线程索引 + 反向映射 + 用户画像）、`*_child_state.yaml`（子 Agent 状态）。

### 收藏夹 —— `experiments/_favorites.yaml`

```yaml
pinned: ["EXP-2026-016", "EXP-2026-019", "EXP-2026-023"]
collections:
  默认收藏夹: ["EXP-2026-003", "EXP-2026-019", "EXP-2026-021"]
```

### 更新日志 —— `experiments/_update_logs/EXP-YYYY-NNN.yaml`

按实验分文件的追加日志。每条记录包含：changes（path/field/old/new）、source（"manual_edit"/"parent_agent"/"child_agent"/"system"）、thread_id、timestamp。

---

## 9. ID 命名规范

| 前缀 | 格式 | 示例 | 生成方式 |
|---|---|---|---|
| `EXP` | `EXP-YYYY-NNN` | `EXP-2026-023` | `YamlExperimentRepository.next_id()`——扫描当年最大 NNN |
| `ANAL` | `ANAL-YYYY-NNN` | `ANAL-2026-004` | `YamlAnalysisRepository.next_id()`——同上 |
| `THR` | `THR-YYYY-NNN` | `THR-2026-020` | `YamlThreadRepository.next_id()`——同上 |
| `UPD` | `UPD-NNN-XXX` | 嵌入在更新日志条目中 | 按实验顺序递增 |

所有计数器扫描文件系统中的当年最大编号后加 1。

---

## 10. 日志系统

`experiments/_logs/` 下 4 个 JSONL 文件：

| 文件 | 内容 | 关键字段 |
|---|---|---|
| `agent.log` | 全部对话消息（父子 Agent 混排，`agent` 字段区分） | `ts`, `agent`, `role`, `content`, `tool_calls`, `exp` |
| `tools.log` | 每次工具调用及结果状态 | `ts`, `agent`, `tool`, `ok`, `exp` + 工具特定摘要 |
| `operations.log` | 文件/状态变更 | `ts`, `op`, `agent` + 操作特定字段 |
| `system.log` | 启动、错误、异常 | `ts`, `level`, `event`, `traceback` |

通过 `lib/logger.py` 中的 `ExdiaryLogger` 访问：`init_logger(base_dir)` → `get_logger()` → `log.agent(...)` 等。

此外，`lib/debug.py` 中的 `DebugTracer` 将完整 LLM 请求/响应负载写入 `experiments/_debug/<时间戳>/` 目录。

---

## 11. Agent 工具清单（16 个）

定义在 `lib/core/agent_tools.py` 中（OpenAI function-calling 格式），由 `lib/agent_v2.py` 的 `ToolExecutor` 执行。

| 工具名 | 可用模式 | 功能描述 |
|---|---|---|
| `load_reference` | 全部 | 通过 EXP ID 加载实验完整数据 |
| `search_experiments` | 全部 | 自然语言搜索历史实验 |
| `list_experiments` | 全部 | 按状态/标签/人员/日期筛选 |
| `query_experiment` | 全部 | 查询实验参数（如"003 的退火温度"） |
| `read_update_log` | 全部 | 查看实验修改历史 |
| `modify_experiment` | 通用/记录 | 增量修改实验字段 |
| `manage_collection` | 全部 | 置顶/取消置顶、收藏/取消收藏 |
| `end_thread` | 全部 | 结束当前线程，回到自由模式 |
| `start_record_thread` | 通用 | 进入 record 模式开始记录新实验 |
| `update_schema` | 记录 | 将确认信息写入内存 Schema |
| `ask_user` | 记录/分析 | 向用户提问（一次最多 3 个） |
| `generate_record` | 记录 | 从累积的 Schema 生成结构化实验记录 |
| `start_analyze_thread` | 通用 | 进入 analyze 模式开始跨实验分析 |
| `select_experiments` | 分析 | 展示实验选择卡片面板 |
| `generate_analysis` | 分析 | 执行跨实验分析并归档 |
| `modify_analysis` | 分析 | 修改已归档分析（替换/扩展实验/扩展维度） |
