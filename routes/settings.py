from flask import Blueprint, request, render_template, g

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        data = {
            "DEEPSEEK_API_KEY": request.form.get("DEEPSEEK_API_KEY", ""),
            "DEEPSEEK_MODEL": request.form.get("DEEPSEEK_MODEL", ""),
            "DEEPSEEK_ANALYZE_MODEL": request.form.get("DEEPSEEK_ANALYZE_MODEL", ""),
            "PORT": request.form.get("PORT", "5000"),
            "HOST": request.form.get("HOST", "0.0.0.0"),
            "GUI": request.form.get("GUI", "false"),
        }
        from app import save_settings
        save_settings(data)
        key = g.config.get("DEEPSEEK_API_KEY", "")
        masked = key[:4] + "****" + key[-4:] if len(key) > 8 else ("*" * len(key))
        return render_template("settings.html", config=g.config,
                               success="设置已保存。端口和 GUI 修改需重启应用生效。",
                               masked_key=masked)

    key = g.config.get("DEEPSEEK_API_KEY", "")
    masked = key[:4] + "****" + key[-4:] if len(key) > 8 else ("*" * len(key))
    return render_template("settings.html", config=g.config, masked_key=masked)
