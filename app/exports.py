"""A month of hours to take away: an Excel sheet, or a one-page PDF.

Both are built from the same month History shows (``_month_page`` in
main.py), with History's rules for what each day says, so a download never
disagrees with the screen.

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

# Plain on purpose: black and grey on white, with one light blue behind the
# table's header row. No logo and no app name on anything downloaded.
INK = "#151823"
INK_2 = "#3d4354"
INK_3 = "#555c6f"
LINE = "#dde1ea"
HAIR = "#e6e8ee"
LIGHT = "#eaf2fb"

MINUS = "−"

# Off only in tests, so the PDF's text can be read straight from the file.
PDF_COMPRESS = True


# ----------------------------------------------------------------- the month

def _minutes(hours):
    return round((hours or 0) * 60)


def fmt_minutes(minutes):
    """8h 42m -- STM's fmt_hours, from whole minutes."""
    h, m = divmod(abs(minutes), 60)
    return f"{MINUS if minutes < 0 else ''}{h}h {m:02d}m"


def fmt_signed(minutes):
    """+42m, +1h 24m, -2h 22m (with a real minus), 0m."""
    if minutes == 0:
        return "0m"
    h, m = divmod(abs(minutes), 60)
    sign = "+" if minutes > 0 else MINUS
    return f"{sign}{m}m" if h == 0 else f"{sign}{h}h {m:02d}m"


def fmt_duration(minutes):
    """25m, 1h 05m, 8h -- STM's fmt_duration, from whole minutes."""
    h, m = divmod(abs(minutes), 60)
    if h == 0:
        return f"{m}m"
    return f"{h}h" if m == 0 else f"{h}h {m:02d}m"


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
        still_going = False

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
            status = " · ".join(s for s in ("Week total", status) if s)
        elif left:
            status = " · ".join(s for s in (status, f"{fmt_minutes(left)} left this week") if s)
        elif banked and not status:
            status = f"Banked {fmt_signed(banked)}"

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
            "still_going": still_going,
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
    # The whole month's target, future days included -- "so far" is the
    # figure the balance uses, this is what the month will ask for in total.
    month_target = _minutes(sum(
        logic.target_hours_for(user, r["date"], r["entry"]) if r["entry"] is not None
        else logic.entry_target_hours(user, r["date"])
        for r in page["rows"]
    ))
    return {
        "year": page["year"],
        "month": page["month"],
        "month_name": page["month_name"],
        "who": user.display_name or user.username,
        "days": days,
        "worked": worked,
        "target": _minutes(summary["target_hours"]),
        "balance": _minutes(summary["balance_hours"]),
        "month_target": month_target,
        "days_worked": days_worked,
        "average": round(worked / days_worked) if days_worked else 0,
        "break_total": sum(d["break_minutes"] for d in days),
        "running": page["is_current_month"],
        "generated": now,
        # Hours added or taken off by hand: in the balance, listed apart.
        "adjustments": [(a.date, _minutes(a.hours), a.reason) for a in summary.get("adjustments", ())],
    }


def _balance_words(minutes):
    return "ahead of target" if minutes > 0 else ("behind target" if minutes < 0 else "on target")


def _days_worked(n):
    return f"{n} day worked" if n == 1 else f"{n} days worked"


def _stamp(now):
    return f"{now:%a %d %b %Y}, {logic.fmt_time(now.time())}"


def build(kind, page, user, now):
    report = month_report(page, user, now)
    return build_xlsx(report) if kind == "xlsx" else build_pdf(report)


