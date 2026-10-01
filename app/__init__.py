import os

import sqlalchemy as sa
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


def _run_light_migrations():
    """Add columns introduced after a database already exists.

    db.create_all() only creates missing *tables*, so an existing SQLite
    file from an earlier version of the app needs its new columns added
    by hand. This is a no-op on a fresh install (create_all already wrote
    the current schema) and a no-op on repeat runs (columns already exist).
    """
    inspector = sa.inspect(db.engine)
    table_names = inspector.get_table_names()
    statements = []

    if "time_entry" in table_names:
        entry_columns = {c["name"] for c in inspector.get_columns("time_entry")}
        if "target_override" not in entry_columns:
            statements.append("ALTER TABLE time_entry ADD COLUMN target_override FLOAT")
        if "leave_label" not in entry_columns:
            statements.append("ALTER TABLE time_entry ADD COLUMN leave_label VARCHAR(60)")
        if "leave_type" not in entry_columns:
            statements.append("ALTER TABLE time_entry ADD COLUMN leave_type VARCHAR(10)")

    if "user" in table_names:
        user_columns = {c["name"] for c in inspector.get_columns("user")}
        if "saturday_login_hint" not in user_columns:
            statements.append("ALTER TABLE user ADD COLUMN saturday_login_hint TIME")
        if "is_admin" not in user_columns:
            statements.append(
                "ALTER TABLE user ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            )
        if "avatar_filename" not in user_columns:
            statements.append("ALTER TABLE user ADD COLUMN avatar_filename VARCHAR(120)")
        if "weekday_break_minutes" not in user_columns:
            statements.append(
                "ALTER TABLE user ADD COLUMN weekday_break_minutes "
                "INTEGER NOT NULL DEFAULT 60"
            )
        if "saturday_break_minutes" not in user_columns:
            statements.append(
                "ALTER TABLE user ADD COLUMN saturday_break_minutes "
                "INTEGER NOT NULL DEFAULT 30"
            )
        if "email" not in user_columns:
            # Added without UNIQUE: SQLite can't add a unique column in place.
            # The unique index below does the same job on an existing table.
            statements.append("ALTER TABLE user ADD COLUMN email VARCHAR(200)")
            statements.append(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_email ON user (email)"
            )

    if "break_segment" in table_names:
        # create_all() only adds indexes for tables it creates, so an existing
        # database needs this one added explicitly.
        statements.append(
            "CREATE INDEX IF NOT EXISTS ix_break_segment_entry_id "
            "ON break_segment (entry_id)"
        )

    if statements:
        with db.engine.begin() as conn:
            for statement in statements:
                conn.execute(sa.text(statement))

    if "hour_note" in table_names:
        _move_hour_notes()

    # A database that predates roles has every account non-admin, which would
    # lock everyone out of user management. Promote the first account created
    # -- the one that ran setup and owns the install -- so ownership lands
    # with the original user rather than whoever was added later.
    if "user" in table_names:
        with db.engine.begin() as conn:
            has_admin = conn.execute(
                sa.text("SELECT 1 FROM user WHERE is_admin = 1 LIMIT 1")
            ).first()
            if not has_admin:
                conn.execute(
                    sa.text(
                        "UPDATE user SET is_admin = 1 WHERE id = "
                        "(SELECT MIN(id) FROM user)"
                    )
                )


def _move_hour_notes():
    """Bring the Timesheet's first version's lines across, once.

    That version saved one line per whole hour (table hour_note); lines are
    now stretches with their own times (work_block). An old note takes the
    shape of the line the page now offers for its hour when it's the only
    note on that line and its hour is the bulk of that line -- a 1-2 PM note
    on a day logged in at 12:47 becomes 12:47 to 2:00 PM -- and otherwise
    keeps its own hour, trimmed to the day's line. Reading, copying and
    dropping the old table happen under one write lock: nothing can be
    written to it in between, and if anything fails, the old notes stay
    exactly as they were, to be brought across next start.
    """
    from datetime import datetime
    from types import SimpleNamespace

    from app import logic
    from app.models import TimeEntry, WorkBlock

    entries = TimeEntry.__table__
    old_notes = sa.text("SELECT user_id, date, hour, text, updated_at FROM hour_note").columns(
        user_id=sa.Integer, date=sa.Date, hour=sa.Integer, text=sa.String, updated_at=sa.DateTime
    )
    now = datetime.now()

    def overlap(line, hour):
        return min(line[1], hour * 60 + 60) - max(line[0], hour * 60)

    with db.engine.begin() as conn:
        # SQLite's driver otherwise starts the transaction only at the first
        # write, leaving the read below outside it.
        conn.exec_driver_sql("BEGIN IMMEDIATE")
        days = {}
        for note in conn.execute(old_notes):
            days.setdefault((note.user_id, note.date), []).append(note)
        rows = []
        for (user_id, day), notes in days.items():
            times = conn.execute(
                sa.select(entries.c.login_time, entries.c.logout_time)
                .where(entries.c.user_id == user_id, entries.c.date == day)
            ).first()
            entry = SimpleNamespace(date=day, login_time=times[0], logout_time=times[1]) if times else None
            lines = logic.timesheet_lines(entry, day, now)
            home = {}
            for note in notes:
                best = max(lines, key=lambda line: overlap(line, note.hour), default=None)
                home[note.hour] = best if best and overlap(best, note.hour) > 0 else None
            for note in notes:
                line = home[note.hour]
                start, end = note.hour * 60, note.hour * 60 + 60
                # A note on just the 13-minute end of a 12:47-2:00 PM line
                # mustn't grow over the hour it never described.
                if (line and list(home.values()).count(line) == 1
                        and overlap(line, note.hour) >= logic.TIMESHEET_SHORT_PIECE):
                    start, end = line[0], line[1]
                elif line:
                    start, end = max(start, line[0]), min(end, line[1])
                rows.append({
                    "user_id": user_id, "date": day, "text": note.text, "updated_at": note.updated_at,
                    "start_time": logic.minutes_clock(start), "end_time": logic.minutes_clock(end),
                })
        if rows:
            conn.execute(WorkBlock.__table__.insert(), rows)
        conn.execute(sa.text("DROP TABLE hour_note"))


def create_app(config_object="config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)

    # The default SQLite URI (config.Config) points at <project_root>/instance/stm.db;
    # make sure that directory exists regardless of how Flask resolves instance_path.
    db_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if db_uri.startswith("sqlite:///"):
        os.makedirs(os.path.dirname(db_uri.replace("sqlite:///", "", 1)), exist_ok=True)
    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.main import main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)

    with app.app_context():
        db.create_all()
        _run_light_migrations()

    @app.errorhandler(413)
    def too_large(_error):
        # Without this, an oversized photo returns a bare browser error page.
        from flask import flash, redirect, url_for

        flash("That image is too large -- please pick one under 8 MB.", "error")
        return redirect(url_for("main.settings"))

    @app.url_defaults
    def add_static_version(endpoint, values):
        """Stamp every static URL with the file's modification time.

        Browsers cache CSS and JS hard, so after a deploy the page keeps
        using the old stylesheet until someone force-refreshes. Changing
        the file changes the URL, which makes the browser fetch it again
        on its own.
        """
        if endpoint != "static" or "filename" not in values:
            return
        try:
            values["v"] = int(
                os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime
            )
        except OSError:
            pass  # missing file - let the request 404 on its own terms

    @app.context_processor
    def inject_globals():
        from datetime import date

        return {"today": date.today()}

    return app
