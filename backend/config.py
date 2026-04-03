import os
from dotenv import load_dotenv
from datetime import timedelta

load_dotenv()


class Config:
    # ── Flask ──
    SECRET_KEY = os.environ.get("SECRET_KEY", "alienware")
    DEBUG = os.environ.get("FLASK_DEBUG", "true").lower() == "true"

    # ── MongoDB ──
    MONGO_URI = os.environ.get(
        "MONGO_URI",
        "mongodb+srv://student_bazzar:alienware@studentbazzar.16jaqjs.mongodb.net/studentbazar"
    )

    # ── JWT ──
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "jwt-super-secret-key-alienware")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"

    # ── Twilio SMS ──
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_TOKEN", "")
    TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM", "")
    SMS_DEMO_MODE = os.environ.get("SMS_DEMO_MODE", "true").lower() == "true"

    # ── Firebase ──
    FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "")
    FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
    FIREBASE_DATABASE_URL = os.environ.get("FIREBASE_DATABASE_URL", "")
    FIREBASE_STORAGE_BUCKET = os.environ.get("FIREBASE_STORAGE_BUCKET", "")
    FIREBASE_APP_ID = os.environ.get("FIREBASE_APP_ID", "")
    FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")

    # ── Rate Limiting ──
    RATELIMIT_DEFAULT = "200 per day"
    RATELIMIT_STORAGE_URI = "memory://"

    # ── CORS ──
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

    # ── Site Settings ──
    SITE_NAME = "Student Bazar"
    SITE_TAGLINE = "Trade Smart. Study More."
    SUPPORT_EMAIL = "support@studentbazar.in"
    COINS_PER_SALE = 50
    BOOST_COST_COINS = 100
    MAX_UPLOAD_SIZE_MB = 5
