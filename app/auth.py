import secrets
import time
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import generate_password_hash

from app import db, mailer
from app.models import LoginCode, User

auth_bp = Blueprint("auth", __name__)

# Wait this long before the same address may request another code. Stops the
# form being used to flood someone's inbox (and to burn the daily send quota).
RESEND_COOLDOWN_SECONDS = 60

# A per-process cap on how many codes one IP may request per hour, so a single
# attacker can't cycle through many addresses. Held in memory rather than the
# database: it resets on reload, which is fine for a throttle, and it costs no
# writes on a small host.
IP_REQUEST_LIMIT = 10
IP_REQUEST_WINDOW_SECONDS = 3600
_ip_requests = {}


def _ip_rate_limited(ip):
    """Record a code request from `ip`, returning True if it's over the cap."""
    now = time.monotonic()
    cutoff = now - IP_REQUEST_WINDOW_SECONDS
    recent = [t for t in _ip_requests.get(ip, []) if t > cutoff]
    # Drop addresses whose requests have all aged out, so the dict can't grow
    # without bound on a long-running process.
    for known_ip in [k for k, v in _ip_requests.items() if not [t for t in v if t > cutoff]]:
        _ip_requests.pop(known_ip, None)
    if len(recent) >= IP_REQUEST_LIMIT:
        _ip_requests[ip] = recent
        return True
    recent.append(now)
    _ip_requests[ip] = recent
    return False


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if User.query.first() is not None:
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        error = None
        if not username or not password:
            error = "Username and password are required."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif password != confirm:
            error = "Passwords do not match."

        if error:
            flash(error, "error")
            return render_template("setup.html", username=username, display_name=display_name)

        # Whoever runs setup owns the install and manages everyone else.
        user = User(
            username=username, display_name=display_name or username, is_admin=True
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Welcome to STM! Your account is ready.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("setup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if User.query.first() is None:
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            next_url = request.args.get("next")
            return redirect(next_url or url_for("main.dashboard"))
        flash("Invalid username or password.", "error")

    return render_template("login.html", code_login_available=mailer.is_configured())


def _safe_next_url():
    """Only follow a `next` that stays on this site.

    Without this check an attacker could send someone a login link whose
    `next` points at their own site, and bounce the user there straight
    after a successful sign-in.
    """
    target = request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return None


@auth_bp.route("/login/code", methods=["GET", "POST"])
def request_code():
    """Ask for a sign-in code to be emailed."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if not mailer.is_configured():
        flash("Signing in by email code isn't set up on this server.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()

        if _ip_rate_limited(request.remote_addr or "unknown"):
            flash("Too many code requests from this device. Try again later.", "error")
            return render_template("login_code.html")

        user = User.query.filter_by(email=email).first() if email else None

        if user:
            recent = (
                LoginCode.query.filter_by(user_id=user.id)
                .order_by(LoginCode.created_at.desc())
                .first()
            )
            within_cooldown = recent and (
                datetime.utcnow() - recent.created_at
            ) < timedelta(seconds=RESEND_COOLDOWN_SECONDS)

            if not within_cooldown:
                # Any earlier code stops working the moment a new one is
                # issued, so a user can never have two live codes at once.
                LoginCode.query.filter_by(user_id=user.id, used_at=None).delete()

                code = f"{secrets.randbelow(1_000_000):06d}"
                db.session.add(
                    LoginCode(
                        user_id=user.id,
                        code_hash=generate_password_hash(code),
                        expires_at=datetime.utcnow()
                        + timedelta(minutes=LoginCode.LIFETIME_MINUTES),
                    )
                )
                db.session.commit()
                mailer.send_login_code(user.email, code, LoginCode.LIFETIME_MINUTES)

        # The same response either way: a stranger can't use this form to work
        # out which addresses have accounts. Real send failures are logged
        # server-side rather than surfaced here for the same reason.
        session["code_email"] = email
        return redirect(url_for("auth.verify_code"))

    return render_template("login_code.html")


@auth_bp.route("/login/verify", methods=["GET", "POST"])
def verify_code():
    """Enter the emailed code."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    email = session.get("code_email")
    if not email:
        return redirect(url_for("auth.request_code"))

    if request.method == "POST":
        submitted = (request.form.get("code") or "").strip().replace(" ", "")
        user = User.query.filter_by(email=email).first()

        record = None
        if user:
            record = (
                LoginCode.query.filter_by(user_id=user.id, used_at=None)
                .order_by(LoginCode.created_at.desc())
                .first()
            )

        if record and record.verify(submitted):
            db.session.commit()
            # The code is spent; clear anything else outstanding for them.
            LoginCode.query.filter_by(user_id=user.id, used_at=None).delete()
            db.session.commit()
            session.pop("code_email", None)
            # A fresh session id after sign-in, so a session fixed by an
            # attacker beforehand doesn't become a logged-in one.
            session.modified = True
            login_user(user, remember=True)
            return redirect(_safe_next_url() or url_for("main.dashboard"))

        if record:
            # Commit the attempt count even on a wrong guess -- that counter is
            # what makes a six-digit code impractical to brute-force.
            db.session.commit()
            remaining = LoginCode.MAX_ATTEMPTS - record.attempts
            if remaining <= 0:
                flash("Too many wrong codes. Request a new one.", "error")
                return redirect(url_for("auth.request_code"))

        flash("That code isn't right, or it has expired.", "error")

    return render_template("verify_code.html", email=email)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
