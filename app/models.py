from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app import db

DEFAULT_WORKDAYS = "0,1,2,3,4,5"  # Monday .. Saturday (Python weekday(): Mon=0 .. Sun=6)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120), nullable=True)

    # Only admins can add or remove accounts, or see that other accounts
    # exist at all. The first account created (the one that runs setup)
    # becomes the admin.
    is_admin = db.Column(db.Boolean, nullable=False, default=False)

    # Filename of the uploaded profile picture, relative to
    # app/static/uploads/avatars/. None means "show initials instead".
    avatar_filename = db.Column(db.String(120), nullable=True)

    daily_target_hours = db.Column(db.Float, nullable=False, default=8.0)
    weekly_target_hours = db.Column(db.Float, nullable=False, default=48.0)
    workdays = db.Column(db.String(20), nullable=False, default=DEFAULT_WORKDAYS)

    # Optional "usual" Saturday start time, used only to project a Saturday
    # logout clock-time before Saturday actually starts (Settings).
    saturday_login_hint = db.Column(db.Time, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship(
        "TimeEntry", backref="user", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def workday_set(self):
        return {int(d) for d in self.workdays.split(",") if d != ""}

    def target_hours_for_weekday(self, weekday):
        return self.daily_target_hours if weekday in self.workday_set() else 0.0

    @property
    def initials(self):
        source = (self.display_name or self.username).strip()
        parts = [p for p in source.split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return source[:1].upper() if source else "?"


class TimeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    date = db.Column(db.Date, nullable=False)
    login_time = db.Column(db.Time, nullable=True)
    logout_time = db.Column(db.Time, nullable=True)
    notes = db.Column(db.String(500), nullable=True)

    # When set, replaces the usual weekday-based target for this specific date
    # (e.g. 0 for a day off, half the daily target for a half day).
    target_override = db.Column(db.Float, nullable=True)
    leave_label = db.Column(db.String(60), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    breaks = db.relationship(
        "BreakSegment",
        backref="entry",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="BreakSegment.break_start",
    )

    __table_args__ = (db.UniqueConstraint("user_id", "date", name="uq_user_date"),)

    def is_open(self):
        return self.login_time is not None and self.logout_time is None

    def open_break(self):
        for b in self.breaks:
            if b.break_end is None:
                return b
        return None


class BreakSegment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Indexed: every dashboard/history render loads the breaks for each entry
    # on screen, and without this SQLite scans every break row in the table
    # (all users, all history) once per entry.
    entry_id = db.Column(
        db.Integer, db.ForeignKey("time_entry.id"), nullable=False, index=True
    )

    break_start = db.Column(db.Time, nullable=False)
    break_end = db.Column(db.Time, nullable=True)
