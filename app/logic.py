"""
Core time-tracking calculations for STM.

Mirrors the rules from the user's original spreadsheet:
  - "Total hours" for a day = (logout - login) - actual break time taken,
    with overnight shifts (logout time-of-day earlier than login) rolling
    into the next day.
  - Standard/target hours default to 8h on workdays (Mon-Sat) and 0h on
    the rest day (Sun), for a 48h weekly target -- both configurable per
    user in Settings.
  - The week runs Monday -> Sunday. Once the running weekly total reaches
    the weekly target, no more hours are owed for that week (e.g. an
    early Saturday finish).
"""

import calendar
from datetime import date, datetime, time, timedelta

# A typed time may run a little ahead of the server's clock without being in
# the future in any sense that matters.
PUNCH_GRACE = timedelta(minutes=2)
# Past this, a shift still open is taken to be a forgotten logout rather than
# a long day, and the dashboard asks when it really ended.
STALE_SHIFT_HOURS = 16
# The longest shift the dashboard will close on its own. Longer than this
# nearly always means a missed logout, so the real time has to be given.
MAX_LIVE_SHIFT_HOURS = 20
# A saved day longer than this gets a heads-up: usually an AM/PM slip.
LONG_SHIFT_WARNING_HOURS = 16
# The Timesheet's span for a day with no login to go by, in minutes from
# midnight: 1 PM to 10 PM.
TIMESHEET_DAY = (13 * 60, 22 * 60)
# A Timesheet line is an hour, o'clock to o'clock, except at the ends: a
# piece this short or shorter joins the hour beside it, so a 12:47 login
# gives a first line of 12:47 to 2:00 PM rather than a 13-minute one, and a
# break at 8:30 PM leaves 7:00 to 8:30 PM before it.
TIMESHEET_SHORT_PIECE = 30
# The block of a line that is a recorded break, rather than None (time to
# fill in) or a saved WorkBlock.
TIMESHEET_BREAK = "break"
# Time left uncovered beside a saved line, shorter than this, isn't offered
# as a line of its own. Time between punches always is.
TIMESHEET_SMALL_GAP = 10


def _combine(date_, time_, reference_dt=None):
    """Combine a date and a time-of-day into a datetime, rolling forward
    a day if the result would fall before ``reference_dt`` (handles shifts
    and breaks that cross midnight)."""
    dt = datetime.combine(date_, time_)
    if reference_dt is not None and dt < reference_dt:
        dt += timedelta(days=1)
    return dt


def is_open_shift(entry):
    return entry is not None and entry.login_time is not None and entry.logout_time is None


def is_stale_shift(entry, now=None):
    """An open shift that has run on so long it must be a forgotten logout."""
    if not is_open_shift(entry):
        return False
    now = now or datetime.now()
    login_dt = datetime.combine(entry.date, entry.login_time)
    return now - login_dt > timedelta(hours=STALE_SHIFT_HOURS)


def work_day(open_entry, now):
    """The day being worked: the date -- or, past midnight on a shift begun
    the day before and still open, that shift's day, until its logout. A
    3 PM to 1 AM Friday stays Friday on every page through to 1 AM."""
    if is_open_shift(open_entry) and open_entry.date < now.date() and not is_stale_shift(open_entry, now):
        return open_entry.date
    return now.date()


def day_minute(day, now):
    """Minutes into ``day`` at ``now``: 1,470 at 00:30 the morning after."""
    return int((now - datetime.combine(day, time())).total_seconds() // 60)


def break_segment_hours(entry, segment, now=None):
    """Hours for a single break segment. If the break is still open,
    counts up to ``now`` (or the current time). A break starting at a time
    of day before the login was taken after midnight, on an overnight shift."""
    if segment.break_start is None:
        return 0.0
    start_dt = place_on_shift(entry.date, segment.break_start,
                              shift_login_dt(entry) if entry.login_time is not None else None)
    if segment.break_end is not None:
        end_dt = _combine(entry.date, segment.break_end, reference_dt=start_dt)
    else:
        end_dt = now or datetime.now()
        if end_dt < start_dt:
            return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds() / 3600.0)


def entry_break_hours(entry, now=None):
    return sum(break_segment_hours(entry, b, now=now) for b in entry.breaks)


def closed_break_hours(entry):
    """Sum of only the completed (non-open) break segments."""
    return sum(
        break_segment_hours(entry, b) for b in entry.breaks if b.break_end is not None
    )


def entry_raw_hours(entry, now=None):
    """Login-to-logout span, ignoring breaks. Live (uses ``now``) if the
    entry is still open."""
    if entry.login_time is None:
        return 0.0
    login_dt = datetime.combine(entry.date, entry.login_time)
    if entry.logout_time is not None:
        logout_dt = _combine(entry.date, entry.logout_time, reference_dt=login_dt)
    else:
        logout_dt = now or datetime.now()
        if logout_dt < login_dt:
            return 0.0
        # Left open this long, it's a forgotten logout and its real length is
        # unknown -- so it counts for nothing until the logout time is put in,
        # rather than as a 30-hour day that swamps the week and the month.
        if logout_dt - login_dt > timedelta(hours=STALE_SHIFT_HOURS):
            return 0.0
    return max(0.0, (logout_dt - login_dt).total_seconds() / 3600.0)


def entry_total_hours(entry, now=None):
    """Total worked hours for the day, net of breaks (never negative)."""
    raw = entry_raw_hours(entry, now=now)
    brk = entry_break_hours(entry, now=now)
    return max(0.0, raw - brk)


