import json
from datetime import datetime as dt
from flask import Blueprint, request, jsonify, g
from lib.agent_v2 import AgentLoop

api_child_bp = Blueprint("api_child", __name__)


def _migrate_legacy_analysis(anal_id, analysis_data):
    tid = g.thread_repo.next_id()
    now = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    messages = [
        {"role": "system", "content": f"旧分析记录 {anal_id}，于 {now} 迁移至线程系统。"},
        {"role": "user", "content": analysis_data.get("question", "")},
        {"role": "assistant", "content": "（分析报告见系统消息）"},
        {"role": "system", "content": f"[分析报告内容]\n{analysis_data.get('analysis', '')}"},
    ]
    thread = {"id": tid, "type": "analyze", "status": "done",
              "created": now, "updated": now,
              "title": (analysis_data.get("question") or "分析")[:30],
              "summary": f"迁移自旧分析记录 {anal_id}",
              "anal_generated": anal_id,
              "messages": messages, "branches": []}
    g.thread_repo.save(thread)
    g.thread_repo.update_index(thread)
    return tid


def _make_analysis_chat_response(agent, result, thread_id):
    state = agent.state_to_dict()
    if thread_id:
        g.thread_repo.save_child_state(thread_id, state)
    return jsonify({"ok": True, "state": state,
                    "type": result.get("type", "reply"),
                    "message": result.get("message", "")})


def _make_chat_response(agent, result, thread_id):
    state = agent.state_to_dict()
    key = thread_id or agent.child.exp_id
    if key:
        g.thread_repo.save_child_state(key, state)
    if result["type"] in ("extract", "generate"):
        preview = agent._generated_preview
        return jsonify({"ok": True, "type": "extract", "state": state,
                        "message": result.get("message", "实验记录已生成，请在预览中确认。"),
                        "preview": preview})
    return jsonify({"ok": True, "state": state, "type": result["type"],
                    "message": result.get("message", "")})


