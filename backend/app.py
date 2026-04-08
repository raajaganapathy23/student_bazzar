import sys
try:
    import eventlet
    eventlet.monkey_patch()
    ASYNC_MODE = "eventlet"
except Exception as e:
    print(f"Eventlet failed to start, falling back to threading: {e}")
    ASYNC_MODE = "threading"

import os
import json
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.local import LocalProxy
from dotenv import load_dotenv

# ── Load Environment ──
load_dotenv()
from flask_jwt_extended import JWTManager
from flask_pymongo import PyMongo
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config

# ════════════════════════════════════════
# App Factory
# ════════════════════════════════════════
app = Flask(__name__, static_folder="../frontend", static_url_path="")
app.config.from_object(Config)
app.config["MONGO_CONNECT"] = False  # Critical for Eventlet compatibility

# ── Extensions ──
cors = CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]},
                            r"/admin/*": {"origins": app.config["CORS_ORIGINS"]}})
jwt = JWTManager(app)
mongo = PyMongo(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=ASYNC_MODE)
limiter = Limiter(get_remote_address, app=app,
                  default_limits=[app.config["RATELIMIT_DEFAULT"]],
                  storage_uri=app.config["RATELIMIT_STORAGE_URI"])

# Expose db dynamically to ensure it's not None at import time
db = LocalProxy(lambda: mongo.db)

# ════════════════════════════════════════
# SMS Utility (used by all route modules)
# ════════════════════════════════════════
def send_sms(to_number, message, msg_type="manual", user_id=None, related_id=None):
    """Send SMS via Twilio or log in demo mode. Always logs to MongoDB."""
    log_entry = {
        "to": to_number,
        "message": message,
        "type": msg_type,
        "sid": None,
        "status": "demo",
        "error_code": None,
        "cost": None,
        "currency": "USD",
        "timestamp": datetime.now(timezone.utc),
        "userId": user_id,
        "relatedId": related_id,
        "retryCount": 0
    }

    if app.config.get("SMS_DEMO_MODE", True):
        print(f"[SMS DEMO] To: +91{to_number} | {message}")
        log_entry["status"] = "demo_sent"
        try:
            db.sms_logs.insert_one(log_entry)
        except Exception as e:
            print(f"DB Logging Error (Demo): {e}")
        return {"success": True, "sid": "demo", "demo": True}

    try:
        from twilio.rest import Client
        client = Client(app.config["TWILIO_ACCOUNT_SID"], app.config["TWILIO_AUTH_TOKEN"])
        msg = client.messages.create(
            body=message,
            from_=app.config["TWILIO_FROM_NUMBER"],
            to="+91" + str(to_number)
        )
        log_entry["sid"] = msg.sid
        log_entry["status"] = msg.status
        try:
            db.sms_logs.insert_one(log_entry)
        except Exception as e:
            print(f"DB Logging Error (Live): {e}")
        return {"success": True, "sid": msg.sid}
    except Exception as e:
        print(f"Twilio Error: {e}")
        log_entry["status"] = "failed"
        log_entry["error_code"] = str(e)
        try:
            db.sms_logs.insert_one(log_entry)
        except Exception as db_e:
            print(f"DB Logging Error (Failure): {db_e}")
        return {"success": False, "error": str(e)}


# Make send_sms available to route modules
app.send_sms = send_sms

# ════════════════════════════════════════
# Register Blueprints
# ════════════════════════════════════════
from routes.auth import auth_bp
from routes.products import products_bp
from routes.orders import orders_bp
from routes.sms import sms_bp
from routes.admin import admin_bp
from routes.tracking import tracking_bp
from routes.chat import chat_bp