def unrecorded_break_hours(user, entry_date, entry=None, now=None):
    """How much of the day's usual break is still to come.

    A clock-out suggestion has to allow for the break that will be taken but
    hasn't been logged yet, or it lands an hour early. Break time already
    recorded counts against the allowance, so the suggestion doesn't jump
    later the moment a real break is logged.
    """
    # A leave day carries no assumed break: a half day is worked straight
    # through, and a full day off isn't worked at all. Same for any day whose
    # target has been zeroed, which covers holidays recorded before leave
    # types existed.
    if entry is not None and (entry.is_leave or entry.target_override == 0):
        return 0.0
    expected = user.expected_break_hours_for(entry_date.weekday())
    taken = entry_break_hours(entry, now=now) if entry is not None else 0.0
    return max(0.0, expected - taken)


def entry_target_hours(user, entry_date):
    """The default, weekday-based target for a date (ignores any override)."""
    return user.target_hours_for_weekday(entry_date.weekday())


def target_hours_for(user, entry_date, entry=None):
    """The effective target for a date: an explicit override on the entry
    (e.g. 0 for a day off, half the daily target for a half day) if one is
    set, otherwise the normal weekday-based default."""
    if entry is not None and entry.target_override is not None:
        return entry.target_override
    return entry_target_hours(user, entry_date)


def accrued_target_hours(full_target, entry, entry_date, today, now=None):
    """How much of a day's target has actually fallen due yet.

    Charging a day its whole target from midnight makes an ordinary
    lunchtime look like a six-hour deficit -- those hours aren't missing,
    they just haven't come round yet. So while today is still running only
    the hours worked so far count against it: the balance sits at zero
    through the day and starts climbing the moment real overtime begins,
    which is the figure actually worth watching.

    Once the day has been logged out of -- and for every day already past
    -- the full target applies again, because a short day that has *ended*
    really is time owed. A day not reached yet owes nothing, even with leave
    booked on it: that shows as the day's target, but it can't put the
    month behind before the day arrives.
    """
    if entry_date > today:
        return 0.0
    if is_open_shift(entry) and not is_stale_shift(entry, now):
        # Still being worked -- today, or an overnight shift carrying on from
        # yesterday, which is just as unfinished.
        return min(full_target, entry_total_hours(entry, now=now))
    if entry_date < today:
        return full_target
    if entry is not None and (entry.logout_time is not None or is_stale_shift(entry, now)):
        return full_target
    # Today with nothing recorded yet, or leave booked with no login: none of
    # the day has come round.
    worked = entry_total_hours(entry, now=now) if entry is not None else 0.0
    return min(full_target, worked)


def month_summary(user, entries_by_date, year, month, today, now=None):
    """Every day of a month -- what it owes, what was worked -- and the
    month's totals.

    History and the dashboard both read the month from here, so the balance
    in one place is always the balance in the other.
    """
    first_day = date(year, month, 1)
    rows = []
    for i in range(calendar.monthrange(year, month)[1]):
        d = first_day + timedelta(days=i)
        entry = entries_by_date.get(d)
        is_future = d > today
        # A day you haven't reached yet can't owe hours. Leave booked ahead
        # still shows its target, since it was set on purpose.
        if entry is not None:
            target = target_hours_for(user, d, entry)
        elif is_future:
            target = 0.0
        else:
            target = entry_target_hours(user, d)
        worked = entry_total_hours(entry, now=now) if entry else 0.0
        # Today is only part-way through, so only the slice of its target
        # that has already come round counts against the balance.
        accrued = accrued_target_hours(target, entry, d, today, now=now)
        rows.append(
            {
                "date": d,
                "entry": entry,
                "target_hours": target,
                "accrued_target_hours": accrued,
                "worked_hours": worked,
                "balance_hours": worked - accrued,
                "is_future": is_future,
                "is_today": d == today,
                "in_progress": not is_future and accrued < target,
                # Its hours count for nothing until the real logout is given.
                "needs_logout": is_stale_shift(entry, now),
            }
        )

    worked_total = sum(r["worked_hours"] for r in rows)
    target_total = sum(r["accrued_target_hours"] for r in rows)
    return {
        "rows": rows,
        "worked_hours": worked_total,
        "target_hours": target_total,
        "balance_hours": worked_total - target_total,
        "today_in_progress": any(r["in_progress"] for r in rows),
    }


def adjusted_month_summary(user, entries_by_date, year, month, today, adjustments, now=None):
    """month_summary, with the month's balance adjustments -- hours added or
    taken off by hand, each with a date, hours and a reason -- counted in
    its balance. The days stay as they were; the adjustments are listed
    beside them, and month_summary itself is unchanged."""
    summary = dict(month_summary(user, entries_by_date, year, month, today, now=now))
    mine = sorted((a for a in adjustments if (a.date.year, a.date.month) == (year, month)),
                  key=lambda a: (a.date, a.id or 0))
    summary["adjustments"] = mine
    summary["adjusted_hours"] = sum(a.hours for a in mine)
    summary["unadjusted_balance_hours"] = summary["balance_hours"]
    summary["balance_hours"] += summary["adjusted_hours"]
    return summary


def month_workdays(user, year, month):
    """How many of the month's days are workdays in Settings: Mon-Sat gives
    26 in September 2026. Leave days count -- they're paid."""
    work = user.workday_set()
    return sum(1 for d in range(1, calendar.monthrange(year, month)[1] + 1)
               if date(year, month, d).weekday() in work)