def _create_analysis_child_agent(llm_client, thread, anal_id):
    agent = AgentLoop(llm_client, g.exp_repo,
                      thread_store=g.thread_repo,
                      update_log_store=g.update_log_repo,
                      favorites_store=g.favorites_repo,
                      analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
    for m in thread.get("messages", []):
        if m.get("role") != "system" or "[全局上下文]" not in (m.get("content") or ""):
            agent.history.append(dict(m))
    agent.child.agent_role = "analysis_reviewer"
    agent.child.exp_id = anal_id
    agent.child.initial_history_len = len(agent.history)
    agent.thread.id = thread.get("id")
    agent.history.append({
        "role": "system",
        "content": ("[系统状态] 你正在审阅/修改一份已完成的分析报告。"
                    "可用工具：load_reference（查看报告中引用的实验）、search_experiments、"
                    "read_update_log、modify_analysis（修改报告内容）。"
                    "修改报告时直接调用 modify_analysis 工具，会自动保存。")
    })
    return agent


@api_child_bp.route("/analysis/<anal_id>/chat", methods=["POST"])
def api_analysis_chat(anal_id):
    llm = g.get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    a = g.analysis_repo.load(anal_id)
    if not a:
        return jsonify({"ok": False, "error": "分析报告不存在"}), 404

    data = request.get_json() or {}
    user_message = (data.get("message") or "").strip()
    state_dict = data.get("state")

    idx = g.thread_repo.get_index()
    thread_id = idx.get("anal_to_thread", {}).get(anal_id)

    if not thread_id:
        if not user_message and not state_dict:
            return jsonify({"ok": True, "is_legacy": True,
                            "anal_data": {"id": a.get("id"),
                                          "question": a.get("question", ""),
                                          "timestamp": a.get("timestamp", ""),
                                          "selected_ids": a.get("selected_ids", []),
                                          "analysis": (a.get("analysis") or "")[:500]}})
        thread_id = _migrate_legacy_analysis(anal_id, a)

    if not state_dict:
        disk_state = g.thread_repo.load_child_state(thread_id)
        if disk_state:
            state_dict = disk_state

    if state_dict:
        agent = AgentLoop.from_dict(llm, g.exp_repo, state_dict,
                                    thread_store=g.thread_repo,
                                    update_log_store=g.update_log_repo,
                                    favorites_store=g.favorites_repo,
                                    analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
        if user_message:
            result = agent.run(user_message)
            return _make_analysis_chat_response(agent, result, thread_id)
        state = agent.state_to_dict()
        g.thread_repo.save_child_state(thread_id, state)
        return jsonify({"ok": True, "state": state})

    thread = g.thread_repo.load(thread_id)
    if not thread:
        return jsonify({"ok": False, "error": "线程不存在"}), 500

    agent = _create_analysis_child_agent(llm, thread, anal_id)
    if user_message:
        result = agent.run(user_message)
        return _make_analysis_chat_response(agent, result, thread_id)
    state = agent.state_to_dict()
    g.thread_repo.save_child_state(thread_id, state)
    return jsonify({"ok": True, "state": state})


@api_child_bp.route("/exp/<exp_id>/chat", methods=["POST"])
def api_exp_chat(exp_id):
    llm = g.get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    data = request.get_json() or {}
    user_message = (data.get("message") or "").strip()
    state_dict = data.get("state")
    is_legacy = data.get("is_legacy", False)

    idx = g.thread_repo.get_index()
    thread_id = idx.get("exp_to_thread", {}).get(exp_id)

    if not thread_id:
        exp = g.exp_repo.load(exp_id)
        if not exp:
            return jsonify({"ok": False, "error": "实验不存在"}), 404

        disk_state = g.thread_repo.load_child_state(exp_id)
        if disk_state and not is_legacy:
            agent = AgentLoop.from_dict(llm, g.exp_repo, disk_state,
                                        thread_store=g.thread_repo,
                                        update_log_store=g.update_log_repo,
                                        favorites_store=g.favorites_repo,
                                        analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
            if user_message:
                result = agent.run(user_message)
                return _make_chat_response(agent, result, None)
            state = agent.state_to_dict()
            g.thread_repo.save_child_state(exp_id, state)
            return jsonify({"ok": True, "state": state})

        if not user_message and not is_legacy:
            return jsonify({"ok": True, "is_legacy": True,
                            "exp_data": {"id": exp.get("id"),
                                         "title": exp.get("title", ""),
                                         "date": exp.get("date", ""),
                                         "status": exp.get("status", ""),
                                         "tags": exp.get("tags", []),
                                         "purpose": (exp.get("purpose") or "")[:200],
                                         "materials": exp.get("materials", []),
                                         "sop": exp.get("sop", []),
                                         "process_parameters": exp.get("process_parameters", []),
                                         "results": exp.get("results", {}),
                                         "conclusion": (exp.get("conclusion") or "")[:200],
                                         "next_steps": exp.get("next_steps", [])}})

        exp_data = {"id": exp.get("id"), "title": exp.get("title", ""),
                    "tags": exp.get("tags", []),
                    "purpose": (exp.get("purpose") or "")[:200],
                    "materials": exp.get("materials", []), "sop": exp.get("sop", []),
                    "process_parameters": exp.get("process_parameters", []),
                    "results": exp.get("results", {}),
                    "conclusion": (exp.get("conclusion") or "")[:200],
                    "next_steps": exp.get("next_steps", []),
                    "status": exp.get("status", "done"),
                    "date": exp.get("date", ""),
                    "experimenter": exp.get("experimenter", "")}
        agent = AgentLoop.create_legacy_child_agent(llm, g.exp_repo, exp_data,
                                                    thread_store=g.thread_repo,
                                                    update_log_store=g.update_log_repo,
                                                    favorites_store=g.favorites_repo,
                                                    analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
        agent.child.exp_id = exp_id
        agent.history.append({
            "role": "system",
            "content": f"[修改模式] 你正在修改已完成的实验 {exp_id}。修改前先用 load_reference 加载磁盘最新数据（不要依赖对话记忆）。修改用 modify_experiment 工具直接执行，会自动保存和记录日志。不要用 update_schema 或 generate_record。查询信息用 query_experiment，查历史用 read_update_log。"
        })
        result = agent.run(user_message)
        return _make_chat_response(agent, result, thread_id)

    if not state_dict:
        disk_state = g.thread_repo.load_child_state(thread_id)
        if disk_state:
            state_dict = disk_state

    if state_dict:
        agent = AgentLoop.from_dict(llm, g.exp_repo, state_dict,
                                    thread_store=g.thread_repo,
                                    update_log_store=g.update_log_repo,
                                    favorites_store=g.favorites_repo,
                                    analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
        if user_message:
            result = agent.run(user_message)
            return _make_chat_response(agent, result, thread_id)
        state = agent.state_to_dict()
        if thread_id:
            g.thread_repo.save_child_state(thread_id, state)
        return jsonify({"ok": True, "state": state})

    parent = AgentLoop(llm, g.exp_repo,
                       thread_store=g.thread_repo,
                       update_log_store=g.update_log_repo,
                       favorites_store=g.favorites_repo,
                       analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
    agent = AgentLoop.create_child_agent(parent, thread_id)
    agent.child.exp_id = exp_id
    agent.history.append({
        "role": "system",
        "content": f"[修改模式] 你正在修改已完成的实验 {exp_id}。修改前先用 load_reference 加载磁盘最新数据（不要依赖对话记忆）。修改用 modify_experiment 工具直接执行，会自动保存和记录日志。不要用 update_schema 或 generate_record。查询信息用 query_experiment，查历史用 read_update_log。"
    })

    if user_message:
        result = agent.run(user_message)
        return _make_chat_response(agent, result, thread_id)
    state = agent.state_to_dict()
    if thread_id:
        g.thread_repo.save_child_state(thread_id, state)
    return jsonify({"ok": True, "state": state})


@api_child_bp.route("/exp/<exp_id>/confirm", methods=["POST"])
def api_exp_confirm(exp_id):
    body = request.get_json()
    if not body or not isinstance(body, dict):
        return jsonify({"ok": False, "error": "无效的请求数据"}), 400

    data = body.get("preview") or {}
    state_dict = body.get("state")
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "缺少实验数据"}), 400

    old_exp = g.exp_repo.load(exp_id)
    data["id"] = exp_id

    notes = data.get("original_notes", "")
    refs = g.experiment_svc.extract_references(notes)
    old_refs = old_exp.get("references", []) if old_exp else []
    data["references"] = refs

    thread_id = None
    if state_dict and isinstance(state_dict, dict):
        thread_id = state_dict.get("thread_id")

    g.experiment_svc.save_with_log(exp_id, data, "child_agent", thread_id=thread_id)
    g.exp_repo.save(data)
    g.experiment_svc.update_referenced_by(exp_id, refs, old_refs=old_refs)
    return jsonify({"ok": True, "exp_id": exp_id})
