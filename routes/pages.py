"""Auto-generated from app.py."""
from flask import Blueprint, request, redirect, url_for, jsonify, render_template, send_from_directory, g

pages_bp = Blueprint("pages", __name__)

@pages_bp.route("/analyze")
def analyze_page():
    return render_template("analyze.html")


# ---------------------------------------------------------------------------
# Analysis detail page
# ---------------------------------------------------------------------------

@pages_bp.route("/analysis/<anal_id>")
def view_analysis(anal_id):
    a = g.analysis_repo.load(anal_id)
    if not a:
        return "Analysis not found", 404
    return render_template("analysis_detail.html", analysis=a)


# ---------------------------------------------------------------------------
# Analysis child agent
# ---------------------------------------------------------------------------

