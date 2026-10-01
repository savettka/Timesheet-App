import calendar
import math
import os
import secrets
import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app import db
from app import logic
from app.models import BreakSegment, TimeEntry, User, WorkBlock

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
    today = now.date()
    open_entry = get_open_entry(user.id)
    # A shift left open so long it's almost certainly a forgotten logout:
    # logging out now would record a day of 20-odd hours, so the dashboard
    # asks when it really ended, and offers no suggestions built on it.
    stale = logic.is_stale_shift(open_entry, now)
    reference_date = open_entry.date if open_entry else today

    week_start, week_end = logic.week_bounds(reference_date)
    entries_by_date = get_entries_for_range(user.id, week_start, week_end)
    weekly = logic.weekly_totals(user, entries_by_date, reference_date, now=now)

    today_entry = entries_by_date.get(today)
    if today_entry is None and not (week_start <= today <= week_end):
        # An open shift from last week leaves today outside the week just
        # loaded, so fetch it on its own rather than miss it.
        today_entry = TimeEntry.query.filter_by(user_id=user.id, date=today).first()
    today_target = logic.target_hours_for(user, today, today_entry)
    today_worked = logic.entry_total_hours(today_entry, now=now) if today_entry else 0.0
    # Same rule as History: an unfinished day owes only what it has had the
    # chance to work, so the morning doesn't read as a whole day behind.
    today_accrued_target = logic.accrued_target_hours(
        today_target, today_entry, today, today, now=now
    )

    suggestion = None
    if open_entry and not stale:
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
            "today_target_fmt": logic.fmt_duration(today_target),
            "break_allowance_fmt": (
                logic.fmt_duration(break_to_come) if break_to_come > 0 else None
            ),
        }

    saturday = logic.saturday_plan(
        user, entries_by_date, week_start, date.today(), now=now
    )

    open_break = open_entry.open_break() if open_entry else None
    open_entry_closed_break_seconds = (
        int(logic.closed_break_hours(open_entry) * 3600) if open_entry else 0
    )

    # Which moment of the day the dashboard is showing.
    if open_entry is not None:
        day_state = "stale" if stale else ("break" if open_break else "working")
    elif today_entry is not None and today_entry.login_time and today_entry.logout_time:
        day_state = "done"
    else:
        day_state = "ready"

    if open_entry is not None:
        shift_worked = logic.entry_total_hours(open_entry, now=now)
    elif day_state == "done":
        shift_worked = today_worked
    else:
        shift_worked = 0.0
    break_elapsed = (
        logic.break_segment_hours(open_entry, open_break, now=now) if open_break else 0.0
    )

    if now.hour < 12:
        greeting = "Good morning"
    elif now.hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

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
        "today_accrued_target": today_accrued_target,
        "today_in_progress": today_accrued_target < today_target,
        "today_worked": today_worked,
        "suggestion": suggestion,
        "saturday": saturday,
        "recent_entries": recent_entries,
        "now": now,
        "day_state": day_state,
        # The live timer counts on from these instead of re-deriving times
        # from a login clock time, which the browser would read in its own
        # time zone -- wrong whenever the phone and server disagree.
        "shift_worked_seconds": int(shift_worked * 3600),
        "break_elapsed_seconds": int(break_elapsed * 3600),
        "greeting": greeting,
        "fmt_balance": logic.fmt_balance,
        "fmt_duration": logic.fmt_duration,
        "fmt_hours_compact": logic.fmt_hours_compact,
        "fmt_hours": logic.fmt_hours,
        "fmt_target_hours": logic.fmt_target_hours,
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
    ctx = build_dashboard_context()
    today = ctx["now"].date()
    first_day = today.replace(day=1)
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    month_entries = get_entries_for_range(current_user.id, first_day, last_day)
    ctx["month"] = logic.month_summary(
        current_user, month_entries, today.year, today.month, today, now=ctx["now"]
    )
    return render_template("dashboard.html", **ctx)


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
        "weekly_target_fmt": logic.fmt_target_hours(ctx["weekly"]["target_hours"]),
        "day_state": ctx["day_state"],
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
                "target_fmt": logic.fmt_target_hours(ctx["saturday"].get("target_hours")),
            }
            if ctx["saturday"]
            else None
        ),
        "server_time": now.strftime("%H:%M:%S"),
    }
    return jsonify(payload)