def salary_in(salaries, year, month):
    """The salary a month is paid on: the latest one starting on or before
    its 1st. None before the first salary."""
    first = date(year, month, 1)
    started = [s for s in salaries if s.starts <= first]
    return max(started, key=lambda s: s.starts).amount if started else None


def month_pay(user, salary, year, month, balance_hours):
    """A month's pay as STM estimates it: the salary over the month's working
    hours (its workdays times the daily target) gives an hourly rate, and
    the month's overtime -- its balance, as Calendar shows it -- is paid at
    that rate. A month behind earns no overtime. None without a salary."""
    if not salary:
        return None
    workdays = month_workdays(user, year, month)
    hours = workdays * user.daily_target_hours
    if hours <= 0:
        return None
    rate = salary / hours
    overtime = max(0, round(balance_hours * 60)) / 60
    return {
        "salary": salary,
        "workdays": workdays,
        "rate": rate,
        "balance_hours": balance_hours,
        "overtime_hours": overtime,
        "overtime_pay": overtime * rate,
        "total": salary + overtime * rate,
    }


def week_bounds(any_date):
    start = any_date - timedelta(days=any_date.weekday())  # Monday
    end = start + timedelta(days=6)  # Sunday
    return start, end


def week_dates(any_date):
    start, _ = week_bounds(any_date)
    return [start + timedelta(days=i) for i in range(7)]


def week_target_hours(user, entries_by_date, week_start, year, month):
    """The weekly target scaled to just the days of this week that belong to
    the given month.

    ``user.weekly_target_hours`` assumes a whole Mon-Sun week, and two things
    move it. A day-off/half-day override shifts it by exactly the difference
    from that day's usual target, so a full day of leave on an 8h day relieves
    8h from what's owed. And when the week straddles a month change, a day
    belonging to the other month drops out entirely, taking its usual target
    with it -- each month's part-week only owes its own days.

    This is the single place that split is worked out, so the weekly card and
    the Saturday plan can never disagree about what the week owes.
    """
    total = user.weekly_target_hours
    for d in week_dates(week_start):
        entry = entries_by_date.get(d)
        default_target = entry_target_hours(user, d)
        if (d.year, d.month) != (year, month):
            # The other month's day: drop its usual target. Any override on it
            # belongs to that month's accounting, not this one's.
            total -= default_target
        elif entry is not None and entry.target_override is not None:
            total += target_hours_for(user, d, entry) - default_target
    return max(0.0, total)


def weekly_totals(user, entries_by_date, any_date, now=None):
    """Aggregate hours for the week containing ``any_date``.

    ``entries_by_date`` maps date -> TimeEntry for the relevant week.
    Returns a dict with worked/target/remaining hours and per-day rows.
    """
    days = week_dates(any_date)
    rows = []
    worked_total = 0.0
    # When the week straddles a month change, the day(s) from the other month
    # are still shown for context but dropped from this week's own figures, so
    # a leftover weekday doesn't pad the current month's worked total.
    for d in days:
        entry = entries_by_date.get(d)
        target = target_hours_for(user, d, entry)
        worked = entry_total_hours(entry, now=now) if entry else 0.0
        in_month = (d.year, d.month) == (any_date.year, any_date.month)
        if in_month:
            worked_total += worked
        rows.append(
            {
                "date": d,
                "entry": entry,
                "target_hours": target,
                "worked_hours": worked,
                "balance_hours": worked - target,
                "in_month": in_month,
                "needs_logout": is_stale_shift(entry, now),
            }
        )

    target_total = week_target_hours(
        user, entries_by_date, days[0], any_date.year, any_date.month
    )
    remaining = max(0.0, target_total - worked_total)
    # The stretch of the week these figures actually cover. On a week split by
    # a month change that's shorter than Mon-Sun (01-06 Sep, not 31 Aug-06
    # Sep), so the dates over the card match the hours underneath them.
    counted_days = [row["date"] for row in rows if row["in_month"]]
    return {
        "week_start": days[0],
        "week_end": days[-1],
        "span_start": counted_days[0],
        "span_end": counted_days[-1],
        "rows": rows,
        "worked_hours": worked_total,
        "target_hours": target_total,
        "remaining_hours": remaining,
        "complete": remaining <= 0,
        "progress_pct": min(100.0, (worked_total / target_total * 100.0) if target_total else 0.0),
    }


