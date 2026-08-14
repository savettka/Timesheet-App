import calendar
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app import logic
from app.models import BreakSegment, TimeEntry

main_bp = Blueprint("main", __name__)


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
    today_target = logic.entry_target_hours(user, date.today())
    today_worked = logic.entry_total_hours(today_entry, now=now) if today_entry else 0.0

    suggestion = None
    if open_entry:
        worked_today_in_week = logic.entry_total_hours(open_entry, now=now)
        weekly_before_today = weekly["worked_hours"] - worked_today_in_week
        reached, suggested_dt, still_needed = logic.suggested_logout(
            open_entry, weekly_before_today, user.weekly_target_hours, now=now
        )
        suggestion = {
            "reached": reached,
            "suggested_time": suggested_dt.strftime("%H:%M") if suggested_dt else None,
            "still_needed_hours": still_needed,
        }

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
        # once the day has actually arrived (matches how the weekly target works).
        target = 0.0 if is_future else logic.entry_target_hours(current_user, d)
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

    if request.method == "POST":
        login_t = parse_time_field(request.form.get("login_time"))
        logout_t = parse_time_field(request.form.get("logout_time"))
        notes = (request.form.get("notes") or "").strip() or None

        break_starts = request.form.getlist("break_start")
        break_ends = request.form.getlist("break_end")

        if entry is None:
            entry = TimeEntry(user_id=current_user.id, date=entry_date)
            db.session.add(entry)

        entry.login_time = login_t
        entry.logout_time = logout_t
        entry.notes = notes

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

    return render_template("entry_form.html", entry=entry, entry_date=entry_date)


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

            workdays = request.form.getlist("workdays")
            user.daily_target_hours = max(0.0, daily)
            user.weekly_target_hours = max(0.0, weekly)
            user.workdays = ",".join(sorted(set(workdays))) if workdays else ""
            db.session.commit()
            flash("Targets updated.", "success")

        elif form_type == "profile":
            display_name = (request.form.get("display_name") or "").strip()
            user.display_name = display_name or user.username
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

        return redirect(url_for("main.settings"))

    return render_template("settings.html", user=user)
