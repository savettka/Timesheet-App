import calendar
import os
import secrets
import uuid
from datetime import date, datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app import logic
from app.models import BreakSegment, TimeEntry, User

main_bp = Blueprint("main", __name__)

AVATAR_SUBDIR = os.path.join("uploads", "avatars")
AVATAR_PIXELS = 256
ALLOWED_AVATAR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


# ---------------------------------------------------------------- helpers

def get_open_entry(user_id):
    return (
        TimeEntry.query.filter_by(user_id=user_id, logout_time=None)
        .filter(TimeEntry.login_time.isnot(None))
        .order_by(TimeEntry.date.desc())
        .first()
    )


def get_entries_for_range(user_id, start_date, end_date):
    entries = TimeEntry.query.filter(
        TimeEntry.user_id == user_id,
        TimeEntry.date >= start_date,
        TimeEntry.date <= end_date,
    ).all()
    return {e.date: e for e in entries}


def generate_temp_password(length=12):
    """A readable one-off password for an admin-initiated reset.

    Uses `secrets` rather than `random` so the value can't be predicted from
    other generated passwords, and drops characters that get misread when
    someone reads a password out loud or copies it by hand (0/O, 1/l/I).
    """
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def avatar_dir():
    path = os.path.join(current_app.static_folder, AVATAR_SUBDIR)
    os.makedirs(path, exist_ok=True)
    return path


def delete_avatar_file(filename):
    """Remove a stored avatar, ignoring one that's already gone."""
    if not filename:
        return
    # Guard against a stored value ever containing a path -- only ever
    # delete inside the avatars directory.
    safe = os.path.basename(filename)
    try:
        os.remove(os.path.join(avatar_dir(), safe))
    except OSError:
        pass


def save_avatar(file_storage):
    """Validate, square-crop and store an uploaded profile picture.

    Returns (filename, error_message); exactly one of the two is None.
    Re-encoding through Pillow both shrinks the phone-sized photos people
    actually upload and guarantees the stored file really is an image.
    """
    filename = (file_storage.filename or "").strip()
    if not filename:
        return None, None

    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        return None, "Profile picture must be a PNG, JPG, GIF or WEBP image."

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None, (
            "Image support isn't installed on the server yet. Run "
            "'pip install -r requirements.txt' and reload the web app."
        )

    try:
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)  # honour phone photo rotation
        image = image.convert("RGB")
        image = ImageOps.fit(
            image, (AVATAR_PIXELS, AVATAR_PIXELS), method=Image.LANCZOS, centering=(0.5, 0.4)
        )
    except Exception:
        return None, "That file doesn't look like an image we can read."

    stored_name = f"{uuid.uuid4().hex}.jpg"
    image.save(os.path.join(avatar_dir(), stored_name), "JPEG", quality=85, optimize=True)
    return stored_name, None


