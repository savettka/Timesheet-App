"""The emailed sign-in code: six boxes drawn by app.js over one real box,
and a code pasted with spaces or dashes still signs in."""
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app import db
from app.models import LoginCode, User


def waiting_for_a_code(app, user_id, code="123456"):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.email = "tester@example.org"
        db.session.add(LoginCode(user_id=user_id, code_hash=generate_password_hash(code),
                                 expires_at=datetime.utcnow() + timedelta(minutes=10)))
        db.session.commit()
    visitor = app.test_client()
    with visitor.session_transaction() as session:
        session["code_email"] = "tester@example.org"
    return visitor


def test_one_real_box_marked_for_six(app, user_id):
    page = waiting_for_a_code(app, user_id).get("/login/verify").get_data(as_text=True)
    assert 'data-code-boxes="6"' in page and 'autocomplete="one-time-code"' in page
    assert page.count('name="code"') == 1
    # The button stays for a browser without the script; app.js hides it.
    assert 'class="btn btn-primary btn-block btn-lg code-submit"' in page


def test_a_pasted_code_with_a_dash_signs_in(app, user_id):
    response = waiting_for_a_code(app, user_id).post("/login/verify", data={"code": " 123-456 "})
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard")
