"""Tables only the Windows app has. Imported before the app's database is
created, so they exist on the PC and never on the server."""

from datetime import datetime

from app import db


class PendingChange(db.Model):
    """A day changed on this PC that the server hasn't had yet.

    `base_version` is the server version the change was made on top of --
    how the server tells a plain update from the same day having been
    changed on the web in the meantime.

    `revision` goes up by one with every save of the day. It's what a sync
    checks to see whether the day was edited again while it was on its
    way -- not `modified_at`, because Windows' clock moves in steps of about
    15 ms, so two quick saves can carry the very same time.
    """

    __tablename__ = "pending_change"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, nullable=False)
    modified_at = db.Column(db.DateTime, nullable=False)
    base_version = db.Column(db.Integer, nullable=True)
    revision = db.Column(db.Integer, nullable=False, default=1)

    __table_args__ = (db.UniqueConstraint("user_id", "date", name="uq_pending_user_date"),)


class SyncConflict(db.Model):
    """A day edited both here and on the web while apart. The newer edit
    was kept; this is the other one, so it can be brought back."""

    __tablename__ = "sync_conflict"
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    kept = db.Column(db.String(10), nullable=False)  # "app" or "server"
    other_json = db.Column(db.Text, nullable=True)  # null: the other side had deleted it
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class SyncMeta(db.Model):
    """The app's own settings: which server, the sign-in token (encrypted
    for this Windows account), and how far it has synced."""

    __tablename__ = "sync_meta"
    key = db.Column(db.String(40), primary_key=True)
    value = db.Column(db.Text, nullable=True)
