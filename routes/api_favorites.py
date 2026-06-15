from flask import Blueprint, request, jsonify, g

api_favorites_bp = Blueprint("api_favorites", __name__)


@api_favorites_bp.route("/experiments/<exp_id>/pin", methods=["POST"])
def api_toggle_pin(exp_id):
    return jsonify(g.favorites_repo.toggle_pin(exp_id))


@api_favorites_bp.route("/experiments/<exp_id>/favorite", methods=["POST"])
def api_toggle_favorite(exp_id):
    collection = request.json.get("collection", "默认收藏夹") if request.json else "默认收藏夹"
    return jsonify(g.favorites_repo.toggle_favorite(exp_id, collection))


@api_favorites_bp.route("/list-collections")
def api_list_collections():
    return jsonify(g.favorites_repo.get_collections())


@api_favorites_bp.route("/collections", methods=["POST"])
def api_create_collection():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"ok": False, "error": "收藏夹名称不能为空"}), 400
    return jsonify(g.favorites_repo.create_collection(data["name"].strip()))


@api_favorites_bp.route("/collections/<name>", methods=["DELETE"])
def api_delete_collection(name):
    return jsonify(g.favorites_repo.delete_collection(name))