def saturday_plan(user, entries_by_date, week_start, today, now=None):
    """A forward-looking plan for when Saturday's shift can end, visible on
    any day of the week (not just Saturday itself) so it can be planned for
    in advance.

    Assumes the plan holds for every remaining weekday up to Friday (hits
    the normal/overridden target for today and any day still to come, keeps
    whatever actually happened on days already passed), then works out how
    much of the weekly target is left for Saturday. Once Saturday itself
    has actually started, switches to the exact live figure instead of a
    projection.

    Returns ``None`` if Saturday isn't a target day at all (e.g. the user
    doesn't work Saturdays and hasn't overridden it).
    """
    now = now or datetime.now()
    saturday_date = week_start + timedelta(days=5)
    sunday_date = week_start + timedelta(days=6)
    saturday_entry = entries_by_date.get(saturday_date)

    if target_hours_for(user, saturday_date, saturday_entry) <= 0:
        return None

    # Anchored on Saturday's own month rather than today's: in a week split by
    # a month change the two differ (standing on Mon 31 Aug, Saturday is
    # already September), and this plan is about Saturday's part-week. The
    # target comes from the same helper the weekly card uses, so the month's
    # days are dropped exactly once.
    month = (saturday_date.year, saturday_date.month)
    weekly_target_total = week_target_hours(user, entries_by_date, week_start, *month)

    banked = 0.0
    for d in week_dates(week_start):
        if d in (saturday_date, sunday_date):
            continue
        if (d.year, d.month) != month:
            # A weekday left over from last month: its hours belong to that
            # month, so they don't count towards this Saturday.
            continue
        entry = entries_by_date.get(d)
        if d < today:
            banked += entry_total_hours(entry, now=now) if entry else 0.0
        elif d == today:
            # Today is partly known, so use it as it happens rather than
            # waiting for midnight: once the day is closed its real total
            # counts, and while it is still open it counts for at least the
            # target, plus any hours already worked beyond it.
            worked = entry_total_hours(entry, now=now) if entry else 0.0
            target = target_hours_for(user, d, entry)
            if entry is not None and entry.logout_time is not None:
                banked += worked
            else:
                banked += max(worked, target)
        else:
            # Still to come: assume the plan holds.
            banked += target_hours_for(user, d, entry)
    if sunday_date < today and (sunday_date.year, sunday_date.month) == month:
        sunday_entry = entries_by_date.get(sunday_date)
        banked += entry_total_hours(sunday_entry, now=now) if sunday_entry else 0.0

    remaining_for_saturday = max(0.0, weekly_target_total - banked)

    if saturday_entry and saturday_entry.login_time and not saturday_entry.logout_time:
        reached, suggested_dt, still_needed = suggested_logout(
            saturday_entry, banked, weekly_target_total, now=now, user=user
        )
        return {
            "mode": "live",
            "saturday_date": saturday_date,
            "reached": reached,
            "suggested_time": fmt_suggested_datetime(suggested_dt, saturday_date),
            "still_needed_hours": still_needed,
            "target_hours": weekly_target_total,
        }

    if saturday_entry and saturday_entry.logout_time:
        worked = entry_total_hours(saturday_entry)
        return {
            "mode": "done",
            "saturday_date": saturday_date,
            "worked_hours": worked,
            "week_complete": (banked + worked) >= weekly_target_total,
            "target_hours": weekly_target_total,
        }

    projected_dt = None
    saturday_break = unrecorded_break_hours(user, saturday_date, saturday_entry, now=now)
    if remaining_for_saturday > 0 and user.saturday_login_hint:
        login_dt = datetime.combine(saturday_date, user.saturday_login_hint)
        # Sitting at the desk for the work plus the usual Saturday break.
        projected_dt = login_dt + timedelta(
            hours=remaining_for_saturday + saturday_break
        )

    return {
        "mode": "projection",
        "saturday_date": saturday_date,
        "remaining_hours": remaining_for_saturday,
        # More than one Saturday can hold: the "logout time" would land after
        # midnight, which is arithmetic rather than a plan.
        "projected_spills": projected_dt is not None and projected_dt.date() != saturday_date,
        "projected_time": fmt_suggested_datetime(projected_dt, saturday_date),
        "has_login_hint": bool(user.saturday_login_hint),
        "reached": remaining_for_saturday <= 0,
        "target_hours": weekly_target_total,
    }


def clock_minutes(t, ending=False):
    """A time of day as minutes from midnight. As the end of a stretch, 00:00
    is the midnight that ends the day (1440), not the one that starts it."""
    minutes = t.hour * 60 + t.minute
    return 1440 if ending and minutes == 0 else minutes


