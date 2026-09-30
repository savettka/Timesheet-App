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
ACCENT = "#4f46e5"
ACCENT_SOFT = "#eceeff"
AHEAD = "#157a3a"
BEHIND = "#b91c1c"
WARN_TEXT = "#7a4300"

# The PDF's pastels: a fill, and the text colour that sits on it. Every pair
# reads at 4.5:1 or better (WCAG AA); the lowest, rose on rose, is 5.30:1.
BRAND, BRAND_TEXT, BRAND_MUTED = "#eef0ff", "#3f3697", "#57519f"
SKY, SKY_TEXT = "#e4f0fb", "#1f4f7a"
PEACH, PEACH_TEXT = "#fdeee0", "#86461c"
LILAC, LILAC_TEXT = "#f1eafc", "#553a93"
MINT, MINT_TEXT = "#e3f2e9", "#2f6b4e"
ROSE, ROSE_TEXT = "#f9e4e7", "#9e3f48"
AMBER, AMBER_TEXT = "#fdefd2", "#7a4a00"
EVEN = "#f1f3f8"  # a balance of exactly 0
# Quieter tints for whole table rows, and the calendar's day tiles.
ROW_REST, ROW_LEAVE, ROW_TODAY = "#faf6ef", "#eef5fc", "#f3f2ff"
TILE_REST, TILE_FUTURE, TILE_TODAY = "#f5efe4", "#f5f6f9", "#e8e9ff"
HAIR = "#e6e8ee"

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
    ws.write_string(row, 10, _days_worked(report["days_worked"]), total)

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

    MARGIN = 40
    BAND_H = 78
    LEAD = 12  # where words start inside a tile, a band or the table

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
    def balance_color(self, minutes):
        return MINT_TEXT if minutes > 0 else (ROSE_TEXT if minutes < 0 else INK)

    def balance_fill(self, minutes):
        return MINT if minutes > 0 else (ROSE if minutes < 0 else None)

    def pill(self, x, baseline, value, font, size, color, fill, align="right"):
        """A soft pill of colour behind one value. x is where the text ends (or
        starts), as for a plain value, so a column of figures still lines up
        whether or not a figure has a pill."""
        w = self.width(value, font, size)
        pad_x, pad_y, cap = 5.5, 3.4, 0.718 * size
        h = cap + 2 * pad_y
        left = x - w if align == "right" else x
        self.rect(left - pad_x, baseline - cap - pad_y, w + 2 * pad_x, h, fill=fill, radius=h / 2)
        self.text(x, baseline, value, font, size, color, align)
        return w + 2 * pad_x

    def lines(self, value, max_width, font="Helvetica", size=9):
        """One line, or two split at a space -- a label in a narrow tile."""
        if self.width(value, font, size) <= max_width or " " not in value:
            return [self.fit(value, max_width, font, size)]
        words = value.split(" ")
        for cut in range(len(words) - 1, 0, -1):
            first = " ".join(words[:cut])
            if self.width(first, font, size) <= max_width:
                return [first, self.fit(" ".join(words[cut:]), max_width, font, size)]
        return [self.fit(value, max_width, font, size)]

    def band(self, heading):
        """The lavender band across the top: the app on the left, the month and
        whose it is on the right."""
        r = self.report
        self.rect(0, 0, self.W, self.BAND_H, fill=BRAND)
        x0, mid = self.MARGIN, self.BAND_H / 2
        size = 34
        s = size / 120.0
        # The mark's ink spans x 17-104, y 20-100 of its 120 box: its left edge
        # lines up with the margin, its middle with the band's.
        self.logo(x0 - 17 * s, mid - 60 * s, size, color=ACCENT)
        text_x = x0 + (104 - 17) * s + 9
        top_line, sub_line = mid - 1.6, mid + 12.4
        self.text(text_x, top_line, APP_NAME, "Helvetica-Bold", 18, BRAND_TEXT)
        self.text(text_x, sub_line, APP_FULL_NAME, "Helvetica", 9, BRAND_MUTED)
        right = self.W - x0
        self.text(right, top_line, f"{r['month_name']} {r['year']}", "Helvetica-Bold", 16, INK, "right")
        self.text(right, sub_line, self.fit(f"{heading} · {r['who']}", 240), "Helvetica", 9, BRAND_MUTED, "right")
        return self.BAND_H

    def footer(self, page_no, pages):
        y = self.H - 24
        self.text(self.MARGIN, y, f"Generated {_stamp(self.report['generated'])}", "Helvetica", 8, INK_3)
        self.text(self.W - self.MARGIN, y, f"Page {page_no} of {pages}", "Helvetica", 8, INK_3, "right")

    # -- page 1: every day
    def days_page(self):
        r = self.report
        x0, content = self.MARGIN, self.W - 2 * self.MARGIN
        top = self.band("Timesheet") + 18

        # The month's figures, a pastel tile each.
        gap, tile_h = 10, 58
        tile_w = (content - 3 * gap) / 4
        bal = r["balance"]
        tiles = [
            ("Worked", fmt_minutes(r["worked"]), SKY, SKY_TEXT, INK),
            ("Target so far" if r["running"] else "Target", fmt_minutes(r["target"]), PEACH, PEACH_TEXT, INK),
            ("Overtime", fmt_signed(bal), self.balance_fill(bal) or EVEN, self.balance_color(bal) if bal else INK_2,
             self.balance_color(bal)),
            ("Avg / day", fmt_minutes(r["average"]), LILAC, LILAC_TEXT, INK),
        ]
        for i, (name, value, fill, name_color, value_color) in enumerate(tiles):
            x = x0 + i * (tile_w + gap)
            self.rect(x, top, tile_w, tile_h, fill=fill, radius=9)
            self.text(x + self.LEAD, top + 21, name, "Helvetica-Bold", 8.5, name_color)
            self.text(x + self.LEAD, top + 45, value, "Helvetica-Bold", 18, value_color)
        y = top + tile_h + 18

        # The table.
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
            start of words. The first column starts where the tiles' words do."""
            _, w, align = cols[i]
            if align == "r":
                return edges[i] + w - inset
            return edges[i] + (self.LEAD if i == 0 else inset)

        def put(i, value, yy, font="Helvetica", size=9, color=INK):
            _, w, align = cols[i]
            if align == "r":
                self.text(at(i), yy, value, font, size, color, "right")
            else:
                self.text(at(i), yy, self.fit(value, edges[i] + w - 4 - at(i), font, size), font, size, color)

        def balance(i, minutes, yy, size):
            fill = self.balance_fill(minutes)
            if fill:
                self.pill(at(i), yy, fmt_signed(minutes), "Helvetica-Bold", size, self.balance_color(minutes), fill)
            else:
                put(i, fmt_signed(minutes), yy, "Helvetica-Bold", size, self.balance_color(minutes))

        head_h = 24
        self.rect(x0, y, content, head_h, fill=BRAND, radius=6)
        for i, (name, _, _) in enumerate(cols):
            put(i, name, y + 15.5, "Helvetica-Bold", 8.5, BRAND_TEXT)
        y += head_h

        # Room for the totals band and the footer comes off first, so the last
        # day can never land underneath them.
        days = r["days"]
        row_h = min(19.0, (self.H - 82 - y) / max(1, len(days)))
        notes_x = at(7)
        for day in days:
            tint = (ROW_TODAY if day["is_today"] else ROW_LEAVE if day["is_leave"]
                    else ROW_REST if day["is_rest"] else None)
            if tint:
                self.rect(x0, y, content, row_h, fill=tint)
            if day is not days[-1]:
                self.hline(x0, x0 + content, y + row_h, HAIR, 0.5)
            muted = day["is_rest"] or day["is_future"]
            ink = INK_3 if muted else INK
            base = y + row_h / 2 + 3.2
            put(0, f"{day['date']:%a %d %b}", base, "Helvetica-Bold" if day["is_today"] else "Helvetica", 9,
                BRAND_TEXT if day["is_today"] else ink)
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
                balance(6, day["balance"], base, 9)

            # A forgotten logout is a tag in its own row, not a footnote.
            x = notes_x
            if day["needs_logout"]:
                x += self.pill(x, base, day["status"], "Helvetica-Bold", 8, AMBER_TEXT, AMBER, "left") + 2
                note = day["notes"]
            else:
                note = " · ".join(part for part in (day["status"], day["notes"]) if part)
            if note:
                self.text(x, base, self.fit(note, x0 + content - 4 - x, "Helvetica", 8.5), "Helvetica", 8.5,
                          BRAND_TEXT if day["is_today"] else (INK_3 if muted else INK_2))
            y += row_h

        # The month's totals, in a band that closes the table.
        y += 6
        band_h = 26
        self.rect(x0, y, content, band_h, fill=BRAND, radius=6)
        base = y + band_h / 2 + 3.4
        put(0, "Total", base, "Helvetica-Bold", 9.5, BRAND_TEXT)
        put(3, fmt_duration(r["break_total"]), base, "Helvetica-Bold", 9.5)
        put(4, fmt_minutes(r["worked"]), base, "Helvetica-Bold", 9.5)
        put(5, fmt_minutes(r["target"]), base, "Helvetica-Bold", 9.5)
        balance(6, r["balance"], base, 9.5)
        self.text(notes_x, base, _days_worked(r["days_worked"]), "Helvetica", 8.5, BRAND_MUTED)
        self.footer(1, 2)

    # -- page 2: the month as pastel tiles
    def calendar_page(self):
        r = self.report
        x0, content = self.MARGIN, self.W - 2 * self.MARGIN
        head_y = self.band("Calendar") + 28
        gap, pad = 5, 9
        pitch = (content + gap) / 7
        tile_w = pitch - gap
        for i, name in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
            self.text(x0 + i * pitch + pad, head_y, name, "Helvetica-Bold", 8.5, INK_3)
        grid_top = head_y + 10

        weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(r["year"], r["month"])
        by_date = {d["date"]: d for d in r["days"]}
        pitch_h = min(104.0, (self.H - 46 - grid_top + gap) / len(weeks))
        tile_h = pitch_h - gap
        mid = tile_h * 0.5 + 2  # the hours, or what the day was, sit mid-tile
        inner = tile_w - 2 * pad
        small = ("Helvetica", 8)

        for w, week in enumerate(weeks):
            for i, d in enumerate(week):
                day = by_date.get(d)
                if day is None:  # a day of the months either side
                    continue
                x, y = x0 + i * pitch, grid_top + w * pitch_h
                bal = day["balance"]
                muted = day["is_rest"] or day["is_future"]
                # The tile's colour says what kind of day it was.
                if day["needs_logout"]:
                    fill, color = AMBER, AMBER_TEXT
                elif day["is_today"]:
                    fill, color = TILE_TODAY, BRAND_TEXT
                elif day["is_leave"]:
                    fill, color = SKY, SKY_TEXT
                elif day["is_rest"]:
                    fill, color = TILE_REST, INK_3
                elif day["is_future"]:
                    fill, color = TILE_FUTURE, INK_3
                elif bal:
                    fill, color = self.balance_fill(bal), self.balance_color(bal)
                else:
                    fill, color = EVEN, INK_2
                self.rect(x, y, tile_w, tile_h, fill=fill, radius=8)
                self.text(x + pad, y + 18, str(d.day), "Helvetica-Bold", 9.5,
                          BRAND_TEXT if day["is_today"] else (INK_3 if muted else INK_2))

                if day["needs_logout"] or day["worked"] is None:
                    # No hours to show: say what the day was instead. A rest day
                    # or a day still to come says enough by its colour.
                    if not day["status"] or (muted and not day["is_leave"]):
                        continue
                    label = self.lines(day["status"], inner, *small)
                    for n, line in enumerate(label):
                        self.text(x + pad, y + mid + n * 10, line, *small, color)
                    under, extra = y + mid + 10 * len(label) + 6, None
                else:
                    self.text(x + pad, y + mid, fmt_compact(day["worked"]), "Helvetica-Bold", 14, INK)
                    under = y + mid + 16
                    # With no balance to show: the leave it was, or "In progress".
                    extra = day["status"] if day["is_leave"] or (day["is_today"] and not day["logout"]) else None
                if bal and not day["needs_logout"]:
                    self.text(x + pad, under, fmt_signed_compact(bal), "Helvetica-Bold", 9, self.balance_color(bal))
                elif extra:
                    self.text(x + pad, under, self.fit(extra, inner, *small), *small, color)
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
