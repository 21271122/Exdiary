"""Agent 工厂函数 — 消除路由中的 Agent 构造/恢复重复。"""

from __future__ import annotations

from typing import Any
from lib.agent_v2 import AgentLoop


def get_or_create_agent(
    llm: Any,
    exp_repo: Any,
    state_dict: dict[str, Any] | None,
    thread_repo: Any,
    update_log_repo: Any = None,
    favorites_repo: Any = None,
    analysis_repo: Any = None,
    analysis_svc: Any = None,
    extraction_svc: Any = None,
) -> AgentLoop:
    """三步回退：state_dict → 磁盘状态 → 新建。"""
    if state_dict:
        return AgentLoop.from_dict(
            llm, exp_repo, state_dict,
            thread_store=thread_repo,
            update_log_store=update_log_repo,
            favorites_store=favorites_repo,
            analysis_store=analysis_repo,
            analysis_svc=analysis_svc,
            extraction_svc=extraction_svc,
        )
    saved = thread_repo.load_current_state()
    if saved:
        return AgentLoop.from_dict(
            llm, exp_repo, saved,
            thread_store=thread_repo,
            update_log_store=update_log_repo,
            favorites_store=favorites_repo,
            analysis_store=analysis_repo,
            analysis_svc=analysis_svc,
            extraction_svc=extraction_svc,
        )
    return AgentLoop(
        llm, exp_repo,
        thread_store=thread_repo,
        update_log_store=update_log_repo,
        favorites_store=favorites_repo,
        analysis_store=analysis_repo,
        analysis_svc=analysis_svc,
        extraction_svc=extraction_svc,
    )


def build_child_for_thread(
    parent: AgentLoop,
    thread_id: str,
    role: str,
) -> AgentLoop:
    """从已有线程创建子 Agent。role: 'exp_editor' | 'analysis_reviewer'."""
    child = AgentLoop.create_child_agent(parent, thread_id)
    child.child.agent_role = role
    return child


def build_analysis_child(
    llm: Any,
    store: Any,
    thread: dict[str, Any],
    anal_id: str,
    thread_repo: Any,
    update_log_repo: Any = None,
    favorites_repo: Any = None,
    analysis_repo: Any = None,
    analysis_svc: Any = None,
    extraction_svc: Any = None,
) -> AgentLoop:
    """从线程文件创建分析审阅子 Agent。"""
    agent = AgentLoop(
        llm, store,
        thread_store=thread_repo,
        update_log_store=update_log_repo,
        favorites_store=favorites_repo,
        analysis_store=analysis_repo,
        analysis_svc=analysis_svc,
        extraction_svc=extraction_svc,
    )
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
        ),
    })
    return agent


def build_legacy_child(
    llm: Any,
    store: Any,
    exp_data: dict[str, Any],
    thread_repo: Any = None,
    update_log_repo: Any = None,
    favorites_repo: Any = None,
    analysis_repo: Any = None,
) -> AgentLoop:
    """为无线程关联的旧实验创建子 Agent。"""
    return AgentLoop.create_legacy_child_agent(
        llm, store, exp_data,
        thread_store=thread_repo,
        update_log_store=update_log_repo,
        favorites_store=favorites_repo,
        analysis_store=analysis_repo,
    )
