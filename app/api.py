"""The sync API used by the STM Windows app.

Everything here is authenticated by a bearer token the app got from
``/api/v1/login``, never by the browser's session cookie -- so another
website can't make a signed-in browser call these, and the web's cookies
mean nothing to them.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

import sqlalchemy as sa
from flask import Blueprint, jsonify, request
from sqlalchemy.orm import selectinload

from app import db
from app import auth
from app.models import ApiToken, DeletedDay, TimeEntry, User
from app.sync import (
    APPLYING,
    apply_day,
    clean_payload,
    day_payload,
    next_version,
    parse_day,
    parse_edit_time,
    user_payload,
)

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# One request can carry a long offline stretch, but not an unbounded one.
MAX_CHANGES_PER_SYNC = 1000
# Recording "last used" on every sync would be a write each minute per PC.
LAST_USED_RESOLUTION = timedelta(minutes=10)


def hash_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def api_error(message, status):
    return jsonify(error=message), status


def token_from_request():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    raw = header[len("Bearer "):].strip()
    if not raw:
        return None
    token = ApiToken.query.filter_by(token_hash=hash_token(raw)).first()
    if token is not None:
        now = datetime.utcnow()
        if token.last_used_at is None or now - token.last_used_at > LAST_USED_RESOLUTION:
            token.last_used_at = now
            db.session.commit()
    return token


def _stamp(dt):
    return dt.isoformat() if dt else None


@api_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    ip = auth.client_ip()
    if auth.login_blocked(ip):
        return api_error("Too many wrong passwords. Wait 15 minutes and try again.", 429)

    username = (data.get("username") or "").strip() if isinstance(data.get("username"), str) else ""
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    user = User.query.filter_by(username=username).first() if username else None
    if user is None or not user.check_password(password):
        auth.record_login_failure(ip)
        return api_error("That username and password don't match.", 401)
    auth.clear_login_failures(ip)

    device = data.get("device") if isinstance(data.get("device"), str) else ""
    raw = secrets.token_urlsafe(32)
    db.session.add(ApiToken(
        user_id=user.id, token_hash=hash_token(raw), name=(device.strip() or "Windows PC")[:80]
    ))
    db.session.commit()
    return jsonify(token=raw, user=user_payload(user))


@api_bp.route("/logout", methods=["POST"])
def logout():
    token = token_from_request()
    if token is not None:
        db.session.delete(token)
        db.session.commit()
    return jsonify(ok=True)


@api_bp.route("/sync", methods=["POST"])
def sync():
    token = token_from_request()
    if token is None:
        return api_error("This PC isn't signed in any more. Sign in again.", 401)
    user = db.session.get(User, token.user_id)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_error("Expected a JSON object.", 400)

    since = data.get("since") or 0
    changes = data.get("changes") or []
    if not isinstance(since, int) or since < 0:
        return api_error("'since' must be a whole number.", 400)
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES_PER_SYNC:
        return api_error("'changes' must be a list of at most %d days." % MAX_CHANGES_PER_SYNC, 400)

    now = datetime.utcnow()
    conflicts, rejected = [], []
    version = None
    db.session.info[APPLYING] = True
    try:
        for change in changes:
            if not isinstance(change, dict):
                rejected.append({"date": None, "reason": "each change must be an object"})
                continue
            try:
                day = parse_day(change.get("date"))
                edited = parse_edit_time(change.get("modified_at"), now)
                incoming = change.get("day")
                clean = None if incoming is None else clean_payload(incoming)
                base = change.get("base_version")
                if base is not None and not isinstance(base, int):
                    raise ValueError("base_version must be a whole number")
            except ValueError as problem:
                rejected.append({"date": change.get("date"), "reason": str(problem)})
                continue

            entry = TimeEntry.query.filter_by(user_id=user.id, date=day).first()
            tomb = DeletedDay.query.filter_by(user_id=user.id, date=day).first()
            current = entry or tomb
            apply = True
            # Changed here since the app last saw it: the same day was edited
            # in two places. The newer edit wins, and the other version goes
            # back so the app can offer it instead -- nothing is thrown away.
            if current is not None and (current.sync_version or 0) != (base or 0):
                app_is_newer = current.modified_at is None or edited > current.modified_at
                if app_is_newer:
                    conflicts.append({"date": day.isoformat(), "kept": "app",
                                      "other": day_payload(entry), "other_modified_at": _stamp(current.modified_at)})
                else:
                    apply = False
                    conflicts.append({"date": day.isoformat(), "kept": "server",
                                      "other": incoming, "other_modified_at": _stamp(edited)})
            if apply:
                if version is None:
                    version = next_version(db.session.connection(), user.id)
                apply_day(db.session, user.id, day, clean, version=version, modified_at=edited)
        db.session.commit()
    finally:
        db.session.info.pop(APPLYING, None)

    # Breaks loaded in one query, not one per day: a first sync sends the
    # whole history, and a small host feels every extra round trip.
    entries = TimeEntry.query.options(selectinload(TimeEntry.breaks)).filter(
        TimeEntry.user_id == user.id
    )
    tombs = DeletedDay.query.filter(DeletedDay.user_id == user.id)
    if since:
        # Days never changed since this feature arrived have no version; the
        # app got them on its first sync, so later ones can skip them.
        entries = entries.filter(TimeEntry.sync_version > since)
        tombs = tombs.filter(DeletedDay.sync_version > since)
    else:
        tombs = tombs.filter(sa.false())  # a first sync has nothing to delete

    entries, tombs = list(entries), list(tombs)
    # Where the server's copy won a clash, the app must be sent that copy even
    # if it's older than the app's last sync -- otherwise the app would keep
    # its losing edit and the two would quietly stay different.
    kept_here = {parse_day(c["date"]) for c in conflicts if c["kept"] == "server"}
    have = {e.date for e in entries} | {t.date for t in tombs}
    for day in kept_here - have:
        e = TimeEntry.query.filter_by(user_id=user.id, date=day).first()
        if e is not None:
            entries.append(e)
        else:
            t = DeletedDay.query.filter_by(user_id=user.id, date=day).first()
            if t is not None:
                tombs.append(t)

    counter = db.session.execute(
        sa.select(User.__table__.c.sync_counter).where(User.__table__.c.id == user.id)
    ).scalar() or 0
    return jsonify(
        version=counter,
        days=[{"date": e.date.isoformat(), "version": e.sync_version,
               "modified_at": _stamp(e.modified_at), "day": day_payload(e)} for e in entries],
        deleted=[{"date": t.date.isoformat(), "version": t.sync_version,
                  "modified_at": _stamp(t.modified_at)} for t in tombs],
        conflicts=conflicts,
        rejected=rejected,
        user=user_payload(user),
    )
