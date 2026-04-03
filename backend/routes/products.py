"""
Products Routes — Full CRUD for marketplace listings
"""

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from bson import ObjectId

products_bp = Blueprint("products", __name__)

def get_db():
    from app import db
    return db

def sms(to, msg, **kw):
    from app import send_sms
    return send_sms(to, msg, **kw)


# ── List Products ──
@products_bp.route("/api/products", methods=["GET"])
def list_products():
    db = get_db()
    query = {}

    # Filters
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    price_min = request.args.get("price_min", type=int)
    price_max = request.args.get("price_max", type=int)
    city = request.args.get("city", "").strip()
    condition = request.args.get("condition", "").strip()
    status = request.args.get("status", "active").strip()
    seller_id = request.args.get("seller_id", "").strip()

    if q:
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
            {"category": {"$regex": q, "$options": "i"}}
        ]
    if category:
        query["category"] = {"$regex": f"^{category}$", "$options": "i"}
    if price_min is not None:
        query.setdefault("price", {})["$gte"] = price_min
    if price_max is not None:
        query.setdefault("price", {})["$lte"] = price_max
    if city:
        query["city"] = {"$regex": city, "$options": "i"}
    if condition:
        query["condition"] = condition
    if status:
        query["status"] = status
    if seller_id:
        query["sellerId"] = seller_id

    # Sort
    sort_by = request.args.get("sort", "latest")
    sort_field = {"latest": ("createdAt", -1), "price_asc": ("price", 1),
                  "price_desc": ("price", -1), "rating": ("sellerRating", -1)}
    sort_key, sort_dir = sort_field.get(sort_by, ("createdAt", -1))

    # Pagination
    page = max(1, request.args.get("page", 1, type=int))
    limit = min(50, max(1, request.args.get("limit", 20, type=int)))
    skip = (page - 1) * limit

    total = db.products.count_documents(query)
    products = list(db.products.find(query).sort(sort_key, sort_dir).skip(skip).limit(limit))

    for p in products:
        p["_id"] = str(p["_id"])
        if p.get("createdAt"):
            p["createdAt"] = p["createdAt"].isoformat()

    return jsonify({
        "products": products,
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }), 200


# ── Get Single Product ──
@products_bp.route("/api/products/<product_id>", methods=["GET"])
def get_product(product_id):
    db = get_db()
    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
    except Exception:
        return jsonify({"error": "Invalid product ID"}), 400

    if not product:
        return jsonify({"error": "Product not found"}), 404

    # Increment views
    db.products.update_one({"_id": ObjectId(product_id)}, {"$inc": {"views": 1}})

    product["_id"] = str(product["_id"])
    if product.get("createdAt"):
        product["createdAt"] = product["createdAt"].isoformat()

    return jsonify(product), 200


# ── Create Product ──
@products_bp.route("/api/products", methods=["POST"])
@jwt_required()
def create_product():
    identity = get_jwt_identity()
    claims = get_jwt()
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ["title", "price", "category"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} is required"}), 400

    db = get_db()
    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    now = datetime.now(timezone.utc)
    product = {
        "title": data["title"].strip(),
        "description": data.get("description", "").strip(),
        "price": int(data["price"]),
        "negotiable": data.get("negotiable", False),
        "category": data["category"].strip(),
        "condition": data.get("condition", "Good"),
        "images": data.get("images", []),
        "emoji": data.get("emoji", "📦"),
        "sellerId": identity,
        "sellerName": f"{user['firstName']} {user.get('lastName', '')}".strip(),
        "sellerRating": user.get("rating", 0),
        "location": user.get("college", ""),
        "city": user.get("city", ""),
        "status": "active",
        "views": 0,
        "favorites": 0,
        "featured": False,
        "createdAt": now
    }

    result = db.products.insert_one(product)
    product["_id"] = str(result.inserted_id)
    product["createdAt"] = now.isoformat()

    # SMS to seller confirming listing
    sms(
        user["mobile"],
        f"Your item '{product['title']}' is now LIVE on Student Bazar! View: studentbazar.in/item/{product['_id']} -SB Team",
        msg_type="listing_posted",
        user_id=identity,
        related_id=product["_id"]
    )

    return jsonify({"message": "Product listed successfully", "product": product}), 201


# ── Update Product ──
@products_bp.route("/api/products/<product_id>", methods=["PUT"])
@jwt_required()
def update_product(product_id):
    identity = get_jwt_identity()
    claims = get_jwt()
    db = get_db()

    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
    except Exception:
        return jsonify({"error": "Invalid product ID"}), 400

    if not product:
        return jsonify({"error": "Product not found"}), 404

    if product["sellerId"] != identity and claims.get("role") != "admin":
        return jsonify({"error": "Not authorized"}), 403

    data = request.get_json()
    allowed = ["title", "description", "price", "negotiable", "category",
               "condition", "images", "emoji", "status"]
    update = {k: v for k, v in data.items() if k in allowed}

    if "price" in update:
        update["price"] = int(update["price"])

    db.products.update_one({"_id": ObjectId(product_id)}, {"$set": update})
    return jsonify({"message": "Product updated"}), 200


# ── Delete Product ──
@products_bp.route("/api/products/<product_id>", methods=["DELETE"])
@jwt_required()
def delete_product(product_id):
    identity = get_jwt_identity()
    claims = get_jwt()
    db = get_db()

    try:
        product = db.products.find_one({"_id": ObjectId(product_id)})
    except Exception:
        return jsonify({"error": "Invalid product ID"}), 400

    if not product:
        return jsonify({"error": "Product not found"}), 404

    if product["sellerId"] != identity and claims.get("role") != "admin":
        return jsonify({"error": "Not authorized"}), 403

    db.products.delete_one({"_id": ObjectId(product_id)})
    return jsonify({"message": "Product deleted"}), 200


