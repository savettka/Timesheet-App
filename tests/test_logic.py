"""The hours, targets and balances as STM works them out today. These pin
the current behaviour down, so a change that moves any of these figures
fails here first."""
import datetime as dt

import pytest
from conftest import a_user

from app import logic
from app.models import BreakSegment, TimeEntry, WorkBlock

T = dt.time
D = dt.date
AT = dt.datetime
BREAK = logic.TIMESHEET_BREAK

MON, TUE, WED, THU, SAT, SUN = (D(2026, 11, d) for d in (9, 10, 11, 12, 14, 15))


def day(date, login=None, logout=None, breaks=(), **fields):
    """A day as the database keeps it: punches and breaks (start, end)."""
    entry = TimeEntry(date=date, login_time=login, logout_time=logout, **fields)
    for start, end in breaks:
        entry.breaks.append(BreakSegment(break_start=start, break_end=end))
    return entry


def leave(date, hours=0.0, kind="full"):
    return day(date, target_override=hours, leave_type=kind)


def spans(lines):
    return [(a, b, "break" if block == BREAK else (block.text if block is not None else None))
            for a, b, block in lines]


# ------------------------------------------------------- entry_total_hours

class TestEntryTotalHours:
    def test_a_day_less_its_break(self):
        entry = day(THU, T(9, 0), T(17, 30), [(T(13, 0), T(13, 45))])
        assert logic.entry_total_hours(entry) == pytest.approx(7.75)

    def test_a_shift_past_midnight_with_a_break_after_it(self):
        entry = day(THU, T(15, 0), T(0, 30), [(T(0, 5), T(0, 15))])
        assert logic.entry_total_hours(entry) == pytest.approx(9 + 20 / 60)

    def test_open_with_a_break_running(self):
        entry = day(THU, T(9, 0), breaks=[(T(12, 0), None)])
        assert logic.entry_total_hours(entry, now=AT(2026, 11, 12, 12, 30)) == pytest.approx(3.0)

    def test_a_break_started_after_midnight_still_running(self):
        entry = day(THU, T(15, 0), breaks=[(T(0, 30), None)])
        assert logic.entry_total_hours(entry, now=AT(2026, 11, 13, 0, 45)) == pytest.approx(9.5)

    def test_leave_with_no_login_is_nothing_worked(self):
        assert logic.entry_total_hours(leave(THU)) == 0.0

    def test_a_forgotten_logout_counts_for_nothing(self):
        entry = day(MON, T(9, 0))
        assert logic.entry_total_hours(entry, now=AT(2026, 11, 12, 9, 0)) == 0.0


# --------------------------------------------------------- target_hours_for

class TestTargetHoursFor:
    def test_a_workday(self):
        assert logic.target_hours_for(a_user(), THU) == 8.0

    def test_the_rest_day(self):
        assert logic.target_hours_for(a_user(), SUN) == 0.0

    def test_a_half_day(self):
        assert logic.target_hours_for(a_user(), THU, leave(THU, 4.0, "half")) == 4.0

    def test_a_day_off(self):
        assert logic.target_hours_for(a_user(), THU, leave(THU)) == 0.0

    def test_an_ordinary_entry_keeps_the_weekday_target(self):
        assert logic.target_hours_for(a_user(), SAT, day(SAT, T(9, 0), T(13, 0))) == 8.0


# ---------------------------------------------------- accrued_target_hours

class TestAccruedTargetHours:
    now = AT(2026, 11, 12, 12, 0)

    def test_a_day_not_reached_owes_nothing(self):
        assert logic.accrued_target_hours(8.0, None, D(2026, 11, 13), THU, now=self.now) == 0.0

    def test_a_day_gone_owes_it_all(self):
        assert logic.accrued_target_hours(8.0, None, WED, THU, now=self.now) == 8.0

    def test_today_still_working_owes_only_what_was_worked(self):
        entry = day(THU, T(9, 0))
        assert logic.accrued_target_hours(8.0, entry, THU, THU, now=self.now) == pytest.approx(3.0)

    def test_today_nothing_yet(self):
        assert logic.accrued_target_hours(8.0, None, THU, THU, now=self.now) == 0.0

    def test_today_logged_out_short_owes_it_all(self):
        entry = day(THU, T(9, 0), T(11, 0))
        assert logic.accrued_target_hours(8.0, entry, THU, THU, now=self.now) == 8.0


