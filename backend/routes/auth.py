"""
Auth Routes — Register, Login, OTP, Token Refresh, Logout
"""

from datetime import datetime, timezone, timedelta
import random
import bcrypt
from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import (
    create_access_token, create_refresh_token,
    jwt_required, get_jwt_identity, get_jwt
)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

auth_bp = Blueprint("auth", __name__)

# In-memory OTP store (production: use Redis)
otp_store = {}  # {mobile: {otp, expires, attempts}}

def get_db():
    from app import db
    return db

def sms(to, msg, **kw):
    from app import send_sms
    return send_sms(to, msg, **kw)


# ── Register ──
@auth_bp.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ["firstName", "lastName", "mobile"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"{field} is required"}), 400

    mobile = data["mobile"].strip()
    if len(mobile) != 10 or not mobile.isdigit():
        return jsonify({"error": "Invalid mobile number"}), 400

    db = get_db()
    if db.users.find_one({"mobile": mobile}):
        return jsonify({"error": "Mobile number already registered"}), 409

    password = data.get("password", "1234")
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    now = datetime.now(timezone.utc)
    user = {
        "firstName": data["firstName"].strip(),
        "lastName": data["lastName"].strip(),
        "email": data.get("email", "").strip(),
        "mobile": mobile,
        "passwordHash": pw_hash,
        "college": data.get("college", "").strip(),
        "city": data.get("city", "").strip(),
        "avatar": "",
        "coins": 150,
        "rating": 0,
        "ratingCount": 0,
        "role": "user",
        "verified": False,
        "banned": False,
        "createdAt": now,
        "lastLogin": now
    }
    result = db.users.insert_one(user)
    user["_id"] = str(result.inserted_id)

    access_token = create_access_token(
        identity=str(result.inserted_id),
        additional_claims={"role": "user", "mobile": mobile}
    )
    refresh_token = create_refresh_token(identity=str(result.inserted_id))

    return jsonify({
        "message": "Registration successful",
        "user": {
            "_id": user["_id"],
            "firstName": user["firstName"],
            "lastName": user["lastName"],
            "mobile": user["mobile"],
            "college": user["college"],
            "city": user["city"],
            "coins": user["coins"],
            "rating": user["rating"],
            "role": user["role"],
            "avatar": user["avatar"]
        },
        "access_token": access_token,
        "refresh_token": refresh_token
    }), 201


# ── Login ──
@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    if not data or not data.get("mobile"):
        return jsonify({"error": "Mobile number required"}), 400

    mobile = data["mobile"].strip()
    db = get_db()
    user = db.users.find_one({"mobile": mobile})

    if not user:
        return jsonify({"error": "User not found. Please register first."}), 404

    if user.get("banned"):
        return jsonify({"error": "Account is banned. Contact support."}), 403

    password = data.get("password", "")
    if password and not bcrypt.checkpw(password.encode("utf-8"), user["passwordHash"].encode("utf-8")):
        return jsonify({"error": "Invalid password"}), 401

    db.users.update_one({"_id": user["_id"]}, {"$set": {"lastLogin": datetime.now(timezone.utc)}})

    access_token = create_access_token(
        identity=str(user["_id"]),
        additional_claims={"role": user["role"], "mobile": mobile}
    )
    refresh_token = create_refresh_token(identity=str(user["_id"]))

    return jsonify({
        "message": "Login successful",
        "user": {
            "_id": str(user["_id"]),
            "firstName": user["firstName"],
            "lastName": user["lastName"],
            "mobile": user["mobile"],
            "college": user.get("college", ""),
            "city": user.get("city", ""),
            "coins": user.get("coins", 0),
            "rating": user.get("rating", 0),
            "role": user["role"],
            "avatar": user.get("avatar", "")
        },
        "access_token": access_token,
        "refresh_token": refresh_token
    }), 200


# ── Send OTP ──
@auth_bp.route("/api/auth/otp/send", methods=["POST"])
def send_otp():
    data = request.get_json()
    if not data or not data.get("mobile"):
        return jsonify({"error": "Mobile number required"}), 400

    mobile = data["mobile"].strip()

    # Check lockout
    if mobile in otp_store:
        entry = otp_store[mobile]
        if entry.get("locked_until") and datetime.now(timezone.utc) < entry["locked_until"]:
            remaining = int((entry["locked_until"] - datetime.now(timezone.utc)).total_seconds())
            return jsonify({"error": f"Too many attempts. Try again in {remaining}s"}), 429

    otp = str(random.randint(100000, 999999))
    otp_store[mobile] = {
        "otp": otp,
        "expires": datetime.now(timezone.utc) + timedelta(minutes=5),
        "attempts": 0,
        "locked_until": None
    }

    message = f"Your Student Bazar OTP is {otp}. Valid for 5 minutes. Do not share with anyone. -Alienware"
    result = sms(mobile, message, msg_type="otp", user_id=None, related_id=None)

    return jsonify({
        "message": "OTP sent successfully",
        "demo_otp": otp if current_app.config.get("SMS_DEMO_MODE") else None
    }), 200


