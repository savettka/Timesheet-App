"""Every page tells search engines to stay away: the app is private, and a
sign-in page has no business in search results."""
import pytest

from app.models import User  # noqa: F401  (the user fixture needs the tables)


@pytest.mark.parametrize("path", ["/", "/login", "/login?next=/boards"])
def test_signed_out_pages_ask_not_to_be_listed(app, user_id, path):
    page = app.test_client().get(path)
    assert page.status_code == 200
    assert page.headers["X-Robots-Tag"] == "noindex"


def test_a_redirect_asks_too(app, user_id):
    response = app.test_client().get("/dashboard")
    assert response.status_code == 302
    assert response.headers["X-Robots-Tag"] == "noindex"


def test_a_missing_page_asks_too(app, user_id):
    response = app.test_client().get("/no-such-page")
    assert response.status_code == 404
    assert response.headers["X-Robots-Tag"] == "noindex"


def test_signed_in_pages_ask_too(client):
    assert client.get("/dashboard").headers["X-Robots-Tag"] == "noindex"
