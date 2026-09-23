"""Building STM in "PC mode": the same app, pages and rules as the website,
on the PC's own copy of the hours.

The differences from the website:
* it answers only its own window -- each launch gets a secret key, so other
  programs on the PC can't read or change the hours through it;
* there's no sign-in page -- the window opens straight into the account
  this PC is connected to;
* settings are shown, not edited (they're changed on the web and synced in);
* every change is queued for the server by the sync engine.
"""

import os
import secrets

from flask import redirect, request, session, url_for
from sqlalchemy import event
from flask_login import current_user, login_user
from jinja2 import ChoiceLoader, FileSystemLoader

import config
from app import create_app, db

HERE = os.path.dirname(os.path.abspath(__file__))


def _secret_key(data_dir):
    """A key for this install's session cookies, made once and kept."""
    path = os.path.join(data_dir, "secret.key")
    try:
        with open(path, "r", encoding="ascii") as handle:
            key = handle.read().strip()
            if len(key) >= 32:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    with open(path, "w", encoding="ascii") as handle:
        handle.write(key)
    return key


def _one_writer_at_a_time(engine):
    """Make every transaction on the PC's database hold the write lock from
    its first read to its commit.

    The window and the sync thread both change the same days. Python's
    SQLite driver normally starts a transaction only at the first write, so
    the sync could read a day, a click could save a new edit, and the sync
    would then write over it. Taking the lock up front (BEGIN IMMEDIATE)
    makes each save and each sync step happen whole, one after another.
    Everything here is quick, so the wait is never noticeable.
    """
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _record):
        dbapi_connection.isolation_level = None  # transactions are started below
        # Write-ahead logging: readers don't block the one writer.
        dbapi_connection.execute("PRAGMA journal_mode=WAL")

    @event.listens_for(engine, "begin")
    def _on_begin(connection):
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    engine.dispose()  # connections made before this carry the old behaviour


def create_desktop_app(data_dir, transport=None):
    os.makedirs(data_dir, exist_ok=True)
    # Registered before the database is created, so the app's own tables exist.
    import desktop.local_models  # noqa: F401
    from desktop.engine import SyncEngine, http_transport, install_change_hook
    from desktop.views import desktop_bp

    class DesktopConfig(config.Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(data_dir, "stm-local.db").replace(os.sep, "/")
        # The window and the sync thread share the file; wait for each other
        # rather than failing with "database is locked".
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15, "check_same_thread": False}}
        SECRET_KEY = _secret_key(data_dir)
        SYNC_ROLE = "desktop"
        SESSION_COOKIE_SAMESITE = "Strict"

    install_change_hook()
    app = create_app(DesktopConfig)
    app.config["DESKTOP_KEY"] = secrets.token_urlsafe(24)
    with app.app_context():
        _one_writer_at_a_time(db.engine)

    # The app's extra pages, and its versions of shared bits, come first.
    app.jinja_loader = ChoiceLoader([FileSystemLoader(os.path.join(HERE, "templates")), app.jinja_loader])
    app.register_blueprint(desktop_bp)

    engine = SyncEngine(app, transport or http_transport)
    app.extensions["stm_sync"] = engine

    open_without_key = {"static", "desktop.enter"}
    open_before_connecting = {"desktop.connect", "desktop.status"}

    @app.before_request
    def only_from_the_window():
        endpoint = request.endpoint or ""
        if endpoint in open_without_key:
            return None
        if not session.get("desktop_ok"):
            return ("Open STM from its own window.", 403)
        if endpoint.startswith("auth."):
            return redirect(url_for("main.dashboard"))
        if not engine.is_connected():
            if endpoint in open_before_connecting:
                return None
            return redirect(url_for("desktop.connect"))
        if not current_user.is_authenticated:
            login_user(engine.local_user())
        if endpoint == "main.settings":
            return redirect(url_for("desktop.settings"))
        return None

    @app.context_processor
    def desktop_globals():
        from desktop.local_models import SyncConflict

        if not session.get("desktop_ok") or not engine.is_connected():
            return {"desktop": True, "sync_status": None, "sync_conflicts": []}
        return {
            "desktop": True,
            "sync_status": engine.status(),
            "sync_conflicts": SyncConflict.query.order_by(SyncConflict.date).all(),
        }

    return app
