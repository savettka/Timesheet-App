"""The Windows app's half of sync.

Local edits are noted as they're saved (``_note_local_changes``), sent to the
server by a background thread, and the server's own changes come back in the
same round trip. The status the app shows -- synced, offline, sign in again
-- lives here too.
"""

import json
import logging
import platform
import threading
import time as _time
import urllib.error
import urllib.request
from datetime import date, datetime, time
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from flask import current_app, has_app_context
from sqlalchemy import event

from app import db
from app import sync as sync_core
from app.models import BreakSegment, DeletedDay, TimeEntry, User
from desktop.local_models import PendingChange, SyncConflict, SyncMeta
from desktop.secure_store import protect, unprotect

log = logging.getLogger("stm.sync")


# ----------------------------------------------------------------- problems

class SyncProblem(Exception):
    """Something to tell the person about, already in plain words."""


class Offline(SyncProblem):
    pass


class SignedOut(SyncProblem):
    pass


class Refused(SyncProblem):
    pass


# ---------------------------------------------------------------- transport

USER_AGENT = "STM-Windows/1.0"


def http_transport(url, payload, token=None, timeout=20):
    """POST JSON to the server and return its JSON reply."""
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        message = _error_message(err)
        if err.code == 401:
            raise SignedOut(message) from None
        if err.code == 404:
            raise Refused("That address doesn't have STM's sync service. Check the address, "
                          "and that the server has the latest update.") from None
        if err.code in (400, 413, 429):
            raise Refused(message) from None
        raise Offline("The server had a problem (error %s). STM will try again shortly." % err.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise Offline("Can't reach the server right now.") from None
    try:
        return json.loads(body)
    except ValueError:
        raise Refused("That address doesn't look like an STM server.") from None


def _error_message(err):
    try:
        return json.loads(err.read().decode("utf-8")).get("error") or "The server said no."
    except Exception:
        return "The server said no (error %s)." % err.code


def normalise_server(text):
    """Turn whatever was typed into the server's base address. A password is
    never sent unencrypted: plain http is only allowed to this PC itself."""
    text = (text or "").strip()
    if not text:
        raise Refused("Enter the address you open STM at, like https://yourname.pythonanywhere.com")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if not host or parts.scheme.lower() not in ("http", "https"):
        raise Refused("That doesn't look like a web address.")
    scheme = parts.scheme.lower()
    if scheme == "http" and host not in ("localhost", "127.0.0.1"):
        scheme = "https"
    # Only the site itself -- a pasted page address like /dashboard is dropped.
    return urlunsplit((scheme, parts.netloc.lower(), "", "", ""))


def device_name():
    return ("STM on " + (platform.node() or "Windows PC"))[:80]


def _stamp(text):
    if not text:
        return None
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (stamp - stamp.utcoffset()).replace(tzinfo=None) if stamp.tzinfo else stamp


# ---------------------------------------------------------- local changes

def _note_local_changes(session, touched):
    """Change hook for the app (see app/sync.py): each changed day is marked
    as waiting to be sent, remembering the server version it started from."""
    now = datetime.utcnow()
    conn = session.connection()
    pending = PendingChange.__table__
    gone = {(o.user_id, o.date): o.sync_version for o in session.deleted if isinstance(o, TimeEntry)}
    for (user_id, day), entry in touched.items():
        row = conn.execute(sa.select(pending.c.id).where(
            pending.c.user_id == user_id, pending.c.date == day)).first()
        if row is not None:
            conn.execute(sa.update(pending).where(pending.c.id == row.id).values(
                modified_at=now, revision=pending.c.revision + 1))
        else:
            base = entry.sync_version if entry is not None else gone.get((user_id, day))
            conn.execute(sa.insert(pending).values(
                user_id=user_id, date=day, modified_at=now, base_version=base, revision=1))
        if entry is not None:
            entry.modified_at = now
    session.info["stm_changed"] = True


def _after_commit(session):
    if session.info.pop("stm_changed", False) and has_app_context():
        engine = current_app.extensions.get("stm_sync")
        if engine is not None:
            engine.nudge()


def install_change_hook():
    sync_core.desktop_change_hook = _note_local_changes
    if not event.contains(db.session, "after_commit", _after_commit):
        event.listen(db.session, "after_commit", _after_commit)


def write_local_day(session, user_id, day, clean):
    """Save a day as an ordinary edit on this PC (so it syncs as the newest
    version), from a payload -- used to bring back the other side of a clash."""
    entry = session.query(TimeEntry).filter_by(user_id=user_id, date=day).first()
    if clean is None:
        if entry is not None:
            session.delete(entry)
        return
    if entry is None:
        entry = TimeEntry(user_id=user_id, date=day)
        session.add(entry)
    for field in ("login_time", "logout_time", "notes", "target_override", "leave_label", "leave_type"):
        setattr(entry, field, clean[field])
    entry.breaks.clear()
    for start, end in clean["breaks"]:
        entry.breaks.append(BreakSegment(break_start=start, break_end=end))


# ------------------------------------------------------------------ engine

class SyncEngine:
    INTERVAL = 60        # check with the server at least this often
    RETRY_OFFLINE = 30   # try again this soon after losing the connection
    SETTLE = 1.5         # let a burst of taps settle so it goes as one sync

    def __init__(self, app, transport=http_transport):
        self.app = app
        self.transport = transport
        self.state = "idle"
        self.message = ""
        self.last_ok = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._thread = None

    # -- small settings store
    def get(self, key):
        row = db.session.get(SyncMeta, key)
        return row.value if row is not None else None

    def put(self, key, value):
        row = db.session.get(SyncMeta, key)
        if row is None:
            row = SyncMeta(key=key)
            db.session.add(row)
        row.value = None if value is None else str(value)

    def local_user(self):
        return User.query.order_by(User.id).first()

    def is_connected(self):
        """Has an account on this PC -- even one waiting to sign in again."""
        return bool(self.get("server_url")) and self.local_user() is not None

    # -- signing in and out
    def connect(self, server_text, username, password):
        url = normalise_server(server_text)
        # Nothing holds the database while waiting on the network.
        db.session.commit()
        try:
            reply = self.transport(url + "/api/v1/login", {
                "username": (username or "").strip(), "password": password or "", "device": device_name()})
        except SignedOut as problem:
            raise Refused(str(problem)) from None
        token = reply.get("token") if isinstance(reply, dict) else None
        info = reply.get("user") if isinstance(reply, dict) else None
        if not token or not isinstance(info, dict) or not info.get("username"):
            raise Refused("That address doesn't look like an STM server.")

        # Same account on the same server: keep what's here, including edits
        # still waiting to go. Anything else starts from a clean copy.
        same = self.get("server_url") == url and self.get("username") == info["username"]
        if not same:
            self._wipe()
            self.put("since", 0)
        self.put("server_url", url)
        self.put("username", info["username"])
        self.put("token", protect(token))
        self._apply_user(info)
        db.session.commit()
        self.state, self.message = "idle", ""
        self.sync_once()

    def sign_out(self):
        """Send anything waiting, end this PC's sign-in, clear the local copy."""
        db.session.commit()  # let the sync below have the database
        try:
            self.sync_once()
        except SyncProblem:
            pass
        url, token = self.get("server_url"), unprotect(self.get("token"))
        db.session.commit()  # and not hold it while waiting on the network
        if url and token:
            try:
                self.transport(url + "/api/v1/logout", {}, token, timeout=8)
            except SyncProblem:
                pass
        self._wipe()
        db.session.commit()
        self.state, self.message, self.last_ok = "idle", "", None

    def _wipe(self):
        for model in (SyncConflict, PendingChange, BreakSegment, TimeEntry, DeletedDay, User):
            db.session.execute(sa.delete(model))
        for key in ("since", "username", "token"):
            self.put(key, None)

    def _apply_user(self, info):
        user = self.local_user()
        if user is None:
            # No password: this account can only be opened from the app window.
            user = User(username=info["username"], password_hash="!", is_admin=False)
            db.session.add(user)
        user.username = info["username"]
        user.display_name = info.get("display_name") or info["username"]
        for field in ("daily_target_hours", "weekly_target_hours", "workdays",
                      "weekday_break_minutes", "saturday_break_minutes"):
            if info.get(field) is not None:
                setattr(user, field, info[field])
        hint = info.get("saturday_login_hint")
        user.saturday_login_hint = time.fromisoformat(hint) if hint else None
        db.session.flush()
        return user

    # -- one sync
    def sync_once(self):
        """One round trip to the server. Raises SyncProblem when it can't."""
        if not self._busy.acquire(timeout=30):
            return
        try:
            with self.app.app_context():
                try:
                    self._sync()
                finally:
                    db.session.remove()
        finally:
            self._busy.release()

    def _sync(self):
        url, user = self.get("server_url"), self.local_user()
        if not url or user is None:
            self.state = "idle"
            return
        token = unprotect(self.get("token"))
        if not token:
            self.state, self.message = "signed_out", "Sign in again to keep syncing."
            return
        since = int(self.get("since") or 0)
        user_id = user.id
        changes, sent = [], {}
        for p in PendingChange.query.filter_by(user_id=user_id).order_by(PendingChange.date):
            entry = TimeEntry.query.filter_by(user_id=user_id, date=p.date).first()
            changes.append({"date": p.date.isoformat(), "base_version": p.base_version,
                            "modified_at": p.modified_at.isoformat(), "day": sync_core.day_payload(entry)})
            sent[p.date] = p.revision
        db.session.commit()  # end the read before waiting on the network

        self.state = "syncing"
        try:
            reply = self.transport(url + "/api/v1/sync", {"since": since, "changes": changes}, token)
        except SignedOut as problem:
            self.state, self.message = "signed_out", str(problem)
            raise
        except Offline as problem:
            self.state, self.message = "offline", str(problem)
            raise
        except Refused as problem:
            self.state, self.message = "error", str(problem)
            raise
        self._apply(reply, user_id, sent)
        self.state, self.message, self.last_ok = "ok", "", datetime.utcnow()

    def _apply(self, reply, user_id, sent):
        session = db.session
        session.info[sync_core.APPLYING] = True
        try:
            info = reply.get("user")
            if isinstance(info, dict) and info.get("username"):
                self._apply_user(info)
            kept_server = {c.get("date") for c in reply.get("conflicts", []) if c.get("kept") == "server"}
            for c in reply.get("conflicts", []):
                session.add(SyncConflict(
                    date=date.fromisoformat(c["date"]), kept=c["kept"],
                    other_json=None if c.get("other") is None else json.dumps(c["other"])))
            for item in reply.get("rejected", []):
                log.warning("server refused %s: %s", item.get("date"), item.get("reason"))

            incoming = [(d["date"], d.get("day"), d.get("version"), d.get("modified_at"))
                        for d in reply.get("days", [])]
            incoming += [(t["date"], None, t.get("version"), t.get("modified_at"))
                         for t in reply.get("deleted", [])]
            for day_text, payload, version, modified in incoming:
                day = date.fromisoformat(day_text)
                waiting = PendingChange.query.filter_by(user_id=user_id, date=day).first()
                if waiting is not None and (day not in sent or waiting.revision != sent[day]):
                    # Edited here again while this sync was on its way: keep
                    # that edit for the next round. If the server took what
                    # was sent, build the next change on the version it made.
                    if day in sent and day_text not in kept_server:
                        waiting.base_version = version
                    continue
                clean = None if payload is None else sync_core.clean_payload(payload)
                sync_core.apply_day(session, user_id, day, clean,
                                    version=version, modified_at=_stamp(modified))

            for day, revision in sent.items():
                waiting = PendingChange.query.filter_by(user_id=user_id, date=day).first()
                if waiting is not None and waiting.revision == revision:
                    session.delete(waiting)
            self.put("since", int(reply.get("version") or 0))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.info.pop(sync_core.APPLYING, None)

    # -- the background thread
    def nudge(self):
        self._wake.set()

    def start(self):
        self._thread = threading.Thread(target=self._run, name="stm-sync", daemon=True)
        self._thread.start()

    def stop(self, final_sync=True):
        self._stop.set()
        self._wake.set()
        if final_sync:
            try:
                self.sync_once()
            except Exception:
                pass

    def _run(self):
        wait = 0
        while not self._stop.is_set():
            woken = self._wake.wait(timeout=wait)
            if self._stop.is_set():
                break
            if woken:
                self._wake.clear()
                _time.sleep(self.SETTLE)
                self._wake.clear()
            try:
                self.sync_once()
                wait = self.INTERVAL
            except SignedOut:
                wait = self.INTERVAL * 5
            except Offline:
                wait = self.RETRY_OFFLINE
            except SyncProblem:
                wait = self.INTERVAL
            except Exception:
                log.exception("sync failed")
                wait = self.INTERVAL

    # -- what the app shows
    def status(self):
        """For the status pill. Call inside a request."""
        connected = self.is_connected()
        pending = PendingChange.query.count() if connected else 0
        state = self.state if connected else "not_connected"
        if state == "syncing":
            label = "Syncing…"
        elif state == "offline":
            label = "Offline" + (" · %d saved here" % pending if pending else "")
        elif state == "signed_out":
            label = "Sign in again"
        elif state == "error":
            label = "Sync problem"
        elif state == "not_connected":
            label = "Not connected"
        elif pending:
            state, label = "pending", "%d to send" % pending
        elif self.last_ok:
            label = "Synced " + _ago(self.last_ok)
        else:
            label = "Up to date"
        return {"state": state, "label": label, "detail": self.message, "pending": pending,
                "last_ok": self.last_ok.isoformat() + "Z" if self.last_ok else None}


def _ago(then):
    seconds = max(0, (datetime.utcnow() - then).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return "%d min ago" % minutes
    hours = minutes // 60
    return "%d hr ago" % hours if hours < 24 else "%d days ago" % (hours // 24)
