"""
Admin Routes — Dashboard, User/Listing/Order management, Settings
"""

from datetime import datetime, timezone, date
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from bson import ObjectId
from functools import wraps

admin_bp = Blueprint("admin", __name__)

def get_db():
    from app import db
    return db

def sms_send(to, msg, **kw):
    from app import send_sms
    return send_sms(to, msg, **kw)

def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def wrapper(*args, **kwargs):
        claims = get_jwt()
        if claims.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return fn(*args, **kwargs)
    return wrapper


@admin_bp.route("/admin/stats", methods=["GET"])
@admin_required
def dashboard_stats():
    db = get_db()
    today_start = datetime.combine(date.today(), datetime.min.time()).replace(tzinfo=timezone.utc)

    total_users = db.users.count_documents({})
    total_listings = db.products.count_documents({"status": "active"})
    orders_today = db.orders.count_documents({"createdAt": {"$gte": today_start}})
    sms_today = db.sms_logs.count_documents({"timestamp": {"$gte": today_start}})

    # Order status breakdown
    statuses = ["pending", "confirmed", "en_route", "arrived", "complete", "cancelled"]
    order_breakdown = {}
    for s in statuses:
        order_breakdown[s] = db.orders.count_documents({"status": s})

    # Recent users
    recent_users = list(db.users.find().sort("createdAt", -1).limit(5))
    for u in recent_users:
        u["_id"] = str(u["_id"])
        u.pop("passwordHash", None)
        if u.get("createdAt"): u["createdAt"] = u["createdAt"].isoformat()

    # Recent orders
    recent_orders = list(db.orders.find().sort("createdAt", -1).limit(5))
    for o in recent_orders:
        o["_id"] = str(o["_id"])
        if o.get("createdAt"): o["createdAt"] = o["createdAt"].isoformat()
        if o.get("completedAt"): o["completedAt"] = o["completedAt"].isoformat()

    # Category breakdown
    cat_pipeline = [
        {"$match": {"status": "active"}},
        {"$group": {"_id": "$category", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}
    ]
    categories = list(db.products.aggregate(cat_pipeline))

    return jsonify({
        "totalUsers": total_users,
        "totalListings": total_listings,
        "ordersToday": orders_today,
        "smsToday": sms_today,
        "orderBreakdown": order_breakdown,
        "recentUsers": recent_users,
        "recentOrders": recent_orders,
        "categoryBreakdown": [{"category": c["_id"], "count": c["count"]} for c in categories]
    }), 200


@admin_bp.route("/admin/users", methods=["GET"])
@admin_required
def list_users():
    db = get_db()
    query = {}
    q = request.args.get("q", "").strip()
    college = request.args.get("college", "").strip()
    role = request.args.get("role", "").strip()
    status = request.args.get("status", "").strip()

    if q:
        query["$or"] = [
            {"firstName": {"$regex": q, "$options": "i"}},
            {"lastName": {"$regex": q, "$options": "i"}},
            {"mobile": {"$regex": q}}
        ]
    if college: query["college"] = {"$regex": college, "$options": "i"}
    if role: query["role"] = role
    if status == "banned": query["banned"] = True
    elif status == "active": query["banned"] = False

    page = max(1, request.args.get("page", 1, type=int))
    limit = min(100, request.args.get("limit", 20, type=int))
    skip = (page - 1) * limit

    total = db.users.count_documents(query)
    users = list(db.users.find(query).sort("createdAt", -1).skip(skip).limit(limit))
    for u in users:
        u["_id"] = str(u["_id"])
        u.pop("passwordHash", None)
        if u.get("createdAt"): u["createdAt"] = u["createdAt"].isoformat()
        if u.get("lastLogin"): u["lastLogin"] = u["lastLogin"].isoformat()

    return jsonify({"users": users, "total": total, "page": page}), 200


@admin_bp.route("/admin/users/<user_id>/ban", methods=["PUT"])
@admin_required
def ban_user(user_id):
    db = get_db()
    data = request.get_json()
    banned = data.get("banned", True)
    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"banned": banned}})
    action = "banned" if banned else "unbanned"
    return jsonify({"message": f"User {action}"}), 200


@admin_bp.route("/admin/users/<user_id>/role", methods=["PUT"])
@admin_required
def change_role(user_id):
    db = get_db()
    data = request.get_json()
    new_role = data.get("role", "user")
    if new_role not in ["user", "admin"]:
        return jsonify({"error": "Invalid role"}), 400
    db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"role": new_role}})
    return jsonify({"message": f"Role changed to {new_role}"}), 200


