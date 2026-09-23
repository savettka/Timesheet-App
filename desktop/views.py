"""Pages only the Windows app has: connecting to the server, this PC's
settings, and settling a day that was changed in two places."""

import json

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app import db
from app import sync as sync_core
from desktop.engine import SyncProblem, write_local_day
from desktop.local_models import PendingChange, SyncConflict

desktop_bp = Blueprint("desktop", __name__, url_prefix="/desktop")


def _engine():
    return current_app.extensions["stm_sync"]


@desktop_bp.route("/enter")
def enter():
    """The one door in: the window opens this with the key made at launch."""
    if request.args.get("key") != current_app.config["DESKTOP_KEY"]:
        return ("Open STM from its own window.", 403)
    session.clear()
    session["desktop_ok"] = True
    return redirect(url_for("main.dashboard"))


@desktop_bp.route("/connect", methods=["GET", "POST"])
def connect():
    engine = _engine()
    signed_out = engine.is_connected() and engine.state == "signed_out"
    if engine.is_connected() and not signed_out and request.method == "GET":
        return redirect(url_for("main.dashboard"))

    server = engine.get("server_url") or ""
    username = engine.get("username") or ""
    if request.method == "POST":
        server = request.form.get("server", "")
        username = request.form.get("username", "")
        try:
            engine.connect(server, username, request.form.get("password", ""))
        except SyncProblem as problem:
            flash(str(problem), "error")
        else:
            flash("Connected. Your hours are on this PC now, and sync whenever you're online.", "success")
            return redirect(url_for("main.dashboard"))
    return render_template("desktop/connect.html", server=server, username=username,
                           signed_out=signed_out, pending=PendingChange.query.count())


@desktop_bp.route("/status")
def status():
    return jsonify(_engine().status())


@desktop_bp.route("/settings")
def settings():
    from desktop import __version__

    engine = _engine()
    return render_template("desktop/settings.html", user=engine.local_user(),
                           server=engine.get("server_url"), status=engine.status(),
                           app_version=__version__)


@desktop_bp.route("/sync-now", methods=["POST"])
def sync_now():
    db.session.commit()  # this request's reads hold the database; let the sync have it
    try:
        _engine().sync_once()
    except SyncProblem as problem:
        flash(str(problem), "error")
    else:
        flash("Synced with the server.", "success")
    return redirect(request.referrer or url_for("desktop.settings"))


@desktop_bp.route("/sign-out", methods=["POST"])
def sign_out():
    _engine().sign_out()
    session.clear()
    session["desktop_ok"] = True
    flash("Signed out. This PC no longer has a copy of your hours.", "success")
    return redirect(url_for("desktop.connect"))


@desktop_bp.route("/conflicts/<int:conflict_id>", methods=["POST"])
def settle(conflict_id):
    conflict = db.session.get(SyncConflict, conflict_id)
    if conflict is None:
        return redirect(request.referrer or url_for("main.dashboard"))
    if request.form.get("choice") == "other":
        other = json.loads(conflict.other_json) if conflict.other_json else None
        clean = None if other is None else sync_core.clean_payload(other)
        # Saved as a fresh edit here, so it goes to the server as the newest.
        write_local_day(db.session, _engine().local_user().id, conflict.date, clean)
        flash("Brought back the other version of %s." % conflict.date.strftime("%a %d %b"), "success")
    db.session.delete(conflict)
    db.session.commit()
    return redirect(request.referrer or url_for("main.dashboard"))