# ---------------------------------------------------------------- punches

def typed_punch_time():
    """The time typed in for a punch that was missed live, if any."""
    return parse_time_field(request.form.get("time"))


def stale_shift_redirect(entry):
    flash(
        f"You're still logged in from {entry.date.strftime('%a %d %b')} "
        f"({logic.fmt_time(entry.login_time)}). Enter the time you finished "
        "that day below, or fix it in History.",
        "error",
    )
    return redirect(url_for("main.dashboard"))


def mark_label(entry, mark):
    """Names the latest recorded point on a shift, for an error message."""
    return "you logged in" if mark == logic.shift_login_dt(entry) else "your last break"


@main_bp.route("/punch/in", methods=["POST"])
@login_required
def punch_in():
    user_id = current_user.id
    now = datetime.now()
    today = now.date()
    if get_open_entry(user_id):
        flash("You're already logged in.", "error")
        return redirect(url_for("main.dashboard"))

    typed = typed_punch_time()
    entry = TimeEntry.query.filter_by(user_id=user_id, date=today).first()

    # Logged out already today: carry on with the same day, the time away
    # recorded as a break. This used to be refused because the day existed,
    # which left the dashboard's main button a dead end after every logout.
    if entry is not None and entry.login_time is not None and entry.logout_time is not None:
        logout_dt = logic.place_on_shift(today, entry.logout_time, logic.shift_login_dt(entry))
        if typed:
            when, error = logic.check_typed_time(
                today, typed, now, earliest=logout_dt, earliest_label="you logged out"
            )
            if error:
                flash(error, "error")
                return redirect(url_for("main.dashboard"))
        else:
            when = now
        if when < logout_dt:
            flash(
                f"You logged out at {logic.fmt_time(entry.logout_time)}, so you can't "
                "log in again before then.",
                "error",
            )
            return redirect(url_for("main.dashboard"))
        gap = (when - logout_dt).total_seconds() / 3600.0
        # Under a minute is an accidental logout being undone, not a break.
        if gap >= 1 / 60:
            entry.breaks.append(
                BreakSegment(break_start=entry.logout_time, break_end=when.time())
            )
            message = (
                f"Logged in again at {logic.fmt_time(when.time())}. The "
                f"{logic.fmt_duration(gap)} since you logged out counts as a break."
            )
        else:
            message = f"Logged in again at {logic.fmt_time(when.time())}."
        entry.logout_time = None
        db.session.commit()
        flash(message, "success")
        return redirect(url_for("main.dashboard"))

    if typed:
        when, error = logic.check_typed_time(today, typed, now)
        if error:
            flash(error, "error")
            return redirect(url_for("main.dashboard"))
    else:
        when = now

    if entry is not None:
        # The day already exists without a login -- leave booked in advance,
        # or its own target set from History. Log in to that day rather than
        # turning the login away.
        entry.login_time = when.time()
        entry.logout_time = None
    else:
        entry = TimeEntry(user_id=user_id, date=today, login_time=when.time())
        db.session.add(entry)
    db.session.commit()
    flash(f"Logged in at {logic.fmt_time(entry.login_time)}.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/punch/break/start", methods=["POST"])
@login_required
def punch_break_start():
    now = datetime.now()
    entry = get_open_entry(current_user.id)
    if not entry:
        flash("You're not logged in yet — tap Login first.", "error")
        return redirect(url_for("main.dashboard"))
    if entry.open_break():
        flash("You're already on a break.", "error")
        return redirect(url_for("main.dashboard"))
    if logic.is_stale_shift(entry, now):
        return stale_shift_redirect(entry)

    typed = typed_punch_time()
    if typed:
        mark = logic.last_mark_dt(entry, now)
        _, error = logic.check_typed_time(
            entry.date, typed, now, earliest=mark, earliest_label=mark_label(entry, mark)
        )
        if error:
            flash(error, "error")
            return redirect(url_for("main.dashboard"))
    t = typed or now.time()
    db.session.add(BreakSegment(entry_id=entry.id, break_start=t))
    db.session.commit()
    flash(f"Break started at {logic.fmt_time(t)}. Tap Back when you return.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/punch/break/end", methods=["POST"])
