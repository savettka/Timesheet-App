"""A month of hours to take away: an Excel sheet, or a one-page PDF.

Both are built from the same month History shows (``_month_page`` in
main.py), with History's rules for what each day says, so a download never
disagrees with the screen.

Both look like a sheet someone typed into a spreadsheet: Name and Month at
the top, then a grid of days in the spreadsheet's own formats (dd/mm/yyyy,
h:mm) with a Total. Black on white, one light blue behind the header row,
and no logo, app name or "generated" stamp.

The two libraries are imported inside the builders. A server that hasn't
installed them yet keeps running everything else; only the download itself
reports what's missing (as an ImportError, which the route turns into a
message).
"""

import unicodedata
from datetime import datetime, time, timedelta
from io import BytesIO

from app import logic
from app.models import TimeEntry

INK = "#000000"
GREY = "#595959"
GRID = "#bfbfbf"
LIGHT = "#eaf2fb"

MINUS = "−"

# Off only in tests, so the PDF's text can be read straight from the file.
PDF_COMPRESS = True


# ----------------------------------------------------------------- the month

def _minutes(hours):
    return round((hours or 0) * 60)


def hm(minutes, signed=False):
    """8:30, 0:45, 24:30; signed: +0:30, -2:15, 0:00 -- a spreadsheet's h:mm."""
    h, m = divmod(abs(minutes), 60)
    sign = "-" if minutes < 0 else ("+" if signed and minutes > 0 else "")
    return f"{sign}{h}:{m:02d}"


def _leave_label(entry):
    if entry.leave_label:
        return entry.leave_label
    kind = entry.effective_leave_type
    return TimeEntry.LEAVE_TYPES.get(kind) or ("Custom hours" if kind == "custom" else "Leave")


def month_report(page, user, now):
    """The month as plain values: one record per day, and the totals."""
    days = []
    for row in page["rows"]:
        e = row["entry"]
        d = row["date"]
        worked_any = bool(e and e.login_time)
        is_leave = bool(e and e.target_override is not None)
        is_rest = row["target_hours"] == 0 and not e and not row["is_future"]
        is_missing = not e and row["target_hours"] > 0 and not row["is_future"] and not row["is_today"]
        # Overtime counts by the week (logic.settle_weeks): a weekday banks
        # its hours, shown beside it; the week's result sits on its last
        # workday once that's begun. Each row's balance is what it adds to
        # the month, so the columns still add up.
        settles_now = bool(row.get("settles") and row.get("settled"))
        contribution = _minutes(row["balance_hours"])
        balance = contribution if settles_now or contribution else None
        banked = row.get("banked_hours")
        banked = _minutes(banked) if banked is not None and not settles_now else None
        left = (_minutes(row["target_left"]) if row.get("settles") and not row.get("settled")
                and not row["is_future"] else None)

        if row["needs_logout"]:
            status = "Logout missing"
        elif is_leave:
            status = _leave_label(e)
        elif is_rest:
            status = "Rest day"
        elif is_missing:
            status = "Not logged"
        elif row["is_today"] and worked_any and not e.logout_time:
            status = "In progress"
        elif row["is_today"]:
            status = "Today"
        else:
            status = ""
        if settles_now:
            status = ", ".join(s for s in ("Week total", status) if s)
        elif left:
            status = ", ".join(s for s in (status, f"{hm(left)} left this week") if s)
        elif banked and not status:
            status = f"Banked {hm(banked, signed=True)}"

        days.append({
            "date": d,
            "login": e.login_time if e else None,
            "logout": e.logout_time if e else None,
            "breaks": [(b.break_start, b.break_end) for b in e.breaks] if e else [],
            "break_minutes": _minutes(logic.entry_break_hours(e, now=now)) if e else 0,
            "worked": _minutes(row["worked_hours"]) if worked_any else None,
            "target": (_minutes(row["target_hours"])
                       if (not row["is_future"] or is_leave) and not is_rest else None),
            "balance": balance,
            "banked": banked,
            "week": settles_now,
            "left": left,
            "status": status,
            "notes": (e.notes or "").strip() if e else "",
            "is_today": row["is_today"],
            "is_future": row["is_future"],
            "is_rest": is_rest,
            "is_leave": is_leave,
            "is_missing": is_missing,
            "needs_logout": row["needs_logout"],
            "worked_any": worked_any,
            "by_hand": d in page.get("edited", ()),
        })

    summary = page["summary"]
    days_worked = sum(1 for d in days if d["worked_any"])
    worked = _minutes(summary["worked_hours"])
    return {
        "year": page["year"],
        "month": page["month"],
        "month_name": page["month_name"],
        "who": user.display_name or user.username,
        "days": days,
        "worked": worked,
        "target": _minutes(summary["target_hours"]),
        "balance": _minutes(summary["balance_hours"]),
        "days_worked": days_worked,
        "break_total": sum(d["break_minutes"] for d in days),
        "running": page["is_current_month"],
        "generated": now,
        # Hours added or taken off by hand: in the balance, listed apart.
        "adjustments": [(a.date, _minutes(a.hours), a.reason) for a in summary.get("adjustments", ())],
    }