app.register_blueprint(auth_bp)
app.register_blueprint(products_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(sms_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(tracking_bp)
app.register_blueprint(chat_bp)

# ════════════════════════════════════════
# Static File Serving (Frontend)
# ════════════════════════════════════════
@app.route("/")
def serve_user_portal():
    return app.send_static_file("user/index.html")

@app.route("/user/")
@app.route("/user/<path:path>")
def serve_user_files(path="index.html"):
    return app.send_static_file(f"user/{path}")

@app.route("/admin/")
@app.route("/admin/<path:path>")
def serve_admin_files(path="index.html"):
    return app.send_static_file(f"admin/{path}")

# Serve intro.mp4 and logo.png from project root
@app.route("/intro.mp4")
def serve_intro():
    return app.send_static_file("intro.mp4")

@app.route("/logo.png")
def serve_logo():
    import os
    from flask import send_file
    # Logo is at project root, not inside frontend/
    logo_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logo.png')
    return send_file(logo_path, mimetype='image/png')

# ════════════════════════════════════════
# SocketIO Events — Real-time Chat
# ════════════════════════════════════════
@socketio.on("join_room")
def handle_join(data):
    room_id = data.get("room_id")
    user_id = data.get("user_id")
    if room_id:
        join_room(room_id)
        emit("user_joined", {"user_id": user_id}, room=room_id)
        print(f"[CHAT] User {user_id} joined room {room_id}")

@socketio.on("leave_room")
def handle_leave(data):
    room_id = data.get("room_id")
    if room_id:
        leave_room(room_id)

@socketio.on("send_message")
def handle_message(data):
    room_id = data.get("room_id")
    text = data.get("text", "")
    sender_id = data.get("sender_id")
    sender_name = data.get("sender_name", "User")
    product_id = data.get("product_id")
    receiver_id = data.get("receiver_id")

    message = {
        "roomId": room_id,
        "senderId": sender_id,
        "receiverId": receiver_id,
        "productId": product_id,
        "text": text,
        "read": False,
        "timestamp": datetime.now(timezone.utc)
    }
    db.messages.insert_one(message)
    message["_id"] = str(message["_id"])
    message["timestamp"] = message["timestamp"].isoformat()

    emit("receive_message", message, room=room_id)

    # SMS notification to receiver
    if receiver_id:
        receiver = db.users.find_one({"_id": receiver_id})
        if receiver and receiver.get("mobile"):
            product = db.products.find_one({"_id": product_id}) if product_id else None
            title = product.get("title", "an item") if product else "an item"
            send_sms(
                receiver["mobile"],
                f"New inquiry on your item '{title}' from {sender_name}. Reply on Student Bazar app. -SB",
                msg_type="chat_notify",
                user_id=sender_id,
                related_id=product_id
            )

@socketio.on("typing")
def handle_typing(data):
    room_id = data.get("room_id")
    user_id = data.get("user_id")
    emit("user_typing", {"user_id": user_id}, room=room_id, include_self=False)

@socketio.on("message_read")
def handle_read(data):
    room_id = data.get("room_id")
    message_id = data.get("message_id")
    if message_id:
        from bson import ObjectId
        db.messages.update_one({"_id": ObjectId(message_id)}, {"$set": {"read": True}})
    emit("message_was_read", {"message_id": message_id}, room=room_id)

# ════════════════════════════════════════
# Twilio Webhook — Delivery Reports
# ════════════════════════════════════════
@app.route("/webhooks/twilio/status", methods=["POST"])
def twilio_status_webhook():
    sid = request.form.get("MessageSid")
    status = request.form.get("MessageStatus")
    error_code = request.form.get("ErrorCode")

    if sid:
        update = {"status": status}
        if error_code:
            update["error_code"] = error_code
        db.sms_logs.update_one({"sid": sid}, {"$set": update})

    return "", 200

# ════════════════════════════════════════
# Seed Demo Data (on first run)
# ════════════════════════════════════════
def seed_demo_data():
    """Populate collections with demo data if empty."""
    if db.users.count_documents({}) > 0:
        return

    import bcrypt
    now = datetime.now(timezone.utc)
    pw = bcrypt.hashpw("1234".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    # Admin user
    db.users.insert_one({
        "firstName": "Admin", "lastName": "Alienware",
        "email": "admin@studentbazar.in", "mobile": "9999999999",
        "passwordHash": pw, "college": "IIT Bombay", "city": "Mumbai",
        "avatar": "", "coins": 9999, "rating": 5.0, "ratingCount": 0,
        "role": "admin", "verified": True, "banned": False,
        "createdAt": now, "lastLogin": now
    })

    # Demo users
    demo_users = [
        {"firstName": "Arun", "lastName": "Kumar", "mobile": "9876543210",
         "college": "VIT Vellore", "city": "Vellore", "coins": 250, "rating": 4.8},
        {"firstName": "Priya", "lastName": "Sharma", "mobile": "9876543211",
         "college": "BITS Pilani", "city": "Pilani", "coins": 180, "rating": 4.5},
        {"firstName": "Rahul", "lastName": "Verma", "mobile": "9876543212",
         "college": "NIT Trichy", "city": "Trichy", "coins": 320, "rating": 4.9},
        {"firstName": "Sneha", "lastName": "Patel", "mobile": "9876543213",
         "college": "IIIT Hyderabad", "city": "Hyderabad", "coins": 150, "rating": 4.2},
        {"firstName": "Vikram", "lastName": "Singh", "mobile": "9876543214",
         "college": "DTU Delhi", "city": "Delhi", "coins": 400, "rating": 4.7},
    ]
    for u in demo_users:
        u.update({"email": f"{u['firstName'].lower()}@student.edu", "passwordHash": pw,
                  "avatar": "", "ratingCount": 0, "role": "user", "verified": True,
                  "banned": False, "createdAt": now, "lastLogin": now})
    db.users.insert_many(demo_users)

    # Reload users to get _id
    users = list(db.users.find({"role": "user"}))

    # Demo products
    products = [
        {"title": "Advanced Calculus by Thomas", "description": "Barely used, no highlights. 3rd edition with all chapters intact.",
         "price": 499, "negotiable": True, "category": "Books", "condition": "Like New",
         "emoji": "📚", "sellerId": str(users[0]["_id"]), "sellerName": f"{users[0]['firstName']} {users[0]['lastName']}",
         "sellerRating": users[0]["rating"], "location": users[0]["college"], "city": users[0]["city"],
         "status": "active", "views": 234, "favorites": 18, "featured": True},
        {"title": "MacBook Air M1 2020", "description": "8GB/256GB, Space Gray. Battery health 94%. Charger included.",
         "price": 45000, "negotiable": True, "category": "Electronics", "condition": "Good",
         "emoji": "💻", "sellerId": str(users[1]["_id"]), "sellerName": f"{users[1]['firstName']} {users[1]['lastName']}",
         "sellerRating": users[1]["rating"], "location": users[1]["college"], "city": users[1]["city"],
         "status": "active", "views": 567, "favorites": 45, "featured": True},
        {"title": "iPhone 13 128GB Blue", "description": "10 months old, with box and all accessories. No scratches.",
         "price": 38000, "negotiable": False, "category": "Mobiles", "condition": "Like New",
         "emoji": "📱", "sellerId": str(users[2]["_id"]), "sellerName": f"{users[2]['firstName']} {users[2]['lastName']}",
         "sellerRating": users[2]["rating"], "location": users[2]["college"], "city": users[2]["city"],
         "status": "active", "views": 891, "favorites": 67, "featured": True},
        {"title": "University Hoodie - VIT", "description": "Size L, very comfortable. Worn only 3 times.",
         "price": 599, "negotiable": True, "category": "Fashion", "condition": "Like New",
         "emoji": "👕", "sellerId": str(users[0]["_id"]), "sellerName": f"{users[0]['firstName']} {users[0]['lastName']}",
         "sellerRating": users[0]["rating"], "location": users[0]["college"], "city": users[0]["city"],
         "status": "active", "views": 123, "favorites": 9, "featured": False},
        {"title": "Firefox Sports Cycle 21-Speed", "description": "Disc brakes, 26 inch wheels. 1 year old, well maintained.",
         "price": 3500, "negotiable": True, "category": "Cycles", "condition": "Good",
         "emoji": "🚲", "sellerId": str(users[3]["_id"]), "sellerName": f"{users[3]['firstName']} {users[3]['lastName']}",
         "sellerRating": users[3]["rating"], "location": users[3]["college"], "city": users[3]["city"],
         "status": "active", "views": 345, "favorites": 22, "featured": False},
        {"title": "Casio FX-991EX Scientific Calculator", "description": "Solar powered, all functions working. Exam approved.",
         "price": 850, "negotiable": False, "category": "Electronics", "condition": "Good",
         "emoji": "🔬", "sellerId": str(users[4]["_id"]), "sellerName": f"{users[4]['firstName']} {users[4]['lastName']}",
         "sellerRating": users[4]["rating"], "location": users[4]["college"], "city": users[4]["city"],
         "status": "active", "views": 189, "favorites": 14, "featured": False},
        {"title": "Yamaha F310 Acoustic Guitar", "description": "Great for beginners. Comes with bag, capo, and picks.",
         "price": 4200, "negotiable": True, "category": "Music", "condition": "Good",
         "emoji": "🎸", "sellerId": str(users[1]["_id"]), "sellerName": f"{users[1]['firstName']} {users[1]['lastName']}",
         "sellerRating": users[1]["rating"], "location": users[1]["college"], "city": users[1]["city"],
         "status": "active", "views": 456, "favorites": 31, "featured": True},
        {"title": "White Lab Coat Size M", "description": "Cotton lab coat, used for 1 semester. Clean and pressed.",
         "price": 250, "negotiable": True, "category": "Lab Gear", "condition": "Good",
         "emoji": "🥼", "sellerId": str(users[2]["_id"]), "sellerName": f"{users[2]['firstName']} {users[2]['lastName']}",
         "sellerRating": users[2]["rating"], "location": users[2]["college"], "city": users[2]["city"],
         "status": "active", "views": 78, "favorites": 5, "featured": False},
        {"title": "Study Table with Drawer", "description": "Compact wooden study table. Perfect for hostel rooms.",
         "price": 1200, "negotiable": True, "category": "Hostel Items", "condition": "Fair",
         "emoji": "🏠", "sellerId": str(users[3]["_id"]), "sellerName": f"{users[3]['firstName']} {users[3]['lastName']}",
         "sellerRating": users[3]["rating"], "location": users[3]["college"], "city": users[3]["city"],
         "status": "active", "views": 156, "favorites": 11, "featured": False},
        {"title": "HP 15s Ryzen 5 Laptop", "description": "16GB RAM, 512GB SSD. Perfect for coding and design work.",
         "price": 32000, "negotiable": True, "category": "Electronics", "condition": "Good",
         "emoji": "💻", "sellerId": str(users[4]["_id"]), "sellerName": f"{users[4]['firstName']} {users[4]['lastName']}",
         "sellerRating": users[4]["rating"], "location": users[4]["college"], "city": users[4]["city"],
         "status": "active", "views": 678, "favorites": 52, "featured": True},
        {"title": "Physics HC Verma Vol 1 & 2", "description": "Both volumes, some highlighting but clean otherwise.",
         "price": 350, "negotiable": True, "category": "Books", "condition": "Fair",
         "emoji": "📚", "sellerId": str(users[0]["_id"]), "sellerName": f"{users[0]['firstName']} {users[0]['lastName']}",
         "sellerRating": users[0]["rating"], "location": users[0]["college"], "city": users[0]["city"],
         "status": "active", "views": 290, "favorites": 20, "featured": False},
        {"title": "JBL Flip 5 Bluetooth Speaker", "description": "Waterproof, amazing bass. Battery lasts 12 hours.",
         "price": 5500, "negotiable": True, "category": "Electronics", "condition": "Like New",
         "emoji": "🔊", "sellerId": str(users[2]["_id"]), "sellerName": f"{users[2]['firstName']} {users[2]['lastName']}",
         "sellerRating": users[2]["rating"], "location": users[2]["college"], "city": users[2]["city"],
         "status": "active", "views": 412, "favorites": 33, "featured": False},
    ]
    for p in products:
        p["images"] = []
        p["createdAt"] = now
    db.products.insert_many(products)

    # Demo orders
    prods = list(db.products.find())
    orders = [
        {"productId": str(prods[0]["_id"]), "productTitle": prods[0]["title"], "productEmoji": prods[0]["emoji"],
         "buyerId": str(users[1]["_id"]), "buyerPhone": users[1]["mobile"],
         "sellerId": str(users[0]["_id"]), "sellerPhone": users[0]["mobile"],
         "price": prods[0]["price"], "status": "complete",
         "meetupPoint": "Library Gate", "instructions": "Meet at main gate",
         "createdAt": now, "completedAt": now},
        {"productId": str(prods[2]["_id"]), "productTitle": prods[2]["title"], "productEmoji": prods[2]["emoji"],
         "buyerId": str(users[3]["_id"]), "buyerPhone": users[3]["mobile"],
         "sellerId": str(users[2]["_id"]), "sellerPhone": users[2]["mobile"],
         "price": prods[2]["price"], "status": "confirmed",
         "meetupPoint": "Canteen", "instructions": "",
         "createdAt": now, "completedAt": None},
    ]
    db.orders.insert_many(orders)

    # Site settings
    db.settings.insert_one({
        "siteName": "Student Bazar",
        "tagline": "Trade Smart. Study More.",
        "supportEmail": "support@studentbazar.in",
        "supportWhatsApp": "919999999999",
        "coinsPerSale": 50,
        "boostCostCoins": 100,
        "maxUploadMB": 5,
        "maintenanceMode": False,
        "announcementBanner": "",
        "announcementBg": "#4F46E5",
        "announcementCta": "",
        "announcementCtaUrl": ""
    })

    print("[SEED] Demo data inserted successfully!")


# ════════════════════════════════════════
# Health Check
# ════════════════════════════════════════
@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "app": "Student Bazar by Alienware", "version": "1.0.0"})


# ════════════════════════════════════════
# Run
# ════════════════════════════════════════
if __name__ == "__main__":
    with app.app_context():
        seed_demo_data()
    socketio.run(app, host="0.0.0.0", port=5000, debug=app.config["DEBUG"])