@login_required
def punch_break_end():
    now = datetime.now()
    entry = get_open_entry(current_user.id)
    open_break = entry.open_break() if entry else None
    if not open_break:
        flash("You're not on a break right now.", "error")
        return redirect(url_for("main.dashboard"))
    if logic.is_stale_shift(entry, now):
        return stale_shift_redirect(entry)

    start_dt = logic.place_on_shift(
        entry.date, open_break.break_start, logic.shift_login_dt(entry)
    )
    typed = typed_punch_time()
    if typed:
        when, error = logic.check_typed_time(
            entry.date, typed, now, earliest=start_dt, earliest_label="your break started"
        )
        if error:
            flash(error, "error")
            return redirect(url_for("main.dashboard"))
    else:
        when = now
    open_break.break_end = when.time()
    db.session.commit()
    length = max(0.0, (when - start_dt).total_seconds() / 3600.0)
    flash(
        f"Back at {logic.fmt_time(open_break.break_end)}, after a "
        f"{logic.fmt_duration(length)} break.",
        "success",
    )
    return redirect(url_for("main.dashboard"))


@main_bp.route("/punch/out", methods=["POST"])
@login_required
def punch_out():
    now = datetime.now()
    entry = get_open_entry(current_user.id)
    if not entry:
        flash("You're not logged in right now.", "error")
        return redirect(url_for("main.dashboard"))
    if entry.open_break():
        flash("You're on a break — tap Back first, then Logout.", "error")
        return redirect(url_for("main.dashboard"))

    typed = typed_punch_time()
    if typed:
        mark = logic.last_mark_dt(entry, now)
        when, error = logic.check_typed_time(
            entry.date, typed, now, earliest=mark, earliest_label=mark_label(entry, mark)
        )
        if error:
            flash(error, "error")
            return redirect(url_for("main.dashboard"))
    else:
        when = now

    # A logout the day after, from a tab left open or a forgotten evening,
    # would otherwise quietly record a 24h+ day.
    span = (when - logic.shift_login_dt(entry)).total_seconds() / 3600.0
    if span > logic.MAX_LIVE_SHIFT_HOURS:
        flash(
            f"That would make a {logic.fmt_duration(span)} day. Enter the time you "
            f"actually finished on {entry.date.strftime('%a %d %b')}.",
            "error",
        )
        return redirect(url_for("main.dashboard"))

    entry.logout_time = when.time()
    db.session.commit()
    total = logic.entry_total_hours(entry)
    day = "today" if entry.date == now.date() else f"on {entry.date.strftime('%a %d %b')}"
    flash(
        f"Logged out at {logic.fmt_time(entry.logout_time)}. "
        f"You worked {logic.fmt_hours(total)} {day}.",
        "success",
    )
    return redirect(url_for("main.dashboard"))


# ------------------------------------------------------- history, calendar

def _month_page(year, month):
    """Everything History and Calendar show for a month -- the same figures,
    laid out two ways. None for a month that can't exist (a hand-typed /13,
    which would otherwise be a server error)."""
    today = date.today()
    year = year or today.year
    month = month or today.month
    if not (1 <= month <= 12 and 1970 <= year <= 2100):
        return None

    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    now = datetime.now()
    entries_by_date = get_entries_for_range(current_user.id, first_day, last_day)
    summary = logic.month_summary(current_user, entries_by_date, year, month, today, now=now)

    prev_month = (first_day - timedelta(days=1)).replace(day=1)
    next_month_first = last_day + timedelta(days=1)
    return dict(
        summary=summary,
        rows=summary["rows"],
        year=year,
        month=month,
        month_name=calendar.month_name[month],
        is_current_month=(year, month) == (today.year, today.month),
        prev_year=prev_month.year,
        prev_month=prev_month.month,
        next_year=next_month_first.year,
        next_month=next_month_first.month,
        fmt_hours=logic.fmt_hours,
        fmt_balance=logic.fmt_balance,
        fmt_duration=logic.fmt_duration,
        fmt_time=logic.fmt_time,
        entry_break_hours=lambda e: logic.entry_break_hours(e, now=now),
        now=now,
    )


