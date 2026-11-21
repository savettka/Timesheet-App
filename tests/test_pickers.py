"""STM's own pickers (app.js, initPickers): every number box that offers
choices says what they are, a step apart, with its unit; the rest -- time,
date and dropdown boxes -- the script finds by their type."""
import re


def options(page, name):
    found = re.search(rf'name="{name}"[^>]*?data-options="([^"]+)"', page) or \
        re.search(rf'data-options="([^"]+)"[^>]*?name="{name}"', page)
    return found.group(1).split(",") if found else None


def test_settings_boxes_offer_choices(client):
    page = client.get("/settings").get_data(as_text=True)
    for day in range(7):
        assert options(page, f"hours_{day}") == ["0", "4", "5", "6", "7", "7.5", "8", "8.5", "9", "10"]
    assert options(page, "weekday_break_minutes") == ["0", "15", "30", "45", "60", "75", "90"]
    assert options(page, "long_day_hours") and options(page, "idle_minutes")
    assert options(page, "timesheet_written")[-1] == "100"
    assert 'data-unit="%"' in page and 'data-unit="h"' in page and 'data-unit="m"' in page


def test_other_pages(client):
    assert options(client.get("/history").get_data(as_text=True), "hours")[0] == "+0:30"
    assert options(client.get("/entry/2026-11-11").get_data(as_text=True), "target_override")


def test_the_script_is_there(app):
    with open(app.static_folder + "/js/app.js", encoding="utf-8") as f:
        js = f.read()
    assert "function initPickers()" in js and "initPickers();" in js
