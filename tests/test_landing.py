"""The landing page at / for anyone signed out; Home for anyone signed in;
the sign-in page its own page, with a way back."""
import json
import os
import re

from app.models import User  # noqa: F401  (the user fixture needs the tables)


def test_signed_out_root_is_the_landing_page(app, user_id):
    page = app.test_client().get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'name="robots" content="noindex"' in html
    assert 'href="/login"' in html and 'class="lp"' in html
    assert 'class="sidebar' not in html


def test_free_and_who_to_ask(app, user_id):
    html = app.test_client().get("/").get_data(as_text=True)
    assert html.count("anyone can use it for free") == 1 and "mailto:" not in html  # STM_CONTACT_EMAIL unset
    app.config["CONTACT_EMAIL"] = "someone@example.org"
    html = app.test_client().get("/").get_data(as_text=True)
    assert html.count('href="mailto:someone@example.org?subject=STM%20account"') == 1
    assert "with the username you'd like" in html and 'class="btn btn-lg lp-mail-btn"' in html


def test_no_underlined_text_anywhere(app):
    with open(os.path.join(app.static_folder, "css", "style.css"), encoding="utf-8") as f:
        css = f.read()
    assert not re.search(r"text-decoration[\w-]*\s*:[^;{}]*underline", css)


def test_signed_in_root_opens_home(client):
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")


def test_sign_in_page_links_back(app, user_id):
    html = app.test_client().get("/login").get_data(as_text=True)
    assert 'href="/"' in html


def test_manifest_keeps_its_id_and_opens_home(app):
    with open(os.path.join(app.static_folder, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    assert manifest["id"] == "/" and manifest["start_url"] == "/dashboard"