@admin_bp.route("/admin/users/<user_id>", methods=["DELETE"])
@admin_required
def delete_user(user_id):
    db = get_db()
    db.users.delete_one({"_id": ObjectId(user_id)})
    db.products.delete_many({"sellerId": user_id})
    return jsonify({"message": "User deleted"}), 200


@admin_bp.route("/admin/listings", methods=["GET"])
@admin_required
def list_all_listings():
    db = get_db()
    query = {}
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    status = request.args.get("status", "").strip()

    if q:
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"sellerName": {"$regex": q, "$options": "i"}}
        ]
    if category: query["category"] = category
    if status: query["status"] = status

    page = max(1, request.args.get("page", 1, type=int))
    limit = min(100, request.args.get("limit", 20, type=int))
    skip = (page - 1) * limit

    total = db.products.count_documents(query)
    products = list(db.products.find(query).sort("createdAt", -1).skip(skip).limit(limit))
    for p in products:
        p["_id"] = str(p["_id"])
        if p.get("createdAt"): p["createdAt"] = p["createdAt"].isoformat()

    return jsonify({"listings": products, "total": total, "page": page}), 200


@admin_bp.route("/admin/listings/<product_id>", methods=["PUT"])
@admin_required
def admin_update_listing(product_id):
    db = get_db()
    data = request.get_json()
    allowed = ["status", "featured", "category"]
    update = {k: v for k, v in data.items() if k in allowed}
    db.products.update_one({"_id": ObjectId(product_id)}, {"$set": update})
    return jsonify({"message": "Listing updated"}), 200


@admin_bp.route("/admin/listings/<product_id>", methods=["DELETE"])
@admin_required
def admin_delete_listing(product_id):
    db = get_db()
    db.products.delete_one({"_id": ObjectId(product_id)})
    return jsonify({"message": "Listing deleted"}), 200


@admin_bp.route("/admin/orders", methods=["GET"])
@admin_required
def list_all_orders():
    db = get_db()
    query = {}
    status = request.args.get("status", "").strip()
    if status: query["status"] = status

    page = max(1, request.args.get("page", 1, type=int))
    limit = min(100, request.args.get("limit", 20, type=int))
    skip = (page - 1) * limit

    total = db.orders.count_documents(query)
    orders = list(db.orders.find(query).sort("createdAt", -1).skip(skip).limit(limit))
    for o in orders:
        o["_id"] = str(o["_id"])
        if o.get("createdAt"): o["createdAt"] = o["createdAt"].isoformat()
        if o.get("completedAt"): o["completedAt"] = o["completedAt"].isoformat()

    return jsonify({"orders": orders, "total": total, "page": page}), 200


@admin_bp.route("/admin/orders/<order_id>/force", methods=["PUT"])
@admin_required
def force_order_status(order_id):
    db = get_db()
    data = request.get_json()
    new_status = data.get("status")
    update = {"status": new_status}
    if new_status in ["complete", "cancelled"]:
        update["completedAt"] = datetime.now(timezone.utc)
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": update})
    return jsonify({"message": f"Order forced to {new_status}"}), 200


@admin_bp.route("/admin/settings", methods=["GET"])
@admin_required
def get_settings():
    db = get_db()
    settings = db.settings.find_one()
    if settings:
        settings["_id"] = str(settings["_id"])
    return jsonify(settings or {}), 200


@admin_bp.route("/admin/settings", methods=["POST"])
@admin_required
def save_settings():
    db = get_db()
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400
    existing = db.settings.find_one()
    if existing:
        db.settings.update_one({"_id": existing["_id"]}, {"$set": data})
    else:
        db.settings.insert_one(data)
    return jsonify({"message": "Settings saved"}), 200


@admin_bp.route("/admin/map", methods=["GET"])
@admin_required
def get_active_tracking():
    db = get_db()
    active = list(db.orders.find(
        {"status": {"$in": ["confirmed", "en_route", "arrived"]}},
        {"_id": 1, "sellerName": 1, "buyerName": 1, "productTitle": 1,
         "productEmoji": 1, "status": 1, "createdAt": 1}
    ))
    for o in active:
        o["_id"] = str(o["_id"])
        if o.get("createdAt"): o["createdAt"] = o["createdAt"].isoformat()
    return jsonify({"activeOrders": active}), 200
