"""A month of hours to take away: an Excel sheet, or a PDF in STM's colours.

Both are built from the same month History shows (``_month_page`` in
main.py), with History's rules for what each day says, so a download never
disagrees with the screen.

The two libraries are imported inside the builders. A server that hasn't
installed them yet keeps running everything else; only the download itself
reports what's missing (as an ImportError, which the route turns into a
message).
"""

import calendar
import math
import unicodedata
from datetime import datetime, time, timedelta
from io import BytesIO

from app import logic
from app.models import TimeEntry

# STM's light-theme colours (app/static/css/style.css).
INK = "#151823"
INK_2 = "#3d4354"
INK_3 = "#555c6f"
LINE = "#dde1ea"
SURFACE_2 = "#f1f3f8"
ZEBRA = "#f7f8fb"
ACCENT = "#4f46e5"
ACCENT_SOFT = "#eceeff"
ACCENT_TEXT = "#4338ca"
ON_ACCENT_SOFT = "#e0e3ff"  # quiet text on the indigo band
AHEAD = "#157a3a"
BEHIND = "#b91c1c"
WARN_BG = "#fff3dc"
WARN_TEXT = "#7a4300"

APP_NAME = "STM"
APP_FULL_NAME = "Simple Time Manager"
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


def fmt_compact(minutes):
    """8h42, 45m -- the Calendar page's squeezed form."""
    h, m = divmod(abs(minutes), 60)
    return f"{m}m" if h == 0 else f"{h}h{m:02d}"


def fmt_signed_compact(minutes):
    """+42m, +1h24, -2h22."""
    if minutes == 0:
        return "0"
    return ("+" if minutes > 0 else MINUS) + fmt_compact(minutes)


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
        balance = _minutes(row["balance_hours"])

        # History's rules for the balance column.
        still_going = row["in_progress"] and balance <= 0
        if still_going or row["is_future"] or is_rest or (not worked_any and balance == 0):
            balance = None

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
    }


