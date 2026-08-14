from app import create_app, db
from app.models import User, TimeEntry, BreakSegment
from datetime import date, time

app = create_app()

# (date, login, logout or None, [(break_start, break_end), ...])
DATA = [
    (date(2026, 8, 1),  time(10, 35), time(17, 20), [(time(16, 26), time(16, 56))]),
    (date(2026, 8, 3),  time(12, 52), time(23, 0),  [(time(20, 0), time(21, 0))]),
    (date(2026, 8, 4),  time(13, 0),  time(22, 31), [(time(16, 0), time(16, 59)), (time(19, 59), time(20, 29))]),
    (date(2026, 8, 5),  time(12, 50), time(22, 50), [(time(20, 0), time(21, 0))]),
    (date(2026, 8, 6),  time(12, 44), time(22, 30), [(time(20, 0), time(21, 0))]),
    (date(2026, 8, 7),  time(12, 47), time(3, 51),  [(time(20, 5), time(21, 5))]),
    (date(2026, 8, 10), time(12, 57), time(22, 14), [(time(16, 3), time(16, 53)), (time(21, 5), time(21, 15))]),
    (date(2026, 8, 11), time(17, 26), time(23, 11), [(time(20, 23), time(20, 53))]),
    (date(2026, 8, 12), time(12, 40), time(23, 6),  [(time(20, 0), time(21, 0))]),
    (date(2026, 8, 13), time(12, 55), time(22, 5),  [(time(20, 0), time(21, 0))]),
    (date(2026, 8, 14), time(12, 54), None,         []),
]

with app.app_context():
    user = User.query.first()
    if not user:
        print("No account found yet — visit your site once to create your STM login, then re-run this.")
    else:
        added, skipped = 0, 0
        for d, login, logout, breaks in DATA:
            if TimeEntry.query.filter_by(user_id=user.id, date=d).first():
                skipped += 1
                continue
            entry = TimeEntry(user_id=user.id, date=d, login_time=login, logout_time=logout)
            db.session.add(entry)
            db.session.flush()
            for bs, be in breaks:
                db.session.add(BreakSegment(entry_id=entry.id, break_start=bs, break_end=be))
            added += 1
        db.session.commit()
        print(f"Added {added} entries, skipped {skipped} that already existed.")