def _note(day):
    """What the Notes cell says: the day's status, then its own words."""
    return ", ".join(part for part in (day["status"], day["notes"]) if part)


def build(kind, page, user, now):
    report = month_report(page, user, now)
    return build_xlsx(report) if kind == "xlsx" else build_pdf(report)


# --------------------------------------------------------------------- Excel

COLUMNS = [
    ("Date", 11), ("Day", 6), ("Login", 10), ("Logout", 10), ("Break", 8), ("Breaks", 28),
    ("Worked", 9), ("Target", 9), ("Over", 9), ("Short", 9), ("Status", 24), ("Notes", 36),
]


def build_xlsx(report):
    import xlsxwriter

    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    title = f"{report['month_name']} {report['year']}"
    wb.set_properties({"title": f"Timesheet {title}", "author": report["who"]})
    ws = wb.add_worksheet(f"{report['month_name'][:3]} {report['year']}")

    formats = {}

    def fmt(**props):
        key = tuple(sorted(props.items()))
        if key not in formats:
            formats[key] = wb.add_format({"font_name": "Calibri", "font_size": 11, **props})
        return formats[key]

    for i, (_, width) in enumerate(COLUMNS):
        ws.set_column(i, i, width)

    bold = fmt(bold=True)
    ws.write_string(0, 0, "Name:", bold)
    ws.write_string(0, 1, report["who"])
    ws.write_string(1, 0, "Month:", bold)
    ws.write_string(1, 1, title)

    head_row = 3
    for i, (name, _) in enumerate(COLUMNS):
        ws.write_string(head_row, i, name, fmt(bold=True, bg_color=LIGHT, border=1, border_color=GRID))

    def cell(kind="text", **extra):
        props = {"border": 1, "border_color": GRID, **extra}
        if kind == "date":
            props["num_format"] = "dd/mm/yyyy"
        elif kind == "time":
            props["num_format"] = "h:mm AM/PM"
        elif kind == "duration":
            props["num_format"] = "[h]:mm"
        return fmt(**props)

    def duration_or_blank(row, col, minutes):
        if minutes:
            ws.write_datetime(row, col, timedelta(minutes=minutes), cell("duration"))
        else:
            ws.write_blank(row, col, None, cell())

    row = head_row + 1
    first_day_row = row
    for day in report["days"]:
        ws.write_datetime(row, 0, datetime.combine(day["date"], time()), cell("date"))
        ws.write_string(row, 1, f"{day['date']:%a}", cell())
        for col, value in ((2, day["login"]), (3, day["logout"])):
            if value is not None:
                ws.write_datetime(row, col, value, cell("time"))
            else:
                ws.write_blank(row, col, None, cell())
        duration_or_blank(row, 4, day["break_minutes"])
        spans = ", ".join(
            f"{logic.fmt_time(start)}-{logic.fmt_time(end) if end else 'now'}" for start, end in day["breaks"]
        )
        ws.write_string(row, 5, spans, cell())
        for col, value in ((6, day["worked"]), (7, day["target"])):
            if value is not None:
                ws.write_datetime(row, col, timedelta(minutes=value), cell("duration"))
            else:
                ws.write_blank(row, col, None, cell())
        # Over and Short are both plain durations, so each column adds up;
        # Excel can't show a negative time, which one signed column would need.
        balance = day["balance"] or 0
        duration_or_blank(row, 8, balance if balance > 0 else 0)
        duration_or_blank(row, 9, -balance if balance < 0 else 0)
        ws.write_string(row, 10, day["status"], cell())
        # write_string, never write: a note starting with "=" stays words.
        ws.write_string(row, 11, day["notes"], cell())
        row += 1

    last_day_row = row - 1
    ws.write_string(row, 0, "Total", cell(bold=True))
    for col in range(1, len(COLUMNS)):
        ws.write_blank(row, col, None, cell(bold=True))
    for col in (4, 6, 7, 8, 9):
        letter = chr(ord("A") + col)
        ws.write_formula(row, col, f"=SUM({letter}{first_day_row + 1}:{letter}{last_day_row + 1})",
                         cell("duration", bold=True))

    # Balance adjustments: in the month's overtime below, each a row of its own.
    for day, minutes, reason in report["adjustments"]:
        row += 1
        ws.write_datetime(row, 0, datetime.combine(day, time()), cell("date"))
        ws.write_string(row, 1, "Adjustment", cell())
        over, short = (8, 9) if minutes > 0 else (9, 8)
        ws.write_datetime(row, over, timedelta(minutes=abs(minutes)), cell("duration"))
        ws.write_blank(row, short, None, cell())
        ws.write_string(row, 11, reason, cell())

    row += 1
    ws.write_string(row, 7, "Overtime", bold)
    ws.write_string(row, 8, hm(report["balance"], signed=True), fmt(bold=True, align="right"))

    ws.set_landscape()
    ws.set_paper(9)  # A4
    ws.fit_to_pages(1, 0)
    wb.close()
    return buf.getvalue()


