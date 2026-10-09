"""config.py — MahiTech configuration."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    SECRET_KEY = "mahitech-super-secret-key-change-me"

    DATABASE = os.path.join(BASE_DIR, "mahitech.db")

    # Admin — verified directly on the login page
    ADMIN_USERNAME = "Mahesh"
    ADMIN_PASSWORD = "Mahesh123"

    # ---------------- Razorpay ----------------
    RAZORPAY_KEY_ID = "rzp_test_TlIH3jGp1woHCW"
    RAZORPAY_KEY_SECRET = "J4ELJiNqQ6XIIPHfv7sU03mw"

    DEMO_MODE = False
    RAZORPAY_TEST_MODE = RAZORPAY_KEY_ID.startswith("rzp_test_")

    # ---------------- Cloudinary (file storage) ----------------
    # Hardcoded so the debug reloader's child process always sees the same
    # values. (Env vars caused the child to start in local-storage mode.)
    CLOUDINARY_CLOUD_NAME = "qwx7mhnn"
    CLOUDINARY_API_KEY = "712527662128967"
    CLOUDINARY_API_SECRET = "ClrjKXSYhheaf_IAlnbhdBMLU80"

    # Local fallback storage (also used by files uploaded before Cloudinary)
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
    THUMBNAIL_FOLDER = os.path.join(UPLOAD_FOLDER, "thumbnails")
    PDF_FOLDER = os.path.join(UPLOAD_FOLDER, "pdfs")
    VIDEO_FOLDER = os.path.join(UPLOAD_FOLDER, "videos")

    ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
    ALLOWED_PDF_EXTENSIONS = {"pdf"}
    ALLOWED_VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "mkv"}

    MAX_CONTENT_LENGTH = 500 * 1024 * 1024  # 500 MB

    CATEGORIES = [
        "Government Exams",
        "Competitive Exams",
        "Entrance Exams",
        "Academic Preparation",
        "Career Preparation",
    ]

    SEED_DEMO_DATA = True