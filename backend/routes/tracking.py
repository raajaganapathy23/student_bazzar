"""
Tracking Routes — Firebase location helpers
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity

tracking_bp = Blueprint("tracking", __name__)

def get_db():
    from app import db
    return db


@tracking_bp.route("/api/tracking/<order_id>", methods=["GET"])
@jwt_required()
def get_tracking_data(order_id):
    """Return tracking metadata for an order. 
    Actual live GPS data comes from Firebase Realtime DB on the frontend."""
    db = get_db()
    from bson import ObjectId
    
    try:
        order = db.orders.find_one({"_id": ObjectId(order_id)})
    except Exception:
        return jsonify({"error": "Invalid order ID"}), 400

    if not order:
        return jsonify({"error": "Order not found"}), 404

    return jsonify({
        "orderId": order_id,
        "status": order["status"],
        "sellerName": order.get("sellerName", ""),
        "sellerPhone": order.get("sellerPhone", ""),
        "buyerName": order.get("buyerName", ""),
        "buyerPhone": order.get("buyerPhone", ""),
        "productTitle": order.get("productTitle", ""),
        "productEmoji": order.get("productEmoji", "📦"),
        "meetupPoint": order.get("meetupPoint", ""),
        "firebaseRef": f"tracking/{order_id}",
        "trackable": order["status"] in ["confirmed", "en_route", "arrived"]
    }), 200


@tracking_bp.route("/api/tracking/<order_id>/stop", methods=["POST"])
@jwt_required()
def stop_tracking(order_id):
    """Mark tracking as stopped in DB. Frontend handles Firebase cleanup."""
    db = get_db()
    from bson import ObjectId

    try:
        order = db.orders.find_one({"_id": ObjectId(order_id)})
    except Exception:
        return jsonify({"error": "Invalid order ID"}), 400

    if not order:
        return jsonify({"error": "Order not found"}), 404

    return jsonify({"message": "Tracking stopped", "firebaseRef": f"tracking/{order_id}"}), 200
