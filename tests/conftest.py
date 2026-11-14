"""What every test starts from: the real app on a database of its own in
memory, a signed-in user, and a clock that stands still until it's moved.

The app asks for the time with datetime.now() and date.today() in app.main
and app.logic; the clock fixture stands in for both there.
"""
import datetime as dt

import pytest
from sqlalchemy.pool import StaticPool

import app.logic as logic
import app.main as main
from app import create_app, db
from app.models import User

# A Thursday afternoon in an ordinary Mon-Sat week.
FROZEN = dt.datetime(2026, 11, 12, 14, 0)


class TestConfig:
    TESTING = True
    SECRET_KEY = "test-only"
    # One in-memory database, shared by every connection the app opens.
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    # No email: sign-in codes aren't offered.
    SMTP_HOST = "localhost"
    SMTP_PORT = 25
    SMTP_USERNAME = None
    SMTP_PASSWORD = None
    SMTP_USE_SSL = False
    MAIL_FROM = None


class Clock:
    """The time the app sees. clock.set(2026, 11, 12, 9, 0) moves it."""

    def __init__(self, now):
        self.now = now

    def set(self, *parts):
        self.now = parts[0] if isinstance(parts[0], dt.datetime) else dt.datetime(*parts)


@pytest.fixture
def clock(monkeypatch):
    held = Clock(FROZEN)

    class FrozenDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.combine(held.now.date(), held.now.time())

    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return held.now.date()

    for module in (main, logic):
        monkeypatch.setattr(module, "datetime", FrozenDateTime)
        monkeypatch.setattr(module, "date", FrozenDate)
    return held


@pytest.fixture
def app(clock):
    application = create_app(TestConfig)
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def user_id(app):
    with app.app_context():
        user = User(username="tester", display_name="Test User", is_admin=True)
        user.set_password("password1")
        db.session.add(user)
        db.session.commit()
        return user.id


@pytest.fixture
def client(app, user_id):
    signed_in = app.test_client()
    with signed_in.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    return signed_in


def a_user(**overrides):
    """A user that's never saved, for the pure functions in app.logic: the
    defaults of a new account (8h on Mon-Sat, 48h a week, an hour's break on
    weekdays and half an hour on Saturday)."""
    values = dict(daily_target_hours=8.0, weekly_target_hours=48.0, workdays="0,1,2,3,4,5",
                  weekday_break_minutes=60, saturday_break_minutes=30)
    values.update(overrides)
    return User(username="someone", **values)
