import json
import os

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Local settings from .env (if present). Real environment variables, e.g. on Render, win.
load_dotenv(os.path.join(BASE_DIR, ".env"))
with open(os.path.join(BASE_DIR, "theme.json"), encoding="utf-8") as _f:
    THEME = json.load(_f)


class Config:
    THEME_KEY = THEME["key"]
    BRAND_COLOR = THEME["brand"]
    CATEGORY_ICONS = THEME.get("category_icons", {})
    SHOP_NAME = os.environ.get("SHOP_NAME", THEME["shop_name"])
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "shop.db")
    ).replace("postgres://", "postgresql://")
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(BASE_DIR, "static", "uploads"))
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024
    ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "webp"}

    # S3 bucket for product images. When set, images go to the bucket and the database
    # stores only the file path (e.g. "uploads/abc.jpg"). Empty = keep them in static/uploads.
    S3_BUCKET = os.environ.get("BUCKET", "")
    S3_REGION = os.environ.get("REGION", "ap-southeast-1")
    AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY", "")
    AWS_SECRET_KEY = os.environ.get("AWS_SECRET_KEY", "")
    _endpoint = os.environ.get("ENDPOINT_STORAGE_URL", "") or (
        f"{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com" if S3_BUCKET else "")
    S3_PUBLIC_URL = (_endpoint if _endpoint.startswith("http") else "https://" + _endpoint).rstrip("/")
    # 1 = the bucket allows public read, so use plain links. 0 (default) = signed links (private bucket)
    S3_PUBLIC_READ = os.environ.get("S3_PUBLIC_READ", "0") == "1"

    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

    # KHQR / Bakong merchant settings -- set KHQR_ACCOUNT_ID to your real Bakong account
    KHQR_ACCOUNT_ID = os.environ.get("KHQR_ACCOUNT_ID", "yourname@bank")
    KHQR_MERCHANT_NAME = os.environ.get("KHQR_MERCHANT_NAME", THEME["khqr_name"])
    KHQR_MERCHANT_CITY = os.environ.get("KHQR_MERCHANT_CITY", "Phnom Penh")
    KHQR_CURRENCY = os.environ.get("KHQR_CURRENCY", "USD")
    KHQR_PHONE = os.environ.get("KHQR_PHONE", "85512345678")
    # Optional: with a Bakong developer token the server verifies payment by MD5
    BAKONG_TOKEN = os.environ.get("BAKONG_TOKEN", "")
    # Testing only: shows a "Test payment" button at checkout that places the order as paid
    # without scanning the KHQR. Never turn this on for a real shop -- anyone could order for free.
    TEST_PAYMENTS = os.environ.get("TEST_PAYMENTS", "0") == "1"

    # Admin dashboard
    LOW_STOCK_THRESHOLD = int(os.environ.get("LOW_STOCK_THRESHOLD", 5))  # "low stock" at or below this
    TZ_OFFSET_HOURS = int(os.environ.get("TZ_OFFSET_HOURS", 7))  # shop's local time = UTC+7 (Cambodia)
