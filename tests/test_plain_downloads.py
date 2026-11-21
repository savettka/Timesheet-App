"""Everything downloaded is plain, like a sheet typed into a spreadsheet:
no logo, no app name in the file or its name, no "generated" stamp, and one
light colour."""

import io
import pathlib
import re
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
BRANDS = ("STM", "Simple Time Manager")


def _month(client):
    client.post("/entry/2026-11-10", data={"login_time": "09:00", "logout_time": "18:00"})


def test_pdf_is_plain(client, monkeypatch):
    pytest.importorskip("reportlab")
    import app.exports as exports

    monkeypatch.setattr(exports, "PDF_COMPRESS", False)
    _month(client)
    r = client.get("/history/2026/11/export.pdf")
    assert r.status_code == 200
    assert 'filename="Timesheet November 2026.pdf"' in r.headers["Content-Disposition"]
    text = r.data.decode("latin-1")
    assert not any(b in text for b in BRANDS)
    assert "(Generated" not in text  # no stamp drawn on the page
    assert len(re.findall(r"/Type /Page[^s]", text)) == 1
    assert "(Name:)" in text and "(November 2026)" in text and "(Total)" in text
    assert "(10/11/2026)" in text and "(8:00)" in text  # dd/mm/yyyy and h:mm, as a spreadsheet shows them


def test_excel_is_plain(client):
    pytest.importorskip("xlsxwriter")
    _month(client)
    r = client.get("/history/2026/11/export.xlsx")
    assert r.status_code == 200
    assert 'filename="Timesheet November 2026.xlsx"' in r.headers["Content-Disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.data))
    for name in ("xl/sharedStrings.xml", "xl/worksheets/sheet1.xml", "docProps/core.xml", "docProps/app.xml"):
        body = z.read(name).decode("utf-8")
        assert not any(b in body for b in BRANDS), name
    # One fill colour only: the header row's light blue.
    fills = set(re.findall(r'<fgColor rgb="FF([0-9A-F]{6})"', z.read("xl/styles.xml").decode()))
    assert fills <= {"EAF2FB"}, fills


def test_csv_name_is_plain(client):
    r = client.get("/reports/lines.csv?range=week")
    assert r.status_code == 200
    assert 'filename="Timesheet lines ' in r.headers["Content-Disposition"]
    assert "STM" not in r.headers["Content-Disposition"]


def test_png_is_drawn_plain():
    js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    start = js.index("function drawTimesheet")
    body = js[start:js.index("function initTimesheetPng")]
    assert not any(b in body for b in BRANDS)
    assert "drawImage" not in body  # no logo
    assert "Generated" not in body
    assert all(word in js for word in ('"Name:"', '"Date:"', '"Start"', '"End"', '"Hours"', '"Task"', '"Total"'))
    page = (ROOT / "app" / "templates" / "timesheet.html").read_text(encoding="utf-8")
    assert 'data-png-name="Timesheet {{ day.isoformat() }}.png"' in page