# ── Boost Product (spend coins) ──
@products_bp.route("/api/products/<product_id>/boost", methods=["POST"])
@jwt_required()
def boost_product(product_id):
    identity = get_jwt_identity()
    db = get_db()

    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    product = db.products.find_one({"_id": ObjectId(product_id)})
    if not product:
        return jsonify({"error": "Product not found"}), 404

    if product["sellerId"] != identity:
        return jsonify({"error": "Not your product"}), 403

    boost_cost = 100  # coins
    if user.get("coins", 0) < boost_cost:
        return jsonify({"error": f"Not enough coins. Need {boost_cost}, have {user.get('coins', 0)}"}), 400

    # Deduct coins
    db.users.update_one({"_id": ObjectId(identity)}, {"$inc": {"coins": -boost_cost}})
    db.products.update_one({"_id": ObjectId(product_id)}, {"$set": {"featured": True, "status": "active"}})

    # Log coin spend
    db.coins_log.insert_one({
        "userId": identity,
        "type": "spend",
        "amount": boost_cost,
        "reason": f"Boosted listing: {product['title']}",
        "balanceBefore": user["coins"],
        "balanceAfter": user["coins"] - boost_cost,
        "timestamp": datetime.now(timezone.utc)
    })

    return jsonify({"message": "Product boosted!", "coinsRemaining": user["coins"] - boost_cost}), 200


# ── Favorites ──
@products_bp.route("/api/favorites", methods=["GET"])
@jwt_required()
def get_favorites():
    identity = get_jwt_identity()
    db = get_db()
    favs = list(db.favorites.find({"userId": identity}))

    product_ids = [ObjectId(f["productId"]) for f in favs if f.get("productId")]
    products = list(db.products.find({"_id": {"$in": product_ids}}))

    for p in products:
        p["_id"] = str(p["_id"])
        if p.get("createdAt"):
            p["createdAt"] = p["createdAt"].isoformat()

    return jsonify({"favorites": products}), 200


@products_bp.route("/api/favorites/<product_id>", methods=["POST"])
@jwt_required()
def add_favorite(product_id):
    identity = get_jwt_identity()
    db = get_db()

    existing = db.favorites.find_one({"userId": identity, "productId": product_id})
    if existing:
        return jsonify({"message": "Already in favorites"}), 200

    db.favorites.insert_one({
        "userId": identity,
        "productId": product_id,
        "createdAt": datetime.now(timezone.utc)
    })
    db.products.update_one({"_id": ObjectId(product_id)}, {"$inc": {"favorites": 1}})

    return jsonify({"message": "Added to favorites"}), 201


@products_bp.route("/api/favorites/<product_id>", methods=["DELETE"])
@jwt_required()
def remove_favorite(product_id):
    identity = get_jwt_identity()
    db = get_db()

    db.favorites.delete_one({"userId": identity, "productId": product_id})
    db.products.update_one({"_id": ObjectId(product_id)}, {"$inc": {"favorites": -1}})

    return jsonify({"message": "Removed from favorites"}), 200


# ── Coins ──
@products_bp.route("/api/coins", methods=["GET"])
@jwt_required()
def get_coins():
    identity = get_jwt_identity()
    db = get_db()
    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    logs = list(db.coins_log.find({"userId": identity}).sort("timestamp", -1).limit(20))
    for l in logs:
        l["_id"] = str(l["_id"])
        if l.get("timestamp"):
            l["timestamp"] = l["timestamp"].isoformat()

    return jsonify({"balance": user.get("coins", 0), "logs": logs}), 200


@products_bp.route("/api/coins/earn", methods=["POST"])
@jwt_required()
def earn_coins():
    identity = get_jwt_identity()
    data = request.get_json()
    amount = data.get("amount", 0)
    reason = data.get("reason", "Reward")

    db = get_db()
    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    before = user.get("coins", 0)
    after = before + amount

    db.users.update_one({"_id": ObjectId(identity)}, {"$set": {"coins": after}})
    db.coins_log.insert_one({
        "userId": identity, "type": "earn", "amount": amount,
        "reason": reason, "balanceBefore": before, "balanceAfter": after,
        "timestamp": datetime.now(timezone.utc)
    })

    sms(
        user["mobile"],
        f"You earned {amount} Bazar Coins! Balance: {after}. Use coins to boost listings. -SB",
        msg_type="coin_earned", user_id=identity
    )

    return jsonify({"message": f"+{amount} coins", "balance": after}), 200


@products_bp.route("/api/coins/spend", methods=["POST"])
@jwt_required()
def spend_coins():
    identity = get_jwt_identity()
    data = request.get_json()
    amount = data.get("amount", 0)
    reason = data.get("reason", "Spend")

    db = get_db()
    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    before = user.get("coins", 0)
    if before < amount:
        return jsonify({"error": "Insufficient coins"}), 400

    after = before - amount
    db.users.update_one({"_id": ObjectId(identity)}, {"$set": {"coins": after}})
    db.coins_log.insert_one({
        "userId": identity, "type": "spend", "amount": amount,
        "reason": reason, "balanceBefore": before, "balanceAfter": after,
        "timestamp": datetime.now(timezone.utc)
    })

    return jsonify({"message": f"-{amount} coins", "balance": after}), 200
