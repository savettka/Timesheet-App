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
from datetime import date, datetime, timedelta

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


def break_segment_hours(entry, segment, now=None):
    """Hours for a single break segment. If the break is still open,
    counts up to ``now`` (or the current time)."""
    if segment.break_start is None:
        return 0.0
    start_dt = datetime.combine(entry.date, segment.break_start)
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


def fmt_clock(value):
    """A time of day on a 12-hour clock: 9:05 AM, 12:30 PM, 6:00 PM.

    Written out rather than using %I/%p so there's no leading zero and no
    dependence on the machine's locale.
    """
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {suffix}"


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