@main_bp.route("/history")
@main_bp.route("/history/<int:year>/<int:month>")
@login_required
def history(year=None, month=None):
    page = _month_page(year, month)
    if page is None:
        return redirect(url_for("main.history"))
    return render_template("history.html", **page)


EXPORTS = {
    "xlsx": ("Excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": ("PDF", "application/pdf"),
}


@main_bp.route("/history/<int:year>/<int:month>/export.<kind>")
@login_required
def export_month(year, month, kind):
    """The month History shows, as a file to keep: Excel or PDF."""
    if kind not in EXPORTS:
        abort(404)
    page = _month_page(year, month)
    if page is None:
        abort(404)
    label, mimetype = EXPORTS[kind]
    from app import exports

    try:
        data = exports.build(kind, page, current_user, page["now"])
    except ImportError:
        # The libraries arrive with `pip install -r requirements.txt`. Until
        # then only the download is missing -- the rest of the site runs.
        current_app.logger.exception("%s export library missing", label)
        flash(f"{label} downloads aren't set up on this server yet. Run the update's "
              "install step, then Reload the web app.", "error")
        return redirect(url_for("main.history", year=year, month=month))
    response = send_file(
        BytesIO(data),
        mimetype=mimetype,
        as_attachment=True,
        download_name=f"STM timesheet {page['month_name']} {year}.{kind}",
    )
    # Someone's hours: never kept by a shared cache or proxy.
    response.headers["Cache-Control"] = "no-store"
    return response


@main_bp.route("/calendar", endpoint="calendar")
@main_bp.route("/calendar/<int:year>/<int:month>", endpoint="calendar")
@login_required
def calendar_page(year=None, month=None):
    page = _month_page(year, month)
    if page is None:
        return redirect(url_for("main.calendar"))
    # Monday-first weeks, like the rest of STM, with blank cells before the
    # 1st and after the month's last day.
    cells = [None] * page["rows"][0]["date"].weekday() + page["rows"]
    cells += [None] * (-len(cells) % 7)
    page["weeks"] = [cells[i:i + 7] for i in range(0, len(cells), 7)]
    return render_template(
        "calendar.html",
        fmt_hours_compact=logic.fmt_hours_compact,
        fmt_balance_compact=logic.fmt_balance_compact,
        **page,
    )


@main_bp.route("/timesheet", endpoint="timesheet", methods=["GET", "POST"])
@main_bp.route("/timesheet/<date_str>", endpoint="timesheet", methods=["GET", "POST"])
@login_required
def timesheet(date_str=None):
    """A day as lines of time -- an hour each, to begin with -- each saying
    what was worked on in it. Every line's times can be changed."""
    now = datetime.now()
    today = now.date()
    day = today
    if date_str is not None:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            day = None
        if day is None or day.year < 1970:  # 0001-01-01 has no day before it
            flash("That isn't a valid date.", "error")
            return redirect(url_for("main.timesheet"))
    if day > today:
        # A day still to come has nothing to say yet.
        return redirect(url_for("main.timesheet"))

    # Today's page is plain /timesheet; any other day carries its date.
    day_args = {} if day == today else {"date_str": day.isoformat()}
    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=day).first()
    blocks = WorkBlock.query.filter_by(user_id=current_user.id, date=day).all()
    # The logout, in minutes, once the day has one on its own date: what
    # "End at logout" brings a line's end to.
    logout_at = None
    if entry is not None and entry.login_time and entry.logout_time and entry.logout_time > entry.login_time:
        logout_at = logic.clock_minutes(entry.logout_time)

    def clean(text):
        return " ".join((text or "").split())[:WorkBlock.MAX_LENGTH].rstrip()

    def clock(value):
        return parse_time_field((value or "")[:5])  # "13:00", or "13:00:00" with seconds

    def hhmm(minutes):
        return logic.minutes_clock(minutes).strftime("%H:%M")

    def line_view(start, end, block=None, text=""):
        # A recorded break comes already written as "Break": saved only if
        # its words or times are changed.
        is_break = block == logic.TIMESHEET_BREAK
        if is_break:
            block, text = None, "Break"
        elif block is not None:
            text = block.text
        shown = block is not None or is_break or not text  # already on the page, as opposed to new
        return SimpleNamespace(
            id=str(block.id) if block else "", start=start, end=end, text=text,
            was_start=start if shown else "", was_end=end if shown else "",
            was_text=text if block is not None or is_break else "", is_now=False, is_break=is_break,
            placeholder="", error="", error_on="", can_end=False, end_confirm="",
        )

    def day_views(lines):
        views = [line_view(hhmm(start), hhmm(end), block) for start, end, block in lines]
        minute = now.hour * 60 + now.minute
        current = next((v for v, (start, end, _) in zip(views, lines)
                        if day == today and start <= minute < end), None)
        if current is not None:
            current.is_now = True
        empty = [v for v in views if not v.text]
        if current is not None and not current.text:
            current.placeholder = "What are you working on?"
        elif empty:
            empty[0].placeholder = "What did you work on?"
        if logout_at is not None:
            # The last lines of a finished day can be brought to its logout:
            # one ending within the hour before it, or one running past it.
            for i, (view, (start, end, _)) in enumerate(zip(views, lines)):
                if view.text and not view.is_break and start < logout_at and end != logout_at                         and end >= logout_at - 60:
                    later = sum(1 for v in views[i + 1:] if v.text and not v.is_break)
                    view.can_end = True
                    if later:
                        view.end_confirm = (f"End this line at {logic.fmt_time(entry.logout_time)} and remove "
                                            f"the {later} line{'s' if later > 1 else ''} after it?")
        return views

    def show(views):
        # What the day's picture (Download PNG) says above its lines.
        summary = ""
        if entry is not None and entry.login_time is not None:
            until = logic.fmt_time(entry.logout_time) if entry.logout_time else "now"
            worked = logic.fmt_hours(logic.entry_total_hours(entry, now=now))
            summary = f"{logic.fmt_time(entry.login_time)} – {until} · {worked} worked"
        return render_template(
            "timesheet.html",
            day=day,
            is_today=day == today,
            prev_date=day - timedelta(days=1),
            next_date=None if day == today else day + timedelta(days=1),
            lines=views,
            max_length=WorkBlock.MAX_LENGTH,
            who=current_user.display_name or current_user.username,
            summary=summary,
            stamp=f"{now:%a %d %b %Y}, {logic.fmt_time(now.time())}",
            logout_label=logic.fmt_time(entry.logout_time) if logout_at is not None else "",
        )

    if request.method == "POST":
        form = request.form
        names = ("id", "start", "end", "text", "was_start", "was_end", "was_text")
        fields = [form.getlist(name) for name in names]
        if len({len(f) for f in fields}) != 1 or len(fields[0]) > 200:
            abort(400)

        if not fields[0]:
            # A page from before this version (one box per hour: h13, was13)
            # left open across the update. Its words come back as new lines
            # to check and save, never quietly dropped.
            typed = []
            for key in form:
                if len(key) <= 3 and key[:1] == "h" and key[1:].isascii() and key[1:].isdigit():
                    hour, text = int(key[1:]), clean(form[key])
                    if hour <= 23 and text and text != clean(form.get(f"was{hour}")):
                        typed.append((hour, text))
            if not typed:
                return redirect(url_for("main.timesheet", **day_args))
            views = day_views(logic.timesheet_lines(entry, day, now, blocks))
            views += [line_view(hhmm(h * 60), hhmm(h * 60 + 60), text=t) for h, t in sorted(typed)]
            flash("This page was open from before an update, so it wasn't saved. Your words are in the "
                  "lines at the bottom: check their times, then press Save.", "error")
            return show(views)

        by_id = {str(b.id): b for b in blocks}
        changed, drafts, gone = [], [], 0
        to_delete = form.get("delete", "")
        to_logout = form.get("to_logout", "")
        if to_logout:
            # "End at logout": that line's end becomes the logout, as if typed.
            if logout_at is None or not to_logout.isascii() or not to_logout.isdigit()                     or int(to_logout) >= len(fields[0]):
                abort(400)
            fields[2][int(to_logout)] = hhmm(logout_at)
        ended = None
        for index, (block_id, start_raw, end_raw, text_raw, was_start, was_end, was_text) in enumerate(zip(*fields)):
            text = clean(text_raw)
            start, end = clock(start_raw), clock(end_raw)
            # As typed, so a line with a problem comes back without retyping.
            draft = SimpleNamespace(
                id=block_id, start=start_raw, end=end_raw, text=text, was_start=was_start, was_end=was_end,
                was_text=was_text, is_now=False, is_break=False, placeholder="", error="", error_on="",
                can_end=False, end_confirm="",
            )
            drafts.append(draft)
            if to_delete == str(index):
                # A deleted line stays as a block with no words, so its time
                # isn't offered as a line again.
                block = by_id.get(block_id)
                if block is not None:
                    changed.append((block.start_time, block.end_time, "Deleted"))
                    block.text = ""
                elif not block_id:
                    start, end = clock(was_start) or start, clock(was_end) or end
                    if start and end and logic.clock_minutes(end, ending=True) > logic.clock_minutes(start):
                        db.session.add(WorkBlock(user_id=current_user.id, date=day, start_time=start,
                                                 end_time=end, text=""))
                        changed.append((start, end, "Deleted"))
                continue
            # Field by field, only what was changed on this page counts, so a
            # page left open can't undo something written since elsewhere.
            start_changed = (start.strftime("%H:%M") if start else "") != was_start.strip()
            end_changed = (end.strftime("%H:%M") if end else "") != was_end.strip()
            text_changed = text != clean(was_text)
            if not (start_changed or end_changed or text_changed):
                continue
            block = by_id.get(block_id)
            if block_id and block is None:
                gone += 1  # cleared on another device since: it stays gone
                continue
            if block is not None:
                if text_changed and not text:
                    changed.append((block.start_time, block.end_time, "Cleared"))
                    db.session.delete(block)
                    continue
                start = start if start_changed else block.start_time
                end = end if end_changed else block.end_time
                text = text if text_changed else block.text
            elif not text:
                if start_changed or end_changed:
                    draft.error, draft.error_on = "Write what you worked on, to keep these times.", "text"
                continue
            if start is None or end is None:
                draft.error, draft.error_on = "Give this line a start and an end time.", "times"
                continue
            if logic.clock_minutes(end, ending=True) <= logic.clock_minutes(start):
                draft.error, draft.error_on = "The end has to be after the start.", "times"
                continue
            if block is None:
                if any((b.start_time, b.end_time, b.text) == (start, end, text) for b in blocks):
                    continue  # the same form sent again (a double tap, or Back then Save)
                block = WorkBlock(user_id=current_user.id, date=day)
                db.session.add(block)
                blocks.append(block)
            block.start_time, block.end_time, block.text = start, end, text
            changed.append((start, end, "Saved"))
            if to_logout == str(index):
                ended = block

        removed = 0
        if to_logout and not any(d.error for d in drafts):
            # ...and the lines after it go: the day ends there.
            index = int(to_logout)
            keep = ended or by_id.get(fields[0][index])
            begin = clock(fields[1][index])
            for b in list(blocks):
                if b is keep or not b.text or b in db.session.deleted or begin is None                         or logic.clock_minutes(b.start_time) < logic.clock_minutes(begin):
                    continue
                if b in db.session.new:
                    db.session.expunge(b)
                else:
                    db.session.delete(b)
                removed += 1

        bad = [d for d in drafts if d.error]
        if bad:
            db.session.rollback()
            flash("Nothing was saved yet. " + ("Fix the line marked below" if len(bad) == 1
                                               else f"Fix the {len(bad)} lines marked below") + ", then save again.",
                  "error")
            return show(drafts)
        db.session.commit()

        def span(start, end):
            return logic.fmt_span(logic.clock_minutes(start), logic.clock_minutes(end, ending=True))

        def count(n):
            return f"{n} line" + ("" if n == 1 else "s")

        if to_logout:
            flash(f"Ended the line at your logout, {logic.fmt_time(entry.logout_time)}"
                  + (f", and removed {count(removed)} after it." if removed else "."), "success")
        elif len(changed) == 1:
            start, end, verb = changed[0]
            flash(f"{verb} {span(start, end)}.", "success")
        elif changed:
            tally = [(verb, sum(1 for *_, v in changed if v == verb)) for verb in ("Saved", "Cleared", "Deleted")]
            words = [f"{verb if not i else verb.lower()} {count(n)}" for i, (verb, n) in
                     enumerate((verb, n) for verb, n in tally if n)]
            flash(", ".join(words) + ".", "success")
        elif not form.get("add") and not gone:
            flash("Already saved.", "success")
        if gone:
            flash(f"{count(gone).capitalize()} you changed had been removed on another device, so "
                  f"{'it was' if gone == 1 else 'they were'} left out.", "warning")

        if form.get("add"):
            # Lines added before, still empty, come back too: count them in.
            fresh = [b for b in blocks if b in db.session]
            last = max((end for _, end, _ in logic.timesheet_lines(entry, day, now, fresh)),
                       default=logic.timesheet_span(entry, day, now)[0])
            waiting = sum(1 for block_id, text, was_start in zip(fields[0], fields[3], fields[4])
                          if not block_id and not clean(text) and clock(was_start)
                          and logic.clock_minutes(clock(was_start)) >= last)
            day_args["add"] = min(24, waiting + 1)
        return redirect(url_for("main.timesheet", **day_args))

    lines = logic.timesheet_lines(entry, day, now, blocks)
    for _ in range(min(24, request.args.get("add", default=0, type=int))):
        # "Add a line": an hour straight after the last line -- or, once the
        # day runs to midnight, the hour just before the first.
        last = max((end for _, end, _ in lines), default=logic.timesheet_span(entry, day, now)[0])
        first = min((start for start, _, _ in lines), default=0)
        if last < 1440:
            lines.append((last, min(1440, last + 60), None))
        elif first > 0:
            lines.insert(0, (max(0, first - 60), first, None))
        else:
            flash("This day's lines already run from midnight to midnight. Change a line's times to "
                  "make room.", "warning")
            break
    return show(day_views(lines))


