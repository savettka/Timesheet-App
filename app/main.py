import calendar
import csv
import io
import json
import math
import os
import secrets
import uuid
from collections import defaultdict
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
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app import db
from app import logic
from app import changes
from app import deploy
from app import holidays
from app import mailer
from app import reminders
from app import weekly_report
from app.models import (BalanceAdjustment, BreakSegment, Goal, Holiday, HoursPlan, PayRecord, Salary, TimeEntry, User,
                        WeekTemplate, WorkBlock)

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


def work_day(user_id, now):
    """The day being worked (logic.work_day): past midnight on a shift still
    open from the day before, that day -- not the new date."""
    return logic.work_day(get_open_entry(user_id), now)


def get_entries_for_range(user_id, start_date, end_date):
    entries = TimeEntry.query.filter(
        TimeEntry.user_id == user_id,
        TimeEntry.date >= start_date,
        TimeEntry.date <= end_date,
    ).all()
    return {e.date: e for e in entries}


def adjustments_between(user_id, first, last):
    """Balance adjustments dated first to last (History adds them)."""
    return (BalanceAdjustment.query.filter(BalanceAdjustment.user_id == user_id, BalanceAdjustment.date >= first,
                                           BalanceAdjustment.date <= last)
            .order_by(BalanceAdjustment.date, BalanceAdjustment.id).all())


def year_entries(user_id, today):
    """Every day recorded for the year's heatmap, breaks and all, in one
    query -- a year of days would otherwise fetch each day's breaks alone."""
    entries = (TimeEntry.query.options(joinedload(TimeEntry.breaks))
               .filter(TimeEntry.user_id == user_id, TimeEntry.date >= logic.heatmap_first(today),
                       TimeEntry.date <= today).all())
    return {e.date: e for e in entries}


def heat_label(day):
    """A heatmap square in words, for its link and the line under the grid."""
    worked, target, kind = day["worked"], day["target"], day["kind"]
    if kind == "leave":
        what = "leave" + (f", {logic.fmt_duration(worked)} worked" if worked else "")
    elif kind == "rest":
        what = "rest day"
    elif kind == "worked":
        what = (f"{logic.fmt_duration(worked)} of {logic.fmt_duration(target)}" if target
                else f"{logic.fmt_duration(worked)} on a rest day")
    elif kind == "missed":
        what = "nothing logged"
    else:
        what = "nothing yet"
    return f"{day['date']:%a %d %b %Y}: {what}"


def written_minutes(user_id, first, last, entries, now):
    """Each day's Timesheet minutes with words (breaks aside), first to last,
    in one query: what a Timesheet-written goal is measured by."""
    out = defaultdict(int)
    for block in WorkBlock.query.filter(WorkBlock.user_id == user_id, WorkBlock.date >= first,
                                        WorkBlock.date <= last, WorkBlock.text != ""):
        if block.text.casefold() != "break":
            a, b = logic.block_span(block, entries.get(block.date), block.date, now)
            out[block.date] += max(0, b - a)
    return out


def goals_view(user, today, now, entries):
    """The active goals for Home (logic.goal_progress), over the year the
    heatmap shows. entries: year_entries."""
    goals = [g for g in user.goals if g.active and g.kind in Goal.KINDS]
    goals.sort(key=lambda g: list(Goal.KINDS).index(g.kind))
    first = logic.heatmap_first(today)
    written = (written_minutes(user.id, first, today, entries, now)
               if any(g.kind == "timesheet_written" for g in goals) else {})
    return [logic.goal_progress(g.kind, g.value, user, entries, written, first, today, now) for g in goals]


GOAL_DEFAULT_LOGOUT = 18 * 60


def goal_rows(user):
    """Each kind of goal for its row in Settings: on or off, and its value
    as the box shows it."""
    saved = {g.kind: g for g in user.goals}
    rows = []
    hours = user.day_hours_on(date.today())
    for kind, name in Goal.KINDS.items():
        goal = saved.get(kind)
        value = goal.value if goal is not None else {
            "daily_hours": max(hours), "weekly_hours": logic.week_base_hours(user, date.today()),
            "timesheet_written": 100, "logout_by": GOAL_DEFAULT_LOGOUT}[kind]
        shown = (logic.minutes_clock(int(value)).strftime("%H:%M") if kind == "logout_by" else f"{value:g}")
        rows.append(SimpleNamespace(kind=kind, name=name, active=goal is not None and goal.active, value=shown,
                                    unit={"daily_hours": "h", "weekly_hours": "h", "timesheet_written": "%"}.get(kind, "")))
    return rows


def goal_value(kind, raw):
    """A goal's value from its box, or None when it isn't one. Log out by
    before 6 AM is the morning after."""
    raw = (raw or "").strip()
    if kind == "logout_by":
        t = parse_time_field(raw)
        if t is None:
            return None
        minutes = t.hour * 60 + t.minute
        return float(minutes + 1440 if minutes < 6 * 60 else minutes)
    try:
        value = float(raw)
    except ValueError:
        return None
    most = {"daily_hours": 24, "weekly_hours": 168, "timesheet_written": 100}[kind]
    return value if math.isfinite(value) and 0 < value <= most else None


def heatmap_view(user, today, now, entries=None):
    """The year's heatmap (logic.year_heatmap) ready for the page."""
    hm = logic.year_heatmap(user, entries if entries is not None else year_entries(user.id, today), today, now=now)
    hm["today_label"] = ""
    for column in hm["columns"]:
        for day in column:
            day["label"] = heat_label(day)
            day["is_today"] = day["date"] == today
            if day["is_today"]:
                hm["today_label"] = day["label"]
    hm["today"] = today
    hm["total"] = logic.fmt_duration(hm["worked_hours"])
    # Each month's name opens that month in History: the squares are a
    # picture, too small to tap one.
    hm["month_links"] = []
    for col, name in hm["months"]:
        column = hm["columns"][col]
        first = next((d["date"] for d in column if d["date"].day == 1), column[0]["date"])
        hm["month_links"].append((col, name, first))
    return hm


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


def local_path(value):
    """A page on this site to go back to, or None -- never another site."""
    value = (value or "").strip()
    return value if value.startswith("/") and "//" not in value and "\\" not in value else None


def pay_for(user, year, month, balance_hours):
    """A month's estimated pay (logic.month_pay) on the salary in force that
    month, or None before any salary."""
    salary = logic.salary_in(user.salaries, year, month)
    return logic.month_pay(user, salary, year, month, balance_hours)


@main_bp.app_template_filter("money")
def money_filter(amount, exact=False, signed=False):
    return logic.fmt_money(amount, current_user.pay_symbol, exact=exact, signed=signed)


def build_dashboard_context():
    user = current_user
    now = datetime.now()
    open_entry = get_open_entry(user.id)
    # Past midnight on a shift begun yesterday, today is still that day.
    today = logic.work_day(open_entry, now)
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
        user, entries_by_date, week_start, today, now=now
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
        "today": today,
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
def index():
    """Home for anyone signed in; for anyone else, the landing page, its
    Sign in leading to the sign-in page (or first-run setup)."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("landing.html", contact=current_app.config.get("CONTACT_EMAIL"))


def days_to_fill(user_id, now, today, days=7):
    """The last week's days with Timesheet lines still to fill in -- each
    finished line of a worked day with no words yet -- newest first. today
    is the day being worked (work_day)."""
    first = today - timedelta(days=days - 1)
    entries = TimeEntry.query.filter(TimeEntry.user_id == user_id, TimeEntry.date >= first,
                                     TimeEntry.date <= today, TimeEntry.login_time.isnot(None)).all()
    blocks = defaultdict(list)
    for b in WorkBlock.query.filter(WorkBlock.user_id == user_id, WorkBlock.date >= first, WorkBlock.date <= today):
        blocks[b.date].append(b)
    minute = logic.day_minute(today, now)
    out = []
    for entry in sorted(entries, key=lambda e: e.date, reverse=True):
        lines = logic.timesheet_lines(entry, entry.date, now, blocks[entry.date])
        empty = sum(1 for _, end, block in lines if block is None and (entry.date < today or end <= minute))
        if empty:
            out.append((entry.date, empty))
    return out


def recent_texts(user_id, day, limit=50):
    """What was written on Timesheet lines in the last 60 days, latest first:
    searched while typing a task (three suggestions show at a time), so a
    task done again is a tap away."""
    rows = (db.session.query(WorkBlock.text)
            .filter(WorkBlock.user_id == user_id, WorkBlock.text != "",
                    WorkBlock.date <= day, WorkBlock.date >= day - timedelta(days=60))
            .group_by(WorkBlock.text)
            .order_by(func.max(WorkBlock.date).desc(), func.count(WorkBlock.id).desc())
            .limit(limit + 1).all())
    return [text for text, in rows if text.casefold() != "break"][:limit]


# Home's cards, in their usual order, by the names the layout keeps.
DASHBOARD_CARDS = {
    "timer": "Timer",
    "fill": "To fill in",
    "status": "Clock in and out",
    "month": "This month",
    "today": "Today",
    "week": "This week",
    "saturday": "Saturday plan",
    "goals": "Goals",
    "heatmap": "Last 12 months",
}
DASHBOARD_FIXED = {"status"}  # moved, never hidden: the way to log in


def dashboard_layout(user):
    """Home's cards as the user arranged them: each card's name, whether it
    shows, and whether it has to. Anything unreadable in what's saved is
    left out; a card it doesn't mention (a new one) goes at the end."""
    try:
        saved = json.loads(user.dashboard_layout or "{}")
    except ValueError:
        saved = {}
    if not isinstance(saved, dict):
        saved = {}

    def names(key):
        value = saved.get(key)
        return [k for k in value if isinstance(k, str) and k in DASHBOARD_CARDS] if isinstance(value, list) else []

    order = list(dict.fromkeys(names("order")))
    order += [k for k in DASHBOARD_CARDS if k not in order]
    hidden = set(names("hidden")) - DASHBOARD_FIXED
    return [SimpleNamespace(key=k, name=DASHBOARD_CARDS[k], shown=k not in hidden, fixed=k in DASHBOARD_FIXED)
            for k in order]


@main_bp.route("/dashboard/layout", methods=["POST"])
@login_required
def dashboard_layout_save():
    """Home's cards rearranged (Arrange): their order, and which are hidden."""
    order = [k for k in dict.fromkeys(request.form.getlist("order")) if k in DASHBOARD_CARDS]
    hidden = [k for k in dict.fromkeys(request.form.getlist("hidden"))
              if k in DASHBOARD_CARDS and k not in DASHBOARD_FIXED]
    current_user.dashboard_layout = json.dumps({"order": order, "hidden": hidden})
    db.session.commit()
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True)
    return redirect(url_for("main.dashboard"))


def month_now(ctx):
    """This month as Home shows it -- overtime by the week, live -- and its
    pay; also what the status poll sends, so the page and its refresh agree."""
    today = ctx["today"]
    first_day = today.replace(day=1)
    last_day = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    month_entries = get_entries_for_range(current_user.id, first_day, last_day)
    month = logic.adjusted_month_summary(
        current_user, month_entries, today.year, today.month, today,
        adjustments_between(current_user.id, first_day, last_day), now=ctx["now"]
    )
    return month, pay_for(current_user, today.year, today.month, month["balance_hours"])


def month_live(month, pay):
    """The month card's figures as words, for the live refresh (app.js)."""
    minutes = round(month["balance_hours"] * 60)
    out = {
        "balance": logic.fmt_balance(month["balance_hours"]),
        "tone": "is-ahead" if minutes > 0 else ("is-behind" if minutes < 0 else ""),
        "label": "ahead of target" if minutes > 0 else ("behind target" if minutes < 0 else "right on target"),
        "note": f"{logic.fmt_hours(month['worked_hours'])} worked of {logic.fmt_hours(month['target_hours'])} due so far",
    }
    if pay:
        out.update({
            "overtime_pay": money_filter(pay["overtime_pay"]),
            "overtime_note": f"{logic.fmt_hours(pay['overtime_hours'])} at {money_filter(pay['rate'], exact=True)}/h",
            "total": money_filter(pay["total"]),
        })
    return out


