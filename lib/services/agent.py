"""Agent 生命周期 + 对话管理服务。整合 app.py 和 agent_v2.py 中的创建逻辑。"""

from lib.agent_v2 import AgentLoop


class AgentService:
    def __init__(self, llm_client, exp_repo, thread_repo, update_log_repo,
                 favorites_repo, analysis_repo, extraction_svc,
                 experiment_svc, analysis_svc):
        self.llm_client = llm_client
        self.exp_repo = exp_repo
        self.thread_repo = thread_repo
        self.update_log_repo = update_log_repo
        self.favorites_repo = favorites_repo
        self.analysis_repo = analysis_repo
        self.extraction_svc = extraction_svc
        self.experiment_svc = experiment_svc
        self.analysis_svc = analysis_svc

    def create_or_resume_agent(self, state_dict=None) -> AgentLoop:
        """创建新 Agent 或从已保存状态恢复。"""
        # 恢复路径
        if state_dict:
            return AgentLoop.from_dict(
                self.llm_client, self.exp_repo, state_dict,
                thread_store=self.thread_repo,
                update_log_store=self.update_log_repo,
                favorites_store=self.favorites_repo,
                analysis_store=self.analysis_repo,
            )

        # 尝试从磁盘恢复
        saved = self.thread_repo.load_current_state()
        if saved:
            return AgentLoop.from_dict(
                self.llm_client, self.exp_repo, saved,
                thread_store=self.thread_repo,
                update_log_store=self.update_log_repo,
                favorites_store=self.favorites_repo,
                analysis_store=self.analysis_repo,
            )

        # 新建
        agent = AgentLoop(
            self.llm_client, self.exp_repo,
            thread_store=self.thread_repo,
            update_log_store=self.update_log_repo,
            favorites_store=self.favorites_repo,
            analysis_store=self.analysis_repo,
        )
        return agent

    def run_message(self, agent: AgentLoop, message: str) -> dict:
        """处理用户消息 → 返回 {type, message, state?, preview?}。"""
        return agent.run(message)

    def create_child_agent(self, parent: AgentLoop, thread_id: str,
                           role: str) -> AgentLoop:
        """创建子 Agent。role 为 'exp_editor' 或 'analysis_reviewer'。"""
        child = AgentLoop.create_child_agent(parent, thread_id)
        child.child.agent_role = role
        return child

    def create_legacy_child_agent(self, exp_data: dict) -> AgentLoop:
        """为无线程关联的旧实验创建子 Agent。"""
        child = AgentLoop.create_legacy_child_agent(
            self.llm_client, self.exp_repo, exp_data,
            thread_store=self.thread_repo,
            update_log_store=self.update_log_repo,
            favorites_store=self.favorites_repo,
            analysis_store=self.analysis_repo,
        )
        return child

    def create_analysis_child_agent(self, llm_client, store, thread, anal_id) -> AgentLoop:
        """从线程文件创建分析子 Agent。"""
        from lib.agent_v2 import AgentLoop as AL
        agent = AL(llm_client, store,
                   thread_store=self.thread_repo,
                   update_log_store=self.update_log_repo,
                   favorites_store=self.favorites_repo,
                   analysis_store=self.analysis_repo)
        for m in thread.get("messages", []):
            if m.get("role") != "system" or "[全局上下文]" not in (m.get("content") or ""):
                agent.history.append(dict(m))
        agent.child.agent_role = "analysis_reviewer"
        agent.child.exp_id = anal_id
        agent.child.initial_history_len = len(agent.history)
        agent.thread.id = thread.get("id")
        agent.history.append({
            "role": "system",
            "content": (
                "[系统状态] 你正在审阅/修改一份已完成的分析报告。"
                "可用工具：load_reference（查看报告中引用的实验）、search_experiments、"
                "read_update_log、modify_analysis（修改报告内容）。"
                "修改报告时直接调用 modify_analysis 工具，会自动保存。"
            )
        })
        return agent

    def save_runtime_state(self, agent: AgentLoop):
        """持久化 Agent 运行时状态到 ThreadStore。"""
        agent._save_runtime_state()