def minutes_clock(minutes):
    """Minutes from midnight back to a time of day; 1440 comes back as 00:00."""
    return time((minutes // 60) % 24, minutes % 60)


def night_reach(entry, day, now):
    """How far into the morning after a day's shift runs, in minutes from
    that midnight -- to the end of the hour of its logout, or, still being
    worked, a little past the hour in progress -- or None for a day that
    doesn't cross midnight. Lines are kept as times of day, so on such a day
    a time before the login, up to here, is after midnight: the same way the
    logout itself is read."""
    if entry is None or getattr(entry, "login_time", None) is None:
        return None
    login = clock_minutes(entry.login_time)
    if entry.logout_time is not None:
        out = clock_minutes(entry.logout_time)
        return (out // 60 + 1) * 60 if out < login else None
    if now.date() > day and not is_stale_shift(entry, now):
        return ((day_minute(day, now) - 1440) // 60 + 2) * 60
    return None


def shift_minutes(t, entry, day, now, ending=False):
    """A line's time of day as minutes from its own day's midnight: past 1,440
    for a time after midnight on a shift that runs on past it (night_reach).
    12:30 AM on Friday's 3 PM to 1 AM shift is 1,470."""
    minutes = clock_minutes(t, ending=ending)
    night = night_reach(entry, day, now)
    if night is not None and minutes < clock_minutes(entry.login_time) and minutes <= night:
        minutes += 1440
    return minutes


def block_span(block, entry, day, now):
    """A saved line's (start, end) in minutes, by shift_minutes."""
    return (shift_minutes(block.start_time, entry, day, now),
            shift_minutes(block.end_time, entry, day, now, ending=True))


def timesheet_span(entry, day, now):
    """Where a day's Timesheet lines begin and end, in minutes from midnight:
    from the login to the logout or -- still being worked -- to the end of
    the hour in progress, past midnight on a shift that runs on past it
    (12:30 AM is 1,470). A day with no login gets an ordinary 1 PM to 10 PM."""
    start, end = TIMESHEET_DAY
    login = entry.login_time if entry else None
    logout = entry.logout_time if entry else None
    if login is not None:
        start = clock_minutes(login)
        if logout is not None:
            end = clock_minutes(logout)
            if end < start:
                end += 1440  # past midnight: on to the logout the morning after
        elif day == now.date() or now - datetime.combine(day, login) <= timedelta(hours=STALE_SHIFT_HOURS):
            end = (day_minute(day, now) // 60 + 1) * 60
            if end - start <= TIMESHEET_SHORT_PIECE:
                # 12:47 at 12:52: the first line is already 12:47-2:00 PM, as
                # it will be once 1 PM comes, not a 13-minute 12:47-1:00 PM.
                end += 60
        else:
            end = TIMESHEET_DAY[1]  # a forgotten logout
        if end <= start:
            end = start + 60
    return start, min(end, 2880)


def split_point(start, end):
    """Where a line splits in two: its middle, on a five-minute mark, at
    least five minutes from either end. None for a line under ten minutes."""
    if end - start < 10:
        return None
    middle = round((start + end) / 2 / 5) * 5
    return min(max(middle, start + 5), end - 5)


def duplicate_span(start, end, limit):
    """Where a copy of a line goes: straight after it and as long, cut short
    at limit (the end of the day it's on). None when there's no room."""
    if end >= limit:
        return None
    return end, min(limit, end + (end - start))


def free_pieces(start, end, taken):
    """What's left of [start, end) in minutes once every (a, b) in taken is
    cut out of it, in order: 9:00-12:00 with 10:00-10:30 taken leaves
    9:00-10:00 and 10:30-12:00."""
    pieces = [(start, end)] if end > start else []
    for a, b in taken:
        cut = []
        for p, q in pieces:
            if b <= p or a >= q:
                cut.append((p, q))
                continue
            if a > p:
                cut.append((p, a))
            if b < q:
                cut.append((b, q))
        pieces = cut
    return pieces


def hour_pieces(start, end):
    """[start, end) in minutes, cut at each o'clock -- with a piece of up to
    TIMESHEET_SHORT_PIECE at either end joined to the hour beside it."""
    points = [start, *range((start // 60 + 1) * 60, end, 60), end]
    if len(points) > 2 and points[1] - points[0] <= TIMESHEET_SHORT_PIECE:
        del points[1]
    if len(points) > 2 and points[-1] - points[-2] <= TIMESHEET_SHORT_PIECE:
        del points[-2]
    return list(zip(points, points[1:]))


def timesheet_lines(entry, day, now, blocks=()):
    """A day's Timesheet lines in time order, as (start, end, block) with the
    times in minutes from midnight.

    Every saved block is a line. The rest of the day's span -- whatever no
    block covers yet -- is offered to fill in: each recorded break as a line
    of its own (block TIMESHEET_BREAK), and the time worked around the breaks
    as hourly lines (block None). On a break right now, the day so far ends
    with that break.
    """
    start, end = timesheet_span(entry, day, now)
    minute = day_minute(day, now)
    login = getattr(entry, "login_time", None)
    night = night_reach(entry, day, now)
    # Being worked right now: today's open shift, or last night's still going.
    live = is_open_shift(entry) and (day == now.date() or night is not None)
    breaks = []
    for segment in getattr(entry, "breaks", None) or ():
        if segment.break_start is None:
            continue
        a = clock_minutes(segment.break_start)
        if login is not None and a < start:
            if night is None:
                continue
            a += 1440  # taken after midnight
        if segment.break_end is not None:
            b = clock_minutes(segment.break_end, ending=True)
            if b < a:
                b += 1440  # over midnight
        elif live:
            b = max(a + 1, minute)
            end = min(end, max(b, start + 1))
        else:
            b = end
        breaks.append((a, b))
    breaks = sorted((max(a, start), min(b, end)) for a, b in breaks if min(b, end) > max(a, start))
    # Back from a break near the end of an hour: as with a login, the line in
    # progress already runs to the next o'clock (2:55 to 4:00 PM), as it will
    # once 3 PM comes, not a 5-minute 2:55-3:00 PM.
    back = max((b for _, b in breaks), default=end)
    if back < end and end - back <= TIMESHEET_SHORT_PIECE and live:
        end = min(end + 60, 2880)

    # A block with no words is a deleted line: not shown, but its time is
    # taken, so it isn't offered again.
    taken = sorted(((*block_span(b, entry, day, now), b) for b in blocks), key=lambda line: line[:2])
    lines = [line for line in taken if line[2].text]
    gaps, cursor = [], start
    for a, b, _ in taken:
        if cursor >= end:
            break
        if a > cursor:
            gaps.append((cursor, min(a, end)))
        cursor = max(cursor, b)
    if cursor < end:
        gaps.append((cursor, end))

    # Time between punches -- login, breaks, logout -- is time worked, however
    # few minutes (back from a break at 10:48 PM, out at 10:52 PM). Only a
    # sliver left beside a line whose times were typed isn't worth a line.
    typed = {edge for a, b, block in taken if block.text.casefold() != "break" for edge in (a, b)}

    def worked(a, b):
        if b <= a or (b - a < TIMESHEET_SMALL_GAP and (a in typed or b in typed)):
            return []
        return [(p, q, None) for p, q in hour_pieces(a, b)]

    for a, b in gaps:
        cursor = a
        for break_start, break_end in breaks:
            if break_end <= cursor or break_start >= b:
                continue
            lines += worked(cursor, max(cursor, break_start))
            lines.append((max(cursor, break_start), min(break_end, b), TIMESHEET_BREAK))
            cursor = min(break_end, b)
        lines += worked(cursor, b)
    return sorted(lines, key=lambda line: line[:2])


def fmt_clock(value):
    """A time of day on a 12-hour clock: 9:05 AM, 12:30 PM, 6:00 PM.

    Written out rather than using %I/%p so there's no leading zero and no
    dependence on the machine's locale.
    """
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {suffix}"


def fmt_span(start, end):
    """A Timesheet line's time, from minutes after midnight: 12:47–2:00 PM,
    11:30 AM–12:30 PM, 11:00 PM–12:00 AM, and after it 12:00–12:30 AM."""
    a, b = fmt_clock(minutes_clock(start)), fmt_clock(minutes_clock(end))
    if a[-2:] == b[-2:] and (end < 1440 or start >= 1440):
        return f"{a[:-3]}–{b}"
    return f"{a}–{b}"


def fmt_length(minutes):
    """How long a Timesheet line is: 1h, 30m, 1h 13m; nothing for none."""
    h, m = divmod(max(0, minutes), 60)
    return " ".join(part for part in (f"{h}h" if h else "", f"{m}m" if m else "") if part)


def fmt_suggested_datetime(dt, reference_date=None):
    """Format a suggested clock-out datetime for display, spelling out the
    date whenever it falls on a different day than ``reference_date``.

    Without this, a logout time that spills into tomorrow (e.g. someone is
    badly behind on hours) would render as a bare "HH:MM" and read as
    "later today" when it's actually a day or more away.
    """
    if dt is None:
        return None
    reference_date = reference_date or dt.date()
    if dt.date() == reference_date:
        return fmt_clock(dt)
    return f"{fmt_clock(dt)} on {dt.strftime('%a %d %b')}"


def suggested_logout(
    open_entry, weekly_before_today, weekly_target_hours, now=None, user=None
):
    """For a currently open (punched-in) entry, suggest a logout time that
    would exactly hit the weekly target, given hours already banked earlier
    in the week (``weekly_before_today``, i.e. not counting today).

    When ``user`` is given, the clock-out time also allows for whatever is
    left of the day's usual break, since that time will be spent but doesn't
    count as worked.

    Returns (already_reached: bool, suggested_dt: datetime | None,
    hours_remaining_after_now: float).
    """
    now = now or datetime.now()
    remaining_for_week = max(0.0, weekly_target_hours - weekly_before_today)

    today_worked_so_far = entry_total_hours(open_entry, now=now)
    if today_worked_so_far >= remaining_for_week:
        return True, now, 0.0

    still_needed = remaining_for_week - today_worked_so_far
    break_to_come = (
        unrecorded_break_hours(user, open_entry.date, open_entry, now=now)
        if user is not None
        else 0.0
    )
    suggested_dt = now + timedelta(hours=still_needed + break_to_come)
    return False, suggested_dt, still_needed


def shift_login_dt(entry):
    return datetime.combine(entry.date, entry.login_time)


def place_on_shift(entry_date, typed, earliest=None):
    """Put a typed time of day onto a shift's timeline. A time earlier than
    ``earliest`` belongs to the next day -- that is how an overnight shift
    carries past midnight, the same way the day's hours are added up."""
    dt = datetime.combine(entry_date, typed)
    if earliest is not None and dt < earliest:
        dt += timedelta(days=1)
    return dt


def last_mark_dt(entry, now):
    """The latest moment already recorded on an open shift: the login, or
    the most recent break start or end. Anything placed after ``now`` is
    impossible, so it's ignored rather than blocking every typed time."""
    login_dt = shift_login_dt(entry)
    mark = login_dt
    for b in entry.breaks:
        start = place_on_shift(entry.date, b.break_start, login_dt)
        points = [start]
        if b.break_end is not None:
            points.append(place_on_shift(entry.date, b.break_end, start))
        for p in points:
            if p <= now + PUNCH_GRACE:
                mark = max(mark, p)
    return mark


def check_typed_time(entry_date, typed, now, earliest=None, earliest_label=None):
    """Check a time typed in for a login, break or logout missed live.

    Returns ``(when, None)`` if it fits the shift, or ``(None, message)``
    saying what's wrong. When the other half of the day would have fitted,
    the message suggests it, because an AM/PM slip is the usual cause --
    and left alone, a 6:00 AM logout meant as 6:00 PM becomes a 21h day.
    """
    def fits(t):
        dt = place_on_shift(entry_date, t, earliest)
        return dt if dt <= now + PUNCH_GRACE else None

    when = fits(typed)
    if when is not None:
        return when, None

    typed_same_day = datetime.combine(entry_date, typed)
    if earliest is not None and entry_date == now.date() and typed_same_day < earliest:
        message = f"{fmt_clock(typed)} is before {earliest_label} ({fmt_clock(earliest)})."
    else:
        message = f"{fmt_clock(typed)} hasn't happened yet."
    flipped = (typed_same_day + timedelta(hours=12)).time()
    if fits(flipped) is not None:
        message += f" Did you mean {fmt_clock(flipped)}?"
    return None, message


def fmt_hours(value):
    """Format decimal hours as 'Hh MMm'."""
    if value is None:
        return "--"
    sign = "-" if value < 0 else ""
    value = abs(value)
    total_minutes = round(value * 60)
    h, m = divmod(total_minutes, 60)
    return f"{sign}{h}h {m:02d}m"


def fmt_balance(value):
    """A balance with its direction written out: +7h 52m, −6h 09m.

    Always signed, so ahead and behind never depend on colour alone, and the
    minus is a real minus sign, which screen readers say as "minus" rather
    than "dash".
    """
    if value is None:
        return "--"
    total_minutes = round(value * 60)
    if total_minutes == 0:
        return "0h 00m"
    h, m = divmod(abs(total_minutes), 60)
    sign = "+" if total_minutes > 0 else "\u2212"
    return f"{sign}{h}h {m:02d}m"


def fmt_duration(value):
    """A length of time for a sentence: 25m, 1h 05m, 8h."""
    total_minutes = max(0, round((value or 0) * 60))
    h, m = divmod(total_minutes, 60)
    if h == 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m:02d}m"


def fmt_hours_compact(value):
    """Hours squeezed for a narrow column on a phone: 8h25, 11h40, 45m."""
    total_minutes = max(0, round((value or 0) * 60))
    h, m = divmod(total_minutes, 60)
    if h == 0:
        return f"{m}m"
    return f"{h}h{m:02d}"


def fmt_balance_compact(value):
    """A balance squeezed for a calendar square: +3h40, −2h15, +40m.
    Signed like fmt_balance, so it never relies on colour alone."""
    total_minutes = round((value or 0) * 60)
    if total_minutes == 0:
        return "0"
    sign = "+" if total_minutes > 0 else "−"
    h, m = divmod(abs(total_minutes), 60)
    return f"{sign}{m}m" if h == 0 else f"{sign}{h}h{m:02d}"


def fmt_target_hours(value):
    """A weekly target written the way it's spoken: 48h, 40h, 47.5h.

    Kept separate from ``fmt_hours`` because a target reads as a round figure
    ("your 40h") where a worked total needs the minutes ("39h 45m").
    """
    if value is None:
        return "--"
    rounded = round(value, 1)
    if rounded == int(rounded):
        return f"{int(rounded)}h"
    return f"{rounded:g}h"


def fmt_time(t):
    return fmt_clock(t) if t else "--:--"


def fmt_money(amount, symbol="₹", exact=False, signed=False):
    """Money as it's written on a payslip: ₹24,390, or ₹106.48 with exact.
    Rupees are grouped the Indian way (₹1,23,456); signed gives +₹390 and
    −₹460, with a real minus sign like fmt_balance."""
    places = 2 if exact else 0
    value = round(abs(amount or 0), places)
    whole, _, pence = f"{value:.{places}f}".partition(".")
    if symbol == "₹" and len(whole) > 3:
        head, groups = whole[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join([head] + groups + [whole[-3:]])
    else:
        whole = f"{int(whole):,}"
    text = symbol + whole + ("." + pence if pence else "")
    if signed and value:
        return ("+" if amount > 0 else "−") + text
    return ("−" + text) if (amount or 0) < 0 and value else text


# ------------------------------------------------------------- the year

HEATMAP_WEEKS = 53


def heatmap_first(today, weeks=HEATMAP_WEEKS):
    """The Monday the year's heatmap starts on: weeks whole weeks, the last
    one today's."""
    return today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)


def heat_level(worked, target):
    """How dark a day's square is, 0 to 4, from its hours against its
    target: none, under half, under the target, the target, and an hour or
    more over it. Any work on a day with nothing due is all over."""
    if worked <= 0:
        return 0
    if target <= 0 or worked >= target + 1:
        return 4
    if worked >= target - 1 / 60:
        return 3
    return 2 if worked >= target / 2 else 1


def year_heatmap(user, entries_by_date, today, now=None, weeks=HEATMAP_WEEKS):
    """The last year a day at a time, for the heatmap: weeks (Monday first)
    of days, each with its hours against its target and a shade; leave is
    marked as well as shaded. Days before the first one recorded, and days
    still to come, are blank. With the months each week starts, and the
    year's totals."""
    first = heatmap_first(today, weeks)
    recorded = [d for d in entries_by_date if first <= d <= today]
    began = min(recorded) if recorded else today
    columns, months, worked_total, days_worked = [], [], 0.0, 0
    for w in range(weeks):
        column = []
        for i in range(7):
            d = first + timedelta(days=w * 7 + i)
            entry = entries_by_date.get(d)
            worked = entry_total_hours(entry, now=now) if entry is not None and entry.login_time else 0.0
            target = target_hours_for(user, d, entry)
            leave = entry is not None and entry.target_override is not None
            if d > today:
                kind = "future"
            elif d < began and entry is None:
                kind = "blank"
            elif leave:
                kind = "leave"
            elif worked > 0:
                kind = "worked"
            elif target <= 0:
                kind = "rest"
            else:
                kind = "missed" if d < today else "none"
            if kind not in ("future", "blank"):
                worked_total += worked
                days_worked += worked > 0
            column.append({"date": d, "worked": worked, "target": target, "kind": kind,
                           "level": heat_level(worked, target) if kind not in ("future", "blank") else 0})
        # A month's name over the week its 1st falls in.
        starts = next((day["date"] for day in column if day["date"].day == 1), None)
        if starts is not None or w == 0:
            months.append((w, (starts or column[0]["date"]).strftime("%b")))
        columns.append(column)
    if len(months) > 1 and months[1][0] - months[0][0] < 3:
        months.pop(0)  # the first week's month, when the next name would crowd it
    return {"first": first, "columns": columns, "months": months,
            "worked_hours": worked_total, "days_worked": days_worked}


# ------------------------------------------------------------- goals

GOAL_SLACK = 1 / 120  # hours: half a minute short still counts


def logout_minute(entry):
    """A logout in minutes from its day's midnight, past 1,440 after it."""
    out = clock_minutes(entry.logout_time)
    return out + 1440 if out < clock_minutes(entry.login_time) else out


def goal_day(kind, value, user, d, entry, written, today, now):
    """How a day went for a daily goal: "met", "missed", "skip" -- it counts
    neither way: leave, a rest day, a day not worked for a goal about work
    done, a shift whose logout was never put in -- or "open": today, not
    met yet, which can't break a streak until it's over. written is the
    day's Timesheet minutes with words."""
    if entry is not None and (entry.target_override is not None or is_stale_shift(entry, now)):
        return "skip"
    logged = entry is not None and entry.login_time is not None
    worked = entry_total_hours(entry, now=now) if logged else 0.0
    ended = logged and entry.logout_time is not None
    if kind == "daily_hours":
        if entry_target_hours(user, d) <= 0:
            return "skip"
        if worked >= value - GOAL_SLACK:
            return "met"
        return "open" if d >= today else "missed"
    if kind == "timesheet_written":
        if worked <= 0 or not ended:
            return "open" if d >= today and logged else "skip"
        if written >= worked * 60 * value / 100 - 0.5:
            return "met"
        return "open" if d >= today else "missed"
    if kind == "logout_by":
        if not logged:
            return "skip"
        if not ended:
            return "missed" if day_minute(d, now) > value else "open"
        return "met" if logout_minute(entry) <= value else "missed"
    raise ValueError(kind)


def goal_week(value, user, days, entries_by_date, today, now):
    """How a week (Monday to Sunday) went for an hours-a-week goal. Leave
    takes its usual hours off what the goal asks, so a week of leave asks
    nothing and is skipped. Returns (outcome, worked, asked)."""
    asked, worked = value, 0.0
    for d in days:
        entry = entries_by_date.get(d)
        if entry is not None and entry.target_override is not None:
            asked -= max(0.0, entry_target_hours(user, d) - entry.target_override)
        if entry is not None and entry.login_time is not None and not is_stale_shift(entry, now):
            worked += entry_total_hours(entry, now=now)
    if asked <= 0:
        return "skip", worked, 0.0
    if worked >= asked - GOAL_SLACK:
        return "met", worked, asked
    return ("open" if days[-1] >= today else "missed"), worked, asked


def streaks(outcomes):
    """The current and the best run of "met", in time order: a "missed"
    ends a run; "skip" and "open" don't count either way."""
    run = best = 0
    for outcome in outcomes:
        if outcome == "met":
            run += 1
            best = max(best, run)
        elif outcome == "missed":
            run = 0
    return run, best


def goal_title(kind, value):
    if kind == "daily_hours":
        return f"{fmt_target_hours(value)} a day"
    if kind == "weekly_hours":
        return f"{fmt_target_hours(value)} a week"
    if kind == "timesheet_written":
        return "Timesheet written" if value >= 100 else f"Timesheet {value:g}% written"
    return f"Out by {fmt_clock(minutes_clock(int(value)))}"


def goal_progress(kind, value, user, entries_by_date, written_by_date, first, today, now):
    """A goal on Home: its title, today's (or this week's) progress -- a
    share for its bar and in words -- and its current and best streak in
    days (weeks, for hours a week), from first to today. Days before the
    first one recorded don't count."""
    recorded = [d for d in entries_by_date if first <= d <= today]
    began = min(recorded) if recorded else today
    entry = entries_by_date.get(today)
    if kind == "weekly_hours":
        weeks, monday = [], week_bounds(began)[0]
        while monday <= today:
            weeks.append(goal_week(value, user, week_dates(monday), entries_by_date, today, now))
            monday += timedelta(days=7)
        state, worked, asked = weeks[-1]
        current, best = streaks(w[0] for w in weeks)
        label = (f"{fmt_duration(worked)} of {fmt_duration(asked)} this week" if asked
                 else "On leave this week")
        share = worked / asked if asked else 0.0
        unit = "week"
    else:
        outcomes, d = [], began
        while d <= today:
            outcomes.append(goal_day(kind, value, user, d, entries_by_date.get(d), written_by_date.get(d, 0),
                                     today, now))
            d += timedelta(days=1)
        state = outcomes[-1]
        current, best = streaks(outcomes)
        unit = "day"
        logged = entry is not None and entry.login_time is not None
        worked = entry_total_hours(entry, now=now) if logged else 0.0
        if kind == "daily_hours":
            share = worked / value if value else 0.0
            label = (f"{fmt_duration(worked)} of {fmt_duration(value)} today" if state != "skip"
                     else ("Leave today" if entry is not None and entry.target_override is not None
                           else "Rest day"))
        elif kind == "timesheet_written":
            written = written_by_date.get(today, 0)
            share = written / (worked * 60) * 100 / value if worked and value else 0.0
            label = (f"{min(100, round(written / (worked * 60) * 100))}% of today written" if worked
                     else "Nothing worked yet today")
        else:
            if not logged:
                share, label = 0.0, "Not logged in today"
            elif entry.logout_time is not None:
                share, label = 1.0, f"Out at {fmt_time(entry.logout_time)}"
            else:
                start, at = clock_minutes(entry.login_time), day_minute(today, now)
                share = (at - start) / (value - start) if value > start else 1.0
                label = (f"{fmt_duration((value - at) / 60)} to go" if at < value
                         else f"{fmt_duration((at - value) / 60)} past it")
    return {"kind": kind, "title": goal_title(kind, value), "state": state, "label": label,
            "pct": round(max(0.0, min(1.0, share)) * 100), "current": current, "best": best, "unit": unit}
