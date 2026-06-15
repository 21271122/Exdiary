import yaml
from flask import Blueprint, request, redirect, url_for, jsonify, render_template, g
from lib.parser import parse_notes, strip_html

experiment_bp = Blueprint("experiment", __name__)


@experiment_bp.route("/<exp_id>")
def view_experiment(exp_id):
    exp = g.exp_repo.load(exp_id)
    if not exp:
        return "Experiment not found", 404
    return render_template("view.html", exp=exp)


@experiment_bp.route("/<exp_id>/yaml")
def view_yaml(exp_id):
    exp = g.exp_repo.load(exp_id)
    if not exp:
        return "Experiment not found", 404
    raw = yaml.dump(exp, allow_unicode=True, sort_keys=False,
                    default_flow_style=False, indent=2)
    return raw, 200, {"Content-Type": "text/plain; charset=utf-8"}


@experiment_bp.route("/<exp_id>/edit", methods=["GET", "POST"])
def edit_experiment(exp_id):
    if request.method == "GET":
        exp = g.exp_repo.load(exp_id)
        if not exp:
            return "Experiment not found", 404
        raw = yaml.dump(exp, allow_unicode=True, sort_keys=False,
                        default_flow_style=False, indent=2)
        return render_template("edit.html", exp_id=exp_id, yaml_raw=raw)

    yaml_text = request.form.get("yaml_content", "")
    try:
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise ValueError("YAML content must be a dictionary")
        old_exp = g.exp_repo.load(exp_id)
        g.exp_repo.update(exp_id, data)
        g.experiment_svc.save_with_log(exp_id, data, "manual_edit")
        return redirect(url_for("experiment.view_experiment", exp_id=exp_id))
    except Exception as e:
        return render_template("edit.html", exp_id=exp_id, yaml_raw=yaml_text,
                               error=f"YAML 解析失败: {str(e)}")


@experiment_bp.route("/<exp_id>/delete", methods=["DELETE"])
def delete_experiment(exp_id):
    g.experiment_svc.delete_with_log(exp_id)
    return "", 200


@experiment_bp.route("/<exp_id>/save-json", methods=["POST"])
def save_experiment_json(exp_id):
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400
    old_exp = g.exp_repo.load(exp_id)
    old_refs = old_exp.get("references", []) if old_exp else []
    g.experiment_svc.save_and_update_refs(exp_id, data, source="manual_edit", old_refs=old_refs)
    return jsonify({"ok": True})


@experiment_bp.route("/<exp_id>/regenerate", methods=["POST"])
def regenerate_experiment(exp_id):
    exp = g.exp_repo.load(exp_id)
    if not exp:
        return jsonify({"ok": False, "error": "Experiment not found"}), 404

    notes_raw = request.form.get("original_notes", "").strip()
    if notes_raw and "<" in notes_raw:
        notes_plain = strip_html(notes_raw)
    else:
        notes_plain = notes_raw
    if not notes_plain or len(notes_plain) < 10:
        return jsonify({"ok": False, "error": "Notes too short"}), 400

    llm = g.get_extract_llm()
    if not llm:
        return jsonify({"ok": False, "error": "No API key configured"}), 500

    try:
        result = parse_notes(notes_plain, llm)
    except Exception as e:
        return jsonify({"ok": False, "error": f"AI processing failed: {str(e)}"}), 500

    result["original_notes"] = notes_raw if notes_raw else notes_plain
    result["id"] = exp_id
    refs = g.experiment_svc.extract_references(notes_raw if notes_raw else notes_plain)
    result["references"] = refs
    old_exp = g.exp_repo.load(exp_id)
    old_refs = old_exp.get("references", []) if old_exp else []
    g.exp_repo.update(exp_id, result)
    g.experiment_svc.update_referenced_by(exp_id, refs, old_refs=old_refs)
    return jsonify({"ok": True})


@experiment_bp.route("/<exp_id>/print")
def print_experiment(exp_id):
    exp = g.exp_repo.load(exp_id)
    if not exp:
        return "Experiment not found", 404
    return render_template("print.html", exp=exp)