# ── Verify OTP ──
@auth_bp.route("/api/auth/otp/verify", methods=["POST"])
def verify_otp():
    data = request.get_json()
    if not data or not data.get("mobile") or not data.get("otp"):
        return jsonify({"error": "Mobile and OTP required"}), 400

    mobile = data["mobile"].strip()
    entered_otp = data["otp"].strip()

    if mobile not in otp_store:
        return jsonify({"error": "No OTP sent for this number. Request a new one."}), 400

    entry = otp_store[mobile]

    if entry.get("locked_until") and datetime.now(timezone.utc) < entry["locked_until"]:
        return jsonify({"error": "Account locked. Try again later."}), 429

    if datetime.now(timezone.utc) > entry["expires"]:
        del otp_store[mobile]
        return jsonify({"error": "OTP expired. Request a new one."}), 400

    if entered_otp != entry["otp"]:
        entry["attempts"] += 1
        if entry["attempts"] >= 3:
            entry["locked_until"] = datetime.now(timezone.utc) + timedelta(minutes=10)
            return jsonify({"error": "Too many wrong attempts. Locked for 10 minutes."}), 429
        remaining = 3 - entry["attempts"]
        return jsonify({"error": f"Incorrect OTP. {remaining} attempts left."}), 400

    # OTP verified — clean up
    del otp_store[mobile]

    db = get_db()
    user = db.users.find_one({"mobile": mobile})

    if user:
        db.users.update_one({"_id": user["_id"]}, {"$set": {"verified": True, "lastLogin": datetime.now(timezone.utc)}})
        user_id = str(user["_id"])
        role = user["role"]
    else:
        # Auto-register on OTP verify if user doesn't exist
        first_name = data.get("firstName", "Student")
        last_name = data.get("lastName", "User")
        pw_hash = bcrypt.hashpw("1234".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        now = datetime.now(timezone.utc)
        new_user = {
            "firstName": first_name, "lastName": last_name,
            "email": "", "mobile": mobile, "passwordHash": pw_hash,
            "college": data.get("college", ""), "city": data.get("city", ""),
            "avatar": "", "coins": 150, "rating": 0, "ratingCount": 0,
            "role": "user", "verified": True, "banned": False,
            "createdAt": now, "lastLogin": now
        }
        result = db.users.insert_one(new_user)
        user_id = str(result.inserted_id)
        role = "user"
        user = new_user
        user["_id"] = result.inserted_id

    access_token = create_access_token(
        identity=user_id,
        additional_claims={"role": role, "mobile": mobile}
    )
    refresh_token = create_refresh_token(identity=user_id)

    return jsonify({
        "message": "OTP verified successfully",
        "user": {
            "_id": user_id,
            "firstName": user.get("firstName", ""),
            "lastName": user.get("lastName", ""),
            "mobile": mobile,
            "college": user.get("college", ""),
            "city": user.get("city", ""),
            "coins": user.get("coins", 150),
            "rating": user.get("rating", 0),
            "role": role,
            "avatar": user.get("avatar", "")
        },
        "access_token": access_token,
        "refresh_token": refresh_token
    }), 200


# ── Refresh Token ──
@auth_bp.route("/api/auth/refresh-token", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    db = get_db()
    from bson import ObjectId
    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    access_token = create_access_token(
        identity=identity,
        additional_claims={"role": user["role"], "mobile": user["mobile"]}
    )
    return jsonify({"access_token": access_token}), 200


# ── Logout ──
@auth_bp.route("/api/auth/logout", methods=["POST"])
@jwt_required()
def logout():
    # In production, add token to blacklist
    return jsonify({"message": "Logged out successfully"}), 200


# ── Users Profile ──
@auth_bp.route("/api/users/me", methods=["GET"])
@jwt_required()
def get_me():
    from bson import ObjectId
    identity = get_jwt_identity()
    db = get_db()
    user = db.users.find_one({"_id": ObjectId(identity)})
    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "_id": str(user["_id"]),
        "firstName": user["firstName"],
        "lastName": user["lastName"],
        "mobile": user["mobile"],
        "email": user.get("email", ""),
        "college": user.get("college", ""),
        "city": user.get("city", ""),
        "coins": user.get("coins", 0),
        "rating": user.get("rating", 0),
        "ratingCount": user.get("ratingCount", 0),
        "role": user["role"],
        "avatar": user.get("avatar", ""),
        "verified": user.get("verified", False),
        "createdAt": user["createdAt"].isoformat() if user.get("createdAt") else None
    }), 200


@auth_bp.route("/api/users/me", methods=["PUT"])
@jwt_required()
def update_me():
    from bson import ObjectId
    identity = get_jwt_identity()
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data"}), 400

    allowed = ["firstName", "lastName", "email", "college", "city", "avatar"]
    update = {k: v for k, v in data.items() if k in allowed and v is not None}

    db = get_db()
    db.users.update_one({"_id": ObjectId(identity)}, {"$set": update})
    return jsonify({"message": "Profile updated"}), 200


@auth_bp.route("/api/users/<user_id>", methods=["GET"])
def get_public_profile(user_id):
    from bson import ObjectId
    db = get_db()
    try:
        user = db.users.find_one({"_id": ObjectId(user_id)})
    except Exception:
        return jsonify({"error": "Invalid user ID"}), 400

    if not user:
        return jsonify({"error": "User not found"}), 404

    return jsonify({
        "_id": str(user["_id"]),
        "firstName": user["firstName"],
        "lastName": user.get("lastName", ""),
        "college": user.get("college", ""),
        "city": user.get("city", ""),
        "rating": user.get("rating", 0),
        "ratingCount": user.get("ratingCount", 0),
        "avatar": user.get("avatar", ""),
        "createdAt": user["createdAt"].isoformat() if user.get("createdAt") else None
    }), 200
