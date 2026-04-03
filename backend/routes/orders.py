"""
Orders Routes — Place, list, update status, track
"""

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId

orders_bp = Blueprint("orders", __name__)

def get_db():
    from app import db
    return db

def sms(to, msg, **kw):
    from app import send_sms
    return send_sms(to, msg, **kw)


# ── Place Order ──
@orders_bp.route("/api/orders", methods=["POST"])
@jwt_required()
def place_order():
    identity = get_jwt_identity()
    data = request.get_json()

    if not data or not data.get("productId"):
        return jsonify({"error": "productId is required"}), 400

    db = get_db()
    buyer = db.users.find_one({"_id": ObjectId(identity)})
    if not buyer:
        return jsonify({"error": "User not found"}), 404

    product = db.products.find_one({"_id": ObjectId(data["productId"])})
    if not product:
        return jsonify({"error": "Product not found"}), 404

    if product["sellerId"] == identity:
        return jsonify({"error": "Cannot buy your own item"}), 400

    if product["status"] != "active":
        return jsonify({"error": "Product is not available"}), 400

    seller = db.users.find_one({"_id": ObjectId(product["sellerId"])})

    now = datetime.now(timezone.utc)
    order = {
        "productId": str(product["_id"]),
        "productTitle": product["title"],
        "productEmoji": product.get("emoji", "📦"),
        "productPrice": product["price"],
        "buyerId": identity,
        "buyerName": f"{buyer['firstName']} {buyer.get('lastName', '')}".strip(),
        "buyerPhone": buyer["mobile"],
        "sellerId": product["sellerId"],
        "sellerName": product.get("sellerName", "Seller"),
        "sellerPhone": seller["mobile"] if seller else "",
        "price": data.get("offeredPrice", product["price"]),
        "status": "pending",
        "meetupPoint": data.get("meetupPoint", ""),
        "instructions": data.get("instructions", ""),
        "createdAt": now,
        "completedAt": None
    }

    result = db.orders.insert_one(order)
    order["_id"] = str(result.inserted_id)
    order["createdAt"] = now.isoformat()

    # SMS to seller
    if seller:
        sms(
            seller["mobile"],
            f"New order! {buyer['firstName']} wants to buy '{product['title']}' for ₹{order['price']}. Confirm on Student Bazar. -SB",
            msg_type="order_placed",
            user_id=identity,
            related_id=order["_id"]
        )

    return jsonify({"message": "Order placed successfully", "order": order}), 201


# ── Get User's Orders ──
@orders_bp.route("/api/orders", methods=["GET"])
@jwt_required()
def get_orders():
    identity = get_jwt_identity()
    db = get_db()

    role = request.args.get("role", "buyer")
    if role == "seller":
        query = {"sellerId": identity}
    else:
        query = {"buyerId": identity}

    status_filter = request.args.get("status")
    if status_filter:
        query["status"] = status_filter

    orders = list(db.orders.find(query).sort("createdAt", -1))
    for o in orders:
        o["_id"] = str(o["_id"])
        if o.get("createdAt"):
            o["createdAt"] = o["createdAt"].isoformat()
        if o.get("completedAt"):
            o["completedAt"] = o["completedAt"].isoformat()

    return jsonify({"orders": orders}), 200


# ── Get Single Order ──
@orders_bp.route("/api/orders/<order_id>", methods=["GET"])
@jwt_required()
def get_order(order_id):
    identity = get_jwt_identity()
    db = get_db()

    try:
        order = db.orders.find_one({"_id": ObjectId(order_id)})
    except Exception:
        return jsonify({"error": "Invalid order ID"}), 400

    if not order:
        return jsonify({"error": "Order not found"}), 404

    if order["buyerId"] != identity and order["sellerId"] != identity:
        return jsonify({"error": "Not authorized"}), 403

    order["_id"] = str(order["_id"])
    if order.get("createdAt"):
        order["createdAt"] = order["createdAt"].isoformat()
    if order.get("completedAt"):
        order["completedAt"] = order["completedAt"].isoformat()

    return jsonify(order), 200


