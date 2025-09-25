#!/usr/bin/env python3
import os
from flask import Flask, request, jsonify
from product_tracker import ProductTracker
from scheduler import scheduler
from flask_cors import CORS


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret")
    tracker = ProductTracker()
    
    # Enable permissive CORS for frontend/API consumers
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Start scheduler if enabled via env (safe on single-worker)
    if os.environ.get("ENABLE_SCHEDULER", "0").strip().lower() in ("1", "true", "yes"): 
        try:
            scheduler.start_scheduler()
        except Exception:
            pass

    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # Add a minimal home route so '/' doesn't 404
    @app.route("/")
    def home():
        return jsonify({"message": "Bol tracker API", "try": ["/health", "/api/products"]})

    # Add a favicon route to avoid automatic 404s from browsers
    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    # --- JSON APIs ---
    @app.route("/api/products", methods=["GET"])  # list products (active only)
    def api_list_products():
        rows = tracker.list_tracked_products(include_inactive=False)
        return jsonify([
            {
                "id": r[0],
                "keyword": r[1],
                "product_id": r[2],
                "product_url": r[3],
                "product_name": r[4],
                "active": bool(r[5]),
                "created_at": r[6],
                "stop_after_days": r[7],
                "daily_scheduler": bool(r[8]),
            }
            for r in rows
        ])

    @app.route("/api/products", methods=["POST"])  # add product
    def api_add_product():
        payload = request.get_json(silent=True) or {}
        keyword = (payload.get("keyword") or "").strip()
        product_url = (payload.get("product_url") or "").strip()
        stop_after_days = payload.get("stop_after_days")
        if not keyword or not product_url:
            return jsonify({"error": "keyword and product_url are required"}), 400
        try:
            created = tracker.add_tracked_product(keyword, product_url, stop_after_days=stop_after_days)
            return jsonify(created), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/products/<int:pid>", methods=["DELETE"])  # remove product (soft-delete)
    def api_remove_product(pid: int):
        try:
            tracker.remove_tracked_product(pid)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/check/<int:tracked_id>", methods=["POST"])  # check one now
    def api_check_one(tracked_id: int):
        try:
            product = tracker.get_tracked_product(tracked_id)
            if not product:
                return jsonify({"error": "not found"}), 404
            keyword, product_id = product[1], product[2]
            result = tracker.find_product_ranking(keyword, product_id)
            product_name = product[4] or tracker._extract_name_from_url(product[3])
            if result.get("product") and result["product"].get("name") and result["product"]["name"] != "Unknown Product":
                product_name = result["product"]["name"]
            tracker.save_to_db(keyword, product_id, product_name, result.get("position"), result.get("page_product"))
            return jsonify({"ok": True, **result})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/run-now", methods=["POST"])  # run all scheduled now
    def api_run_now():
        try:
            results = tracker.run_scheduled_checks()
            return jsonify({"results": results})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/toggle-scheduler/<int:tracked_id>", methods=["POST"])  # toggle per-product
    def api_toggle_scheduler(tracked_id: int):
        try:
            new_state = tracker.toggle_daily_scheduler(tracked_id)
            return jsonify({"daily_scheduler": bool(new_state)})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/scheduler/status")
    def api_scheduler_status():
        return jsonify(scheduler.get_scheduler_status())

    @app.route("/api/scheduler/start", methods=["POST"])  # start global scheduler
    def api_scheduler_start():
        try:
            scheduler.start_scheduler()
            return jsonify({"running": True, **scheduler.get_scheduler_status()})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/scheduler/stop", methods=["POST"])  # stop global scheduler
    def api_scheduler_stop():
        try:
            scheduler.stop_scheduler()
            return jsonify({"running": False, **scheduler.get_scheduler_status()})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, use_reloader=False)