# ----------------------------------------------------------------------- PDF

def _pdf_text(value):
    """Text Helvetica can draw: its built-in encoding has no minus sign, and
    anything else it lacks is spelled out plainly rather than dropped."""
    text = str(value).replace(MINUS, "-")
    try:
        text.encode("cp1252")
        return text
    except UnicodeEncodeError:
        out = []
        for ch in text:
            try:
                ch.encode("cp1252")
                out.append(ch)
            except UnicodeEncodeError:
                plain = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
                out.append(plain or "?")
        return "".join(out)


class _Pdf:
    """Drawing helpers that measure from the top of the page, like the
    screen does, instead of ReportLab's bottom-left origin."""

    MARGIN = 36
    CELL_X = 4  # words start this far inside their cell

    def __init__(self, report):
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas

        self.report = report
        self.W, self.H = A4
        self.buf = BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4, pageCompression=1 if PDF_COMPRESS else 0)
        self._width = stringWidth
        title = f"{report['month_name']} {report['year']}"
        self.c.setTitle(f"Timesheet {title}")
        self.c.setAuthor(_pdf_text(report["who"]))

    # -- primitives
    def color(self, hex_color, stroke=False):
        from reportlab.lib.colors import HexColor

        (self.c.setStrokeColor if stroke else self.c.setFillColor)(HexColor(hex_color))

    def text(self, x, y, value, font="Helvetica", size=8.5, color=INK, align="left"):
        value = _pdf_text(value)
        self.c.setFont(font, size)
        self.color(color)
        y = self.H - y
        if align == "right":
            self.c.drawRightString(x, y, value)
        else:
            self.c.drawString(x, y, value)

    def fit(self, value, max_width, font="Helvetica", size=8.5):
        """Cut short, as a spreadsheet cell cuts what doesn't fit."""
        value = _pdf_text(value)
        while value and self._width(value, font, size) > max_width:
            value = value[:-1]
        return value

    def line(self, x1, y1, x2, y2):
        self.color(GRID, stroke=True)
        self.c.setLineWidth(0.5)
        self.c.line(x1, self.H - y1, x2, self.H - y2)

    # -- the page: Name and Month, then a grid of days with a Total
    def page(self):
        r = self.report
        x0, content = self.MARGIN, self.W - 2 * self.MARGIN
        cx = self.CELL_X

        self.text(x0 + cx, 48, "Name:", "Helvetica-Bold", 9)
        self.text(x0 + 60, 48, r["who"], "Helvetica", 9)
        self.text(x0 + cx, 62, "Month:", "Helvetica-Bold", 9)
        self.text(x0 + 60, 62, f"{r['month_name']} {r['year']}", "Helvetica", 9)

        # Figures at the right of their cell, words at the left, as a
        # spreadsheet puts them; every heading is words.
        cols = [("Date", 56, "l"), ("Day", 28, "l"), ("Login", 48, "r"), ("Logout", 48, "r"),
                ("Break", 36, "r"), ("Worked", 40, "r"), ("Target", 40, "r"), ("Overtime", 46, "r")]
        cols.append(("Notes", content - sum(w for _, w, _ in cols), "l"))
        xs = [x0]
        for _, w, _ in cols:
            xs.append(xs[-1] + w)

        def put(i, value, y, h, font="Helvetica", color=INK, align=None):
            """A value at the foot of its cell, cut to fit."""
            align = align or cols[i][2]
            room = cols[i][1] - 2 * cx
            base = y + h - 4.5
            if align == "r":
                self.text(xs[i + 1] - cx, base, self.fit(value, room, font), font, 8.5, color, "right")
            else:
                self.text(xs[i] + cx, base, self.fit(value, room, font), font, 8.5, color)

        top = y = 80
        row_h = 16.0
        self.color(LIGHT)
        self.c.rect(xs[0], self.H - y - row_h, xs[-1] - xs[0], row_h, stroke=0, fill=1)
        for i, (name, _, _) in enumerate(cols):
            put(i, name, y, row_h, "Helvetica-Bold", align="l")
        edges = [y]
        y += row_h
        edges.append(y)

        days = r["days"]
        row_h = min(row_h, (self.H - 40 - y - 2 * row_h) / max(1, len(days)))
        for day in days:
            put(0, f"{day['date']:%d/%m/%Y}", y, row_h)
            put(1, f"{day['date']:%a}", y, row_h)
            # Only what the day has: a blank cell means nothing was recorded.
            if day["login"]:
                put(2, logic.fmt_time(day["login"]), y, row_h)
            if day["logout"]:
                put(3, logic.fmt_time(day["logout"]), y, row_h)
            if day["break_minutes"]:
                put(4, hm(day["break_minutes"]), y, row_h)
            if day["worked"] is not None:
                put(5, hm(day["worked"]), y, row_h)
            if day["target"] is not None:
                put(6, hm(day["target"]), y, row_h)
            if day["balance"] is not None:
                put(7, hm(day["balance"], signed=True), y, row_h)
            elif day["banked"] is not None:
                # Banked toward the week, not overtime yet: in grey.
                put(7, hm(day["banked"], signed=True), y, row_h, color=GREY)
            note = _note(day)
            if note:
                put(8, note, y, row_h)
            y += row_h
            edges.append(y)

        for _, minutes, reason in r["adjustments"]:
            put(0, "Adjustment", y, row_h)
            put(7, hm(minutes, signed=True), y, row_h)
            put(8, reason, y, row_h)
            y += row_h
            edges.append(y)

        put(0, "Total", y, row_h, "Helvetica-Bold")
        put(4, hm(r["break_total"]), y, row_h, "Helvetica-Bold")
        put(5, hm(r["worked"]), y, row_h, "Helvetica-Bold")
        put(6, hm(r["target"]), y, row_h, "Helvetica-Bold")
        put(7, hm(r["balance"], signed=True), y, row_h, "Helvetica-Bold")
        y += row_h
        edges.append(y)

        # The grid: every cell bordered, from the headings to the total.
        for e in edges:
            self.line(xs[0], e, xs[-1], e)
        for x in xs:
            self.line(x, top, x, y)

    def render(self):
        self.page()
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def build_pdf(report):
    return _Pdf(report).render()