@main_bp.route("/dashboard")
@login_required
def dashboard():
    ctx = build_dashboard_context()
    today = ctx["today"]
    ctx["month"], ctx["pay"] = month_now(ctx)
    ctx["to_fill"] = days_to_fill(current_user.id, ctx["now"], today)
    ctx["timer"] = timer_view(current_user, ctx["now"])
    ctx["can_time"] = ctx["day_state"] == "working"
    ctx["recent"] = recent_texts(current_user.id, today)
    year = year_entries(current_user.id, today)
    ctx["heatmap"] = heatmap_view(current_user, today, ctx["now"], entries=year)
    ctx["goals"] = goals_view(current_user, today, ctx["now"], year)
    ctx["layout"] = dashboard_layout(current_user)
    ctx["layout_default"] = list(DASHBOARD_CARDS)
    return render_template("dashboard.html", **ctx)


@main_bp.route("/api/reminders")
@login_required
def api_reminders():
    """Asked by every open page (app.js, initReminders): the reminders that
    apply now, for the bar at the top; and it sends the weekly report once
    it's due, saying how many seconds until it will be, for the page to ask
    again then."""
    now = datetime.now()
    due = weekly_report.run(current_user, now)
    return jsonify(report_in=round(due) if due is not None else None,
                   reminders=reminders.due(current_user, now, get_open_entry(current_user.id)))


def report_week(value, today):
    """The Monday of the week asked for (?week=YYYY-MM-DD), or this week's."""
    try:
        day = datetime.strptime(value or "", "%Y-%m-%d").date()
    except ValueError:
        day = today
    return logic.week_bounds(min(day, today))[0]


@main_bp.route("/reports/weekly-email")
@login_required
def weekly_email_preview():
    """The weekly report as the email shows it, for a look before it goes."""
    now = datetime.now()
    monday = report_week(request.args.get("week"), work_day(current_user.id, now))
    return weekly_report.render_html(weekly_report.build_report(current_user, monday, now))


@main_bp.route("/reports/weekly-email/send", methods=["POST"])
@login_required
def weekly_email_send():
    """The weekly report emailed now, whatever the day -- to see it, or again
    after a change. The one that goes on its own still goes."""
    now = datetime.now()
    monday = report_week(request.form.get("week"), work_day(current_user.id, now))
    back = redirect(url_for("main.reports", range=request.form.get("range") or "week"))
    if not current_user.email:
        flash("Add your email address to your profile first.", "error")
        return back
    if not mailer.is_configured():
        flash("Email isn't set up on this server yet.", "error")
        return back
    report = weekly_report.build_report(current_user, monday, now)
    if weekly_report.send(current_user, report):
        flash(f"Sent the week of {monday:%d %b} to {current_user.email}.", "success")
    else:
        flash("Couldn't send it just now. Try again in a minute.", "error")
    return back


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
        "timer": (
            {"text": current_user.timer_text or "", "elapsed": timer_view(current_user, now).elapsed}
            if current_user.timer_start is not None else None
        ),
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
        # Home's month card, overtime and pay as they stand this minute.
        "month": month_live(*month_now(ctx)),
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


# ------------------------------------------------------------------ timer

def timer_view(user, now):
    """The running timer as the page shows it, or None."""
    if user.timer_start is None:
        return None
    elapsed = max(0, int((now - user.timer_start).total_seconds()))
    h, rest = divmod(elapsed, 3600)
    return SimpleNamespace(text=user.timer_text or "", elapsed=elapsed, clock=f"{h}:{rest // 60:02d}:{rest % 60:02d}")


def place_timed_line(user_id, day, a, b, text, now=None, join=True):
    """Write a timed line, a to b in minutes, onto a day's Timesheet. The same
    words carrying straight on from a line make that line longer (unless
    join is False: a duplicate stays a line of its own); lines it runs over
    give way -- cut short, or split around it. Minutes past 1,440 are after
    midnight, on a shift that runs on past it."""
    now = now or datetime.now()
    entry = TimeEntry.query.filter_by(user_id=user_id, date=day).first()
    blocks = WorkBlock.query.filter_by(user_id=user_id, date=day).all()

    def span(blk):
        return logic.block_span(blk, entry, day, now)

    line = next((blk for blk in blocks if blk.text == text and span(blk)[1] == a), None) if join else None
    if line is not None:
        a = span(line)[0]
        line.end_time = logic.minutes_clock(b)
    else:
        line = WorkBlock(user_id=user_id, date=day, start_time=logic.minutes_clock(a),
                         end_time=logic.minutes_clock(b), text=text)
        db.session.add(line)
    for blk in blocks:
        if blk is line:
            continue
        s, e = span(blk)
        if e <= a or s >= b:
            continue
        if s < a and e > b:
            db.session.add(WorkBlock(user_id=user_id, date=day, start_time=logic.minutes_clock(b),
                                     end_time=blk.end_time, text=blk.text))
            blk.end_time = logic.minutes_clock(a)
        elif s < a:
            blk.end_time = logic.minutes_clock(a)
        elif e > b:
            blk.start_time = logic.minutes_clock(b)
        elif not blk.text:
            db.session.delete(blk)  # a deleted line's time, now the timer's
    return line


LINE_ACTIONS = ("mark", "unmark", "duplicate", "split", "delete")


def apply_line_action(block, action, now):
    """One of the line menu's actions on a saved line with words: mark it
    posted or not, duplicate it, split it in two, or delete it (kept with
    no words, as the Timesheet's Delete does, so its time isn't offered
    again). Returns (done, what to say)."""
    entry = TimeEntry.query.filter_by(user_id=block.user_id, date=block.date).first()
    a, b = logic.block_span(block, entry, block.date, now)
    span = logic.fmt_span(a, b)
    if action == "mark":
        block.copied = block.copy_mark()
        return True, f"Marked {span} as posted."
    if action == "unmark":
        block.copied = None
        return True, f"{span} is no longer marked as posted."
    if action == "delete":
        block.text, block.copied = "", None
        return True, f"Deleted {span}."
    if action == "split":
        middle = logic.split_point(a, b)
        if middle is None:
            return False, f"{span} is too short to split."
        db.session.add(WorkBlock(user_id=block.user_id, date=block.date, start_time=logic.minutes_clock(middle),
                                 end_time=block.end_time, text=block.text))
        block.end_time = logic.minutes_clock(middle)
        return True, f"Split {span} in two: {logic.fmt_span(a, middle)} and {logic.fmt_span(middle, b)}."
    if action == "duplicate":
        # Only into free time: up to the next line with words, or the day's
        # end -- never over anything written.
        free_until = 1440 + (logic.night_reach(entry, block.date, now) or 0)
        for other in WorkBlock.query.filter_by(user_id=block.user_id, date=block.date):
            if other is block or not other.text:
                continue
            s, e = logic.block_span(other, entry, block.date, now)
            if e > b:
                free_until = min(free_until, max(s, b))
        copy = logic.duplicate_span(a, b, free_until)
        if copy is None:
            return False, f"There's no free time straight after {span} for a copy."
        place_timed_line(block.user_id, block.date, *copy, block.text, now=now, join=False)
        return True, f"Duplicated {span} at {logic.fmt_span(*copy)}."
    raise ValueError(action)


@main_bp.route("/line/<int:block_id>/<action>", methods=["POST"])
@login_required
def line_action(block_id, action):
    """The line menu on Boards and the Calendar, for one saved line. Answers
    in JSON when asked to (marking, without leaving the page), and
    otherwise goes back where it came from with what it did."""
    if action not in LINE_ACTIONS:
        abort(404)
    block = WorkBlock.query.filter_by(id=block_id, user_id=current_user.id).first()
    if block is None or not block.text:
        abort(404)
    done, message = apply_line_action(block, action, datetime.now())
    if done:
        db.session.commit()
    else:
        db.session.rollback()
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=done, message=message, copied=block.is_copied if done else None)
    flash(message, "success" if done else "error")
    return redirect(local_path(request.form.get("back")) or url_for("main.timesheet", date_str=block.date.isoformat()))


def stop_timer(user, entry, when):
    """Stop the timer at `when` and write its line on the shift's day, with
    the exact times -- past midnight too, on a shift still going then. Under
    a minute isn't a line. Returns the line, or None."""
    started, text = user.timer_start, user.timer_text or ""
    user.timer_start = user.timer_text = None
    if started is None or entry is None or not text:
        return None
    a, b = logic.day_minute(entry.date, started), min(2880, logic.day_minute(entry.date, when))
    if a < 0 or b <= a:
        return None
    return place_timed_line(user.id, entry.date, a, b, text, now=when)


def timer_back():
    """Where Start or Stop goes back to: the page it was pressed on -- the
    timer bar sends its own address -- or Home."""
    back = request.form.get("back", "")
    if back == "timesheet":
        return url_for("main.timesheet")
    return local_path(back) or url_for("main.dashboard")


# What the dot and word in the timer bar say for each moment of the day.
PUNCH_WORDS = {"working": "Logged in", "break": "On break", "done": "Logged out",
               "out": "Not logged in", "stale": "Logout missing"}


def punch_state(user_id, now):
    """Where the day is: working, on a break, done (logged out today), out
    (not logged in yet) or stale (a logout forgotten) -- and the shift."""
    entry = get_open_entry(user_id)
    if entry is not None:
        if logic.is_stale_shift(entry, now):
            return "stale", entry
        return ("break" if entry.open_break() else "working"), entry
    today = TimeEntry.query.filter_by(user_id=user_id, date=now.date()).first()
    if today is not None and today.login_time and today.logout_time:
        return "done", today
    return "out", None


# What /q offers in each state: (wanted name, words, punch endpoint, style).
QUICK_ACTIONS = {
    "out": [("login", "Login", "main.punch_in", "btn-go")],
    "done": [("login", "Login again", "main.punch_in", "btn-go")],
    "working": [("break", "Break", "main.punch_break_start", "btn-pause"),
                ("logout", "Logout", "main.punch_out", "btn-stop")],
    "break": [("back", "Back", "main.punch_break_end", "btn-go")],
    "stale": [],
}


@main_bp.route("/q")
@login_required
def quick_punch():
    """One plain address for a tag, a QR code or a home-screen shortcut: the
    next punch for whoever is signed in, one tap away. A visit never punches
    by itself -- the tap sends the usual punch form. ?want= (the app's
    shortcuts) puts that one forward when it's possible now."""
    now = datetime.now()
    state, entry = punch_state(current_user.id, now)
    actions = QUICK_ACTIONS[state]
    wanted = request.args.get("want", "")
    first = [a for a in actions if a[0] == wanted]
    if first:
        actions = first + [a for a in actions if a[0] != wanted]
    return render_template(
        "quick.html", state=state, word=PUNCH_WORDS[state], entry=entry, actions=actions,
        missed=bool(wanted) and not first, now=now, fmt_time=logic.fmt_time,
    )


@main_bp.route("/api/palette")
@login_required
def api_palette():
    """What the command palette offers beyond the pages: the day's next
    punches, the timer, and the last ten tasks to start again."""
    now = datetime.now()
    state, entry = punch_state(current_user.id, now)
    punches = {
        "out": [("Login", "main.punch_in")],
        "done": [("Login again", "main.punch_in")],
        "working": [("Break", "main.punch_break_start"), ("Logout", "main.punch_out")],
        "break": [("Back from break", "main.punch_break_end")],
        "stale": [],
    }[state]
    timer = timer_view(current_user, now)
    day = logic.work_day(entry if state != "done" else None, now)
    return jsonify(
        state=state,
        word=PUNCH_WORDS[state],
        punches=[{"label": label, "url": url_for(endpoint)} for label, endpoint in punches],
        fix_url=url_for("main.edit_entry", date_str=entry.date.isoformat()) if state == "stale" else None,
        can_time=state == "working",
        timer={"text": timer.text, "clock": timer.clock} if timer else None,
        start_url=url_for("main.timer_start"),
        stop_url=url_for("main.timer_stop"),
        recent=recent_texts(current_user.id, day)[:10],
        today=day.isoformat(),
    )


@main_bp.app_context_processor
def inject_timer_bar():
    """The timer bar at the top of every signed-in page: what's being timed
    and for how long, or a box to start, and where the day is."""
    if not current_user.is_authenticated:
        return {}
    now = datetime.now()
    state, entry = punch_state(current_user.id, now)
    timer = timer_view(current_user, now)
    idle = reminders.settings_for(current_user)["idle"]
    return {"bar": SimpleNamespace(
        state=state, word=PUNCH_WORDS[state], timer=timer,
        can_time=state == "working", stale_day=entry.date if state == "stale" else None,
        recent=recent_texts(current_user.id, logic.work_day(entry if state != "done" else None, now)),
        # The idle prompt's wait in minutes, while a timer runs on a shift.
        idle=idle["minutes"] if idle["on"] and timer is not None and state == "working" else None,
    )}


