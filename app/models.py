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

    # Where sign-in codes are sent. Stored lowercased so lookups are
    # case-insensitive; unique so a code request can only ever match one
    # account.
    email = db.Column(db.String(200), unique=True, nullable=True, index=True)

    daily_target_hours = db.Column(db.Float, nullable=False, default=8.0)
    weekly_target_hours = db.Column(db.Float, nullable=False, default=48.0)
    workdays = db.Column(db.String(20), nullable=False, default=DEFAULT_WORKDAYS)

    # Optional "usual" Saturday start time, used only to project a Saturday
    # logout clock-time before Saturday actually starts (Settings).
    saturday_login_hint = db.Column(db.Time, nullable=True)

    # The break normally taken, used to push suggested clock-out times later
    # so they hold even before the break has been recorded. These never touch
    # recorded hours -- those always come from breaks actually logged.
    weekday_break_minutes = db.Column(db.Integer, nullable=False, default=60)
    saturday_break_minutes = db.Column(db.Integer, nullable=False, default=30)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    entries = db.relationship(
        "TimeEntry", backref="user", lazy=True, cascade="all, delete-orphan"
    )
    login_codes = db.relationship(
        "LoginCode", backref="user", lazy=True, cascade="all, delete-orphan"
    )

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return check_password_hash(self.password_hash, raw_password)

    def workday_set(self):
        return {int(d) for d in self.workdays.split(",") if d != ""}

    def target_hours_for_weekday(self, weekday):
        return self.daily_target_hours if weekday in self.workday_set() else 0.0

    def expected_break_hours_for(self, weekday):
        """The break normally taken on this weekday, in hours. Saturday (5)
        and Sunday (6) use the shorter weekend allowance."""
        minutes = (
            self.saturday_break_minutes if weekday >= 5 else self.weekday_break_minutes
        )
        return max(0, minutes or 0) / 60.0

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

    # How the day was classified on the entry form: "half", "full", "custom",
    # or None for an ordinary working day. target_override still holds the
    # resulting hours -- this records the intent, so a leave day can skip the
    # assumed break and show the right option when the form is reopened.
    leave_type = db.Column(db.String(10), nullable=True)

    LEAVE_TYPES = {"half": "Half day leave", "full": "Full day leave"}

    @property
    def is_leave(self):
        return self.leave_type in self.LEAVE_TYPES

    @property
    def effective_leave_type(self):
        """Which option the entry form should start on.

        Entries saved before day types existed only have the hours, so infer
        the type from those rather than showing them as ordinary working days
        -- reopening and saving one would otherwise wipe its override.
        """
        if self.leave_type:
            return self.leave_type
        if self.target_override is None:
            return None
        return "full" if self.target_override == 0 else "custom"

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


class LoginCode(db.Model):
    """A one-time code emailed to a user so they can sign in without a password.

    The code itself is never stored -- only a hash of it -- so reading the
    database gives an attacker nothing usable. Each code is single-use, expires
    quickly, and dies after a handful of wrong guesses, which is what stops a
    six-digit code (a million combinations) from being brute-forced.
    """

    MAX_ATTEMPTS = 5
    LIFETIME_MINUTES = 10

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True
    )

    code_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    used_at = db.Column(db.DateTime, nullable=True)

    def is_usable(self, now=None):
        now = now or datetime.utcnow()
        return (
            self.used_at is None
            and self.attempts < self.MAX_ATTEMPTS
            and now < self.expires_at
        )

    def verify(self, submitted_code):
        """Check a submitted code, counting the attempt either way.

        Returns True only for a correct code on a still-usable record. The
        attempt is recorded before the comparison so a caller that crashes
        mid-check can't be used to get unlimited free guesses.
        """
        if not self.is_usable():
            return False
        self.attempts += 1
        # check_password_hash compares in constant time, so a wrong code can't
        # be narrowed down by how long the check took.
        if check_password_hash(self.code_hash, submitted_code):
            self.used_at = datetime.utcnow()
            return True
        return False