def _balance_words(minutes):
    return "ahead of target" if minutes > 0 else ("behind target" if minutes < 0 else "on target")


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
    wb.set_properties({
        "title": f"{APP_NAME} timesheet {title}",
        "author": report["who"],
        "comments": f"Exported from {APP_NAME} — {APP_FULL_NAME}",
    })
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
        ("Status", 16), ("Notes", 36),
    ]
    for i, (_, width) in enumerate(columns):
        ws.set_column(i, i, width)

    # Heading and the month's figures.
    ws.set_row(0, 26)
    ws.write_string(0, 0, f"{APP_NAME} — {APP_FULL_NAME}", fmt(bold=True, font_size=16, font_color=ACCENT))
    ws.write_string(1, 0, f"Timesheet for {title} · {report['who']}", fmt(font_color=INK_2))

    label = fmt(bold=True, font_size=9, font_color=INK_3)
    big = dict(bold=True, font_size=14, font_color=INK, align="left")
    summary = [
        ("Worked", timedelta(minutes=report["worked"]), fmt(num_format="[h]:mm", **big)),
        ("Target so far" if report["running"] else "Target", timedelta(minutes=report["target"]),
         fmt(num_format="[h]:mm", **big)),
        ("Overtime", f"{fmt_signed(report['balance'])} {_balance_words(report['balance'])}",
         fmt(**{**big, "font_color": AHEAD if report["balance"] > 0 else (BEHIND if report["balance"] < 0 else INK)})),
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
    head = fmt(bold=True, font_color="#ffffff", bg_color=ACCENT, border=1, border_color=ACCENT)
    for i, (name, _) in enumerate(columns):
        ws.write_string(head_row, i, name, head)
    ws.set_row(head_row, 20)

    def cell(kind, day, **extra):
        props = {"border": 1, "border_color": LINE}
        if day["is_today"]:
            props["bg_color"] = ACCENT_SOFT
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
            ws.write_datetime(row, 8, timedelta(minutes=balance), cell("duration", day, font_color=AHEAD))
        else:
            ws.write_blank(row, 8, None, cell("text", day))
        if balance is not None and balance < 0:
            ws.write_datetime(row, 9, timedelta(minutes=-balance), cell("duration", day, font_color=BEHIND))
        else:
            ws.write_blank(row, 9, None, cell("text", day))
        status_extra = {"font_color": WARN_TEXT, "bold": True} if day["needs_logout"] else {}
        ws.write_string(row, 10, "Still going" if day["still_going"] and not day["status"] else day["status"],
                        cell("text", day, **status_extra))
        # write_string, never write: a note starting with "=" stays words.
        ws.write_string(row, 11, day["notes"], cell("text", day))
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
    ws.write_string(row, 10, f"{report['days_worked']} days worked", total)

    ws.freeze_panes(head_row + 1, 0)
    ws.autofilter(head_row, 0, last_day_row, len(columns) - 1)
    ws.set_landscape()
    ws.set_paper(9)  # A4
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(head_row)
    ws.set_header(f"&L&\"Calibri,Bold\"{APP_NAME} — {APP_FULL_NAME}&R{title} · {report['who'].replace('&', '&&')}")
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


def _svg_arc(x1, y1, x2, y2, r, large, sweep):
    """Centre and angles of an SVG arc (circle), from its end points."""
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    square = max(0.0, (r * r - dx * dx - dy * dy) / (dx * dx + dy * dy))
    coef = (1 if large != sweep else -1) * math.sqrt(square)
    cx, cy = coef * dy + (x1 + x2) / 2, -coef * dx + (y1 + y2) / 2
    start = math.atan2(y1 - cy, x1 - cx)
    extent = math.atan2(y2 - cy, x2 - cx) - start
    if sweep == 0 and extent > 0:
        extent -= 2 * math.pi
    elif sweep == 1 and extent < 0:
        extent += 2 * math.pi
    return cx, cy, start, extent


# The STM mark from partials/logo.html, in its 120x120 box.
_RING = _svg_arc(71.06, 27.56, 91.71, 52.16, 38, 1, 0)


class _Pdf:
    """Drawing helpers that measure from the top of the page, like the SVG
    and the screen do, instead of ReportLab's bottom-left origin."""

    MARGIN = 36

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
        self.c.setTitle(f"{APP_NAME} timesheet — {title}")
        self.c.setAuthor(_pdf_text(report["who"]))
        self.c.setSubject(f"Hours worked in {title}")
        self.c.setCreator(f"{APP_NAME} — {APP_FULL_NAME}")

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
        elif align == "center":
            self.c.drawCentredString(x, y, value)
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

    def rect(self, x, y, w, h, fill=None, stroke=None, radius=0, line=0.75):
        if fill:
            self.color(fill)
        if stroke:
            self.color(stroke, stroke=True)
            self.c.setLineWidth(line)
        if radius:
            self.c.roundRect(x, self.H - y - h, w, h, radius, stroke=1 if stroke else 0, fill=1 if fill else 0)
        else:
            self.c.rect(x, self.H - y - h, w, h, stroke=1 if stroke else 0, fill=1 if fill else 0)

    def hline(self, x1, x2, y, color=LINE, width=0.75):
        self.color(color, stroke=True)
        self.c.setLineWidth(width)
        self.c.line(x1, self.H - y, x2, self.H - y)

    def vline(self, x, y1, y2, color=LINE, width=0.75):
        self.color(color, stroke=True)
        self.c.setLineWidth(width)
        self.c.line(x, self.H - y1, x, self.H - y2)

    def logo(self, x, y, size, color="#ffffff"):
        s = size / 120.0

        def at(px, py):
            return x + px * s, self.H - (y + py * s)

        c = self.c
        c.saveState()
        self.color(color, stroke=True)
        c.setLineCap(1)
        c.setLineJoin(1)
        cx, cy, start, extent = _RING
        ring = c.beginPath()
        ring.moveTo(*at(cx + 38 * math.cos(start), cy + 38 * math.sin(start)))
        for i in range(1, 49):
            a = start + extent * i / 48
            ring.lineTo(*at(cx + 38 * math.cos(a), cy + 38 * math.sin(a)))
        c.setLineWidth(8.5 * s)
        c.drawPath(ring, stroke=1, fill=0)
        curve = c.beginPath()
        curve.moveTo(*at(70, 47))
        curve.curveTo(*at(68, 37), *at(50, 34), *at(45, 43))
        curve.curveTo(*at(40, 52), *at(55, 59), *at(63, 66))
        curve.curveTo(*at(71, 73), *at(64, 88), *at(47, 84))
        c.drawPath(curve, stroke=1, fill=0)
        c.setLineWidth(7.5 * s)
        arrow = c.beginPath()
        arrow.moveTo(*at(34, 88))
        arrow.lineTo(*at(104, 20))
        arrow.moveTo(*at(94.2, 41.6))
        arrow.lineTo(*at(104, 20))
        arrow.lineTo(*at(81.8, 28.6))
        c.drawPath(arrow, stroke=1, fill=0)
        c.restoreState()

    # -- page furniture
    def band(self, heading):
        r = self.report
        self.rect(0, 0, self.W, 78, fill=ACCENT)
        self.logo(self.MARGIN, 19, 40)
        self.text(self.MARGIN + 50, 42, APP_NAME, "Helvetica-Bold", 20, "#ffffff")
        self.text(self.MARGIN + 50, 57, APP_FULL_NAME, "Helvetica", 9, ON_ACCENT_SOFT)
        right = self.W - self.MARGIN
        self.text(right, 38, f"{r['month_name']} {r['year']}", "Helvetica-Bold", 16, "#ffffff", "right")
        self.text(right, 55, self.fit(f"{heading} · {r['who']}", 260), "Helvetica", 9, ON_ACCENT_SOFT, "right")

    def footer(self, page_no, pages):
        y = self.H - 26
        self.hline(self.MARGIN, self.W - self.MARGIN, y - 12)
        self.text(self.MARGIN, y, f"{APP_NAME} · {APP_FULL_NAME}", "Helvetica-Bold", 8, ACCENT_TEXT)
        self.text(self.W - self.MARGIN, y, f"Generated {_stamp(self.report['generated'])} · Page {page_no} of {pages}",
                  "Helvetica", 8, INK_3, "right")

    def balance_color(self, minutes):
        return AHEAD if minutes > 0 else (BEHIND if minutes < 0 else INK)

    # -- page 1: every day
    def days_page(self):
        r = self.report
        self.band("Timesheet")
        x0, content = self.MARGIN, self.W - 2 * self.MARGIN

        # The month's figures.
        top, gap = 96, 10
        card_w = (content - 3 * gap) / 4
        target_sub = f"so far, of {fmt_duration(r['month_target'])}" if r["running"] else "for the month"
        cards = [
            ("WORKED", fmt_minutes(r["worked"]), INK, f"{r['days_worked']} days worked"),
            ("TARGET", fmt_minutes(r["target"]), INK, target_sub),
            ("OVERTIME", fmt_signed(r["balance"]), self.balance_color(r["balance"]), _balance_words(r["balance"])),
            ("AVERAGE DAY", fmt_minutes(r["average"]), INK, "per day worked"),
        ]
        for i, (name, value, color, sub) in enumerate(cards):
            x = x0 + i * (card_w + gap)
            self.rect(x, top, card_w, 62, fill=SURFACE_2, radius=8)
            self.text(x + 11, top + 17, name, "Helvetica-Bold", 7.5, INK_3)
            self.text(x + 11, top + 38, value, "Helvetica-Bold", 16, color)
            self.text(x + 11, top + 53, self.fit(sub, card_w - 22, size=7.5), "Helvetica", 7.5, INK_3)
        y = top + 62 + 16
        if r["running"]:
            self.text(x0, y, f"The month is still running: figures are as of {_stamp(r['generated'])}.",
                      "Helvetica-Oblique", 8, INK_3)
            y += 12

        # The table.
        cols = [("Date", 70, "l"), ("Login", 54, "l"), ("Logout", 54, "l"), ("Breaks", 50, "r"),
                ("Worked", 56, "r"), ("Target", 52, "r"), ("Overtime", 60, "r"), ("Notes", 127, "l")]
        pad = 6
        edges, x = [], x0
        for _, w, _ in cols:
            edges.append(x)
            x += w

        def put(i, value, yy, font="Helvetica", size=8.5, color=INK):
            name, w, align = cols[i]
            if align == "r":
                self.text(edges[i] + w - pad, yy, value, font, size, color, "right")
            else:
                self.text(edges[i] + pad, yy, self.fit(value, w - 2 * pad, font, size), font, size, color)

        head_h = 20
        self.rect(x0, y, content, head_h, fill=ACCENT_SOFT, radius=4)
        for i, (name, _, _) in enumerate(cols):
            put(i, name, y + 13.5, "Helvetica-Bold", 8, ACCENT_TEXT)
        y += head_h

        days = r["days"]
        bottom = self.H - 58
        row_h = min(19.0, (bottom - y - 22) / max(1, len(days)))
        for n, day in enumerate(days):
            if day["is_today"]:
                self.rect(x0, y, content, row_h, fill=ACCENT_SOFT)
            elif n % 2:
                self.rect(x0, y, content, row_h, fill=ZEBRA)
            muted = day["is_rest"] or day["is_future"]
            ink = INK_3 if muted else INK
            # A rest day, a day off or a day still to come has nothing to
            # report, so its cells stay empty; a dash marks something missing
            # on a working day.
            none = "" if muted or (day["is_leave"] and not day["worked_any"]) else "–"
            base = y + row_h / 2 + 3
            put(0, f"{day['date']:%a %d %b}", base, "Helvetica-Bold" if day["is_today"] else "Helvetica", 8.5, ink)
            put(1, logic.fmt_time(day["login"]) if day["login"] else none, base, color=ink if day["login"] else INK_3)
            put(2, logic.fmt_time(day["logout"]) if day["logout"] else none, base, color=ink if day["logout"] else INK_3)
            put(3, fmt_duration(day["break_minutes"]) if day["break_minutes"] else none, base,
                color=ink if day["break_minutes"] else INK_3)
            put(4, fmt_minutes(day["worked"]) if day["worked"] is not None else none, base,
                "Helvetica-Bold" if day["worked"] is not None else "Helvetica",
                color=ink if day["worked"] is not None else INK_3)
            put(5, fmt_minutes(day["target"]) if day["target"] is not None else none, base,
                color=ink if day["target"] is not None else INK_3)
            if day["still_going"]:
                put(6, "Still going", base, "Helvetica-Oblique", 8, INK_3)
            elif day["balance"] is not None:
                put(6, fmt_signed(day["balance"]), base, "Helvetica-Bold", 8.5, self.balance_color(day["balance"]))
            else:
                put(6, none, base, color=INK_3)
            note = " · ".join(part for part in (day["status"], day["notes"]) if part)
            put(7, note, base, "Helvetica-Bold" if day["needs_logout"] else "Helvetica", 8,
                WARN_TEXT if day["needs_logout"] else INK_2)
            y += row_h

        # The month's totals, under the days they add up.
        self.hline(x0, x0 + content, y, INK, 1.2)
        y += 16
        put(0, "Total", y, "Helvetica-Bold", 9)
        put(3, fmt_minutes(r["break_total"]), y, "Helvetica-Bold", 9)
        put(4, fmt_minutes(r["worked"]), y, "Helvetica-Bold", 9)
        put(5, fmt_minutes(r["target"]), y, "Helvetica-Bold", 9)
        put(6, fmt_signed(r["balance"]), y, "Helvetica-Bold", 9, self.balance_color(r["balance"]))
        put(7, f"{r['days_worked']} days worked", y, "Helvetica", 8, INK_2)
        self.footer(1, 2)

    # -- page 2: the month as a calendar
    def calendar_page(self):
        r = self.report
        self.band("Calendar")
        x0, content = self.MARGIN, self.W - 2 * self.MARGIN
        self.text(x0, 106, f"{r['month_name']} {r['year']} at a glance", "Helvetica-Bold", 15, INK)

        # "176h 48m worked · +16h 10m ahead · target 160h 38m", in pieces.
        direction = "ahead" if r["balance"] > 0 else ("behind" if r["balance"] < 0 else "on target")
        pieces = [(fmt_minutes(r["worked"]), "Helvetica-Bold", INK), (" worked  ·  ", "Helvetica", INK_2),
                  (fmt_signed(r["balance"]), "Helvetica-Bold", self.balance_color(r["balance"])),
                  (f" {direction}  ·  target ", "Helvetica", INK_2),
                  (fmt_minutes(r["target"]), "Helvetica-Bold", INK),
                  (" so far" if r["running"] else "", "Helvetica", INK_2)]
        x = x0
        for value, font, color in pieces:
            self.text(x, 124, value, font, 9.5, color)
            x += self.width(value, font, 9.5)

        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(r["year"], r["month"])
        by_date = {d["date"]: d for d in r["days"]}
        cell_w = content / 7
        top = 146
        for i, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            self.text(x0 + i * cell_w + 8, top + 10, name, "Helvetica-Bold", 8.5, INK_3)
        grid_top = top + 18
        cell_h = min(120.0, (self.H - 64 - grid_top) / len(weeks))

        for w, week in enumerate(weeks):
            for i, d in enumerate(week):
                x = x0 + i * cell_w
                y = grid_top + w * cell_h
                day = by_date.get(d)
                if day is None:  # a day of the months either side
                    continue
                if day["needs_logout"]:
                    self.rect(x, y, cell_w, cell_h, fill=WARN_BG)
                num_color = INK_3 if (day["is_rest"] or day["is_future"]) else INK_2
                if day["is_today"]:
                    self.color(ACCENT)
                    self.c.circle(x + 16, self.H - (y + 15), 9.5, stroke=0, fill=1)
                    self.text(x + 16, y + 18.2, str(d.day), "Helvetica-Bold", 9.5, "#ffffff", "center")
                else:
                    self.text(x + 8, y + 18.2, str(d.day), "Helvetica-Bold", 9.5, num_color)

                if day["needs_logout"]:
                    self.text(x + 8, y + 44, "Fix", "Helvetica-Bold", 13, WARN_TEXT)
                    self.text(x + 8, y + 58, "logout missing", "Helvetica", 7.5, WARN_TEXT)
                elif day["worked"] is not None:
                    self.text(x + 8, y + 44, fmt_compact(day["worked"]), "Helvetica-Bold", 13, INK)
                elif day["is_leave"]:
                    self.text(x + 8, y + 44, self.fit(day["status"], cell_w - 16, "Helvetica", 9.5),
                              "Helvetica", 9.5, INK_2)
                elif day["is_missing"]:
                    self.text(x + 8, y + 44, "–", "Helvetica", 13, INK_3)
                if day["balance"] is not None and not day["needs_logout"]:
                    self.text(x + 8, y + 60, fmt_signed_compact(day["balance"]), "Helvetica-Bold", 9,
                              self.balance_color(day["balance"]))
                if day["notes"] and cell_h >= 84:
                    self.text(x + 8, y + cell_h - 9, self.fit(day["notes"], cell_w - 16, size=7), "Helvetica", 7,
                              INK_3)

        # Hairlines, like the Calendar page: between weeks, and a light frame.
        grid_bottom = grid_top + len(weeks) * cell_h
        for w in range(len(weeks) + 1):
            self.hline(x0, x0 + content, grid_top + w * cell_h, LINE if 0 < w < len(weeks) else INK_3,
                       0.75 if 0 < w < len(weeks) else 1)
        for i in range(1, 7):
            self.vline(x0 + i * cell_w, grid_top, grid_bottom, LINE, 0.5)
        self.footer(2, 2)

    def render(self):
        self.days_page()
        self.c.showPage()
        self.calendar_page()
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def build_pdf(report):
    return _Pdf(report).render()
