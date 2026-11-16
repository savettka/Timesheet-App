"""The weekly report email: the week as the firm's own weekly summary lays
it out -- working days (only days with time worked), time worked and on
breaks, a productivity rate, and each day's hours and breaks -- with the
week against its target. Nothing from the Timesheet's lines.

It goes 15 minutes after the logout on the week's last workday (Saturday,
as Settings has it). There's no scheduler on the free PythonAnywhere plan,
so STM sends it itself: an open page asks /api/reminders, which answers
with how long until it's due, and the page asks again then -- or, with no
page open, the next time one is. ReminderLog keeps it to once a week.
"""

from datetime import datetime, time, timedelta

from flask import render_template, url_for
from sqlalchemy.exc import IntegrityError

from app import db, logic, mailer
from app.models import ReminderLog, TimeEntry

KIND = "weekly_report"
DELAY = timedelta(minutes=15)    # after the last workday's logout
RETRY = timedelta(minutes=15)    # after a failed send
STUCK = timedelta(minutes=10)    # a send that never said how it went
MOST_TRIES = 4
WORKED_DAY = 1  # minutes: less than this worked isn't a working day


def clock(seconds):
    """A length as the firm's report writes it: 16:32:59."""
    seconds = max(0, int(round(seconds)))
    h, rest = divmod(seconds, 3600)
    return f"{h:02d}:{rest // 60:02d}:{rest % 60:02d}"


def productivity(worked_hours, days_worked, daily_target):
    """Hours worked against the days worked at the daily target, as a
    percentage -- the firm's rate: 16:32:59 over two 8h days is 103.44%.
    None with no day worked."""
    expected = days_worked * daily_target
    return worked_hours / expected * 100 if expected > 0 else None


def build_report(user, monday, now):
    """Everything the week's email says, from Monday to Sunday: the days
    worked -- a day with no time worked (logged in by mistake) is neither
    counted nor shown -- with their hours and breaks, and leave."""
    days = logic.week_dates(monday)
    entries = {e.date: e for e in TimeEntry.query.filter(
        TimeEntry.user_id == user.id, TimeEntry.date >= days[0], TimeEntry.date <= days[-1])}

    rows, worked_total, break_total, days_worked, open_days = [], 0.0, 0.0, 0, []
    target = user.weekly_target_hours
    for d in days:
        entry = entries.get(d)
        if entry is not None and entry.target_override is not None:
            target += logic.target_hours_for(user, d, entry) - logic.entry_target_hours(user, d)
        logged = entry is not None and entry.login_time is not None
        leave = (entry.leave_label or "Leave") if entry is not None and entry.target_override is not None else None
        worked = logic.entry_total_hours(entry, now=now) if logged else 0.0
        if logged and logic.is_stale_shift(entry, now):
            open_days.append(f"{d:%a %d %b}")
        if worked * 60 < WORKED_DAY:
            if leave:
                rows.append({"date": d, "label": f"{d:%A, %d %b %Y}", "worked": clock(0), "breaks": clock(0),
                             "span": "", "leave": leave})
            continue
        breaks = logic.entry_break_hours(entry, now=now)
        worked_total += worked
        break_total += breaks
        days_worked += 1
        until = logic.fmt_time(entry.logout_time) if entry.logout_time else "now"
        rows.append({
            "date": d, "label": f"{d:%A, %d %b %Y}", "worked": clock(worked * 3600), "breaks": clock(breaks * 3600),
            "span": f"{logic.fmt_time(entry.login_time)} – {until}", "leave": leave,
        })

    target = max(0.0, target)
    balance = worked_total - target
    rate = productivity(worked_total, days_worked, user.daily_target_hours)
    last = days[-1]
    salary = logic.salary_in(user.salaries, last.year, last.month)
    pay = logic.month_pay(user, salary, last.year, last.month, balance) if salary else None
    name = (user.display_name or user.username or "").split()
    return {
        "name": name[0] if name else "",
        "first": days[0], "last": days[-1],
        "days_worked": days_worked,
        "worked": clock(worked_total * 3600), "breaks": clock(break_total * 3600),
        "productivity": f"{rate:.2f}%" if rate is not None else "–",
        "target": logic.fmt_target_hours(target), "balance": logic.fmt_balance(balance),
        "ahead": round(balance * 60) >= 0,
        "overtime_pay": logic.fmt_money(pay["overtime_pay"], user.pay_symbol) if pay and pay["overtime_pay"] else None,
        "rows": rows, "open_days": open_days,
        "has_work": days_worked > 0,
    }


