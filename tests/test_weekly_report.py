"""6.1 The weekly report email: the week as the firm's weekly summary lays
it out, sent by STM itself 15 minutes after the logout on the last
workday -- once a week, with no scheduler: the page asks /api/reminders."""
import datetime as dt

import pytest

from app import db, mailer, weekly_report
from app.models import BreakSegment, ReminderLog, Salary, TimeEntry, User, WorkBlock

T = dt.time
D = dt.date
MON = D(2026, 11, 9)
SAT = D(2026, 11, 14)


def test_clock_and_rate():
    assert weekly_report.clock(16 * 3600 + 32 * 60 + 59) == "16:32:59"
    assert weekly_report.clock(60 * 3600 + 22 * 60 + 17) == "60:22:17"
    assert weekly_report.clock(-5) == "00:00:00"
    # The firm's own figures: 16:32:59 over two days, 60:22:17 over seven.
    assert round(weekly_report.productivity(16 + 33 / 60 - 1 / 3600, 2, 8), 2) == 103.44
    assert round(weekly_report.productivity(60 + 22 / 60 + 17 / 3600, 7, 8), 2) == 107.81
    assert weekly_report.productivity(0, 0, 8) is None


@pytest.fixture
def week(app, user_id):
    """Mon 9 to 6 with a lunch break and two lines; Tuesday on leave;
    Saturday 9 to 1:30."""
    with app.app_context():
        user = db.session.get(User, user_id)
        user.email = "me@example.com"
        db.session.add(Salary(user_id=user_id, starts=D(2026, 1, 1), amount=23000))
        mon = TimeEntry(user_id=user_id, date=MON, login_time=T(9, 0), logout_time=T(18, 0))
        db.session.add(mon)
        db.session.flush()
        db.session.add(BreakSegment(entry_id=mon.id, break_start=T(13, 0), break_end=T(14, 0)))
        db.session.add(WorkBlock(user_id=user_id, date=MON, start_time=T(9, 0), end_time=T(12, 0), text="VAT return"))
        db.session.add(WorkBlock(user_id=user_id, date=MON, start_time=T(12, 0), end_time=T(13, 0), text="Emails",
                                 copied="12:00-13:00 Emails"))
        db.session.add(WorkBlock(user_id=user_id, date=MON, start_time=T(14, 0), end_time=T(17, 0), text="VAT return"))
        db.session.add(TimeEntry(user_id=user_id, date=D(2026, 11, 10), target_override=0.0, leave_type="full",
                                 leave_label="Diwali"))
        db.session.add(TimeEntry(user_id=user_id, date=SAT, login_time=T(9, 0), logout_time=T(13, 30, 20)))
        db.session.commit()


@pytest.fixture
def mail(app, monkeypatch):
    """Email set up, and every email kept here instead of sent."""
    app.config.update(SMTP_USERNAME="stm@example.com", SMTP_PASSWORD="test-only")

    class Outbox(list):
        ok = True  # set False for the mail server to refuse

    sent = Outbox()

    def fake(to, subject, text, html=None):
        sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return sent.ok

    monkeypatch.setattr(mailer, "send_email", fake)
    return sent


def test_the_report(app, user_id, week, clock):
    clock.set(2026, 11, 14, 14, 0)
    with app.test_request_context():
        user = db.session.get(User, user_id)
        r = weekly_report.build_report(user, MON, clock.now)
        assert (r["days_worked"], r["worked"], r["breaks"]) == (2, "12:30:20", "01:00:00")
        assert r["productivity"] == "78.16%"  # 12:30:20 over two 8h days
        assert (r["target"], r["balance"]) == ("40h", "−27h 30m")  # 48h less Diwali's 8h
        assert r["overtime_pay"] is None
        monday, tuesday, saturday = r["rows"]
        assert monday["label"] == "Monday, 09 Nov 2026" and monday["worked"] == "08:00:00"
        assert monday["tasks"] == [("VAT return", "06:00:00"), ("Emails", "01:00:00")]
        assert monday["unwritten"] == "01:00:00" and monday["breaks"] == "01:00:00"
        assert monday["span"] == "9:00 AM – 6:00 PM"
        assert tuesday["leave"] == "Diwali"
        assert saturday["tasks"] == [] and saturday["unwritten"] == "04:30:20"
        assert r["not_posted"] == 2  # the VAT return lines; Emails is posted
        assert ("Mon 09 Nov", 1) in r["unwritten_days"] and r["unwritten_days"][-1][0] == "Sat 14 Nov"
        html = weekly_report.render_html(r)
        assert "Your weekly work summary" in html and "78.16%" in html and "VAT return" in html
        assert "<img" not in html
        text = weekly_report.render_text(r)
        assert "Productivity: 78.16%" in text and "Tuesday, 10 Nov 2026  Diwali" in text


