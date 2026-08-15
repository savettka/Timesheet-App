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