# ── Update Order Status ──
@orders_bp.route("/api/orders/<order_id>/status", methods=["PUT"])
@jwt_required()
def update_order_status(order_id):
    identity = get_jwt_identity()
    data = request.get_json()
    new_status = data.get("status")

    valid_statuses = ["pending", "confirmed", "en_route", "arrived", "complete", "cancelled"]
    if new_status not in valid_statuses:
        return jsonify({"error": f"Invalid status. Must be one of: {valid_statuses}"}), 400

    db = get_db()
    try:
        order = db.orders.find_one({"_id": ObjectId(order_id)})
    except Exception:
        return jsonify({"error": "Invalid order ID"}), 400

    if not order:
        return jsonify({"error": "Order not found"}), 404

    update = {"status": new_status}

    if new_status == "complete":
        update["completedAt"] = datetime.now(timezone.utc)

        # Award coins to seller
        seller = db.users.find_one({"_id": ObjectId(order["sellerId"])})
        if seller:
            coins_earned = 50
            before = seller.get("coins", 0)
            db.users.update_one(
                {"_id": ObjectId(order["sellerId"])},
                {"$inc": {"coins": coins_earned}}
            )
            db.coins_log.insert_one({
                "userId": order["sellerId"], "type": "earn",
                "amount": coins_earned,
                "reason": f"Sale completed: {order['productTitle']}",
                "balanceBefore": before,
                "balanceAfter": before + coins_earned,
                "timestamp": datetime.now(timezone.utc)
            })

            # SMS to seller
            sms(
                seller["mobile"],
                f"Trade complete! +{coins_earned} coins added to your wallet. -SB",
                msg_type="trade_complete",
                user_id=order["sellerId"],
                related_id=order_id
            )

        # SMS to buyer
        buyer = db.users.find_one({"_id": ObjectId(order["buyerId"])})
        if buyer and seller:
            sms(
                buyer["mobile"],
                f"Trade complete! Rate {seller['firstName']}: studentbazar.in/rate/{order_id}",
                msg_type="trade_complete",
                user_id=order["buyerId"],
                related_id=order_id
            )

        # Mark product as sold
        db.products.update_one(
            {"_id": ObjectId(order["productId"])},
            {"$set": {"status": "sold"}}
        )

        # Item sold SMS
        if seller:
            sms(
                seller["mobile"],
                f"Congratulations! Your item '{order['productTitle']}' is sold for ₹{order['price']}! +{coins_earned} coins added. -SB Team",
                msg_type="item_sold",
                user_id=order["sellerId"],
                related_id=order_id
            )

    elif new_status == "confirmed":
        buyer = db.users.find_one({"_id": ObjectId(order["buyerId"])})
        seller = db.users.find_one({"_id": ObjectId(order["sellerId"])})
        if buyer and seller:
            sms(
                buyer["mobile"],
                f"Seller confirmed! {seller['firstName']} is heading to the meetup point. Track live: studentbazar.in/track/{order_id} -SB",
                msg_type="order_confirmed",
                user_id=order["buyerId"],
                related_id=order_id
            )

    elif new_status == "en_route":
        buyer = db.users.find_one({"_id": ObjectId(order["buyerId"])})
        if buyer:
            sms(
                buyer["mobile"],
                f"Your seller is on the way! Check live map: studentbazar.in/track/{order_id}",
                msg_type="seller_en_route",
                user_id=order["buyerId"],
                related_id=order_id
            )

    elif new_status == "cancelled":
        update["completedAt"] = datetime.now(timezone.utc)
        # Restore product status
        db.products.update_one(
            {"_id": ObjectId(order["productId"])},
            {"$set": {"status": "active"}}
        )

    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": update})

    return jsonify({"message": f"Order status updated to {new_status}"}), 200


# ── Get Tracking Info ──
@orders_bp.route("/api/orders/<order_id>/track", methods=["GET"])
@jwt_required()
def get_tracking(order_id):
    db = get_db()
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
        "buyerPhone": order.get("buyerPhone", ""),
        "firebaseRef": f"tracking/{order_id}",
        "productTitle": order.get("productTitle", ""),
        "productEmoji": order.get("productEmoji", "📦")
    }), 200


# ── Rate Order ──
@orders_bp.route("/api/orders/<order_id>/rate", methods=["POST"])
@jwt_required()
def rate_order(order_id):
    identity = get_jwt_identity()
    data = request.get_json()
    rating = data.get("rating", 0)
    review = data.get("review", "")

    if not (1 <= rating <= 5):
        return jsonify({"error": "Rating must be between 1 and 5"}), 400

    db = get_db()
    order = db.orders.find_one({"_id": ObjectId(order_id)})
    if not order:
        return jsonify({"error": "Order not found"}), 404

    if order["status"] != "complete":
        return jsonify({"error": "Can only rate completed orders"}), 400

    # Determine who is being rated
    if identity == order["buyerId"]:
        rated_user_id = order["sellerId"]
    elif identity == order["sellerId"]:
        rated_user_id = order["buyerId"]
    else:
        return jsonify({"error": "Not authorized"}), 403

    rated_user = db.users.find_one({"_id": ObjectId(rated_user_id)})
    if rated_user:
        old_rating = rated_user.get("rating", 0)
        old_count = rated_user.get("ratingCount", 0)
        new_count = old_count + 1
        new_rating = ((old_rating * old_count) + rating) / new_count

        db.users.update_one(
            {"_id": ObjectId(rated_user_id)},
            {"$set": {"rating": round(new_rating, 1), "ratingCount": new_count}}
        )

    # Save review
    db.reviews.insert_one({
        "orderId": order_id,
        "raterId": identity,
        "ratedUserId": rated_user_id,
        "rating": rating,
        "review": review,
        "timestamp": datetime.now(timezone.utc)
    })

    return jsonify({"message": "Rating submitted"}), 200