# ------------------------------------------------------------ weekly_totals

class TestWeeklyTotals:
    now = AT(2026, 11, 12, 20, 0)

    def week(self, *extra):
        entries = {d: day(d, T(9, 0), T(18, 0)) for d in (MON, TUE, WED, THU)}
        entries.update({e.date: e for e in extra})
        return entries

    def test_four_days_of_nine_hours(self):
        got = logic.weekly_totals(a_user(), self.week(), THU, now=self.now)
        assert (got["worked_hours"], got["target_hours"], got["remaining_hours"]) == (36.0, 48.0, 12.0)
        assert got["complete"] is False
        assert got["progress_pct"] == pytest.approx(75.0)
        assert (got["week_start"], got["week_end"], got["span_start"], got["span_end"]) == (MON, SUN, MON, SUN)
        assert [row["worked_hours"] for row in got["rows"]] == [9.0, 9.0, 9.0, 9.0, 0.0, 0.0, 0.0]

    def test_a_day_of_leave_takes_its_hours_off_the_target(self):
        got = logic.weekly_totals(a_user(), self.week(leave(WED)), THU, now=self.now)
        assert (got["worked_hours"], got["target_hours"]) == (27.0, 40.0)

    def test_a_week_across_a_month_end_counts_only_this_month(self):
        first = D(2026, 10, 1)  # a Thursday: Mon-Wed belong to September
        entries = {d: day(d, T(9, 0), T(18, 0)) for d in (D(2026, 9, 28), D(2026, 9, 30), first)}
        got = logic.weekly_totals(a_user(), entries, first, now=AT(2026, 10, 1, 20, 0))
        assert (got["worked_hours"], got["target_hours"]) == (9.0, 24.0)
        assert (got["span_start"], got["span_end"]) == (first, D(2026, 10, 4))


# ------------------------------------------------------------ month_summary

class TestMonthSummary:
    def test_november_to_thursday_lunchtime(self):
        now = AT(2026, 11, 12, 12, 0)
        entries = {}
        for n in (2, 4, 5, 6, 7, 9, 10, 11):
            d = D(2026, 11, n)
            entries[d] = day(d, T(9, 0), T(18, 0))
        entries[D(2026, 11, 3)] = leave(D(2026, 11, 3))
        entries[THU] = day(THU, T(9, 0))
        got = logic.month_summary(a_user(), entries, 2026, 11, THU, now=now)
        assert got["worked_hours"] == pytest.approx(75.0)
        # Overtime by the week: 2-8 Nov settled on Saturday (45h of 40h, a
        # day's leave off its 48h); this week's hours are banked until its
        # Saturday, so they count as due as they're worked.
        assert got["target_hours"] == pytest.approx(70.0)
        assert got["balance_hours"] == pytest.approx(5.0)
        assert got["today_in_progress"] is True
        assert len(got["rows"]) == 30
        today = got["rows"][11]
        assert (today["is_today"], today["accrued_target_hours"], today["in_progress"]) == (True, pytest.approx(3.0), True)
        assert today["banked_hours"] == pytest.approx(-5.0)
        assert all(r["target_hours"] == 0.0 and r["is_future"] for r in got["rows"][12:])

    def test_a_missed_workday_is_owed_when_its_week_settles(self):
        got = logic.month_summary(a_user(), {}, 2026, 11, D(2026, 11, 3), now=AT(2026, 11, 3, 8, 0))
        assert got["worked_hours"] == 0.0 and got["balance_hours"] == 0.0
        assert got["rows"][1]["banked_hours"] == pytest.approx(-8.0)  # Monday the 2nd, banked short
        later = logic.month_summary(a_user(), {}, 2026, 11, D(2026, 11, 8), now=AT(2026, 11, 8, 8, 0))
        assert later["balance_hours"] == pytest.approx(-48.0)  # Mon 2 to Sat 7, never logged


