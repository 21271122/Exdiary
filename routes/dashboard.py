"""Auto-generated from app.py."""
from flask import Blueprint, request, redirect, url_for, jsonify, render_template, send_from_directory, g

dashboard_bp = Blueprint("dashboard", __name__)

@dashboard_bp.route("/")
def index():
    experiments = g.exp_repo.list_all()
    pinned_ids = g.favorites_repo.get_pinned()
    # Separate pinned experiments (keep order) and the rest
    pinned = []
    others = []
    for exp in experiments:
        if exp["id"] in pinned_ids:
            pinned.append(exp)
        else:
            others.append(exp)
    # Sort pinned by pin order
    pinned.sort(key=lambda e: pinned_ids.index(e["id"]) if e["id"] in pinned_ids else 99)
    # Merge: pinned first, then others
    sorted_experiments = pinned + others

    # Build recent experiment snippets for the dashboard tiles
    import json as _json
    recent_snippets = []
    for exp in g.exp_repo.list_all_full()[:8]:
        snippet = {
            "id": exp.get("id"),
            "title": (exp.get("title") or "")[:40],
            "date": exp.get("date", ""),
            "status": exp.get("status", ""),
            "tags": exp.get("tags", [])[:3],
            "conclusion": (exp.get("conclusion") or "")[:60],
        }
        recent_snippets.append(snippet)

    return render_template("index.html", experiments=sorted_experiments,
                           pinned_ids=pinned_ids,
                           recent_snippets_json=_json.dumps(recent_snippets, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Experiment list page (traditional card view)
# ---------------------------------------------------------------------------

@dashboard_bp.route("/experiments")
def experiment_list():
    experiments = g.exp_repo.list_all()
    pinned_ids = g.favorites_repo.get_pinned()
    pinned = []
    others = []
    for exp in experiments:
        if exp["id"] in pinned_ids:
            pinned.append(exp)
        else:
            others.append(exp)
    pinned.sort(key=lambda e: pinned_ids.index(e["id"]) if e["id"] in pinned_ids else 99)
    sorted_experiments = pinned + others
    return render_template("experiments.html", experiments=sorted_experiments,
                           pinned_ids=pinned_ids)


# ---------------------------------------------------------------------------
# New experiment form
# ---------------------------------------------------------------------------

@dashboard_bp.route("/new")
def new_experiment():
    return render_template("new.html")


# ---------------------------------------------------------------------------
# Parse natural language notes -> structured experiment
# ---------------------------------------------------------------------------

@dashboard_bp.route("/timeline")
def timeline():
    experiments = g.exp_repo.list_all_full()
    experiments.sort(key=lambda e: e.get("date") or "")
    return render_template("timeline.html", experiments=experiments)


# ---------------------------------------------------------------------------
# Analysis page — history list
# ---------------------------------------------------------------------------

@dashboard_bp.route("/compare")
def compare_experiments():
    ids_raw = request.args.get("ids", "")
    ids = [s.strip() for s in ids_raw.split(",") if s.strip()]
    if len(ids) < 2:
        return redirect(url_for("index"))
    experiments = []
    for eid in ids[:4]:  # max 4
        exp = g.exp_repo.load(eid)
        if exp:
            experiments.append(exp)
    if len(experiments) < 2:
        return redirect(url_for("index"))
    # Assign colors
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea"]  # blue, red, green, purple
    for i, exp in enumerate(experiments):
        exp["_color"] = colors[i % len(colors)]
    return render_template("compare.html", experiments=experiments, color_names=["蓝", "红", "绿", "紫"])


# ---------------------------------------------------------------------------
# Agent API v2 — conversational experiment recording (tool-calling)
# ---------------------------------------------------------------------------


@dashboard_bp.route("/api/favorites")
def favorites_page():
    collections = g.favorites_repo.get_collections()
    return render_template("favorites.html", collections=collections)