def subject(report):
    return f"Weekly work summary · {report['first']:%d %b} – {report['last']:%d %b %Y}"


def render_html(report):
    return render_template("email/weekly_report.html", r=report, link=url_for("main.reports", _external=True))


def render_text(report):
    r = report
    out = [subject(r), "",
           f"Working days: {r['days_worked']} day{'s' if r['days_worked'] != 1 else ''}",
           f"Total working time: {r['worked']}",
           f"Total break time: {r['breaks']}",
           f"Productivity: {r['productivity']}",
           f"Against {r['target']}: {r['balance']}"
           + (f" · overtime pay {r['overtime_pay']}" if r["overtime_pay"] else ""), ""]
    for row in r["rows"]:
        out.append(f"{row['label']}  {row['leave'] or row['worked']}")
        if row["span"]:
            out.append(f"  {row['span']} · Break {row['breaks']}")
        out.append("")
    if r["open_days"]:
        out.append("To fix:")
        out += [f"  {day}: no logout" for day in r["open_days"]]
    return "\n".join(out) + "\n"


def send(user, report):
    return mailer.send_email(user.email, subject(report), render_text(report), render_html(report))


# ------------------------------------------------------------- when

def last_workday(user, monday):
    work = user.workday_set()
    return monday + timedelta(days=max(work)) if work else None


def due_at(user, monday, now):
    """When a week's report is due: 15 minutes after the logout on its last
    workday -- or, with no logout that day (not worked, on leave, a logout
    never put in), the midnight after it. None while that day is being
    worked, or still to come."""
    last = last_workday(user, monday)
    if last is None or last > now.date():
        return None
    entry = TimeEntry.query.filter_by(user_id=user.id, date=last).first()
    if entry is not None and entry.login_time is not None:
        if entry.logout_time is not None:
            out = datetime.combine(last, entry.logout_time)
            if entry.logout_time < entry.login_time:
                out += timedelta(days=1)  # out after midnight
            return out + DELAY
        if not logic.is_stale_shift(entry, now):
            return None
    return datetime.combine(last + timedelta(days=1), time())


def pending(user, now):
    """The week whose report is next: the latest one whose last workday has
    come, unless its report has gone (or is going, or gave up). Returns
    (monday, its log or None), or None."""
    this = logic.week_bounds(now.date())[0]
    for monday in (this, this - timedelta(days=7)):
        last = last_workday(user, monday)
        if last is None or last > now.date():
            continue
        log = ReminderLog.query.filter_by(user_id=user.id, kind=KIND, period=monday).first()
        if log is None:
            return monday, None
        if log.status == "failed" and log.attempts < MOST_TRIES:
            return monday, log
        if log.status == "sending" and now - log.tried_at > STUCK:
            return monday, log
        return None
    return None


def run(user, now):
    """Send the week's report if it's due. Returns how many seconds until it
    will be (for the page to ask again then), or None: nothing coming, or
    done."""
    if not user.weekly_report or not user.email or not mailer.is_configured():
        return None
    found = pending(user, now)
    if found is None:
        return None
    monday, log = found
    at = due_at(user, monday, now)
    if at is None:
        return None
    if log is not None and log.status == "failed":
        at = max(at, log.tried_at + RETRY)
    if at > now:
        return (at - now).total_seconds()

    # Claimed before sending, so two pages asking at once send it once.
    if log is None:
        log = ReminderLog(user_id=user.id, kind=KIND, period=monday, status="sending", attempts=0, tried_at=now)
        db.session.add(log)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            return None
    else:
        claimed = (ReminderLog.query.filter_by(id=log.id, status=log.status, attempts=log.attempts)
                   .update({"status": "sending", "tried_at": now}))
        db.session.commit()
        if not claimed:
            return None

    report = build_report(user, monday, now)
    if not report["has_work"]:
        log.status = "skipped"  # nothing to report: no email
        db.session.commit()
        return None
    sent = send(user, report)
    log.attempts += 1
    log.status = "sent" if sent else "failed"
    log.tried_at = now
    db.session.commit()
    return None if sent or log.attempts >= MOST_TRIES else RETRY.total_seconds()
