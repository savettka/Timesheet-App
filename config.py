import os
import time

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# STM has no per-user timezone setting -- every datetime.now()/date.today()
# call in the app uses the server's local clock. PythonAnywhere (and most
# hosts) default that to UTC, which silently shifts every punch, login
# stamp, and suggested logout time by however far the server is from the
# user's real timezone. Pin it here so "now" in the app matches the user's
# actual wall clock instead of the host's.
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Kolkata")
os.environ["TZ"] = TIMEZONE
if hasattr(time, "tzset"):
    time.tzset()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key-change-me-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'instance', 'stm.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Profile pictures get downscaled to a small square on upload, so the
    # only reason to accept a large file is the original phone photo.
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024  # 8 MB

    # Email settings for sign-in codes. Left unset, code sign-in is simply
    # not offered. Defaults suit Gmail, which is the one SMTP host
    # PythonAnywhere's free tier allows out.
    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "").lower() in {"1", "true", "yes"}
    MAIL_FROM = os.environ.get("MAIL_FROM")