# --------------------------------------------------------------------- Excel

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
            formats[key] = wb.add_format({"font_name": "Calibri", "font_size": 11, "valign": "vcenter", **props})
        return formats[key]

    columns = [
        ("Date", 13), ("Day", 6), ("Login", 10), ("Logout", 10), ("Break time", 10),
        ("Breaks", 30), ("Worked", 10), ("Target", 10), ("Over", 10), ("Short", 10),
        ("Status", 16), ("Edited by hand", 14), ("Notes", 36),
    ]
    for i, (_, width) in enumerate(columns):
        ws.set_column(i, i, width)

    # Heading and the month's figures.
    ws.set_row(0, 26)
    ws.write_string(0, 0, f"Timesheet · {title}", fmt(bold=True, font_size=16, font_color=INK))
    ws.write_string(1, 0, report["who"], fmt(font_color=INK_2))

    label = fmt(bold=True, font_size=9, font_color=INK_3)
    big = dict(bold=True, font_size=14, font_color=INK, align="left")
    summary = [
        ("Worked", timedelta(minutes=report["worked"]), fmt(num_format="[h]:mm", **big)),
        ("Target so far" if report["running"] else "Target", timedelta(minutes=report["target"]),
         fmt(num_format="[h]:mm", **big)),
        ("Overtime", f"{fmt_signed(report['balance'])} {_balance_words(report['balance'])}", fmt(**big)),
        ("Days worked", report["days_worked"], fmt(num_format="0", **big)),
        ("Average day", timedelta(minutes=report["average"]), fmt(num_format="[h]:mm", **big)),
    ]
    for i, (name, value, value_format) in enumerate(summary):
        col = i * 2
        ws.write_string(3, col, name, label)
        # Merged empty first, then written with an explicit type, so nothing
        # here can ever be read as a formula.
        ws.merge_range(4, col, 4, col + 1, "", value_format)
        if isinstance(value, timedelta):
            ws.write_datetime(4, col, value, value_format)
        elif isinstance(value, int):
            ws.write_number(4, col, value, value_format)
        else:
            ws.write_string(4, col, value, value_format)
    ws.set_row(4, 22)
    if report["running"]:
        ws.write_string(5, 0, f"The month is still running: figures are as of {_stamp(report['generated'])}. "
                              f"The whole month's target is {fmt_minutes(report['month_target'])}.",
                        fmt(italic=True, font_size=9, font_color=INK_3))

    # The days.
    head_row = 7
    head = fmt(bold=True, font_color=INK, bg_color=LIGHT, border=1, border_color=LINE)
    for i, (name, _) in enumerate(columns):
        ws.write_string(head_row, i, name, head)
    ws.set_row(head_row, 20)

    def cell(kind, day, **extra):
        props = {"border": 1, "border_color": LINE}
        if day["is_rest"] or day["is_future"]:
            props["font_color"] = INK_3
        if kind == "date":
            props["num_format"] = "dd mmm yyyy"
        elif kind == "time":
            props["num_format"] = "h:mm AM/PM"
            props["align"] = "left"
        elif kind == "duration":
            props["num_format"] = "[h]:mm"
        props.update(extra)
        return fmt(**props)

    row = head_row + 1
    first_day_row = row
    for day in report["days"]:
        ws.write_datetime(row, 0, datetime.combine(day["date"], time()), cell("date", day))
        ws.write_string(row, 1, f"{day['date']:%a}", cell("text", day))
        for col, value in ((2, day["login"]), (3, day["logout"])):
            if value is not None:
                ws.write_datetime(row, col, value, cell("time", day))
            else:
                ws.write_blank(row, col, None, cell("text", day))
        if day["break_minutes"]:
            ws.write_datetime(row, 4, timedelta(minutes=day["break_minutes"]), cell("duration", day))
        else:
            ws.write_blank(row, 4, None, cell("text", day))
        spans = ", ".join(
            f"{logic.fmt_time(start)}–{logic.fmt_time(end) if end else 'now'}" for start, end in day["breaks"]
        )
        ws.write_string(row, 5, spans, cell("text", day))
        for col, value in ((6, day["worked"]), (7, day["target"])):
            if value is not None:
                ws.write_datetime(row, col, timedelta(minutes=value), cell("duration", day))
            else:
                ws.write_blank(row, col, None, cell("text", day))
        # Over and Short are both plain durations, so each column adds up;
        # Excel can't show a negative time, which one signed column would need.
        balance = day["balance"]
        if balance is not None and balance > 0:
            ws.write_datetime(row, 8, timedelta(minutes=balance), cell("duration", day))
        else:
            ws.write_blank(row, 8, None, cell("text", day))
        if balance is not None and balance < 0:
            ws.write_datetime(row, 9, timedelta(minutes=-balance), cell("duration", day))
        else:
            ws.write_blank(row, 9, None, cell("text", day))
        status_extra = {"font_color": INK, "bold": True} if day["needs_logout"] else {}
        ws.write_string(row, 10, "Still going" if day["still_going"] and not day["status"] else day["status"],
                        cell("text", day, **status_extra))
        ws.write_string(row, 11, "Yes" if day["by_hand"] else "", cell("text", day))
        # write_string, never write: a note starting with "=" stays words.
        ws.write_string(row, 12, day["notes"], cell("text", day))
        row += 1

    last_day_row = row - 1
    total = fmt(bold=True, top=2, top_color=INK, bottom=1, bottom_color=LINE)
    total_duration = fmt(bold=True, num_format="[h]:mm", top=2, top_color=INK, bottom=1, bottom_color=LINE)
    ws.write_string(row, 0, "Total", total)
    for col in range(1, len(columns)):
        ws.write_blank(row, col, None, total)
    for col in (4, 6, 7, 8, 9):
        letter = chr(ord("A") + col)
        ws.write_formula(row, col, f"=SUM({letter}{first_day_row + 1}:{letter}{last_day_row + 1})", total_duration)
    ws.write_string(row, 10, _days_worked(report["days_worked"]), total)

    # Balance adjustments: in the month's overtime above, each a row of its own.
    for day, minutes, reason in report["adjustments"]:
        row += 1
        plain = {"border": 1, "border_color": LINE}
        ws.write_datetime(row, 0, datetime.combine(day, time()), fmt(num_format="dd mmm yyyy", **plain))
        ws.write_string(row, 1, "Adjustment", fmt(bold=True, **plain))
        over, short = (8, 9) if minutes > 0 else (9, 8)
        ws.write_datetime(row, over, timedelta(minutes=abs(minutes)), fmt(num_format="[h]:mm", **plain))
        ws.write_blank(row, short, None, fmt(**plain))
        ws.write_string(row, 12, reason, fmt(**plain))

    ws.freeze_panes(head_row + 1, 0)
    ws.autofilter(head_row, 0, last_day_row, len(columns) - 1)
    ws.set_landscape()
    ws.set_paper(9)  # A4
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(head_row)
    ws.set_header(f"&L&\"Calibri,Bold\"Timesheet · {title}&R{report['who'].replace('&', '&&')}")
    ws.set_footer("&CPage &P of &N")
    wb.close()
    return buf.getvalue()


