from flask import Blueprint, jsonify, render_template, g

templates_bp = Blueprint("templates", __name__)


@templates_bp.route("/templates")
def template_library():
    templates = g.template_svc.list_all()
    return render_template("templates.html", templates=templates)


@templates_bp.route("/api/templates/<template_id>")
def api_get_template(template_id):
    tmpl = g.template_svc.load(template_id)
    if not tmpl:
        return jsonify({"ok": False, "error": "Template not found"}), 404
    return jsonify({"ok": True, "title": tmpl.get("title", ""),
                    "content": tmpl.get("content", "")})
