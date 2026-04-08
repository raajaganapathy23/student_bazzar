from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from datetime import datetime

chat_bp = Blueprint("chat", __name__)

def get_db():
    from app import db
    return db

@chat_bp.route("/api/chat/rooms", methods=["GET"])
@jwt_required()
def get_rooms():
    identity = get_jwt_identity()
    db = get_db()
    
    pipeline = [
        {"$match": {"$or": [{"senderId": identity}, {"receiverId": identity}]}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$roomId",
            "lastMessage": {"$first": "$$ROOT"}
        }},
        {"$sort": {"lastMessage.timestamp": -1}}
    ]
    rooms = list(db.messages.aggregate(pipeline))
    
    result = []
    for r in rooms:
        msg = r["lastMessage"]
        other_user_id = msg["receiverId"] if msg["senderId"] == identity else msg["senderId"]
        
        other_user = None
        if other_user_id:
            try:
                other_user = db.users.find_one({"_id": ObjectId(other_user_id)})
            except:
                pass
                
        user_name = other_user.get("firstName", "Unknown") if other_user else "Unknown"
        avatar = user_name[0].upper() if user_name else "?"
        
        unread = db.messages.count_documents({
            "roomId": r["_id"],
            "receiverId": identity,
            "read": False
        })
        
        product = None
        if msg.get("productId"):
            try:
                p = db.products.find_one({"_id": ObjectId(msg["productId"])})
                if p:
                    product = {
                        "id": str(p["_id"]),
                        "title": p.get("title", ""),
                        "price": p.get("price", 0),
                        "emoji": p.get("emoji", "📦")
                    }
            except:
                pass
                
        result.append({
            "roomId": r["_id"],
            "otherUser": {
                "id": other_user_id,
                "name": user_name,
                "avatar": avatar
            },
            "lastMessage": {
                "text": msg.get("text", ""),
                "timestamp": msg.get("timestamp"),
                "senderId": msg.get("senderId")
            },
            "unread": unread,
            "product": product
        })
        
    return jsonify({"rooms": result}), 200

@chat_bp.route("/api/chat/messages/<path:room_id>", methods=["GET"])
@jwt_required()
def get_messages(room_id):
    identity = get_jwt_identity()
    db = get_db()
    
    db.messages.update_many(
        {"roomId": room_id, "receiverId": identity, "read": False},
        {"$set": {"read": True}}
    )
    
    msgs = list(db.messages.find({"roomId": room_id}).sort("timestamp", 1))
    
    result = []
    for m in msgs:
        result.append({
            "id": str(m["_id"]),
            "senderId": m.get("senderId"),
            "receiverId": m.get("receiverId"),
            "text": m.get("text", ""),
            "timestamp": m.get("timestamp"),
            "read": m.get("read", False)
        })
        
    return jsonify({"messages": result}), 200