@main_bp.route("/timer/idle", methods=["POST"])
@login_required
def timer_idle():
    """The idle prompt's answer (app.js, initIdle), for the `away` seconds up
    to now with no activity on the page: keep them on the timer's line, make
    them a break -- the line ends where the time away began and carries on
    after it -- or drop them: the line ends there and the timer stops."""
    action = request.form.get("action", "")
    away = request.form.get("away", "")
    if action not in ("keep", "break", "drop") or not (away.isascii() and away.isdigit() and len(away) < 9):
        abort(400)
    user, now = current_user, datetime.now()
    if user.timer_start is None:
        return jsonify(ok=False, message="The timer isn't running any more.")
    if action == "keep":
        return jsonify(ok=True, message="Kept on the timer.")
    entry = get_open_entry(user.id)
    if entry is None or logic.is_stale_shift(entry, now) or entry.open_break() is not None:
        return jsonify(ok=False, message="You're not working on a shift right now.")
    since = max(now - timedelta(seconds=int(away)), user.timer_start)
    if action == "break":
        since = max(since, logic.last_mark_dt(entry, now))
    if now - since < timedelta(minutes=1):
        return jsonify(ok=False, message="That was under a minute: nothing to change.")
    text, length = user.timer_text, logic.fmt_duration((now - since).total_seconds() / 3600)
    stop_timer(user, entry, since)
    if action == "break":
        db.session.add(BreakSegment(entry_id=entry.id, break_start=since.time(), break_end=now.time()))
        user.timer_start, user.timer_text = now, text  # the line carries on after the break
        message = f"Made the {length} away a break, {logic.fmt_time(since.time())} to {logic.fmt_time(now.time())}."
    else:
        message = f"Dropped the {length} away: {text} ends at {logic.fmt_time(since.time())}."
    db.session.commit()
    flash(message, "success")
    return jsonify(ok=True, message=message)


@main_bp.route("/timer/start", methods=["POST"])
@login_required
def timer_start():
    back = timer_back()
    text = " ".join(request.form.get("text", "").split())[:WorkBlock.MAX_LENGTH].rstrip()
    if not text:
        flash("Type what you're working on, then press Start.", "error")
        return redirect(back)
    now = datetime.now()
    entry = get_open_entry(current_user.id)
    if entry is None or entry.open_break() or logic.is_stale_shift(entry, now):
        flash("Log in (or come back from your break) to start the timer.", "error")
        return redirect(back)
    if current_user.timer_start is not None:
        if current_user.timer_text == text:
            return redirect(back)  # already timing that
        stop_timer(current_user, entry, now)
    current_user.timer_start, current_user.timer_text = now, text
    db.session.commit()
    return redirect(back)


@main_bp.route("/timer/stop", methods=["POST"])
@login_required
def timer_stop():
    back = timer_back()
    now = datetime.now()
    text = current_user.timer_text
    entry = get_open_entry(current_user.id)
    line = stop_timer(current_user, entry, now)
    db.session.commit()
    if line is not None:
        span = logic.fmt_span(*logic.block_span(line, entry, entry.date, now))
        flash(f"Saved {span}: {line.text}.", "success")
    elif text:
        flash("Timer stopped. Under a minute isn't a line.", "success")
    return redirect(back)


def fit_line_to_break(entry, break_start):
    """The Timesheet line just before a break ends where the break begins:
    one stopping up to half an hour short is stretched to it (7:00-8:00 PM
    and a break at 8:04 gives 7:00-8:04 PM), and one running on into it is
    cut there. Never over a deleted line, an earlier break or a Break line."""
    now = datetime.now()

    def minutes(t, ending=False):
        return logic.shift_minutes(t, entry, entry.date, now, ending=ending)

    at = minutes(break_start)
    before = [b for b in WorkBlock.query.filter_by(user_id=entry.user_id, date=entry.date)
              if minutes(b.start_time) < at]
    if not before:
        return
    line = max(before, key=lambda b: (minutes(b.start_time), b.id))
    end = minutes(line.end_time, ending=True)
    if not line.text or line.text.casefold() == "break" or end == at:
        return
    if end < at:
        reach = max(minutes(b.end_time, ending=True) for b in before)
        away = any(s.break_start is not None and s.break_end is not None
                   and minutes(s.break_start) < at
                   and minutes(s.break_end, ending=True) > end for s in entry.breaks)
        if reach >= at or away or at - end > logic.TIMESHEET_SHORT_PIECE:
            return
    line.end_time = break_start.replace(second=0, microsecond=0)


# Logging in again this soon after a shift that ended after midnight (or
# just before it) carries that shift on, the time away a break, rather than
# starting the new date's day at 12:40 AM.
OVERNIGHT_RETURN = timedelta(hours=3)


