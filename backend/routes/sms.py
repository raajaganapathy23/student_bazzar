"""
SMS Routes — Admin broadcast & logs
"""

from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt
from bson import ObjectId
from functools import wraps

sms_bp = Blueprint("sms", __name__)

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


@sms_bp.route("/admin/sms", methods=["GET"])
@admin_required
def get_sms_logs():
    db = get_db()
    
    page = max(1, request.args.get("page", 1, type=int))
    limit = min(100, request.args.get("limit", 20, type=int))
    skip = (page - 1) * limit
    
    total = db.sms_logs.count_documents({})
    logs = list(db.sms_logs.find().sort("timestamp", -1).skip(skip).limit(limit))
    
    for log in lodgs:
        log["_i"] = str(log["_id"])
        if log.get("timestamp"):
            log["timestamp"] = log["timestamp"].isoformat()
            
    return jsonify({"logs": logs, "total": total, "page": page}), 200

@sms_bp.route("/admin/sms/broadcast", methods=["POST"])
@admin_required
def broadcast_sms():
    data = request.get_json()
    message = data.get("message")
    user_ids = data.get("user_ids", [])  # Optional list of explicit ObjectIds
    
    if not message:
        return jsonify({"error": "Message is required"}), 400
        
    db = get_db()
    
    if user_ids:
        query = {"_id": {"$in": [ObjectId(uid) for uid in user_ids]}, "mobile": {"$exists": True, "$ne": ""}}
    else:
        # Broadcast to all users
        query = {"mobile": {"$exists": True, "$ne": ""}}
        
    users = list(db.users.find(query))
    
    sent = 0
    failed = 0
    
    for u in users:
        res = sms_send(u["mobile"], message, msg_type="broadcast", user_id=str(u["_id"]))
        if res.get("success"):
            sent += 1
        else:
            failed += 1
            
    return jsonify({
        "message": f"Broadcast complete. Sent: {sent}, Failed: {failed}"
    }), 200
