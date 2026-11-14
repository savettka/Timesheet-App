"""Updates from GitHub (app/deploy.py): the webhook is signed or turned
away, and an update only ever steps forward -- backing up the database,
installing nothing it needn't, and touching the WSGI file to reload."""
import hashlib
import hmac
import json
import os
import subprocess

import pytest

from app import deploy

BRANCH = "claude/stm-timesheet-app-tvtw3c"
SECRET = "test-only-secret"


def sign(body, secret=SECRET):
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify():
    body = b'{"ref": "x"}'
    assert deploy.verify(SECRET, body, sign(body))
    assert not deploy.verify(SECRET, body, sign(body, "another"))
    assert not deploy.verify(SECRET, body + b" ", sign(body))
    assert not deploy.verify(SECRET, body, "")
    assert not deploy.verify(None, body, sign(body))


@pytest.fixture
def hook(app, monkeypatch):
    app.config["DEPLOY_SECRET"] = SECRET
    calls = []
    monkeypatch.setattr(deploy, "run", lambda *a: calls.append(a) or ("updated", "abc1234 -> def5678"))
    return calls


def post(client, event, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    return client.post("/deploy/github", data=body, content_type="application/json",
                       headers={"X-GitHub-Event": event, "X-Hub-Signature-256": sign(body, secret)})


def test_the_webhook(app, hook):
    c = app.test_client()  # GitHub has no session
    assert post(c, "push", {"ref": f"refs/heads/{BRANCH}"}, secret="wrong").status_code == 403
    assert post(c, "ping", {}).get_json() == {"ok": True, "message": "pong"}
    assert post(c, "push", {"ref": "refs/heads/main"}).status_code == 202
    assert post(c, "issues", {"ref": f"refs/heads/{BRANCH}"}).status_code == 202
    assert hook == []
    r = post(c, "push", {"ref": f"refs/heads/{BRANCH}"})
    assert r.status_code == 200 and r.get_json()["status"] == "updated"
    branch, db_path, wsgi = hook[0]
    assert branch == BRANCH and wsgi == "/var/www/localhost_wsgi.py"


def test_off_without_a_secret(app):
    app.config["DEPLOY_SECRET"] = None
    assert app.test_client().post("/deploy/github", data=b"{}").status_code == 404


# ------------------------------------------------------------ a real run

def git(where, *args):
    return subprocess.run(["git", "-C", str(where), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """GitHub (a bare repo), the site's clone of it, and a PC that pushes."""
    origin, site, pc = tmp_path / "origin.git", tmp_path / "site", tmp_path / "pc"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", BRANCH, str(pc)], check=True)
    (pc / "requirements.txt").write_text("flask\n")
    (pc / "app.txt").write_text("one\n")
    git(pc, "add", ".")
    git(pc, "commit", "-qm", "one")
    git(pc, "remote", "add", "origin", str(origin))
    git(pc, "push", "-q", "origin", BRANCH)
    subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(origin), str(site)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(deploy, "ROOT", str(site))
    monkeypatch.setattr(deploy, "HOME", str(home))
    monkeypatch.setattr(deploy, "LOG", str(home / "stm-deploy.log"))
    monkeypatch.setattr(deploy, "LOCK", str(home / ".stm-deploy.lock"))
    monkeypatch.setattr(deploy, "BACKUPS", str(home / "stm-db-backup-auto-*.db"))
    db = tmp_path / "stm.db"
    db.write_text("pretend database")
    wsgi = tmp_path / "site_wsgi.py"
    wsgi.write_text("# wsgi")
    os.utime(wsgi, (1, 1))
    return origin, site, pc, home, db, wsgi


def test_steps_forward(repos):
    origin, site, pc, home, db, wsgi = repos
    assert deploy.run(BRANCH, str(db), str(wsgi))[0] == "current"
    (pc / "app.txt").write_text("two\n")
    git(pc, "commit", "-qam", "two")
    git(pc, "push", "-q", "origin", BRANCH)
    status, message = deploy.run(BRANCH, str(db), str(wsgi))
    assert status == "updated" and "reloaded" in message and "libraries" not in message
    assert (site / "app.txt").read_text() == "two\n"
    assert len(list(home.glob("stm-db-backup-auto-*.db"))) == 1
    assert os.path.getmtime(wsgi) > 1
    assert "updated:" in (home / "stm-deploy.log").read_text()
    assert not (home / ".stm-deploy.lock").exists()


def test_stops_with_nothing_changed(repos):
    origin, site, pc, home, db, wsgi = repos
    (pc / "app.txt").write_text("two\n")
    git(pc, "commit", "-qam", "two")
    git(pc, "push", "-q", "origin", BRANCH)
    (site / "app.txt").write_text("changed by hand\n")
    status, message = deploy.run(BRANCH, str(db), str(wsgi))
    assert status == "stopped" and "aren't committed" in message
    assert (site / "app.txt").read_text() == "changed by hand\n"
    git(site, "checkout", "-q", "--", "app.txt")
    # History rewritten on GitHub: never followed.
    git(pc, "commit", "-q", "--amend", "-m", "two, rewritten")
    git(pc, "push", "-q", "--force", "origin", BRANCH)
    git(site, "commit", "-q", "--allow-empty", "-m", "only here")
    assert deploy.run(BRANCH, str(db), str(wsgi))[0] == "stopped"
    assert list(home.glob("stm-db-backup-auto-*.db")) == []


def test_one_at_a_time(repos):
    origin, site, pc, home, db, wsgi = repos
    (home / ".stm-deploy.lock").write_text("")
    assert deploy.run(BRANCH, str(db), str(wsgi)) == ("busy", "Another update is running.")
