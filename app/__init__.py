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
    if "time_entry" not in inspector.get_table_names():
        return

    existing_columns = {c["name"] for c in inspector.get_columns("time_entry")}
    statements = []
    if "target_override" not in existing_columns:
        statements.append("ALTER TABLE time_entry ADD COLUMN target_override FLOAT")
    if "leave_label" not in existing_columns:
        statements.append("ALTER TABLE time_entry ADD COLUMN leave_label VARCHAR(60)")

    if statements:
        with db.engine.begin() as conn:
            for statement in statements:
                conn.execute(sa.text(statement))


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

    @app.context_processor
    def inject_globals():
        from datetime import date

        return {"today": date.today()}

    return app