@main_bp.route("/entry/<date_str>", methods=["GET", "POST"])
@login_required
def edit_entry(date_str):
    try:
        entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("That isn't a valid date.", "error")
        return redirect(url_for("main.history"))

    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=entry_date).first()
    default_target = logic.entry_target_hours(current_user, entry_date)
    # Opened from the calendar: go back there afterwards, not to History.
    back = "calendar" if request.args.get("back") == "calendar" else None
    month_url = url_for("main.calendar" if back else "main.history",
                        year=entry_date.year, month=entry_date.month)

    def show_form(draft=None):
        return render_template(
            "entry_form.html",
            entry=entry,
            draft=draft,
            entry_date=entry_date,
            default_target=default_target,
            prev_date=entry_date - timedelta(days=1),
            next_date=entry_date + timedelta(days=1),
            back=back,
            month_url=month_url,
        )

    if request.method == "POST":
        form = request.form
        login_t = parse_time_field(form.get("login_time"))
        logout_t = parse_time_field(form.get("logout_time"))
        notes = (form.get("notes") or "").strip() or None
        # The day type picks the hours; "custom" is the escape hatch that still
        # lets a specific number be typed in.
        day_type = (form.get("day_type") or "").strip()
        leave_label = (form.get("leave_label") or "").strip() or None
        target_raw = (form.get("target_override") or "").strip()

        breaks = []
        for bs, be in zip(form.getlist("break_start"), form.getlist("break_end")):
            start, end = parse_time_field(bs), parse_time_field(be)
            if start is not None or end is not None:
                breaks.append((start, end))

        # Everything as typed, so a form sent back with a problem keeps it all
        # instead of making the whole day be entered again.
        draft = SimpleNamespace(
            login_time=login_t,
            logout_time=logout_t,
            notes=notes,
            leave_label=leave_label,
            effective_leave_type=day_type or None,
            target_override=target_raw or None,
            breaks=[SimpleNamespace(break_start=s, break_end=e) for s, e in breaks],
        )

        leave_type, target_override = None, None
        if day_type == "full":
            leave_type, target_override = "full", 0.0
        elif day_type == "half":
            leave_type, target_override = "half", default_target / 2
        elif day_type == "custom" and target_raw:
            try:
                target_override = float(target_raw)
            except ValueError:
                target_override = None
            if target_override is None or not math.isfinite(target_override):
                flash("Hours for this day must be a number, like 4 or 6.5.", "error")
                return show_form(draft)
            leave_type, target_override = "custom", max(0.0, target_override)
        # "custom" with nothing typed is an ordinary working day.

        # Checked before anything is saved. This used to report the problem
        # and save anyway -- showing "Saved" beside the error, with the break
        # silently dropped.
        if any(start is None for start, _ in breaks):
            flash("Each break needs a start time — add one, or clear that row.", "error")
            return show_form(draft)
        if logout_t is not None and login_t is None:
            flash("Add a login time to go with the logout time.", "error")
            return show_form(draft)
        if logout_t is not None and any(end is None for _, end in breaks):
            # A finished day can't have a break still running: it would count
            # up to this minute and wipe out the whole day's hours.
            flash("Add when each break ended — the day has a logout time.", "error")
            return show_form(draft)

        if leave_type in TimeEntry.LEAVE_TYPES and not leave_label:
            leave_label = TimeEntry.LEAVE_TYPES[leave_type]
        if leave_type is None:
            # A reason only means something on a leave or custom-hours day.
            leave_label = None

        if entry is None:
            entry = TimeEntry(user_id=current_user.id, date=entry_date)
            db.session.add(entry)

        entry.login_time = login_t
        entry.logout_time = logout_t
        entry.notes = notes
        entry.target_override = target_override
        entry.leave_label = leave_label
        entry.leave_type = leave_type

        entry.breaks.clear()
        for start, end in breaks:
            entry.breaks.append(BreakSegment(break_start=start, break_end=end))

        db.session.commit()

        day = entry_date.strftime("%a %d %b")
        span = logic.entry_raw_hours(entry) if login_t and logout_t else 0.0
        if span > logic.LONG_SHIFT_WARNING_HOURS:
            # Saved as typed, but kept on the form: a day this long is almost
            # always a logout typed as AM when PM was meant, or the reverse.
            flash(
                f"Saved {day} — but that's a {logic.fmt_duration(span)} day. If the "
                "logout should be AM/PM the other way round, change it here.",
                "warning",
            )
            return redirect(url_for("main.edit_entry", date_str=entry_date.isoformat(), back=back))
        flash(f"Saved {day}.", "success")
        return redirect(month_url)

    return show_form()


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

    view = "main.calendar" if request.args.get("back") == "calendar" else "main.history"
    return redirect(url_for(view, year=entry_date.year, month=entry_date.month))


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
            if not (math.isfinite(daily) and math.isfinite(weekly)):
                flash("Please enter valid numbers.", "error")
                return redirect(url_for("main.settings"))

            try:
                weekday_break = int(request.form.get("weekday_break_minutes", 60) or 0)
                saturday_break = int(request.form.get("saturday_break_minutes", 30) or 0)
            except ValueError:
                flash("Please enter break lengths in whole minutes.", "error")
                return redirect(url_for("main.settings"))

            # Only real weekdays: anything else would break every page that
            # reads the workday list back.
            workdays = [d for d in request.form.getlist("workdays") if d in set("0123456")]
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