def test_when_its_due(app, user_id, week, clock):
    with app.app_context():
        user = db.session.get(User, user_id)
        clock.set(2026, 11, 14, 14, 0)
        assert weekly_report.due_at(user, MON, clock.now) == dt.datetime(2026, 11, 14, 13, 45, 20)
        # Still at it on Saturday: not yet.
        sat = TimeEntry.query.filter_by(user_id=user_id, date=SAT).one()
        sat.logout_time = None
        assert weekly_report.due_at(user, MON, clock.now) is None
        # Out after midnight: the morning after.
        sat.login_time, sat.logout_time = T(15, 0), T(0, 40)
        assert weekly_report.due_at(user, MON, clock.now) == dt.datetime(2026, 11, 15, 0, 55)
        # Saturday not worked: the midnight after it.
        db.session.delete(sat)
        assert weekly_report.due_at(user, MON, clock.now) == dt.datetime(2026, 11, 15, 0, 0)
        # Before Saturday: nothing to say yet.
        clock.set(2026, 11, 13, 18, 0)
        assert weekly_report.due_at(user, MON, clock.now) is None


def test_sent_once_15_minutes_after_the_last_logout(app, client, user_id, week, mail, clock):
    clock.set(2026, 11, 14, 13, 40)
    assert client.get("/api/reminders").get_json()["report_in"] == (320)
    assert mail == []
    clock.set(2026, 11, 14, 13, 46)
    assert client.get("/api/reminders").get_json()["report_in"] == (None)
    assert len(mail) == 1 and mail[0]["to"] == "me@example.com"
    assert mail[0]["subject"] == "Weekly work summary · 09 Nov – 15 Nov 2026"
    # Asked again, by this tab or another: nothing more.
    client.get("/api/reminders")
    clock.set(2026, 11, 15, 10, 0)
    client.get("/api/reminders")
    assert len(mail) == 1
    with app.app_context():
        log = ReminderLog.query.one()
        assert (log.period, log.status, log.attempts) == (MON, "sent", 1)


def test_a_failed_send_is_tried_again(app, client, user_id, week, mail, clock):
    mail.ok = False
    clock.set(2026, 11, 14, 14, 0)
    assert client.get("/api/reminders").get_json()["report_in"] == (900)
    clock.set(2026, 11, 14, 14, 5)
    assert client.get("/api/reminders").get_json()["report_in"] == (600)
    assert len(mail) == 1
    mail.ok = True
    clock.set(2026, 11, 14, 14, 16)
    client.get("/api/reminders")
    assert len(mail) == 2
    with app.app_context():
        assert ReminderLog.query.one().status == "sent"


def test_gives_up_after_four_tries(app, client, user_id, week, mail, clock):
    mail.ok = False
    for minute in (0, 16, 32, 48, 64):
        clock.set(dt.datetime(2026, 11, 14, 14, 0) + dt.timedelta(minutes=minute))
        client.get("/api/reminders")
    assert len(mail) == 4


def test_last_weeks_on_monday(app, client, user_id, week, mail, clock):
    clock.set(2026, 11, 16, 9, 0)  # the browser was shut all weekend
    client.get("/api/reminders")
    assert len(mail) == 1 and "09 Nov – 15 Nov" in mail[0]["subject"]


def test_nothing_worked_nothing_sent(app, client, user_id, mail, clock):
    with app.app_context():
        db.session.get(User, user_id).email = "me@example.com"
        db.session.commit()
    clock.set(2026, 11, 15, 10, 0)
    client.get("/api/reminders")
    assert mail == []
    with app.app_context():
        assert ReminderLog.query.one().status == "skipped"


def test_switched_off_or_no_address(app, client, user_id, week, mail, clock):
    clock.set(2026, 11, 14, 16, 0)
    r = client.post("/settings", data={"form_type": "emails"}, follow_redirects=True)
    assert "Weekly email off." in r.get_data(as_text=True)
    client.get("/api/reminders")
    assert mail == []
    client.post("/settings", data={"form_type": "emails", "weekly_report": "1"})
    with app.app_context():
        db.session.get(User, user_id).email = None
        db.session.commit()
    client.get("/api/reminders")
    assert mail == []


def test_not_set_up(app, client, user_id, week, clock):
    clock.set(2026, 11, 14, 16, 0)
    assert client.get("/api/reminders").get_json()["report_in"] == (None)
    page = client.get("/settings").get_data(as_text=True)
    assert "Email isn't set up on this server" in page


def test_preview_and_send_now(app, client, user_id, week, mail, clock):
    clock.set(2026, 11, 12, 15, 0)
    page = client.get("/reports/weekly-email?week=2026-11-10").get_data(as_text=True)
    assert "09 Nov 2026 – 15 Nov 2026" in page and "VAT return" in page
    reports = client.get("/reports").get_data(as_text=True)
    assert "Weekly email" in reports and "Send now" in reports
    r = client.post("/reports/weekly-email/send", data={"week": "2026-11-09", "range": "week"}, follow_redirects=True)
    assert "Sent the week of 09 Nov to me@example.com." in r.get_data(as_text=True)
    assert len(mail) == 1
    # Sent by hand: the one after Saturday's logout still goes.
    clock.set(2026, 11, 14, 14, 0)
    client.get("/api/reminders")
    assert len(mail) == 2
