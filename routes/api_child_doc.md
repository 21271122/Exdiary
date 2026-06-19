# routes/api_child.py — 说明文档

## 文件作用摘要

子 Agent 对话 API 蓝图 `api_child_bp`，URL 前缀 `/api`。处理实验修改子 Agent（exp_editor）和分析审阅子 Agent（analysis_reviewer）的对话交互。支持三种初始化路径：有线程状态恢复、有磁盘持久化状态恢复、无线程旧实验（legacy 迁移）。

---

## 代码块详细说明

### 模块级常量

- `_MODIFY_MODE_PROMPT: str` — 实验编辑子 Agent 的模式提示模板（告知使用 modify_experiment 而非 update_schema/generate_record）

### 路由函数

- `api_analysis_chat(anal_id)` — POST `/api/analysis/<anal_id>/chat`: 分析审阅子 Agent
  - 请求体: `{message: str, state: dict}` (均可选)
  - **5 条处理路径**:
    1. 无线程 + 无状态 + 首次请求 → 返回 legacy 分析摘要数据
    2. 无线程 + 有用户消息/状态 → `_migrate_legacy_analysis()` 迁移旧分析为线程系统格式
    3. 有线程 + 有状态 → `get_or_create_agent(state_dict)` 恢复
    4. 有线程 + 无状态 + 无用户消息 → 返回已恢复的 state
    5. 有线程 + 无状态 + 有用户消息 → `build_analysis_child()` 创建新 Agent
  - 返回: `{ok, state, type, message}` 或首次返回 `{ok, is_legacy: true, anal_data: {...}}`

- `api_exp_chat(exp_id)` — POST `/api/exp/<exp_id>/chat`: 实验编辑子 Agent
  - 请求体: `{message: str, state: dict, is_legacy: bool}`
  - **6 条处理路径**:
    1. 无线程 + 有磁盘 child_state + 非 legacy → `get_or_create_agent(state_dict=disk_state)` 恢复
    2. 无线程 + 无状态 + 首次请求 → 返回 legacy 实验摘要数据
    3. 无线程 + is_legacy → `build_legacy_child()` 创建
    4. 无线程 + 有用户消息(含初始加载) → `build_legacy_child()` + 注入修改模式 prompt + 执行
    5. 有线程 + 有状态 → `get_or_create_agent(state_dict)` 恢复
    6. 有线程 + 无状态 → `build_child_for_thread()` 创建线程子 Agent + 注入修改模式 prompt
  - 注意: 路径3/4 在无线程时先检查磁盘 child_state（可恢复时优先恢复，避免不必要的 legacy 创建）

- `api_exp_confirm(exp_id)` — POST `/api/exp/<exp_id>/confirm`: 确认子 Agent 的实验修改
  - 请求体: `{preview: dict, state: dict}`
  - `g.experiment_svc.save_with_log()` + `g.exp_repo.save()` + `g.experiment_svc.update_referenced_by()`

### 模块级函数

- `_migrate_legacy_analysis(anal_id, analysis_data) -> str`: 将旧分析报告迁移为线程系统格式。创建 THR 线程文件 → 注入报告内容到 messages → `g.thread_repo.save()` + `g.thread_repo.update_index()` → 返回 thread_id
  - **被调用**: `api_analysis_chat()` 内部

- `_make_analysis_chat_response(agent, result, thread_id) -> Flask response`: 分析子 Agent 的统一 JSON 响应构建
  - **被调用**: `api_analysis_chat()` 内部

- `_make_chat_response(agent, result, thread_id) -> Flask response`: 实验编辑子 Agent 的统一 JSON 响应构建。特别处理 type=extract/generate 时返回 preview
  - **被调用**: `api_exp_chat()` 内部
