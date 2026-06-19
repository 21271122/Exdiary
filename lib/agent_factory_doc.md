# lib/agent_factory.py — 说明文档

## 文件作用摘要

Agent 工厂函数模块。消除路由层中的 Agent 构造/恢复重复。提供统一的 Agent 创建入口 `get_or_create_agent()`（三步回退策略），以及 3 种子 Agent 构造方法。被 `routes/api_agent.py` 和 `routes/api_child.py` 导入使用。

---

## 代码块详细说明

### 模块级函数

#### `get_or_create_agent(llm, exp_repo, state_dict, thread_repo, update_log_repo=None, favorites_repo=None, analysis_repo=None, analysis_svc=None, extraction_svc=None) -> AgentLoop`
- **作用**: 获取或创建 AgentLoop 实例的三步回退统一入口
- **输入**:
  - `llm` — LLM 客户端实例
  - `exp_repo` — 实验仓储
  - `state_dict: dict | None` — 前端传来的序列化 state（来自 sessionStorage/上次请求返回/磁盘持久化文件）
  - `thread_repo` — 线程仓储（ThreadRepository Facade）
  - `update_log_repo / favorites_repo / analysis_repo` — 各仓储（可选，子Agent 路径可能不需要）
  - `analysis_svc / extraction_svc` — 服务实例（可选）
- **三步回退策略**:
  1. `state_dict` 非空 → `AgentLoop.from_dict(llm, exp_repo, state_dict, storage_deps...)`
  2. 否则 `thread_repo.load_current_state()` → 磁盘持久化状态恢复
  3. 磁盘也无 → `AgentLoop(llm, exp_repo, storage_deps...)` 全新实例
- **被调用**:
  - `routes/api_agent.py:14` — `api_agent_start()` 中 state_dict=None → 创建新 Agent
  - `routes/api_agent.py:45` — `api_agent_message()` 中 state_dict=前端传来的 state → 恢复会话
  - `routes/api_child.py:101` — `api_analysis_chat()` 中有 state_dict 时恢复
  - `routes/api_child.py:150` — `api_exp_chat()` 中有磁盘 child_state 时恢复 (line 150)
  - `routes/api_child.py:206` — `api_exp_chat()` 中有线程 state 时恢复 (line 206)

#### `build_child_for_thread(parent: AgentLoop, thread_id: str, role: str) -> AgentLoop`
- **作用**: 从已有线程文件创建子 Agent（内部调用 `AgentLoop.create_child_agent()`）
- **输入**: `parent` (获取依赖), `thread_id`, `role` ("exp_editor" / "analysis_reviewer")
- **实现**: `AgentLoop.create_child_agent(parent, thread_id)` → 覆盖 `child.agent_role = role`
- **被调用**:
  - `routes/api_child.py:224` — `api_exp_chat()` 中有线程但无状态的路径（构造新的 thread-based 子 Agent）

#### `build_analysis_child(llm, store, thread, anal_id, thread_repo, ...) -> AgentLoop`
- **作用**: 从分析线程文件创建分析审阅子 Agent
- **输入**: `llm / store` + `thread: dict` (线程数据) + `anal_id: str` + 各仓储及服务
- **实现**: 新建 AgentLoop → 复制线程 messages（排除已有 L0 摘要）→ 设置 `child.agent_role = "analysis_reviewer"` + `child.exp_id = anal_id` + `child.initial_history_len` → 追加审阅模式系统消息
- **被调用**:
  - `routes/api_child.py:117` — `api_analysis_chat()` 中有线程但无状态的首次创建路径

#### `build_legacy_child(llm, store, exp_data, thread_repo, ...) -> AgentLoop`
- **作用**: 为无线程关联的旧实验创建 legacy 子 Agent
- **输入**: `llm / store` + `exp_data: dict` (实验数据) + 各仓储
- **实现**: 直接调用 `AgentLoop.create_legacy_child_agent(llm, store, exp_data, storage_deps...)`
- **被调用**:
  - `routes/api_child.py:188` — `api_exp_chat()` 的 legacy 路径