# ----------------------------------------------------------------------- PDF

def _pdf_text(value):
    """Text Helvetica can draw: its built-in encoding has no minus sign, and
    anything else it lacks is spelled out plainly rather than dropped."""
    text = str(value).replace(MINUS, "–")
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

    MARGIN = 40
    BLEED = 8  # how far the header fill and the rules reach past the margin

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
        self.c.setSubject(f"Hours worked in {title}")

    # -- primitives
    def color(self, hex_color, stroke=False):
        from reportlab.lib.colors import HexColor

        (self.c.setStrokeColor if stroke else self.c.setFillColor)(HexColor(hex_color))

    def text(self, x, y, value, font="Helvetica", size=9, color=INK, align="left"):
        value = _pdf_text(value)
        self.c.setFont(font, size)
        self.color(color)
        y = self.H - y
        if align == "right":
            self.c.drawRightString(x, y, value)
        else:
            self.c.drawString(x, y, value)

    def width(self, value, font="Helvetica", size=9):
        return self._width(_pdf_text(value), font, size)

    def fit(self, value, max_width, font="Helvetica", size=9):
        """Shorten with an ellipsis until it fits."""
        value = _pdf_text(value)
        if self.width(value, font, size) <= max_width:
            return value
        while value and self.width(value + "…", font, size) > max_width:
            value = value[:-1]
        return value.rstrip() + "…"

    def rect(self, x, y, w, h, fill):
        self.color(fill)
        self.c.rect(x, self.H - y - h, w, h, stroke=0, fill=1)

    def hline(self, x1, x2, y, color=LINE, width=0.75):
        self.color(color, stroke=True)
        self.c.setLineWidth(width)
        self.c.line(x1, self.H - y, x2, self.H - y)

    # -- the page: the month, its figures, then every day
    def page(self):
        r = self.report
        x0, content, bleed = self.MARGIN, self.W - 2 * self.MARGIN, self.BLEED
        left, right = x0 - bleed, x0 + content + bleed

        self.text(x0, 58, f"{r['month_name']} {r['year']}", "Helvetica-Bold", 16, INK)
        self.text(x0, 74, self.fit(f"Timesheet · {r['who']}", content), "Helvetica", 9.5, INK_3)

        # The month's figures, each a small label over its value.
        figures = [
            ("Worked", fmt_minutes(r["worked"])),
            ("Target so far" if r["running"] else "Target", fmt_minutes(r["target"])),
            ("Overtime", fmt_signed(r["balance"])),
            ("Avg / day", fmt_minutes(r["average"])),
        ]
        step = content / len(figures)
        for i, (name, value) in enumerate(figures):
            self.text(x0 + i * step, 104, name, "Helvetica", 8.5, INK_3)
            self.text(x0 + i * step, 121, value, "Helvetica-Bold", 13, INK)
        y = 140

        # The table: the date lines up with the heading, figures end flush.
        cols = [("Date", 80, "l"), ("Login", 50, "r"), ("Logout", 50, "r"), ("Break", 42, "r"),
                ("Worked", 58, "r"), ("Target", 56, "r"), ("Overtime", 64, "r")]
        cols.append(("Notes", content - sum(w for _, w, _ in cols), "l"))
        edges, x = [], x0
        for _, w, _ in cols:
            edges.append(x)
            x += w
        inset = 10

        def at(i):
            """Where column i's text is anchored: the right end of a figure, the
            start of words."""
            _, w, align = cols[i]
            if align == "r":
                return edges[i] + w - inset
            return edges[i] + (0 if i == 0 else inset)

        def put(i, value, yy, font="Helvetica", size=9, color=INK):
            _, w, align = cols[i]
            if align == "r":
                self.text(at(i), yy, value, font, size, color, "right")
            else:
                self.text(at(i), yy, self.fit(value, edges[i] + w - 4 - at(i), font, size), font, size, color)

        head_h = 22
        self.rect(left, y, right - left, head_h, LIGHT)
        for i, (name, _, _) in enumerate(cols):
            put(i, name, y + 14.5, "Helvetica-Bold", 8.5, INK)
        y += head_h

        # Room for the total and the footer comes off first, so the last day
        # can never land underneath them.
        days = r["days"]
        row_h = min(19.0, (self.H - 70 - y) / max(1, len(days)))
        notes_x = at(7)
        for day in days:
            if day is not days[-1]:
                self.hline(left, right, y + row_h, HAIR, 0.5)
            muted = day["is_rest"] or day["is_future"]
            ink = INK_3 if muted else INK
            base = y + row_h / 2 + 3.2
            put(0, f"{day['date']:%a %d %b}", base, "Helvetica-Bold" if day["is_today"] else "Helvetica", 9, ink)
            # Only what the day has: a blank cell means nothing was recorded.
            if day["login"]:
                put(1, logic.fmt_time(day["login"]), base, color=ink)
            if day["logout"]:
                put(2, logic.fmt_time(day["logout"]), base, color=ink)
            if day["break_minutes"]:
                put(3, fmt_duration(day["break_minutes"]), base, color=ink)
            if day["worked"] is not None:
                put(4, fmt_minutes(day["worked"]), base, "Helvetica-Bold", 9, ink)
            if day["target"] is not None:
                put(5, fmt_minutes(day["target"]), base, color=ink)
            if day["balance"] is not None:
                put(6, fmt_signed(day["balance"]), base, "Helvetica-Bold", 9, ink)
            elif day["banked"] is not None:
                # Banked toward the week, not overtime yet: in grey.
                put(6, fmt_signed(day["banked"]), base, "Helvetica", 9, INK_3)

            # Times typed in by hand and a forgotten logout are bold words at
            # the start of the notes; then the day's own words.
            x, end = notes_x, x0 + content
            tags = (["Edited by hand"] if day["by_hand"] else []) + ([day["status"]] if day["needs_logout"] else [])
            for tag in tags:
                self.text(x, base, tag, "Helvetica-Bold", 8, INK)
                x += self.width(tag, "Helvetica-Bold", 8) + 8
            note = day["notes"] if day["needs_logout"] else " · ".join(
                part for part in (day["status"], day["notes"]) if part)
            if note and x < end - 12:
                self.text(x, base, self.fit(note, end - x, "Helvetica", 8.5), "Helvetica", 8.5,
                          INK_3 if muted else INK_2)
            y += row_h

        # The month's total, under a rule.
        y += 4
        self.hline(left, right, y, INK, 1)
        base = y + 15
        put(0, "Total", base, "Helvetica-Bold", 9.5)
        put(3, fmt_duration(r["break_total"]), base, "Helvetica-Bold", 9.5)
        put(4, fmt_minutes(r["worked"]), base, "Helvetica-Bold", 9.5)
        put(5, fmt_minutes(r["target"]), base, "Helvetica-Bold", 9.5)
        put(6, fmt_signed(r["balance"]), base, "Helvetica-Bold", 9.5)
        adjusted = sum(m for _, m, _ in r["adjustments"])
        self.text(notes_x, base, _days_worked(r["days_worked"])
                  + (f" · incl. {fmt_signed(adjusted)} adjusted" if r["adjustments"] else ""),
                  "Helvetica", 8.5, INK_3)

        self.text(x0, self.H - 24, f"Generated {_stamp(r['generated'])}", "Helvetica", 8, INK_3)

    def render(self):
        self.page()
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def build_pdf(report):
    return _Pdf(report).render()
