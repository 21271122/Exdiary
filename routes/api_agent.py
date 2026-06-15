from flask import Blueprint, request, jsonify, g
from lib.agent_v2 import AgentLoop
from routes.api_experiment import api_parse_confirm

api_agent_bp = Blueprint("api_agent", __name__)


@api_agent_bp.route("/start", methods=["POST"])
def api_agent_start():
    llm = g.get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    saved = g.thread_repo.load_current_state()
    if saved:
        agent = AgentLoop.from_dict(llm, g.exp_repo, saved,
                                    thread_store=g.thread_repo,
                                    update_log_store=g.update_log_repo,
                                    favorites_store=g.favorites_repo,
                                    analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
        return jsonify({"ok": True, "state": agent.state_to_dict(),
                        "type": "reply", "message": None, "greeting": None})

    agent = AgentLoop(llm, g.exp_repo, thread_store=g.thread_repo,
                      update_log_store=g.update_log_repo,
                      favorites_store=g.favorites_repo,
                      analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
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

    agent = AgentLoop.from_dict(llm, g.exp_repo, state_dict,
                                thread_store=g.thread_repo,
                                update_log_store=g.update_log_repo,
                                favorites_store=g.favorites_repo,
                                analysis_store=g.analysis_repo, analysis_svc=g.analysis_svc, extraction_svc=g.extraction_svc)
    result = agent.run(user_message)

    if result["type"] in ("extract", "generate"):
        notes = result.get("notes") or _build_notes_from_context(result.get("context", {}))
        preview = result.get("preview") or _extract_or_fallback(notes, result.get("context", {}), agent)
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


@api_agent_bp.route("/confirm", methods=["POST"])
def api_agent_confirm():
    return api_parse_confirm()


def _build_notes_from_context(context: dict) -> str:
    parts = []
    if context.get("title"): parts.append(f"实验标题: {context['title']}")
    if context.get("purpose"): parts.append(f"实验目的: {context['purpose']}")
    materials = context.get("materials", [])
    if materials:
        lines = ["材料与试剂:"]
        for m in materials:
            if isinstance(m, dict):
                name = m.get("name", "")
                purity = f", 纯度 {m['purity']}" if m.get("purity") else ""
                vendor = f", {m['vendor']}" if m.get("vendor") else ""
                amount = f", {m['amount']}" if m.get("amount") else ""
                lines.append(f"  - {name}{purity}{vendor}{amount}")
        parts.append("\n".join(lines))
    sop = context.get("sop", [])
    if sop:
        lines = ["实验步骤:"]
        for i, s in enumerate(sop, 1): lines.append(f"  {i}. {s}")
        parts.append("\n".join(lines))
    params = context.get("process_parameters", [])
    if params:
        lines = ["过程参数:"]
        for p in params:
            if isinstance(p, dict): lines.append(f"  - {p.get('parameter', '')}: {p.get('setpoint', '')}")
        parts.append("\n".join(lines))
    results = context.get("results", {})
    if isinstance(results, dict):
        if results.get("qualitative"): parts.append(f"定性结果: {results['qualitative']}")
        kd = results.get("key_data", [])
        if kd:
            lines = ["关键数据:"]
            for k in kd:
                if isinstance(k, dict): lines.append(f"  - {k.get('metric', '')}: {k.get('value', '')}")
            parts.append("\n".join(lines))
    if context.get("conclusion"): parts.append(f"结论: {context['conclusion']}")
    if context.get("next_steps"):
        parts.append("下一步: " + "; ".join(str(s) for s in context["next_steps"]))
    return "\n\n".join(parts) if parts else "（无实验描述）"


def _extract_or_fallback(notes, context, agent):
    extract_llm = g.get_extract_llm()
    if extract_llm:
        try:
            from lib.parser import parse_notes
            result = parse_notes(notes, extract_llm)
            result["original_notes"] = notes
            result["id"] = g.exp_repo.next_id()
            result["references"] = list(agent.references)
            return result
        except Exception:
            pass
    return {"id": g.exp_repo.next_id(), "title": context.get("title", ""),
            "purpose": context.get("purpose", ""), "materials": context.get("materials", []),
            "sop": context.get("sop", []), "process_parameters": context.get("process_parameters", []),
            "observations": context.get("observations", {"no_anomalies": True, "items": []}),
            "results": context.get("results", {}), "conclusion": context.get("conclusion", ""),
            "next_steps": context.get("next_steps", []), "tags": context.get("tags", []),
            "status": context.get("status", "planned"), "original_notes": notes,
            "references": list(agent.references)}