def parse_time_field(value):
    """Parse an 'HH:MM' string into a time object, or None if blank."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def build_dashboard_context():
    user = current_user
    now = datetime.now()
    open_entry = get_open_entry(user.id)
    reference_date = open_entry.date if open_entry else date.today()

    week_start, week_end = logic.week_bounds(reference_date)
    entries_by_date = get_entries_for_range(user.id, week_start, week_end)
    weekly = logic.weekly_totals(user, entries_by_date, reference_date, now=now)

    today_entry = entries_by_date.get(date.today())
    today_target = logic.target_hours_for(user, date.today(), today_entry)
    today_worked = logic.entry_total_hours(today_entry, now=now) if today_entry else 0.0

    suggestion = None
    if open_entry:
        worked_today_in_week = logic.entry_total_hours(open_entry, now=now)
        weekly_before_today = weekly["worked_hours"] - worked_today_in_week
        reached, suggested_dt, still_needed = logic.suggested_logout(
            open_entry, weekly_before_today, weekly["target_hours"], now=now, user=user
        )
        # A suggested clock-out that lands on a later day is arithmetic, not
        # advice -- it assumes working straight through without ever logging
        # out. Early in the week that is always the case, so fall back to
        # today's own standard hours, which is a target today can actually meet.
        lands_today = suggested_dt is not None and suggested_dt.date() == now.date()
        today_remaining = today_target - today_worked
        # Same allowance as the weekly suggestion: the break still to be taken
        # is time at the desk that won't count as worked.
        break_to_come = logic.unrecorded_break_hours(
            user, open_entry.date, open_entry, now=now
        )
        today_dt = (
            now + timedelta(hours=today_remaining + break_to_come)
            if today_remaining > 0
            else None
        )
        suggestion = {
            "reached": reached,
            "lands_today": lands_today,
            "suggested_time": logic.fmt_suggested_datetime(suggested_dt, open_entry.date),
            "still_needed_hours": still_needed,
            "today_target_met": today_remaining <= 0,
            "today_time": logic.fmt_suggested_datetime(today_dt, open_entry.date),
            "today_target_fmt": logic.fmt_hours(today_target),
            "break_allowance_fmt": (
                logic.fmt_hours(break_to_come) if break_to_come > 0 else None
            ),
        }

    saturday = logic.saturday_plan(
        user, entries_by_date, week_start, weekly["target_hours"], date.today(), now=now
    )

    open_break = open_entry.open_break() if open_entry else None
    open_entry_closed_break_seconds = (
        int(logic.closed_break_hours(open_entry) * 3600) if open_entry else 0
    )

    recent_entries = (
        TimeEntry.query.filter_by(user_id=user.id)
        .order_by(TimeEntry.date.desc())
        .limit(7)
        .all()
    )

    return {
        "open_entry": open_entry,
        "open_break": open_break,
        "open_entry_closed_break_seconds": open_entry_closed_break_seconds,
        "weekly": weekly,
        "today_entry": today_entry,
        "today_target": today_target,
        "today_worked": today_worked,
        "suggestion": suggestion,
        "saturday": saturday,
        "recent_entries": recent_entries,
        "now": now,
        "fmt_hours": logic.fmt_hours,
        "fmt_time": logic.fmt_time,
        "entry_total_hours": lambda e: logic.entry_total_hours(e, now=now),
        "entry_break_hours": lambda e: logic.entry_break_hours(e, now=now),
    }


# ------------------------------------------------------------------ pages

@main_bp.route("/")
@login_required
def index():
    return redirect(url_for("main.dashboard"))


@main_bp.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", **build_dashboard_context())


@main_bp.route("/api/status")
@login_required
def api_status():
    ctx = build_dashboard_context()
    now = ctx["now"]
    open_entry = ctx["open_entry"]
    open_break = ctx["open_break"]
    payload = {
        "today_worked_hours": ctx["today_worked"],
        "today_worked_fmt": logic.fmt_hours(ctx["today_worked"]),
        "today_target_hours": ctx["today_target"],
        "weekly_worked_hours": ctx["weekly"]["worked_hours"],
        "weekly_worked_fmt": logic.fmt_hours(ctx["weekly"]["worked_hours"]),
        "weekly_target_hours": ctx["weekly"]["target_hours"],
        "weekly_remaining_hours": ctx["weekly"]["remaining_hours"],
        "weekly_remaining_fmt": logic.fmt_hours(ctx["weekly"]["remaining_hours"]),
        "weekly_progress_pct": round(ctx["weekly"]["progress_pct"], 1),
        "weekly_complete": ctx["weekly"]["complete"],
        "is_logged_in": bool(open_entry),
        "is_on_break": bool(open_break),
        "login_time": logic.fmt_time(open_entry.login_time) if open_entry else None,
        "login_date": open_entry.date.isoformat() if open_entry else None,
        "break_start_time": logic.fmt_time(open_break.break_start) if open_break else None,
        "closed_break_seconds": int(
            sum(
                logic.break_segment_hours(open_entry, b, now=now)
                for b in (open_entry.breaks if open_entry else [])
                if b.break_end is not None
            )
            * 3600
        ),
        "suggestion": ctx["suggestion"],
        "saturday": (
            {
                **ctx["saturday"],
                "saturday_date": ctx["saturday"]["saturday_date"].isoformat(),
                # Pre-formatted so the live update reads identically to the
                # server-rendered version rather than reimplementing fmt_hours.
                "remaining_fmt": logic.fmt_hours(ctx["saturday"].get("remaining_hours")),
                "worked_fmt": logic.fmt_hours(ctx["saturday"].get("worked_hours")),
            }
            if ctx["saturday"]
            else None
        ),
        "server_time": now.strftime("%H:%M:%S"),
    }
    return jsonify(payload)


# ---------------------------------------------------------------- punches

@main_bp.route("/punch/in", methods=["POST"])
@login_required
def punch_in():
    user_id = current_user.id
    if get_open_entry(user_id):
        flash("You're already logged in.", "error")
        return redirect(url_for("main.dashboard"))

    custom_time = parse_time_field(request.form.get("time"))
    punch_dt = datetime.combine(date.today(), custom_time) if custom_time else datetime.now()
    entry_date = punch_dt.date()

    existing = TimeEntry.query.filter_by(user_id=user_id, date=entry_date).first()
    if existing:
        flash(
            f"There's already an entry for {entry_date.strftime('%d %b %Y')}. "
            "Edit it from History instead.",
            "error",
        )
        return redirect(url_for("main.dashboard"))

    entry = TimeEntry(user_id=user_id, date=entry_date, login_time=punch_dt.time())
    db.session.add(entry)
    db.session.commit()
    flash(f"Punched in at {logic.fmt_time(entry.login_time)}.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/punch/break/start", methods=["POST"])
@login_required
def punch_break_start():
    entry = get_open_entry(current_user.id)
    if not entry:
        flash("You need to punch in first.", "error")
        return redirect(url_for("main.dashboard"))
    if entry.open_break():
        flash("You're already on a break.", "error")
        return redirect(url_for("main.dashboard"))

    custom_time = parse_time_field(request.form.get("time"))
    t = custom_time or datetime.now().time()
    db.session.add(BreakSegment(entry_id=entry.id, break_start=t))
    db.session.commit()
    flash(f"Break started at {logic.fmt_time(t)}.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/punch/break/end", methods=["POST"])
@login_required
def punch_break_end():
    entry = get_open_entry(current_user.id)
    open_break = entry.open_break() if entry else None
    if not open_break:
        flash("You're not currently on a break.", "error")
        return redirect(url_for("main.dashboard"))

    custom_time = parse_time_field(request.form.get("time"))
    t = custom_time or datetime.now().time()
    open_break.break_end = t
    db.session.commit()
    flash(f"Break ended at {logic.fmt_time(t)}.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/punch/out", methods=["POST"])
@login_required
def punch_out():
    entry = get_open_entry(current_user.id)
    if not entry:
        flash("You're not currently logged in.", "error")
        return redirect(url_for("main.dashboard"))
    if entry.open_break():
        flash("End your break before logging out.", "error")
        return redirect(url_for("main.dashboard"))

    custom_time = parse_time_field(request.form.get("time"))
    t = custom_time or datetime.now().time()
    entry.logout_time = t
    db.session.commit()
    total = logic.entry_total_hours(entry)
    flash(f"Punched out at {logic.fmt_time(t)}. Total: {logic.fmt_hours(total)}.", "success")
    return redirect(url_for("main.dashboard"))


# ---------------------------------------------------------------- history

@main_bp.route("/history")
@main_bp.route("/history/<int:year>/<int:month>")
@login_required
def history(year=None, month=None):
    today = date.today()
    year = year or today.year
    month = month or today.month

    first_day = date(year, month, 1)
    last_day_num = calendar.monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)

    entries_by_date = get_entries_for_range(current_user.id, first_day, last_day)
    now = datetime.now()

    rows = []
    for i in range(last_day_num):
        d = first_day + timedelta(days=i)
        is_future = d > today
        entry = entries_by_date.get(d)
        # A day you haven't reached yet can't owe hours -- only count a target
        # once the day has actually arrived, unless it already carries an
        # explicit override (e.g. pre-booked leave), which the user set on
        # purpose and should show up right away.
        if entry is not None:
            target = logic.target_hours_for(current_user, d, entry)
        elif is_future:
            target = 0.0
        else:
            target = logic.entry_target_hours(current_user, d)
        worked = logic.entry_total_hours(entry, now=now) if entry else 0.0
        rows.append(
            {
                "date": d,
                "entry": entry,
                "target_hours": target,
                "worked_hours": worked,
                "is_future": is_future,
            }
        )

    month_total = sum(r["worked_hours"] for r in rows)
    month_target = sum(r["target_hours"] for r in rows)

    prev_month = (first_day - timedelta(days=1)).replace(day=1)
    next_month_first = last_day + timedelta(days=1)

    return render_template(
        "history.html",
        rows=rows,
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        month_total=month_total,
        month_target=month_target,
        prev_year=prev_month.year,
        prev_month=prev_month.month,
        next_year=next_month_first.year,
        next_month=next_month_first.month,
        fmt_hours=logic.fmt_hours,
        fmt_time=logic.fmt_time,
    )


@main_bp.route("/entry/<date_str>", methods=["GET", "POST"])
@login_required
def edit_entry(date_str):
    try:
        entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("main.history"))

    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=entry_date).first()
    default_target = logic.entry_target_hours(current_user, entry_date)

    if request.method == "POST":
        login_t = parse_time_field(request.form.get("login_time"))
        logout_t = parse_time_field(request.form.get("logout_time"))
        notes = (request.form.get("notes") or "").strip() or None

        target_override_raw = (request.form.get("target_override") or "").strip()
        target_override = None
        if target_override_raw:
            try:
                target_override = max(0.0, float(target_override_raw))
            except ValueError:
                flash("Standard hours for this day must be a number.", "error")
                return render_template(
                    "entry_form.html", entry=entry, entry_date=entry_date, default_target=default_target
                )
        leave_label = (request.form.get("leave_label") or "").strip() or None

        break_starts = request.form.getlist("break_start")
        break_ends = request.form.getlist("break_end")

        if entry is None:
            entry = TimeEntry(user_id=current_user.id, date=entry_date)
            db.session.add(entry)

        entry.login_time = login_t
        entry.logout_time = logout_t
        entry.notes = notes
        entry.target_override = target_override
        entry.leave_label = leave_label

        entry.breaks.clear()
        for bs, be in zip(break_starts, break_ends):
            bs_t = parse_time_field(bs)
            be_t = parse_time_field(be)
            if bs_t is None and be_t is None:
                continue
            if bs_t is None:
                flash("Each break needs a start time.", "error")
                continue
            entry.breaks.append(BreakSegment(break_start=bs_t, break_end=be_t))

        db.session.commit()
        flash(f"Saved entry for {entry_date.strftime('%d %b %Y')}.", "success")
        return redirect(url_for("main.history", year=entry_date.year, month=entry_date.month))

    return render_template(
        "entry_form.html", entry=entry, entry_date=entry_date, default_target=default_target
    )


@main_bp.route("/entry/<date_str>/delete", methods=["POST"])
@login_required
def delete_entry(date_str):
    try:
        entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("main.history"))

    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=entry_date).first()
    if entry:
        db.session.delete(entry)
        db.session.commit()
        flash(f"Deleted entry for {entry_date.strftime('%d %b %Y')}.", "success")

    return redirect(url_for("main.history", year=entry_date.year, month=entry_date.month))


# --------------------------------------------------------------- settings

@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user = current_user

    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "targets":
            try:
                daily = float(request.form.get("daily_target_hours", 8))
                weekly = float(request.form.get("weekly_target_hours", 48))
            except ValueError:
                flash("Please enter valid numbers.", "error")
                return redirect(url_for("main.settings"))

            try:
                weekday_break = int(request.form.get("weekday_break_minutes", 60) or 0)
                saturday_break = int(request.form.get("saturday_break_minutes", 30) or 0)
            except ValueError:
                flash("Please enter break lengths in whole minutes.", "error")
                return redirect(url_for("main.settings"))

            workdays = request.form.getlist("workdays")
            user.daily_target_hours = max(0.0, daily)
            user.weekly_target_hours = max(0.0, weekly)
            # Capped at a day: a longer "break" would push every suggestion
            # past midnight rather than mean anything useful.
            user.weekday_break_minutes = min(1440, max(0, weekday_break))
            user.saturday_break_minutes = min(1440, max(0, saturday_break))
            user.workdays = ",".join(sorted(set(workdays))) if workdays else ""
            user.saturday_login_hint = parse_time_field(request.form.get("saturday_login_hint"))
            db.session.commit()
            flash("Targets updated.", "success")

        elif form_type == "profile":
            display_name = (request.form.get("display_name") or "").strip()
            user.display_name = display_name or user.username

            # Lowercased so a code request matches regardless of how the
            # address was typed, and checked for uniqueness so one address
            # can never resolve to two accounts.
            email = (request.form.get("email") or "").strip().lower() or None
            if email != user.email:
                clash = User.query.filter(
                    User.email == email, User.id != user.id
                ).first() if email else None
                if clash:
                    flash("That email address is already used by another account.", "error")
                    return redirect(url_for("main.settings"))
                user.email = email

            if request.form.get("remove_avatar"):
                delete_avatar_file(user.avatar_filename)
                user.avatar_filename = None
                db.session.commit()
                flash("Profile picture removed.", "success")
                return redirect(url_for("main.settings"))

            upload = request.files.get("avatar")
            if upload and upload.filename:
                stored_name, error = save_avatar(upload)
                if error:
                    flash(error, "error")
                    return redirect(url_for("main.settings"))
                # Only drop the old file once the new one is safely written.
                delete_avatar_file(user.avatar_filename)
                user.avatar_filename = stored_name

            db.session.commit()
            flash("Profile updated.", "success")

        elif form_type == "password":
            current_pw = request.form.get("current_password", "")
            new_pw = request.form.get("new_password", "")
            confirm_pw = request.form.get("confirm_password", "")

            if not user.check_password(current_pw):
                flash("Current password is incorrect.", "error")
            elif len(new_pw) < 6:
                flash("New password must be at least 6 characters.", "error")
            elif new_pw != confirm_pw:
                flash("New passwords do not match.", "error")
            else:
                user.set_password(new_pw)
                db.session.commit()
                flash("Password changed.", "success")

        elif form_type == "add_user":
            # Hiding the form isn't access control -- a non-admin can still
            # post this by hand, so the check has to live here.
            if not user.is_admin:
                abort(403)
            new_username = (request.form.get("new_username") or "").strip()
            new_display_name = (request.form.get("new_display_name") or "").strip()
            new_email = (request.form.get("new_email") or "").strip().lower() or None
            new_password = request.form.get("new_user_password", "")
            new_confirm = request.form.get("new_user_confirm", "")

            if not new_username or not new_password:
                flash("Username and password are required.", "error")
            elif User.query.filter_by(username=new_username).first():
                flash("That username is already taken.", "error")
            elif new_email and User.query.filter_by(email=new_email).first():
                flash("That email address is already used by another account.", "error")
            elif len(new_password) < 6:
                flash("Password must be at least 6 characters.", "error")
            elif new_password != new_confirm:
                flash("Passwords do not match.", "error")
            else:
                new_user = User(
                    username=new_username,
                    display_name=new_display_name or new_username,
                    email=new_email,
                    daily_target_hours=user.daily_target_hours,
                    weekly_target_hours=user.weekly_target_hours,
                    workdays=user.workdays,
                )
                new_user.set_password(new_password)
                db.session.add(new_user)
                db.session.commit()
                flash(f"Added {new_user.display_name} as a new user.", "success")

        elif form_type == "reset_password":
            if not user.is_admin:
                abort(403)
            target_id = request.form.get("user_id", type=int)
            if target_id == user.id:
                # The admin changes their own password the normal way, where
                # it's confirmed with the current one.
                flash("Use 'Change password' to set your own password.", "error")
            else:
                target = db.session.get(User, target_id) if target_id else None
                if target:
                    temp_password = generate_temp_password()
                    target.set_password(temp_password)
                    db.session.commit()
                    label = target.display_name or target.username
                    flash(
                        f"New password for {label}: {temp_password} — share it with them "
                        "now, it isn't shown again. Ask them to change it in Settings.",
                        "success",
                    )

        elif form_type == "remove_user":
            if not user.is_admin:
                abort(403)
            target_id = request.form.get("user_id", type=int)
            if target_id == user.id:
                flash("You can't remove your own account.", "error")
            else:
                target = db.session.get(User, target_id) if target_id else None
                if target:
                    label = target.display_name or target.username
                    delete_avatar_file(target.avatar_filename)
                    db.session.delete(target)
                    db.session.commit()
                    flash(f"Removed {label}.", "success")

        return redirect(url_for("main.settings"))

    # Non-admins are never sent the list, so other accounts aren't merely
    # hidden in the markup -- they never reach the page.
    all_users = User.query.order_by(User.username).all() if user.is_admin else []
    return render_template("settings.html", user=user, all_users=all_users)