def last_night_shift(user_id, now):
    """Yesterday's shift, if it ended less than OVERNIGHT_RETURN ago and
    carrying it on to now still makes a day that can be true; else None."""
    entry = TimeEntry.query.filter_by(user_id=user_id, date=now.date() - timedelta(days=1)).first()
    if entry is None or entry.login_time is None or entry.logout_time is None:
        return None
    login_dt = logic.shift_login_dt(entry)
    logout_dt = logic.place_on_shift(entry.date, entry.logout_time, login_dt)
    if not timedelta(0) <= now - logout_dt <= OVERNIGHT_RETURN:
        return None
    if (now - login_dt).total_seconds() / 3600.0 > logic.MAX_LIVE_SHIFT_HOURS:
        return None
    return entry


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
    if entry is None or entry.login_time is None:
        # Back soon after logging out past midnight: last night's shift.
        entry = last_night_shift(user_id, now) or entry

    # Logged out already today: carry on with the same day, the time away
    # recorded as a break. This used to be refused because the day existed,
    # which left the dashboard's main button a dead end after every logout.
    if entry is not None and entry.login_time is not None and entry.logout_time is not None:
        logout_dt = logic.place_on_shift(entry.date, entry.logout_time, logic.shift_login_dt(entry))
        if typed:
            when, error = logic.check_typed_time(
                entry.date, typed, now, earliest=logout_dt, earliest_label="you logged out"
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
        carried = "" if entry.date == today else f", carrying on {entry.date.strftime('%a %d %b')}'s shift"
        # Under a minute is an accidental logout being undone, not a break.
        if gap >= 1 / 60:
            entry.breaks.append(
                BreakSegment(break_start=entry.logout_time, break_end=when.time())
            )
            fit_line_to_break(entry, entry.logout_time)
            message = (
                f"Logged in again at {logic.fmt_time(when.time())}{carried}. The "
                f"{logic.fmt_duration(gap)} since you logged out counts as a break."
            )
        else:
            message = f"Logged in again at {logic.fmt_time(when.time())}{carried}."
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
    # The timer stops at the break, its line ending there.
    stop_timer(current_user, entry, logic.place_on_shift(entry.date, typed, logic.shift_login_dt(entry))
               if typed else now)
    fit_line_to_break(entry, t)
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

    stop_timer(current_user, entry, when)
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


# ------------------------------------------------------- week and reports

def week_strip(user, day, now, today):
    """The week around a day, for the Timesheet's strip: each day's date,
    hours worked, and whether it can be opened yet (today: work_day)."""
    days = logic.week_dates(day)
    entries = get_entries_for_range(user.id, days[0], days[-1])
    strip = []
    for d in days:
        entry = entries.get(d)
        worked = logic.entry_total_hours(entry, now=now) if entry is not None and entry.login_time else 0
        leave = entry is not None and entry.target_override is not None and not worked
        strip.append(SimpleNamespace(date=d, is_today=d == today, is_future=d > today,
                                     hours=logic.fmt_hours_compact(worked) if worked else ("Leave" if leave else "")))
    return strip


@main_bp.route("/timesheet/go")
@login_required
def timesheet_go():
    """The date picker on the Timesheet: straight to that day."""
    try:
        day = datetime.strptime(request.args.get("d", ""), "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("main.timesheet"))
    if day >= work_day(current_user.id, datetime.now()):
        return redirect(url_for("main.timesheet"))
    return redirect(url_for("main.timesheet", date_str=day.isoformat()))


def day_label(d, today):
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return d.strftime("%a %d %b")


@main_bp.route("/timesheet/all", endpoint="timesheet_all")
@login_required
def timesheet_all():
    """Every line written, newest first, a day at a time -- or, with a search,
    every line in all your history that matches."""
    now = datetime.now()
    today = work_day(current_user.id, now)
    query = " ".join(request.args.get("q", "").split())[:100]
    groups, older = [], None

    def line_view(d, a, b, block):
        return SimpleNamespace(span=logic.fmt_span(a, b), text=block.text, length=logic.fmt_length(b - a),
                               copied=block.is_copied, minutes=b - a, id=block.id, pick=[pick_line(d, a, b, block)])

    if query:
        found = (WorkBlock.query
                 .filter(WorkBlock.user_id == current_user.id, WorkBlock.text != "",
                         func.lower(WorkBlock.text).contains(query.lower(), autoescape=True))
                 .order_by(WorkBlock.date.desc(), WorkBlock.start_time).limit(300).all())
        by_day = defaultdict(list)
        for block in found:
            if block.text.casefold() != "break":
                by_day[block.date].append(block)
        shifts = {e.date: e for e in TimeEntry.query.filter(
            TimeEntry.user_id == current_user.id, TimeEntry.date.in_(list(by_day)))} if by_day else {}
        for d in sorted(by_day, reverse=True):
            lines = [line_view(d, *logic.block_span(b, shifts.get(d), d, now), b) for b in by_day[d]]
            groups.append(SimpleNamespace(date=d, label=day_label(d, today), lines=lines,
                                          written=sum(x.minutes for x in lines), worked=None, empty=0))
        total = sum(len(g.lines) for g in groups)
    else:
        try:
            before = datetime.strptime(request.args.get("before", ""), "%Y-%m-%d").date()
        except ValueError:
            before = today + timedelta(days=1)
        last = min(before - timedelta(days=1), today)
        first = last - timedelta(days=13)
        lines, entries = written_lines(current_user.id, first, last, now)
        saved = defaultdict(list)
        for b in WorkBlock.query.filter(WorkBlock.user_id == current_user.id, WorkBlock.date >= first,
                                        WorkBlock.date <= last):
            saved[b.date].append(b)
        minute = logic.day_minute(today, now)
        d = last
        while d >= first:
            entry = entries.get(d)
            day_lines = [line_view(*line) for line in lines if line[0] == d]
            empty = 0
            if entry is not None and entry.login_time:
                empty = sum(1 for _, end, block in logic.timesheet_lines(entry, d, now, saved[d])
                            if block is None and (d < today or end <= minute))
            worked = logic.entry_total_hours(entry, now=now) if entry is not None and entry.login_time else 0
            if day_lines or worked:
                groups.append(SimpleNamespace(date=d, label=day_label(d, today), lines=day_lines, empty=empty,
                                              written=sum(x.minutes for x in day_lines), worked=worked))
            d -= timedelta(days=1)
        has_older = (WorkBlock.query.filter(WorkBlock.user_id == current_user.id, WorkBlock.date < first).first()
                     or TimeEntry.query.filter(TimeEntry.user_id == current_user.id, TimeEntry.date < first).first())
        older = first if has_older else None
        total = sum(len(g.lines) for g in groups)
    return render_template(
        "timesheet_all.html", groups=groups, query=query, older=older, total=total, today=today,
        back=request.full_path.rstrip("?"),
        written=sum(g.written for g in groups), fmt_length=logic.fmt_length, fmt_hours=logic.fmt_hours,
    )


# ------------------------------------------------------------------ boards

def board_card(d, a, b, block, kind):
    return SimpleNamespace(
        date=d, day=d.strftime("%a %d"), start=a, span=logic.fmt_span(a, b), length=logic.fmt_length(b - a),
        minutes=b - a, kind=kind,
        id=str(block.id) if block is not None and block != logic.TIMESHEET_BREAK else "",
        text="Break" if block == logic.TIMESHEET_BREAK else (block.text if block is not None else ""),
        start_hhmm=logic.minutes_clock(a).strftime("%H:%M"), end_hhmm=logic.minutes_clock(b).strftime("%H:%M"),
        copy=copy_words(block.text, a, b) if block is not None and block != logic.TIMESHEET_BREAK and block.text else "",
    )


@main_bp.route("/boards", endpoint="boards")
@login_required
def boards():
    """Two boards of the week's lines. Posting: what's still to fill in,
    written, and posted on the other timesheet -- drag a card across to mark
    it. Week: a column a day, its hours against target over its lines."""
    now = datetime.now()
    today = work_day(current_user.id, now)
    view = "week" if request.args.get("view") == "week" else "posting"
    try:
        day = datetime.strptime(request.args.get("week", ""), "%Y-%m-%d").date()
    except ValueError:
        day = today
    days = logic.week_dates(day)
    entries = get_entries_for_range(current_user.id, days[0], days[-1])
    saved = defaultdict(list)
    for b in WorkBlock.query.filter(WorkBlock.user_id == current_user.id, WorkBlock.date >= days[0],
                                    WorkBlock.date <= days[-1]):
        saved[b.date].append(b)
    minute = logic.day_minute(today, now)
    columns = {"empty": [], "written": [], "posted": []}
    week = []
    for d in days:
        entry = entries.get(d)
        worked = logic.entry_total_hours(entry, now=now) if entry is not None and entry.login_time else 0.0
        target = logic.target_hours_for(current_user, d, entry)
        cards = []
        if d <= today:
            day_lines = logic.timesheet_lines(entry, d, now, saved[d])
            if entry is None or not entry.login_time:
                # Not worked: only lines written in by hand, no empty hours.
                day_lines = [x for x in day_lines if x[2] not in (None, logic.TIMESHEET_BREAK)]
            for a, b, block in day_lines:
                text = "Break" if block == logic.TIMESHEET_BREAK else (block.text if block is not None else "")
                if text.casefold() == "break":
                    kind = "break"
                elif not text:
                    if d == today and b > minute:
                        continue  # still to come
                    kind = "empty"
                else:
                    kind = "posted" if block.is_copied else "written"
                card = board_card(d, a, b, block, kind)
                cards.append(card)
                if kind in ("written", "posted"):
                    columns[kind].append(card)
            # To fill in: a card a day, however many lines it's missing.
            empty = [c for c in cards if c.kind == "empty"]
            if empty:
                minutes = sum(c.minutes for c in empty)
                columns["empty"].append(SimpleNamespace(
                    date=d, day=d.strftime("%a %d"), kind="empty", id="", count=len(empty), minutes=minutes,
                    length=logic.fmt_length(minutes), text=""))
        if entry is not None and entry.target_override is not None and not worked:
            status = entry.leave_label or "Leave"
        elif d > today:
            status = "To come"
        elif not worked:
            status = "Rest day" if target == 0 else "Not logged"
        elif d == today and entry.logout_time is None:
            status = "In progress"
        else:
            status = "Done" if worked >= target else "Short"
        week.append(SimpleNamespace(
            date=d, is_today=d == today, worked=worked, target=target, status=status, cards=cards,
            pct=min(100, round(worked / target * 100)) if target else (100 if worked else 0),
            written=sum(c.minutes for c in cards if c.kind in ("written", "posted")),
        ))
    titles = {"empty": "To fill in", "written": "Written", "posted": "Posted"}
    return render_template(
        "boards.html", view=view, days=days, week=week, today=today,
        columns=[SimpleNamespace(key=k, title=titles[k], cards=v, minutes=sum(c.minutes for c in v))
                 for k, v in columns.items()],
        prev_week=days[0] - timedelta(days=7), next_week=days[0] + timedelta(days=7) if days[-1] < today else None,
        is_current=days[0] <= today <= days[-1],
        fmt_length=logic.fmt_length, fmt_hours_compact=logic.fmt_hours_compact, fmt_hours=logic.fmt_hours,
    )


@main_bp.route("/boards/mark", methods=["POST"])
@login_required
def boards_mark():
    """A card moved to Posted (mark=1) or back to Written (mark=0): its line's
    copied mark, as the green on the Timesheet. JSON for a drag; a page
    otherwise."""
    block_id = request.form.get("id", "")
    block = None
    if block_id.isascii() and block_id.isdigit() and len(block_id) < 19:
        block = WorkBlock.query.filter_by(id=int(block_id), user_id=current_user.id).first()
    if block is None or not block.text or block.text.casefold() == "break":
        abort(404)
    block.copied = block.copy_mark() if request.form.get("mark") == "1" else None
    db.session.commit()
    if request.accept_mimetypes.best == "application/json":
        return jsonify(copied=block.is_copied)
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/boards") and "//" not in back else url_for("main.boards"))

def written_lines(user_id, first, last, now):
    """Every Timesheet line with words from first to last (breaks aside), as
    (date, start, end, block) in time order, and each day's TimeEntry."""
    entries = get_entries_for_range(user_id, first, last)
    saved = defaultdict(list)
    for b in WorkBlock.query.filter(WorkBlock.user_id == user_id, WorkBlock.date >= first, WorkBlock.date <= last):
        saved[b.date].append(b)
    out = []
    d = first
    while d <= last:
        for a, b, block in logic.timesheet_lines(entries.get(d), d, now, saved[d]):
            if block is not None and block != logic.TIMESHEET_BREAK and block.text and block.text.casefold() != "break":
                out.append((d, a, b, block))
        d += timedelta(days=1)
    return out, entries


def copy_words(text, a, b):
    """A line as the copy button puts it: "Emails (13:00 to 14:00 PM)"."""
    to = logic.minutes_clock(b)
    return f"{text} ({logic.minutes_clock(a):%H:%M} to {to:%H:%M} {'PM' if to.hour >= 12 else 'AM'})"


def pick_line(d, a, b, block):
    """A line as a tick box carries it, for the batch bar's Copy: its day,
    the day's heading, where it starts, and its words as Copy puts them."""
    return [d.isoformat(), d.strftime("%a %d %b"), a, copy_words(block.text, a, b)]


@main_bp.route("/timesheet/week", endpoint="timesheet_week")
@main_bp.route("/timesheet/week/<date_str>", endpoint="timesheet_week")
@login_required
def timesheet_week(date_str=None):
    """The week as a timesheet: what you worked on down the side, the days
    across, the hours where they meet -- with each day's total against its
    target, a copy of the whole week to paste elsewhere, and a way to mark
    it all as posted."""
    now = datetime.now()
    today = work_day(current_user.id, now)
    day = today
    if date_str is not None:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return redirect(url_for("main.timesheet_week"))
    days = logic.week_dates(day)
    lines, entries = written_lines(current_user.id, days[0], days[-1], now)
    rows = defaultdict(lambda: [0] * 7)
    for d, a, b, block in lines:
        rows[block.text][(d - days[0]).days] += b - a
    rows = sorted(rows.items(), key=lambda row: (-sum(row[1]), row[0].casefold()))
    worked = [round(logic.entry_total_hours(entries[d], now=now) * 60) if d in entries and entries[d].login_time
              else 0 for d in days]
    written = [sum(minutes[i] for _, minutes in rows) for i in range(7)]
    # The weekly target, as on Home: moved only by leave or a day's own target.
    week_target = logic.week_base_hours(current_user, days[0]) + sum(
        logic.target_hours_for(current_user, d, entries.get(d)) - logic.entry_target_hours(current_user, d) for d in days)
    copy = []
    for d in days:
        today_lines = [copy_words(block.text, a, b) for when, a, b, block in lines if when == d]
        if today_lines:
            copy += ([""] if copy else []) + [d.strftime("%a %d %b")] + today_lines
    # A row's tick box chooses every line it adds up.
    picks = defaultdict(lambda: SimpleNamespace(ids=[], lines=[]))
    for d, a, b, block in lines:
        picks[block.text].ids.append(str(block.id))
        picks[block.text].lines.append(pick_line(d, a, b, block))
    # On a phone, the week a day at a time instead of the table.
    unwritten = [max(0, w - x) for w, x in zip(worked, written)]
    day_lists = []
    for i, d in enumerate(days):
        tasks = defaultdict(int)
        for when, a, b, block in lines:
            if when == d:
                tasks[block.text] += b - a
        if worked[i] or tasks:
            day_lists.append(SimpleNamespace(date=d, worked=worked[i], unwritten=unwritten[i],
                                             tasks=sorted(tasks.items(), key=lambda t: (-t[1], t[0].casefold()))))
    return render_template(
        "timesheet_week.html",
        day_lists=day_lists,
        picks=picks, back=request.path, can_fill=days[0] <= today,
        templates=WeekTemplate.query.filter_by(user_id=current_user.id).order_by(WeekTemplate.name).all(),
        days=days, rows=rows, worked=worked, written=written,
        unwritten=unwritten,
        week_worked=sum(worked), week_target=max(0.0, week_target),
        copied=sum(1 for *_, block in lines if block.is_copied), total_lines=len(lines),
        copy_text="\n".join(copy), today=today,
        prev_url=url_for("main.timesheet_week", date_str=(days[0] - timedelta(days=7)).isoformat()),
        next_url=url_for("main.timesheet_week", date_str=(days[0] + timedelta(days=7)).isoformat()),
        is_current=days[0] <= today <= days[-1],
        fmt_minutes=lambda m: f"{m // 60}:{m % 60:02d}" if m else "",
    )


@main_bp.route("/timesheet/week/<date_str>/posted", methods=["POST"])
@login_required
def timesheet_week_posted(date_str):
    """Every written line of the week marked as copied (posted on the other
    timesheet) -- or, with mark=0, back to not copied."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        abort(400)
    days = logic.week_dates(day)
    lines, _ = written_lines(current_user.id, days[0], days[-1], datetime.now())
    on = request.form.get("mark") != "0"
    for *_, block in lines:
        block.copied = block.copy_mark() if on else None
    db.session.commit()
    n = len(lines)
    if not n:
        flash("No lines with words this week yet.", "warning")
    elif on:
        flash(f"Marked {n} line{'s' if n != 1 else ''} as posted.", "success")
    else:
        flash(f"{n} line{'s' if n != 1 else ''} back to not copied.", "success")
    return redirect(url_for("main.timesheet_week", date_str=days[0].isoformat()))


# ------------------------------------------------------------ batch

BATCH_ACTIONS = ("copy", "mark", "unmark", "delete")  # copy: copied on the page, then marked
BATCH_MOST = 500


def picked_ids(form):
    """The ticked lines' ids. One `ids` value can hold several, comma
    separated: a Week row ticks every line it adds up."""
    ids = []
    for value in form.getlist("ids"):
        for part in value.split(","):
            part = part.strip()
            if part.isascii() and part.isdigit() and len(part) < 19:
                ids.append(int(part))
    return list(dict.fromkeys(ids))


def lines_word(n):
    return f"{n} line{'s' if n != 1 else ''}"


@main_bp.route("/timesheet/batch", methods=["POST"])
@login_required
def timesheet_batch():
    """The ticked lines, all at once: marked posted, marked not posted, or
    deleted -- kept with no words, as one line's Delete does, so their time
    isn't offered again. Then back to the page they were ticked on."""
    action = request.form.get("action", "")
    ids = picked_ids(request.form)
    if action not in BATCH_ACTIONS or len(ids) > BATCH_MOST:
        abort(400)
    blocks = (WorkBlock.query.filter(WorkBlock.user_id == current_user.id, WorkBlock.id.in_(ids),
                                     WorkBlock.text != "").all() if ids else [])
    blocks = [b for b in blocks if b.text.casefold() != "break"]
    for block in blocks:
        if action in ("copy", "mark"):
            block.copied = block.copy_mark()
        elif action == "unmark":
            block.copied = None
        else:
            block.text, block.copied = "", None
    db.session.commit()
    n = len(blocks)
    if not n:
        flash("None of those lines are there any more.", "warning")
    elif action == "copy":
        flash(f"Copied {lines_word(n)} and marked {'them' if n != 1 else 'it'} as posted.", "success")
    elif action == "mark":
        flash(f"Marked {lines_word(n)} as posted.", "success")
    elif action == "unmark":
        flash(f"{lines_word(n).capitalize()} no longer marked as posted.", "success")
    else:
        flash(f"Deleted {lines_word(n)}.", "success")
    return redirect(local_path(request.form.get("back")) or url_for("main.timesheet_all"))


# ------------------------------------------------------------ week fill

FILL_SHORTEST = 10  # minutes: a scrap of free time shorter than this stays free
TEMPLATE_MOST = 30


def fill_week(user_id, days, plan, now):
    """Put lines on a week's free time. plan is (weekday, start, end, text)
    in minutes from the day's midnight, as last week had them or a template
    keeps them. Only days already worked (logged in, and up to now), and
    only time no line covers -- never over a line with words, a deleted
    line's time or a break -- so doing it again adds nothing. Returns how
    many lines went on."""
    today = work_day(user_id, now)
    entries = get_entries_for_range(user_id, days[0], days[-1])
    added = 0
    for d in days:
        entry = entries.get(d)
        wanted = sorted((a, b, text) for weekday, a, b, text in plan if weekday == d.weekday())
        if d > today or entry is None or entry.login_time is None or not wanted:
            continue
        blocks = WorkBlock.query.filter_by(user_id=user_id, date=d).all()
        until = logic.day_minute(d, now)
        # The day's lines still to fill in, joined where they meet.
        free = []
        for a, b, block in logic.timesheet_lines(entry, d, now, blocks):
            b = min(b, until)
            if block is not None or b <= a:
                continue
            if free and free[-1][1] == a:
                free[-1] = (free[-1][0], b)
            else:
                free.append((a, b))
        for a, b, text in wanted:
            placed = [(max(a, p), min(b, q)) for p, q in free if min(b, q) - max(a, p) >= FILL_SHORTEST]
            for s, e in placed:
                db.session.add(WorkBlock(user_id=user_id, date=d, start_time=logic.minutes_clock(s),
                                         end_time=logic.minutes_clock(e), text=text))
            added += len(placed)
            free = [piece for p, q in free for piece in logic.free_pieces(p, q, placed)]
    return added


def week_plan(user_id, first, now):
    """A week's lines with words as fill_week's plan."""
    lines, _ = written_lines(user_id, first, first + timedelta(days=6), now)
    return [(d.weekday(), a, b, block.text) for d, a, b, block in lines]


def template_plan(template):
    """A saved template's lines as fill_week's plan; anything unreadable in
    it is left out."""
    try:
        lines = json.loads(template.lines or "[]")
    except ValueError:
        return []
    plan = []
    for line in lines if isinstance(lines, list) else []:
        try:
            weekday, a, b = int(line["weekday"]), int(line["start"]), int(line["end"])
            text = clean_text(str(line["text"]))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= weekday <= 6 and 0 <= a < b <= 2880 and text and text.casefold() != "break":
            plan.append((weekday, a, b, text))
    return plan


def week_from(date_str):
    try:
        return logic.week_dates(datetime.strptime(date_str, "%Y-%m-%d").date())
    except ValueError:
        abort(400)


def said_filled(n, source):
    if n:
        flash(f"Added {lines_word(n)} from {source}.", "success")
    else:
        flash(f"Nothing added from {source}: there's no free time this week where its lines go.", "warning")


@main_bp.route("/timesheet/week/<date_str>/copy-last", methods=["POST"])
@login_required
def timesheet_week_copy_last(date_str):
    """Last week's lines put on this week's free time, day for day."""
    days = week_from(date_str)
    now = datetime.now()
    plan = week_plan(current_user.id, days[0] - timedelta(days=7), now)
    if not plan:
        flash("Last week has no lines with words to copy.", "warning")
    else:
        said_filled(fill_week(current_user.id, days, plan, now), "last week")
        db.session.commit()
    return redirect(url_for("main.timesheet_week", date_str=days[0].isoformat()))


@main_bp.route("/timesheet/week/<date_str>/template", methods=["POST"])
@login_required
def timesheet_template_save(date_str):
    """The week's lines kept as a template, under a name; the same name
    again replaces it."""
    days = week_from(date_str)
    name = " ".join(request.form.get("name", "").split())[:WeekTemplate.MAX_NAME] \
        or f"Week of {days[0]:%d %b}"
    lines = [{"weekday": weekday, "start": a, "end": b, "text": text}
             for weekday, a, b, text in week_plan(current_user.id, days[0], datetime.now())]
    back = redirect(url_for("main.timesheet_week", date_str=days[0].isoformat()))
    if not lines:
        flash("This week has no lines with words to keep.", "warning")
        return back
    template = WeekTemplate.query.filter_by(user_id=current_user.id, name=name).first()
    if template is None:
        if WeekTemplate.query.filter_by(user_id=current_user.id).count() >= TEMPLATE_MOST:
            flash(f"You have {TEMPLATE_MOST} templates. Delete one first.", "error")
            return back
        template = WeekTemplate(user_id=current_user.id, name=name)
        db.session.add(template)
    template.lines = json.dumps(lines)
    db.session.commit()
    flash(f"Saved {lines_word(len(lines))} as “{name}”.", "success")
    return back


def chosen_template():
    value = request.form.get("template", "")
    template = None
    if value.isascii() and value.isdigit() and len(value) < 19:
        template = WeekTemplate.query.filter_by(id=int(value), user_id=current_user.id).first()
    if template is None:
        abort(404)
    return template


@main_bp.route("/timesheet/week/<date_str>/template/use", methods=["POST"])
@login_required
def timesheet_template_use(date_str):
    """A template's lines put on this week's free time."""
    days = week_from(date_str)
    template = chosen_template()
    said_filled(fill_week(current_user.id, days, template_plan(template), datetime.now()), f"“{template.name}”")
    db.session.commit()
    return redirect(url_for("main.timesheet_week", date_str=days[0].isoformat()))


@main_bp.route("/timesheet/week/<date_str>/template/delete", methods=["POST"])
@login_required
def timesheet_template_delete(date_str):
    days = week_from(date_str)
    template = chosen_template()
    flash(f"Deleted the template “{template.name}”.", "success")
    db.session.delete(template)
    db.session.commit()
    return redirect(url_for("main.timesheet_week", date_str=days[0].isoformat()))


REPORT_RANGES = {"week": "This week", "last-week": "Last week", "month": "This month", "last-month": "Last month"}


def report_range(name, today):
    if name == "last-week":
        first = today - timedelta(days=today.weekday() + 7)
        return first, first + timedelta(days=6)
    if name == "month":
        return today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if name == "last-month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    first = today - timedelta(days=today.weekday())
    return first, first + timedelta(days=6)


@main_bp.route("/reports")
@login_required
def reports():
    """Hours by day against target, breaks, overtime, and when each day began
    and ended -- for a week or a month. Where the time went has its own page."""
    now = datetime.now()
    today = work_day(current_user.id, now)
    name = request.args.get("range", "week")
    if name not in REPORT_RANGES:
        name = "week"
    first, last = report_range(name, today)
    _, entries = written_lines(current_user.id, first, min(last, today), now)
    # Overtime by the week, as History and Home count it (logic.settle_weeks).
    rows = []
    d = first
    while d <= last:
        entry = entries.get(d)
        rows.append({
            "date": d, "entry": entry,
            "full_target": logic.target_hours_for(current_user, d, entry),
            "worked_hours": (logic.entry_total_hours(entry, now=now)
                             if entry is not None and entry.login_time else 0.0),
        })
        d += timedelta(days=1)
    logic.settle_weeks(rows, today, now=now)
    days = [SimpleNamespace(
        date=r["date"], entry=r["entry"], worked=r["worked_hours"],
        target=r["full_target"] if (r["entry"] is not None or r["date"] <= today) else 0.0,
        due=r["accrued_target_hours"], balance=r["balance_hours"], banked=r["banked_hours"],
        week=r["settles"] and r["settled"],
        breaks=logic.entry_break_hours(r["entry"], now=now) if r["entry"] is not None and r["entry"].login_time else 0.0,
        future=r["date"] > today, is_today=r["date"] == today,
    ) for r in rows]
    worked_days = [x for x in days if x.worked > 0]
    starts = [logic.clock_minutes(x.entry.login_time) for x in worked_days]
    # A logout after midnight is late, not early: 12:30 AM counts as 24:30.
    ends = [logic.timesheet_span(x.entry, x.date, now)[1] for x in worked_days if x.entry.logout_time]
    top = max((x.worked for x in days), default=0)
    scale = max(top, max((x.target for x in days), default=0), 1)
    return render_template(
        "reports.html",
        name=name, ranges=REPORT_RANGES, first=first, last=last, days=days, scale=scale,
        worked=sum(x.worked for x in days), due=sum(x.due for x in days),
        breaks=sum(x.breaks for x in days), days_worked=len(worked_days),
        avg_start=logic.fmt_clock(logic.minutes_clock(sum(starts) // len(starts))) if starts else "",
        avg_end=logic.fmt_clock(logic.minutes_clock(sum(ends) // len(ends) % 1440)) if ends else "",
        fmt_hours=logic.fmt_hours, fmt_balance=logic.fmt_balance, fmt_time=logic.fmt_time,
        fmt_length=logic.fmt_length, fmt_hours_compact=logic.fmt_hours_compact,
        heatmap=heatmap_view(current_user, today, now),
        mail_ready=mailer.is_configured(),
    )


@main_bp.route("/reports/time")
@login_required
def reports_time():
    """Where the time went: the range's written lines added up by what they
    say. Off the menus; the command palette (Ctrl+K) finds it."""
    now = datetime.now()
    today = work_day(current_user.id, now)
    name = request.args.get("range", "week")
    if name not in REPORT_RANGES:
        name = "week"
    first, last = report_range(name, today)
    lines, _ = written_lines(current_user.id, first, min(last, today), now)
    tasks = defaultdict(int)
    for _, a, b, block in lines:
        tasks[block.text] += b - a
    tasks = sorted(tasks.items(), key=lambda t: -t[1])
    return render_template(
        "reports_time.html",
        name=name, ranges=REPORT_RANGES, first=first, last=last,
        tasks=tasks, total=sum(m for _, m in tasks), written=len(lines), fmt_length=logic.fmt_length,
    )


@main_bp.route("/reports/lines.csv")
@login_required
def reports_csv():
    """The range's written lines, one a row, for a spreadsheet."""
    now = datetime.now()
    today = work_day(current_user.id, now)
    name = request.args.get("range", "week")
    first, last = report_range(name if name in REPORT_RANGES else "week", today)
    lines, _ = written_lines(current_user.id, first, min(last, today), now)
    out = io.StringIO()
    sheet = csv.writer(out)
    sheet.writerow(["Date", "From", "To", "Hours", "What you worked on", "Copied"])
    for d, a, b, block in lines:
        sheet.writerow([d.isoformat(), logic.minutes_clock(a).strftime("%H:%M"), logic.minutes_clock(b).strftime("%H:%M"),
                        f"{(b - a) / 60:.2f}", block.text, "Yes" if block.is_copied else ""])
    response = current_app.response_class(out.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="Timesheet lines {first.isoformat()} to {last.isoformat()}.csv"'
    return response


# ------------------------------------------------------- calendar grid

def clean_text(text):
    return " ".join((text or "").split())[:WorkBlock.MAX_LENGTH].rstrip()


def move_line(block, a, b, blocks, span):
    """Give a saved line new times, a to b in minutes, with the lines beside
    it following: one that met an edge moves with it, one it now runs into
    starts (or ends) where it does. span gives a line's own minutes
    (logic.block_span). Returns why it can't, or None."""
    old_a, old_b = span(block)
    for other in blocks:
        if other is block:
            continue
        s, e = span(other)
        if s == old_b or (b > old_b and old_b <= s < b):
            if b >= e:
                return "That would run over the whole of the next line. Change it on the Timesheet."
            other.start_time = logic.minutes_clock(b)
        elif e == old_a or (a < old_a and a < e <= old_a):
            if a <= s:
                return "That would run over the whole of the line before. Change it on the Timesheet."
            other.end_time = logic.minutes_clock(a)
    block.start_time, block.end_time = logic.minutes_clock(a), logic.minutes_clock(b)
    return None


def calendar_columns(user_id, days, now):
    """Each day's Timesheet lines as blocks for the time grid, and the hours
    the grid runs over (whole hours, at least 1 PM to 10 PM)."""
    today = work_day(user_id, now)
    entries = {e.date: e for e in TimeEntry.query.filter(
        TimeEntry.user_id == user_id, TimeEntry.date >= days[0], TimeEntry.date <= days[-1])}
    saved = defaultdict(list)
    for b in WorkBlock.query.filter(WorkBlock.user_id == user_id, WorkBlock.date >= days[0],
                                    WorkBlock.date <= days[-1]):
        saved[b.date].append(b)
    minute = logic.day_minute(today, now)
    columns, low, high = [], logic.TIMESHEET_DAY[0], logic.TIMESHEET_DAY[1]
    for d in days:
        entry = entries.get(d)
        if d > today:
            lines = []
        elif entry is None or entry.login_time is None:
            # Not worked: only lines written in by hand, no empty hours.
            lines = [line for line in logic.timesheet_lines(entry, d, now, saved[d]) if line[2] is not None
                     and line[2] != logic.TIMESHEET_BREAK]
        else:
            lines = logic.timesheet_lines(entry, d, now, saved[d])
        blocks = []
        for a, b, block in lines:
            text = "Break" if block == logic.TIMESHEET_BREAK else (block.text if block is not None else "")
            kind = "break" if text.casefold() == "break" else ("saved" if text else "empty")
            blocks.append(SimpleNamespace(
                start=a, end=b, text=text, kind=kind,
                id=str(block.id) if block is not None and block != logic.TIMESHEET_BREAK else "",
                start_hhmm=logic.minutes_clock(a).strftime("%H:%M"), end_hhmm=logic.minutes_clock(b).strftime("%H:%M"),
                span=logic.fmt_span(a, b), length=logic.fmt_length(b - a),
                copied=block is not None and block != logic.TIMESHEET_BREAK and block.is_copied,
                copy=copy_words(text, a, b) if kind == "saved" else "",
                now=d == today and a <= minute < b,
            ))
            low, high = min(low, a), max(high, b)
        worked = logic.entry_total_hours(entry, now=now) if entry is not None and entry.login_time else 0
        columns.append(SimpleNamespace(date=d, blocks=blocks, is_today=d == today, worked=worked,
                                       now_at=minute if d == today else None))
    low, high = low // 60 * 60, min(2880, -(-high // 60) * 60)
    return columns, low, high


def calendar_grid(view, date_str):
    now = datetime.now()
    today = work_day(current_user.id, now)
    day = today
    if date_str is not None:
        try:
            day = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return redirect(url_for(f"main.calendar_{view}"))
    if view == "week":
        first = day - timedelta(days=day.weekday())
        days = [first + timedelta(days=i) for i in range(7)]
        step = timedelta(days=7)
        title = f"{days[0]:%d %b} – {days[-1]:%d %b %Y}"
        is_current = first <= today <= days[-1]
    else:
        days, step, title, is_current = [day], timedelta(days=1), f"{day:%A %d %B}", day == today
    columns, low, high = calendar_columns(current_user.id, days, now)
    endpoint = f"main.calendar_{view}"
    return render_template(
        "calendar_grid.html",
        view=view, columns=columns, grid_from=low, grid_to=high, title=title, is_current=is_current,
        hours=[(m, logic.fmt_clock(logic.minutes_clock(m)).replace(":00", "")) for m in range(low, high + 1, 60)],
        prev_url=url_for(endpoint, date_str=(days[0] - step).isoformat()),
        next_url=url_for(endpoint, date_str=(days[0] + step).isoformat()),
        today_url=url_for(endpoint),
        here=url_for(endpoint, date_str=days[0].isoformat()),
        day_url=url_for("main.calendar_day", date_str=day.isoformat()),
        week_url=url_for("main.calendar_week", date_str=day.isoformat()),
        month_url=url_for("main.calendar", year=day.year, month=day.month),
        today=today, fmt_hours_compact=logic.fmt_hours_compact,
        recent=recent_texts(current_user.id, today),
    )


@main_bp.route("/calendar/week", endpoint="calendar_week")
@main_bp.route("/calendar/week/<date_str>", endpoint="calendar_week")
@login_required
def calendar_week(date_str=None):
    return calendar_grid("week", date_str)


@main_bp.route("/calendar/day", endpoint="calendar_day")
@main_bp.route("/calendar/day/<date_str>", endpoint="calendar_day")
@login_required
def calendar_day(date_str=None):
    return calendar_grid("day", date_str)


@main_bp.route("/calendar/line", methods=["POST"])
@login_required
def calendar_line():
    """A line added or changed from the Calendar: from the box a block opens,
    or by dragging its edge. Back to the same view afterwards."""
    form = request.form
    back = form.get("back", "")
    if not back.startswith("/calendar/") or "//" in back:
        back = url_for("main.calendar_week")
    try:
        day = datetime.strptime(form.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        abort(400)
    if day > datetime.now().date():
        flash("That day hasn't come yet.", "error")
        return redirect(back)
    start, end = parse_time_field(form.get("start")), parse_time_field(form.get("end"))
    text = clean_text(form.get("text"))
    if start is None or end is None:
        flash("Give the line a start and an end time.", "error")
        return redirect(back)
    now = datetime.now()
    entry = TimeEntry.query.filter_by(user_id=current_user.id, date=day).first()
    a = logic.shift_minutes(start, entry, day, now)
    b = logic.shift_minutes(end, entry, day, now, ending=True)
    if b <= a:
        flash("The end has to be after the start.", "error")
        return redirect(back)
    blocks = WorkBlock.query.filter_by(user_id=current_user.id, date=day).all()
    block_id = form.get("id", "")
    if block_id:
        block = next((x for x in blocks if str(x.id) == block_id), None)
        if block is None:
            abort(404)
        if not text:
            # Kept with no words, so its time isn't offered again -- as Delete
            # on the Timesheet does.
            block.text = ""
            db.session.commit()
            flash(f"Deleted {logic.fmt_span(a, b)}.", "success")
            return redirect(back)
        error = move_line(block, a, b, blocks, lambda blk: logic.block_span(blk, entry, day, now))
        if error:
            db.session.rollback()
            flash(error, "error")
            return redirect(back)
        block.text = text
    else:
        if not text:
            flash("Write what you worked on, to keep this line.", "error")
            return redirect(back)
        place_timed_line(current_user.id, day, a, b, text, now=now)
    db.session.commit()
    flash(f"Saved {logic.fmt_span(a, b)}.", "success")
    return redirect(back)


# ------------------------------------------------------- history, calendar

def _month_page(year, month):
    """Everything History and Calendar show for a month -- the same figures,
    laid out two ways. None for a month that can't exist (a hand-typed /13,
    which would otherwise be a server error)."""
    today = work_day(current_user.id, datetime.now())
    year = year or today.year
    month = month or today.month
    if not (1 <= month <= 12 and 1970 <= year <= 2100):
        return None

    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    now = datetime.now()
    entries_by_date = get_entries_for_range(current_user.id, first_day, last_day)
    summary = logic.adjusted_month_summary(current_user, entries_by_date, year, month, today,
                                           adjustments_between(current_user.id, first_day, last_day), now=now)

    prev_month = (first_day - timedelta(days=1)).replace(day=1)
    next_month_first = last_day + timedelta(days=1)
    return dict(
        summary=summary,
        rows=summary["rows"],
        # Days whose punch times were typed in by hand (app/changes.py).
        edited=changes.edited_days(current_user.id, first_day, last_day),
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


def parse_hours(raw):
    """Hours typed for a balance adjustment: 2, -1.5, +2:30 or −0:45. None
    for anything else, nothing at all, or more than 100h either way."""
    raw = (raw or "").strip().replace("−", "-").replace(" ", "")
    sign = -1 if raw.startswith("-") else 1
    raw = raw.lstrip("+-")
    try:
        if ":" in raw:
            h, m = raw.split(":", 1)
            if not (h.isdigit() or h == "") or not m.isdigit() or int(m) >= 60:
                return None
            value = int(h or 0) + int(m) / 60
        else:
            value = float(raw)
    except ValueError:
        return None
    value *= sign
    return value if math.isfinite(value) and 0 < abs(value) <= 100 else None


@main_bp.route("/history/adjust", methods=["POST"])
@login_required
def adjust_balance():
    """A balance adjustment added from History: hours on a date, and why."""
    try:
        day = datetime.strptime(request.form.get("date", ""), "%Y-%m-%d").date()
    except ValueError:
        day = None
    hours = parse_hours(request.form.get("hours"))
    if day is None or not 1970 <= day.year <= 2100 or hours is None:
        flash("Give the adjustment a date and its hours, like 2 or -1:30.", "error")
        return redirect(local_path(request.form.get("back")) or url_for("main.history"))
    reason = " ".join(request.form.get("reason", "").split())[:BalanceAdjustment.MAX_REASON]
    db.session.add(BalanceAdjustment(user_id=current_user.id, date=day, hours=hours, reason=reason))
    db.session.commit()
    flash(f"Adjusted the balance {logic.fmt_balance(hours)} on {day:%a %d %b}.", "success")
    return redirect(url_for("main.history", year=day.year, month=day.month))


@main_bp.route("/history/adjust/<int:adjustment_id>/delete", methods=["POST"])
@login_required
def delete_adjustment(adjustment_id):
    adjustment = BalanceAdjustment.query.filter_by(id=adjustment_id, user_id=current_user.id).first_or_404()
    day, hours = adjustment.date, adjustment.hours
    db.session.delete(adjustment)
    db.session.commit()
    flash(f"Removed the {logic.fmt_balance(hours)} adjustment on {day:%a %d %b}.", "success")
    return redirect(url_for("main.history", year=day.year, month=day.month))


# ------------------------------------------------------------ holidays

def holidays_back(year):
    return redirect(url_for("main.holidays_page", year=year))


def form_year(today):
    try:
        year = int(request.values.get("year", today.year))
    except ValueError:
        year = today.year
    return year if 2000 <= year <= 2100 else today.year


@main_bp.route("/holidays")
@login_required
def holidays_page():
    """A year's public holidays: those saved, the built-in list to pick
    from, one to add, or a list to paste; and the leave calendar's link."""
    today = work_day(current_user.id, datetime.now())
    year = form_year(today)
    saved = (Holiday.query.filter(Holiday.user_id == current_user.id, Holiday.date >= date(year, 1, 1),
                                  Holiday.date <= date(year, 12, 31)).order_by(Holiday.date).all())
    offered, about = holidays.builtin(year)
    have = {h.date for h in saved}
    return render_template(
        "holidays.html", year=year, saved=saved, offered=offered, about=about, have=have, today=today,
        feed=url_for("main.leave_ics", key=current_user.ics_token, _external=True) if current_user.ics_token else None,
    )


@main_bp.route("/holidays/add", methods=["POST"])
@login_required
def holidays_add():
    """One holiday, a list pasted in, or the ticked ones from the built-in
    list -- saved, each day's target set to 0 unless it has its own."""
    today = work_day(current_user.id, datetime.now())
    year = form_year(today)
    how = request.form.get("how")
    if how == "one":
        day = holidays.parse_date(request.form.get("date"))
        name = " ".join(request.form.get("name", "").split())[:Holiday.MAX_NAME]
        if day is None or not name:
            flash("Give the holiday a date and a name.", "error")
            return holidays_back(year)
        chosen, bad = [(day, name)], []
    elif how == "paste":
        chosen, bad = holidays.parse_lines(request.form.get("lines"))
    elif how == "builtin":
        offered = {d.isoformat(): name for d, name, _ in holidays.builtin(year)[0]}
        chosen = [(date.fromisoformat(v), offered[v]) for v in request.form.getlist("pick") if v in offered]
        bad = []
    else:
        abort(400)
    new = holidays.save(current_user, chosen)
    db.session.commit()
    if chosen:
        kept = len(chosen) - new
        flash(f"Saved {new} holiday{'s' if new != 1 else ''}" + (f" ({kept} already saved)" if kept else "") + ".",
              "success")
    if bad:
        flash(f"Couldn't read {len(bad)} line{'s' if len(bad) != 1 else ''}: {'; '.join(bad[:3])}"
              f"{' …' if len(bad) > 3 else ''}. Write each as 2026-12-25, Christmas.", "error")
    elif not chosen:
        flash("Nothing chosen to save.", "warning")
    year = chosen[0][0].year if how != "builtin" and chosen else year
    return holidays_back(year)


@main_bp.route("/holidays/<int:holiday_id>/delete", methods=["POST"])
@login_required
def holidays_delete(holiday_id):
    holiday = Holiday.query.filter_by(id=holiday_id, user_id=current_user.id).first_or_404()
    year, name = holiday.date.year, holiday.name
    holidays.remove(current_user, holiday)
    db.session.commit()
    flash(f"Removed {name}.", "success")
    return holidays_back(year)


@main_bp.route("/holidays/feed", methods=["POST"])
@login_required
def holidays_feed():
    """A new address for the leave calendar; the old one stops working."""
    current_user.ics_token = secrets.token_urlsafe(24)
    db.session.commit()
    flash("New calendar link made. Anywhere still using the old one stops updating.", "success")
    return holidays_back(form_year(work_day(current_user.id, datetime.now())))


@main_bp.route("/deploy/github", methods=["POST"])
def deploy_github():
    """GitHub's webhook after a push (app/deploy.py): signed with the shared
    DEPLOY_SECRET, or turned away. A push to the site's branch brings the
    site forward to it and reloads it; anything else is only acknowledged."""
    secret = current_app.config.get("DEPLOY_SECRET")
    if not secret:
        abort(404)
    if not deploy.verify(secret, request.get_data(cache=True), request.headers.get("X-Hub-Signature-256", "")):
        abort(403)
    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return jsonify(ok=True, message="pong")
    branch = current_app.config.get("DEPLOY_BRANCH") or deploy.DEFAULT_BRANCH
    payload = request.get_json(silent=True) or {}
    if event != "push" or payload.get("ref") != f"refs/heads/{branch}":
        return jsonify(ok=True, message="Nothing to do for this."), 202
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    db_path = uri.replace("sqlite:///", "", 1) if uri.startswith("sqlite:///") else None
    wsgi_file = os.environ.get("STM_WSGI_FILE") or f"/var/www/{request.host.split(':')[0].replace('.', '_')}_wsgi.py"
    status, message = deploy.run(branch, db_path, wsgi_file)
    ok = status in ("updated", "current")
    return jsonify(ok=ok, status=status, message=message), 200 if ok else (409 if status in ("busy", "stopped") else 500)


@main_bp.route("/calendar/leave.ics")
def leave_ics():
    """Leave days and holidays as a calendar to subscribe to -- no sign-in,
    so the secret in its address is the key."""
    key = request.args.get("key", "")
    user = User.query.filter_by(ics_token=key).first() if 20 <= len(key) <= 64 else None
    if user is None:
        abort(404)
    response = current_app.response_class(holidays.ics(user, datetime.now().date()), mimetype="text/calendar")
    response.headers["Content-Disposition"] = 'inline; filename="stm-leave.ics"'
    response.headers["Cache-Control"] = "no-store"
    return response


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
        download_name=f"Timesheet {page['month_name']} {year}.{kind}",
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
    today = work_day(current_user.id, datetime.now())
    page["pay"] = (pay_for(current_user, page["year"], page["month"], page["summary"]["balance_hours"])
                   if (page["year"], page["month"]) <= (today.year, today.month) else None)
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
    today = work_day(current_user.id, now)
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
    # The logout, in minutes -- past 1,440 the morning after an overnight
    # shift: what "End at logout" brings a line's end to.
    logout_at = None
    if entry is not None and entry.login_time and entry.logout_time and entry.logout_time != entry.login_time:
        logout_at = logic.timesheet_span(entry, day, now)[1]
    # On a shift that runs past midnight, a time before the login is after it.
    night = logic.night_reach(entry, day, now)

    def minutes(t, ending=False):
        return logic.shift_minutes(t, entry, day, now, ending=ending)

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
            was_text=text if block is not None or is_break else "", is_now=False,
            # A break moved or reworded is saved as a line, and still shown as a break.
            is_break=is_break or text.casefold() == "break",
            placeholder="", error="", error_on="", can_end=False, end_confirm="",
            copied=block.copied if block is not None and block.is_copied else "", length="", span="",
        )

    def day_views(lines):
        views = [line_view(hhmm(start), hhmm(end), block) for start, end, block in lines]
        for view, (start, end, _) in zip(views, lines):
            view.length = logic.fmt_length(end - start)
            view.span = logic.fmt_span(start, end)  # the phone's list: 9:00–10:00 AM
        minute = logic.day_minute(day, now)
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
                if view.text and not view.is_break and start < logout_at and end != logout_at \
                        and end >= logout_at - 60:
                    later = sum(1 for v in views[i + 1:] if v.text and not v.is_break)
                    view.can_end = True
                    if later:
                        view.end_confirm = (f"End this line at {logic.fmt_time(entry.logout_time)} and remove "
                                            f"the {later} line{'s' if later > 1 else ''} after it?")
        return views

    open_entry = get_open_entry(current_user.id) if day == today else None
    can_time = open_entry is not None and not open_entry.open_break() and not logic.is_stale_shift(open_entry, now)

    def show(views):
        # The day at the top of the page: 1:00 PM – 6:00 PM · 4h 30m worked.
        day_span = worked = ""
        if entry is not None and entry.login_time is not None:
            until = logic.fmt_time(entry.logout_time) if entry.logout_time else "now"
            worked = logic.fmt_hours(logic.entry_total_hours(entry, now=now))
            day_span = f"{logic.fmt_time(entry.login_time)} – {until}"
        return render_template(
            "timesheet.html",
            day=day,
            is_today=day == today,
            prev_date=day - timedelta(days=1),
            next_date=None if day == today else day + timedelta(days=1),
            strip=week_strip(current_user, day, now, today),
            today_date=today,
            prev_week=day - timedelta(days=7),
            next_week=None if day >= today else min(today, day + timedelta(days=7)),
            lines=views,
            max_length=WorkBlock.MAX_LENGTH,
            who=current_user.display_name or current_user.username,
            day_span=day_span,
            day_worked=worked,
            logout_label=logic.fmt_time(entry.logout_time) if logout_at is not None else "",
            recent=recent_texts(current_user.id, day),
            timer=timer_view(current_user, now) if day == today else None,
            can_time=day == today and can_time,
            night=night,
            login_at=logic.clock_minutes(entry.login_time) if entry is not None and entry.login_time else None,
            back=request.path,
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

        def line_number(name):
            # Which line a button was for: its place on the page.
            value = form.get(name, "")
            if value and (not value.isascii() or not value.isdigit() or int(value) >= len(fields[0])):
                abort(400)
            return value

        # Saved as you type (app.js): what can be saved is, a line with a
        # problem waits for more typing, and the answer is the lines as saved.
        autosave = form.get("autosave") == "1"

        # A line squeezed to nothing by a time changed beside it goes. A page
        # from before this was added sends no "drop" at all.
        dropped = form.getlist("drop") or [""] * len(fields[0])
        if len(dropped) != len(fields[0]):
            abort(400)
        by_id = {str(b.id): b for b in blocks}
        changed, drafts, gone, saved, cleared, vanished = [], [], 0, {}, set(), set()
        to_delete = line_number("delete")
        to_copy, to_uncopy = line_number("copied"), line_number("uncopied")
        to_logout = line_number("to_logout")
        # The line menu's Split in two and Duplicate: done once the page is saved.
        to_split, to_duplicate = line_number("split"), line_number("duplicate")
        if to_logout:
            # "End at logout": that line's end becomes the logout, as if typed.
            if logout_at is None:
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
                can_end=False, end_confirm="", copied="", length="", span="",
            )
            drafts.append(draft)
            if dropped[index] == "1":
                # The line beside it has its time now, so it goes for good.
                block = by_id.get(block_id)
                if block is not None:
                    changed.append((block.start_time, block.end_time, "Removed"))
                    db.session.delete(block)
                continue
            if to_delete == str(index):
                # A deleted line stays as a block with no words, so its time
                # isn't offered as a line again.
                block = by_id.get(block_id)
                if block is not None:
                    changed.append((block.start_time, block.end_time, "Deleted"))
                    block.text = ""
                elif not block_id:
                    start, end = clock(was_start) or start, clock(was_end) or end
                    if start and end and minutes(end, ending=True) > minutes(start):
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
                vanished.add(index)
                continue
            if block is not None:
                if text_changed and not text:
                    changed.append((block.start_time, block.end_time, "Cleared"))
                    db.session.delete(block)
                    cleared.add(index)
                    continue
                start = start if start_changed else block.start_time
                end = end if end_changed else block.end_time
                text = text if text_changed else block.text
            elif not text:
                if (start_changed or end_changed) and not autosave:
                    draft.error, draft.error_on = "Write what you worked on, to keep these times.", "text"
                continue
            if start is None or end is None:
                draft.error, draft.error_on = "Give this line a start and an end time.", "times"
                continue
            if minutes(end, ending=True) <= minutes(start):
                draft.error, draft.error_on = "The end has to be after the start.", "times"
                continue
            if block is None:
                if any((b.start_time, b.end_time, b.text) == (start, end, text) for b in blocks):
                    continue  # the same form sent again (a double tap, or Back then Save)
                block = WorkBlock(user_id=current_user.id, date=day)
                db.session.add(block)
                blocks.append(block)
            block.start_time, block.end_time, block.text = start, end, text
            saved[index] = block
            changed.append((start, end, "Saved"))
            if to_logout == str(index):
                ended = block

        if to_copy or to_uncopy:
            # Copied (or its mark reset) on a line with changes not saved yet:
            # the page is saved, and the mark goes on the line as saved.
            index = int(to_copy or to_uncopy)
            block = saved.get(index) or by_id.get(fields[0][index])
            if block is not None and block.text and block not in db.session.deleted:
                block.copied = block.copy_mark() if to_copy else None

        acted = None
        for action, index in (("split", to_split), ("duplicate", to_duplicate)):
            block = (saved.get(int(index)) or by_id.get(fields[0][int(index)])) if index else None
            if block is not None and block.text and block not in db.session.deleted:
                acted = apply_line_action(block, action, now)

        removed = 0
        if to_logout and not any(d.error for d in drafts):
            # ...and the lines after it go: the day ends there.
            index = int(to_logout)
            keep = ended or by_id.get(fields[0][index])
            begin = clock(fields[1][index])
            for b in list(blocks):
                if b is keep or not b.text or b in db.session.deleted or begin is None \
                        or minutes(b.start_time) < minutes(begin):
                    continue
                if b in db.session.new:
                    db.session.expunge(b)
                else:
                    db.session.delete(b)
                removed += 1

        bad = [d for d in drafts if d.error]
        if autosave:
            db.session.commit()
            lines = []
            for index, draft in enumerate(drafts):
                block = saved.get(index)
                if block is not None:
                    lines.append({"id": str(block.id), "was_start": block.start_time.strftime("%H:%M"),
                                  "was_end": block.end_time.strftime("%H:%M"), "was_text": block.text})
                elif dropped[index] == "1":
                    lines.append({"removed": True})
                elif index in cleared or index in vanished:
                    # No words now: an ordinary empty line, at the times it shows.
                    lines.append({"id": "", "was_start": draft.start, "was_end": draft.end, "was_text": ""})
                elif draft.error:
                    lines.append({"error": draft.error, "error_on": draft.error_on})
                else:
                    lines.append(None)  # nothing changed
            return jsonify(ok=not bad, saved_at=logic.fmt_time(datetime.now().time()), lines=lines)
        if bad:
            db.session.rollback()
            flash("Nothing was saved yet. " + ("Fix the line marked below" if len(bad) == 1
                                               else f"Fix the {len(bad)} lines marked below") + ", then save again.",
                  "error")
            return show(drafts)
        db.session.commit()

        def span(start, end):
            return logic.fmt_span(minutes(start), minutes(end, ending=True))

        def count(n):
            return f"{n} line" + ("" if n == 1 else "s")

        if to_logout:
            flash(f"Ended the line at your logout, {logic.fmt_time(entry.logout_time)}"
                  + (f", and removed {count(removed)} after it." if removed else "."), "success")
        elif len(changed) == 1:
            start, end, verb = changed[0]
            flash(f"{verb} {span(start, end)}.", "success")
        elif changed:
            tally = [(verb, sum(1 for *_, v in changed if v == verb))
                     for verb in ("Saved", "Cleared", "Deleted", "Removed")]
            words = [f"{verb if not i else verb.lower()} {count(n)}" for i, (verb, n) in
                     enumerate((verb, n) for verb, n in tally if n)]
            flash(", ".join(words) + ".", "success")
        elif not (form.get("add") or gone or to_copy or to_uncopy or acted):
            flash("Already saved.", "success")
        if acted:
            flash(acted[1], "success" if acted[0] else "error")
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
                          and minutes(clock(was_start)) >= last)
            day_args["add"] = min(24, waiting + 1)
        return redirect(url_for("main.timesheet", **day_args))

    lines = logic.timesheet_lines(entry, day, now, blocks)
    # Past midnight on a shift that runs on past it, lines can be added as
    # far as its morning reaches.
    limit = 1440 + (night or 0)
    for _ in range(min(24, request.args.get("add", default=0, type=int))):
        # "Add a line": an hour straight after the last line -- or, once the
        # day runs to midnight, the hour just before the first.
        last = max((end for _, end, _ in lines), default=logic.timesheet_span(entry, day, now)[0])
        first = min((start for start, _, _ in lines), default=0)
        if last < limit:
            lines.append((last, min(limit, last + 60), None))
        elif first > 0:
            lines.insert(0, (max(0, first - 60), first, None))
        else:
            flash("This day's lines already run from midnight to midnight. Change a line's times to "
                  "make room.", "warning")
            break
    return show(day_views(lines))


@main_bp.route("/timesheet/mark", methods=["POST"])
@login_required
def timesheet_mark():
    """Copy's mark on a saved line, put on or taken off without reloading the
    page. It goes on only while the line still says what was copied."""
    form = request.form
    block_id = form.get("id", "")
    block = None
    if block_id.isascii() and block_id.isdigit() and len(block_id) < 19:
        block = WorkBlock.query.filter_by(id=int(block_id), user_id=current_user.id).first()
    if block is None or not block.text:
        abort(404)
    if form.get("mark") == "1":
        said = f"{form.get('start', '')[:5]}-{form.get('end', '')[:5]} {' '.join(form.get('text', '').split())}"
        if said != block.copy_mark():
            return jsonify(copied=False)  # changed since, here or elsewhere: save first
        block.copied = said
    else:
        block.copied = None
    db.session.commit()
    return jsonify(copied=block.is_copied)


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
            history=changes.day_history(current_user.id, entry_date),
            source_words=changes.SOURCE_WORDS,
            fmt_time=logic.fmt_time,
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


# -------------------------------------------------------------------- pay

def pay_months(user, now):
    """Every month from the first salary to this one with hours in STM,
    newest first: what each pays (logic.month_pay), its hours, and what the
    payslip said, where that's been typed in."""
    if not user.salaries:
        return []
    today = work_day(user.id, now)
    first = min(s.starts for s in user.salaries)
    by_month = defaultdict(dict)
    for d, entry in get_entries_for_range(user.id, first, today).items():
        by_month[(d.year, d.month)][d] = entry
    paid = {r.month: r.paid for r in user.pay_records}
    adjustments = adjustments_between(user.id, first, today)
    months = []
    for year, month in sorted(by_month, reverse=True):
        summary = logic.adjusted_month_summary(user, by_month[(year, month)], year, month, today, adjustments,
                                               now=now)
        pay = pay_for(user, year, month, summary["balance_hours"])
        if pay is None:
            continue
        first_day = date(year, month, 1)
        pay.update(
            first=first_day,
            key=first_day.strftime("%Y-%m"),
            worked_hours=summary["worked_hours"],
            is_current=(year, month) == (today.year, today.month),
            paid=paid.get(first_day),
        )
        pay["gap"] = None if pay["paid"] is None else pay["paid"] - pay["total"]
        months.append(pay)
    return months


def pay_totals(months):
    """All the months together; paid and its gap only over the months whose
    payslip has been typed in."""
    with_paid = [m for m in months if m["paid"] is not None]
    return {
        "overtime_hours": sum(m["overtime_hours"] for m in months),
        "overtime_pay": sum(m["overtime_pay"] for m in months),
        "total": sum(m["total"] for m in months),
        "paid": sum(m["paid"] for m in with_paid) if with_paid else None,
        "gap": sum(m["gap"] for m in with_paid) if with_paid else None,
    }


def pay_tone(gap):
    """How a payslip compares with STM's estimate: paid more, paid less, or
    no payslip (or the same to the rupee)."""
    if gap is None or round(gap) == 0:
        return "is-none" if gap is None else "is-even"
    return "is-over" if gap > 0 else "is-under"


@main_bp.route("/pay", methods=["GET", "POST"])
@login_required
def pay():
    """Each month's salary with its overtime paid at the month's hourly
    rate, beside what the payslip really said. The Paid boxes save as
    they're typed in (autosave=1, answered in JSON with the differences and
    totals worked out again); without script, the form posts as usual."""
    user = current_user
    if request.method == "POST":
        records = {r.month: r for r in user.pay_records}
        bad = set()
        for key, value in request.form.items():
            if not key.startswith("paid_"):
                continue
            try:
                month = datetime.strptime(key[5:], "%Y-%m").date()
            except ValueError:
                continue
            value = value.replace(",", "").strip()
            record = records.get(month)
            if not value:
                if record is not None:
                    db.session.delete(record)
                continue
            try:
                amount = float(value)
            except ValueError:
                amount = float("nan")
            if not (math.isfinite(amount) and 0 <= amount < 1e9):
                bad.add(key[5:])
                continue
            if record is None:
                db.session.add(PayRecord(user_id=user.id, month=month, paid=amount))
            else:
                record.paid = amount
        db.session.commit()
        if request.form.get("autosave") == "1":
            months = pay_months(user, datetime.now())
            totals = pay_totals(months)

            def gap_view(gap):
                return {"difference": "\u2013" if gap is None else logic.fmt_money(gap, user.pay_symbol, signed=True),
                        "tone": pay_tone(gap)}

            return jsonify(
                ok=not bad,
                saved_at=logic.fmt_time(datetime.now().time()),
                rows={m["key"]: dict(gap_view(m["gap"]), bad=m["key"] in bad,
                                     paid="" if m["paid"] is None else logic.fmt_money(m["paid"], user.pay_symbol))
                      for m in months},
                totals=dict(gap_view(totals["gap"]),
                            paid="\u2013" if totals["paid"] is None else logic.fmt_money(totals["paid"], user.pay_symbol)),
            )
        flash("Type amounts as numbers, like 23848." if bad else "Saved.", "error" if bad else "success")
        return redirect(url_for("main.pay"))

    months = pay_months(user, datetime.now())
    return render_template("pay.html", months=months, totals=pay_totals(months),
                           headline=months[0] if months else None,
                           pay_tone=pay_tone, fmt_hours=logic.fmt_hours)


# --------------------------------------------------------------- settings

@main_bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user = current_user

    if request.method == "POST":
        form_type = request.form.get("form_type")
        # The profile pop-up posts here from any page, and goes back to it.
        back = local_path(request.form.get("next")) or url_for("main.settings")

        if form_type == "hours":
            # Each day's standard hours, Mon-Sun, from a date on.
            try:
                values = [float(request.form.get(f"hours_{i}", "")) for i in range(7)]
                starts = date.fromisoformat(request.form.get("starts", ""))
            except ValueError:
                values = starts = None
            if values is None or not all(math.isfinite(v) and 0 <= v <= 24 for v in values):
                flash("Enter each day's hours as a number from 0 to 24.", "error")
                return redirect(url_for("main.settings") + "#targets")
            if not 2000 <= starts.year <= 2100:
                flash("Choose the date these hours start from.", "error")
                return redirect(url_for("main.settings") + "#targets")
            plan = HoursPlan.query.filter_by(user_id=user.id, starts=starts).first()
            if plan is None:
                db.session.add(HoursPlan(user_id=user.id, starts=starts, hours=HoursPlan.pack(values)))
            else:
                plan.hours = HoursPlan.pack(values)
            db.session.commit()
            flash(f"Hours saved: {logic.fmt_target_hours(sum(values))} a week from {starts:%a %d %b %Y}.", "success")
            return redirect(url_for("main.settings") + "#targets")

        elif form_type == "hours_remove":
            plan = HoursPlan.query.filter_by(id=request.form.get("plan_id", type=int), user_id=user.id).first()
            if plan is not None:
                label = f"the hours from {plan.starts:%a %d %b %Y}"
                db.session.delete(plan)
                db.session.commit()
                flash(f"Removed {label}.", "success")
            return redirect(url_for("main.settings") + "#targets")

        elif form_type == "targets":
            # The old daily and weekly targets and workdays, only when a form
            # still sends them; Settings now sets each day's hours instead.
            try:
                daily = float(request.form.get("daily_target_hours", user.daily_target_hours))
                weekly = float(request.form.get("weekly_target_hours", user.weekly_target_hours))
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
            if "workdays" in request.form or "daily_target_hours" in request.form:
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
                    return redirect(back)
                user.email = email

            if request.form.get("remove_avatar"):
                delete_avatar_file(user.avatar_filename)
                user.avatar_filename = None
                db.session.commit()
                flash("Profile picture removed.", "success")
                return redirect(back)

            upload = request.files.get("avatar")
            if upload and upload.filename:
                stored_name, error = save_avatar(upload)
                if error:
                    flash(error, "error")
                    return redirect(back)
                # Only drop the old file once the new one is safely written.
                delete_avatar_file(user.avatar_filename)
                user.avatar_filename = stored_name

            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(back)

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
            return redirect(back)

        elif form_type == "salary":
            currency = request.form.get("currency")
            if currency in User.PAY_CURRENCIES:
                user.pay_currency = currency
            raw = (request.form.get("amount") or "").replace(",", "").strip()
            if not raw:
                db.session.commit()
                if currency in User.PAY_CURRENCIES:
                    flash("Currency saved.", "success")
                else:
                    flash("Enter your monthly salary.", "error")
                return redirect(url_for("main.settings") + "#pay")
            try:
                amount = float(raw)
                starts = date(int(request.form.get("year", "")), int(request.form.get("month", "")), 1)
            except ValueError:
                amount = starts = None
            if (amount is None or not (math.isfinite(amount) and 0 < amount < 1e9)
                    or not 2000 <= starts.year <= 2100):
                flash("Enter your monthly salary as a number.", "error")
                return redirect(url_for("main.settings") + "#pay")
            salary = Salary.query.filter_by(user_id=user.id, starts=starts).first()
            if salary is None:
                db.session.add(Salary(user_id=user.id, starts=starts, amount=amount))
            else:
                salary.amount = amount
            db.session.commit()
            flash(f"Salary saved: {logic.fmt_money(amount, user.pay_symbol)} from {starts:%B %Y}.", "success")
            return redirect(url_for("main.settings") + "#pay")

        elif form_type == "reminders":
            saved = reminders.settings_for(user)
            for key in reminders.DEFAULTS:
                saved[key]["on"] = request.form.get(f"{key}_on") == "1"
            at = parse_time_field(request.form.get("no_login_at"))
            if at is not None:
                saved["no_login"]["at"] = at.hour * 60 + at.minute
            for key, field, kind in (("long_day", "hours", float), ("idle", "minutes", int)):
                try:
                    value = kind(request.form.get(f"{key}_{field}", ""))
                except ValueError:
                    continue
                low, high = reminders.LIMITS[field]
                if math.isfinite(value) and low <= value <= high:
                    saved[key][field] = value
            user.reminder_settings = json.dumps(saved)
            db.session.commit()
            flash("Reminders saved.", "success")
            return redirect(url_for("main.settings") + "#reminders")

        elif form_type == "emails":
            user.weekly_report = request.form.get("weekly_report") == "1"
            db.session.commit()
            flash("Weekly email " + ("on." if user.weekly_report else "off."), "success")
            return redirect(url_for("main.settings") + "#emails")

        elif form_type == "goals":
            on = set(request.form.getlist("on"))
            saved = {g.kind: g for g in user.goals}
            values = {kind: goal_value(kind, request.form.get(kind)) for kind in Goal.KINDS}
            bad = [Goal.KINDS[kind] for kind in Goal.KINDS if kind in on and values[kind] is None]
            if bad:
                flash(f"Check the value for {' and '.join(bad)}.", "error")
                return redirect(url_for("main.settings") + "#goals")
            for kind in Goal.KINDS:
                goal = saved.get(kind)
                if goal is None and kind not in on:
                    continue
                if goal is None:
                    goal = Goal(user_id=user.id, kind=kind)
                    db.session.add(goal)
                if values[kind] is not None:
                    goal.value = values[kind]
                goal.active = kind in on
            db.session.commit()
            flash("Goals saved.", "success")
            return redirect(url_for("main.settings") + "#goals")

        elif form_type == "salary_remove":
            salary = Salary.query.filter_by(
                id=request.form.get("salary_id", type=int), user_id=user.id
            ).first()
            if salary is not None:
                label = f"{logic.fmt_money(salary.amount, user.pay_symbol)} from {salary.starts:%B %Y}"
                db.session.delete(salary)
                db.session.commit()
                flash(f"Removed {label}.", "success")
            return redirect(url_for("main.settings") + "#pay")

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
    today = date.today()
    return render_template(
        "settings.html", user=user, all_users=all_users,
        month_names=list(calendar.month_name)[1:],
        years=range(today.year + 1, today.year - 6, -1),
        goal_rows=goal_rows(user),
        mail_ready=mailer.is_configured(),
        reminder=reminders.settings_for(user), reminder_names=reminders.NAMES,
        holidays_this_year=sum(1 for h in user.holidays if h.date.year == today.year),
        fmt_clock_minutes=lambda m: logic.minutes_clock(int(m)).strftime("%H:%M"),
    )
