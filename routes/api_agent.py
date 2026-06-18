import json
from flask import Blueprint, request, jsonify, Response, g, stream_with_context
from lib.agent_factory import get_or_create_agent
from routes.api_experiment import api_parse_confirm

api_agent_bp = Blueprint("api_agent", __name__)


@api_agent_bp.route("/start", methods=["POST"])
def api_agent_start():
    llm = g.get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    is_resumed = g.thread_repo.load_current_state() is not None

    agent = get_or_create_agent(
        llm=llm, exp_repo=g.exp_repo, state_dict=None,
        thread_repo=g.thread_repo, update_log_repo=g.update_log_repo,
        favorites_repo=g.favorites_repo, analysis_repo=g.analysis_repo,
        analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc,
    )

    if is_resumed:
        return jsonify({"ok": True, "state": agent.state_to_dict(),
                        "type": "resumed", "message": "",
                        "greeting": "会话已恢复。",
                        "context": {}})

    result = agent.run("")
    return jsonify({"ok": True, "state": agent.state_to_dict(),
                    "type": result["type"], "message": result.get("message", ""),
                    "greeting": result.get("message", ""),
                    "context": result.get("context", {})})


@api_agent_bp.route("/message", methods=["POST"])
def api_agent_message():
    llm = g.get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "缺少请求数据"}), 400

    user_message = (data.get("message") or "").strip()
    state_dict = data.get("state")
    if not user_message:
        return jsonify({"ok": False, "error": "消息不能为空"}), 400
    if not state_dict:
        return jsonify({"ok": False, "error": "缺少 state"}), 400

    agent = get_or_create_agent(
        llm=llm, exp_repo=g.exp_repo, state_dict=state_dict,
        thread_repo=g.thread_repo, update_log_repo=g.update_log_repo,
        favorites_repo=g.favorites_repo, analysis_repo=g.analysis_repo,
        analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc,
    )
    result = agent.run(user_message)

    if result["type"] in ("extract", "generate"):
        preview = result["preview"]
        notes = result.get("notes", "")
        preview["id"] = g.exp_repo.next_id()
        refs = g.experiment_svc.extract_references(notes)
        preview["references"] = refs
        g.exp_repo.save(preview)
        g.experiment_svc.update_referenced_by(preview["id"], refs)
        g.experiment_svc.move_draft_images(preview["id"])
        return jsonify({"ok": True, "type": "saved", "exp_id": preview["id"],
                        "state": result.get("state") or agent.state_to_dict(),
                        "message": result.get("message", "实验记录已生成。")})

    return jsonify({"ok": True, "state": agent.state_to_dict(),
                    "type": result["type"], "message": result.get("message", ""),
                    "context": result.get("context", {})})


@api_agent_bp.route("/message/stream", methods=["POST"])
def api_agent_message_stream():
    llm = g.get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "缺少请求数据"}), 400

    user_message = (data.get("message") or "").strip()
    state_dict = data.get("state")
    if not user_message:
        return jsonify({"ok": False, "error": "消息不能为空"}), 400
    if not state_dict:
        return jsonify({"ok": False, "error": "缺少 state"}), 400

    agent = get_or_create_agent(
        llm=llm, exp_repo=g.exp_repo, state_dict=state_dict,
        thread_repo=g.thread_repo, update_log_repo=g.update_log_repo,
        favorites_repo=g.favorites_repo, analysis_repo=g.analysis_repo,
        analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc,
    )

    def generate():
        for event in agent.run_stream(user_message):
            # 检查是否是 final done 事件
            if event.get("event") == "done":
                preview = agent._generated_preview
                if preview is not None:
                    notes = agent._generated_notes or ""
                    preview["id"] = g.exp_repo.next_id()
                    refs = g.experiment_svc.extract_references(notes)
                    preview["references"] = refs
                    g.exp_repo.save(preview)
                    g.experiment_svc.update_referenced_by(preview["id"], refs)
                    g.experiment_svc.move_draft_images(preview["id"])
                    event["exp_id"] = preview["id"]
                    event["type"] = "saved"
                    agent._generated_preview = None
                event["state"] = agent.state_to_dict()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            else:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@api_agent_bp.route("/confirm", methods=["POST"])
def api_agent_confirm():
    return api_parse_confirm()
