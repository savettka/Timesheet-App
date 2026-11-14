"""5.3 Arrange Home: each card shown or hidden and put in order, kept as
JSON in User.dashboard_layout. The clock card can move but never hide."""
import json
import re

from app import db
from app.models import User
import app.main as main


def home_cards(client):
    page = client.get("/dashboard").get_data(as_text=True)
    grid = page.split('<div class="dash-grid">', 1)[1]
    return re.findall(r'data-card="(\w+)"( hidden)?', grid)


def saved(app, user_id):
    with app.app_context():
        return json.loads(db.session.get(User, user_id).dashboard_layout)


def test_the_usual_order(client):
    # No timer or days to fill in for someone not logged in.
    assert [name for name, _ in home_cards(client)] == ["status", "month", "today", "week", "saturday", "goals",
                                                       "heatmap"]
    page = client.get("/dashboard").get_data(as_text=True)
    panel = page.split('id="dash-arrange"', 1)[1].split("</section>", 1)[0]
    assert re.findall(r'class="arr-item" data-card="(\w+)"', panel) == list(main.DASHBOARD_CARDS)
    assert "checked disabled" in panel  # the clock: always shown


def test_rearranged(app, client, user_id):
    r = client.post("/dashboard/layout", headers={"Accept": "application/json"},
                    data={"order": ["heatmap", "goals", "status", "nonsense", "goals"],
                          "hidden": ["goals", "status", "nonsense"]})
    assert r.get_json() == {"ok": True}
    assert saved(app, user_id) == {"order": ["heatmap", "goals", "status"], "hidden": ["goals"]}
    # Those it names first, then the rest in their usual order.
    assert home_cards(client) == [("heatmap", ""), ("goals", " hidden"), ("status", ""), ("month", ""),
                                  ("today", ""), ("week", ""), ("saturday", "")]


def test_unreadable_layouts(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        for bad in ("{not json", "[1, 2]", '{"order": "status", "hidden": 5}', '{"order": [1, null, "week"]}'):
            user.dashboard_layout = bad
            layout = main.dashboard_layout(user)
            assert sorted(c.key for c in layout) == sorted(main.DASHBOARD_CARDS)
            assert all(c.shown for c in layout)
        assert main.dashboard_layout(user)[0].key == "week"
        user.dashboard_layout = '{"hidden": ["status", "heatmap"]}'
        shown = {c.key: c.shown for c in main.dashboard_layout(user)}
        assert shown["status"] and not shown["heatmap"]


def test_without_a_page_to_come_back_to(client):
    r = client.post("/dashboard/layout", data={"order": ["week"]})
    assert r.headers["Location"].endswith("/dashboard")


def test_signed_out(app):
    r = app.test_client().post("/dashboard/layout", data={"order": ["week"]})
    assert r.status_code in (302, 401)
