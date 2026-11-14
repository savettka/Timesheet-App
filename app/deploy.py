"""Updates from GitHub: the site moves itself forward to what was pushed.

After a push, GitHub calls POST /deploy/github (a webhook, signed with a
secret only it and the site know). STM checks the signature, then -- in
order, stopping with nothing changed at the first problem:

  1. fetches its branch from GitHub;
  2. stops unless that's a straight step forward from what's running (no
     history rewritten) and nothing here has been changed by hand;
  3. backs up the database (the last ten are kept);
  4. moves the code forward;
  5. installs any new libraries, if requirements.txt changed;
  6. touches the WSGI file -- how PythonAnywhere reloads a site.

Every step goes to ~/stm-deploy.log. The same thing from a Bash console,
for when GitHub can't reach the site (in the app's folder, with its
virtualenv's Python):

    python -m app.deploy
"""

import glob
import hashlib
import hmac
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
LOG = os.path.join(HOME, "stm-deploy.log")
LOCK = os.path.join(HOME, ".stm-deploy.lock")
BACKUPS = os.path.join(HOME, "stm-db-backup-auto-*.db")
KEEP_BACKUPS = 10
DEFAULT_BRANCH = "claude/stm-timesheet-app-tvtw3c"
STALE_LOCK = 10 * 60  # seconds: an update that never finished


def verify(secret, body, signature):
    """Whether a webhook's X-Hub-Signature-256 is the body signed with the
    secret -- compared in constant time."""
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _log(line):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp}  {line}\n")
    except OSError:
        pass


def _git(*args, timeout=90):
    done = subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout + done.stderr).strip()


def _python():
    """The virtualenv's Python -- under a web server, sys.executable may be
    the server itself."""
    for name in ("bin/python", "Scripts/python.exe"):
        path = os.path.join(sys.prefix, name)
        if os.path.exists(path):
            return path
    return sys.executable


def _backup(db_path):
    if not db_path or not os.path.exists(db_path):
        return None
    copy = os.path.join(HOME, f"stm-db-backup-auto-{datetime.now():%Y-%m-%d-%H%M%S}.db")
    shutil.copy2(db_path, copy)
    for old in sorted(glob.glob(BACKUPS))[:-KEEP_BACKUPS]:
        os.remove(old)
    return copy


def _lock():
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if time.time() - os.path.getmtime(LOCK) < STALE_LOCK:
            return False
        os.remove(LOCK)
        return _lock()
    os.close(fd)
    return True


def run(branch, db_path=None, wsgi_file=None):
    """Bring the site forward to its branch on GitHub. Returns (status,
    message): "updated", "current" (nothing new), "busy" (another update
    is running), "stopped" (nothing changed: why) or "failed"."""
    if not _lock():
        return "busy", "Another update is running."
    try:
        status, message = _run(branch, db_path, wsgi_file)
    except Exception as error:  # a timeout, a missing git: say so, keep the site as it is
        status, message = "failed", f"{type(error).__name__}: {error}"
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass
    _log(f"{status}: {message}")
    return status, message


def _run(branch, db_path, wsgi_file):
    code, out = _git("fetch", "--quiet", "origin", branch)
    if code:
        return "failed", "Couldn't fetch from GitHub: " + out[-300:]
    _, head = _git("rev-parse", "HEAD")
    _, new = _git("rev-parse", "FETCH_HEAD")
    if head == new:
        return "current", f"Already at {head[:7]}."
    _, here = _git("rev-parse", "--abbrev-ref", "HEAD")
    if here != branch:
        return "stopped", f"The site is on {here}, not {branch}."
    if _git("merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD")[0]:
        return "stopped", f"{new[:7]} isn't a straight step forward from {head[:7]}."
    _, dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        return "stopped", "Files here have changes that aren't committed: " + dirty.replace("\n", ", ")[:200]

    copy = _backup(db_path)
    _, old_requirements = _git("show", "HEAD:requirements.txt")
    code, out = _git("merge", "--ff-only", "--quiet", "FETCH_HEAD")
    if code:
        return "failed", "Couldn't move forward: " + out[-300:]
    done = [f"{head[:7]} -> {new[:7]}", f"database saved to {copy}" if copy else "no database to save"]

    try:
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            changed = f.read().strip() != old_requirements.strip()
    except OSError:
        changed = False
    if changed:
        pip = subprocess.run([_python(), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r",
                              os.path.join(ROOT, "requirements.txt")], capture_output=True, text=True, timeout=600)
        done.append("libraries installed" if pip.returncode == 0 else "pip couldn't finish: " + pip.stderr[-200:])

    if wsgi_file and os.path.exists(wsgi_file):
        os.utime(wsgi_file, None)
        done.append("reloaded")
    else:
        done.append("press Reload on the Web tab: no WSGI file found")
    return "updated", "; ".join(done)


def _wsgi_here():
    found = os.environ.get("STM_WSGI_FILE") or next(iter(glob.glob("/var/www/*_wsgi.py")), None)
    return found


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    branch = os.environ.get("DEPLOY_BRANCH", DEFAULT_BRANCH)
    status, message = run(branch, os.path.join(ROOT, "instance", "stm.db"), _wsgi_here())
    print(f"{status}: {message}")
    sys.exit(0 if status in ("updated", "current") else 1)