# --------------------------------------------------------- suggested_logout

class TestSuggestedLogout:
    now = AT(2026, 11, 12, 12, 0)

    def test_time_to_reach_the_week_allowing_the_break_to_come(self):
        reached, when, still = logic.suggested_logout(day(THU, T(9, 0)), 36.0, 48.0, now=self.now, user=a_user())
        assert (reached, when, still) == (False, AT(2026, 11, 12, 22, 0), pytest.approx(9.0))

    def test_without_the_user_no_break_is_allowed(self):
        _, when, _ = logic.suggested_logout(day(THU, T(9, 0)), 36.0, 48.0, now=self.now)
        assert when == AT(2026, 11, 12, 21, 0)

    def test_already_there(self):
        assert logic.suggested_logout(day(THU, T(9, 0)), 46.0, 48.0, now=self.now) == (True, self.now, 0.0)


# ---------------------------------------------------------- timesheet_lines

class TestTimesheetLines:
    def test_an_hour_a_line_with_a_short_end_joined_on(self):
        entry = day(WED, T(13, 0), T(17, 30))
        assert spans(logic.timesheet_lines(entry, WED, AT(2026, 11, 12, 9, 0))) == [
            (780, 840, None), (840, 900, None), (900, 960, None), (960, 1050, None)]

    def test_a_break_is_a_line_of_its_own(self):
        entry = day(WED, T(13, 0), T(17, 30), [(T(14, 0), T(14, 30))])
        assert spans(logic.timesheet_lines(entry, WED, AT(2026, 11, 12, 9, 0))) == [
            (780, 840, None), (840, 870, "break"), (870, 960, None), (960, 1050, None)]

    def test_saved_lines_and_the_gaps_round_them(self):
        entry = day(WED, T(13, 0), T(16, 0))
        blocks = [WorkBlock(date=WED, start_time=T(13, 0), end_time=T(14, 30), text="VAT return")]
        assert spans(logic.timesheet_lines(entry, WED, AT(2026, 11, 12, 9, 0), blocks)) == [
            (780, 870, "VAT return"), (870, 960, None)]

    def test_today_to_the_end_of_the_hour_in_progress(self):
        entry = day(THU, T(9, 0))
        assert spans(logic.timesheet_lines(entry, THU, AT(2026, 11, 12, 11, 20))) == [
            (540, 600, None), (600, 660, None), (660, 720, None)]

    def test_on_a_break_now_the_day_ends_with_it(self):
        entry = day(THU, T(9, 0), breaks=[(T(12, 0), None)])
        assert spans(logic.timesheet_lines(entry, THU, AT(2026, 11, 12, 12, 20))) == [
            (540, 600, None), (600, 660, None), (660, 720, None), (720, 740, "break")]

    def test_a_shift_past_midnight_runs_on_to_its_logout(self):
        entry = day(WED, T(15, 0), T(0, 30))
        lines = spans(logic.timesheet_lines(entry, WED, AT(2026, 11, 12, 9, 0)))
        assert lines[0] == (900, 960, None)
        assert lines[-1] == (1380, 1470, None)

    def test_a_line_written_after_midnight_stays_after_it(self):
        entry = day(WED, T(15, 0), T(0, 30))
        blocks = [WorkBlock(date=WED, start_time=T(23, 0), end_time=T(0, 0), text="Encryption"),
                  WorkBlock(date=WED, start_time=T(0, 0), end_time=T(0, 30), text="Testing")]
        assert spans(logic.timesheet_lines(entry, WED, AT(2026, 11, 12, 9, 0), blocks))[-2:] == [
            (1380, 1440, "Encryption"), (1440, 1470, "Testing")]

    def test_a_leave_day_offers_an_ordinary_afternoon(self):
        lines = logic.timesheet_lines(leave(WED), WED, AT(2026, 11, 12, 9, 0))
        assert [(a, b) for a, b, _ in lines] == [(m, m + 60) for m in range(780, 1320, 60)]
