# app.py — 说明文档

## 文件作用摘要

应用入口模块。负责配置加载（config.yaml → Pydantic Settings）、Flask 应用工厂（依赖注入 + 13 个蓝图注册）、以及启动逻辑（headless Web 模式 / pywebview 桌面窗口模式）。

---

## 代码块详细说明

### 数据类

#### `Settings` (Pydantic BaseModel)
- **字段**: `DEEPSEEK_API_KEY: str = ""`, `DEEPSEEK_MODEL: str = "deepseek-v4-flash"`, `DEEPSEEK_ANALYZE_MODEL: str = "deepseek-v4-pro"`, `PORT: int = 5000 (ge=1024, le=65535)`, `HOST: str = "0.0.0.0"`, `GUI: str = "true"`
- **方法**:
  - `_warn_unknown_model(model_name, field_name)` (classmethod): 对非 `deepseek-` 前缀的模型名发 warnings.warn
  - `validate_model_names()`: 校验两个模型名，在 `load_settings()` 中调用
- **被实例化**: `load_settings()` 和 `save_settings()` 中创建 Settings 实例

### 模块级函数 — 配置管理

- `_parse_dotenv(path: Path) -> dict[str, str]` (私有): 解析旧 `.env` 文件（key=value 格式，跳过注释和空行），返回 dict。**仅被 `load_settings()` 调用**
- `load_settings() -> Settings`: 优先读 `config.yaml`(yaml.safe_load) → 回退到 `.env`(自动迁移保存为 YAML)。验证模型名。**仅在模块级 (line 119) 调用一次**
- `save_settings(data: Settings | dict) -> None`: 保存配置到 `config.yaml`。处理表单提交的类型转换。更新全局 `config` 变量。**被调用**: `routes/settings.py:18` (POST 设置页); 自身 `load_settings()` 中 `.env` 迁移时

### 模块级函数 — LLM 工厂

- `get_extract_llm()`: 返回 `LLMClient(api_key=config["DEEPSEEK_API_KEY"], model=config["DEEPSEEK_MODEL"])`。无 API Key 返回 None。**被调用**: `routes/api_experiment.py:24` (api_parse), `routes/api_agent.py:76` (_extract_or_fallback), `routes/experiment.py:81` (regenerate_experiment), `routes/api_search.py:55` (api_resolve_reference 的 LLM 语义搜索回退)
- `get_analyze_llm()`: 返回 `LLMClient(api_key=config["DEEPSEEK_ANALYZE_MODEL"] or config["DEEPSEEK_MODEL"], model=analyze_model)`。返回的 LLM 实例用于跨实验分析（模型为 `DEEPSEEK_ANALYZE_MODEL`，默认 `deepseek-v4-pro`，推理增强；不用于 function calling，仅用于 `analyze()` 方法）。无 API Key 返回 None。**被调用**: `app.py:157` (注入到 `AnalysisService` 构造函数, line 157)；同时注入到 `g.get_analyze_llm` (line 175)，供路由层按需动态获取。`lib/analyzer.analyze_experiments()` 作为 `AnalysisService` 运行失败时的兜底路径（使用 Agent 自身的 `loop.llm`）
- `get_agent_llm()`: 返回 `LLMClient(api_key=config["DEEPSEEK_API_KEY"], model=config["DEEPSEEK_MODEL"])`。**被调用**: `routes/api_agent.py:10` (api_agent_start), `routes/api_agent.py:29` (api_agent_message), `routes/api_child.py:70` (api_analysis_chat), `routes/api_child.py:131` (api_exp_chat)
- 以上三个函数均注入到 `g.` 中 (`g.get_extract_llm = get_extract_llm` 等)，供路由层通过 flask.g 动态获取

### 应用工厂

#### `create_app() -> tuple[Flask, dict]`
- **流程**: Flask(__name__) → 设置 MAX_CONTENT_LENGTH=16MB → 5 个 Repository → 4 个 Service → `before_request` 注入所有依赖到 g → 注册 13 个蓝图 → 返回 `(app, config)`
- **注入到 g 的依赖**: `g.config`, `g.exp_repo`, `g.analysis_repo`, `g.thread_repo`, `g.favorites_repo`, `g.update_log_repo`, `g.experiment_svc`, `g.extraction_svc`, `g.analysis_svc`, `g.template_svc`, `g.base_dir`, `g.get_extract_llm`, `g.get_analyze_llm`, `g.get_agent_llm`

### 启动入口

#### `if __name__ == "__main__":` 块
- 调用 `create_app()` → 获取 LAN IP (UDP socket 8.8.8.8) → 打印启动信息 → 判断模式:
  - **headless**: `--headless` 或 `GUI=false` → `app.run(host, port, debug=True)`
  - **GUI**: import pywebview → daemon 线程运行 Flask (debug=False, use_reloader=False) → `webview.create_window(title="Exdiary", url=..., width=1100, height=750, min_size=(800,500), text_select=True)` → `webview.start()`

### 模块级变量

- `BASE_DIR: Path` = `Path(__file__).parent`
- `SETTINGS_PATH: Path` = `BASE_DIR / "config.yaml"`
- `config: dict` = `load_settings().model_dump()` — 全局配置 dict，由 `save_settings()` 更新，被 `flask.g.config` 引用
