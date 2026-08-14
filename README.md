# STM — Simple Time Manager

A personal punch-clock web app that replaces the manual Excel timesheet: punch in/out and
break start/end live, and STM works out **total hours without break** and **weekly hours**
for you — including telling you when you've hit your **48-hour weekly target** so you know
you can log out early (e.g. on Saturday).

## How the numbers are worked out

This mirrors the logic in the original spreadsheet:

- **Total hours for a day** = (logout time − login time) − actual break time taken.
  If logout is earlier in the day than login, STM assumes the shift ran past midnight.
- **Daily target** = 8 hours on workdays (Mon–Sat by default), 0 hours on your rest day
  (Sun by default). Both the days and the daily target are editable in **Settings**.
- **Weekly target** = 48 hours (Mon–Sat × 8h) by default, also editable in Settings.
- Once your running total for the week reaches the weekly target, STM tells you your
  target is complete — if you're still punched in, it shows a suggested logout time so
  you can leave early instead of working a full extra day.

## Features

- Live **Punch in / Start break / End break / Punch out** buttons, plus an option to
  enter a different time for any punch (for when you forget to punch live).
- **History** — a full month view of every day, with an **Add/Edit** screen to enter or
  correct all 4 times (and multiple breaks) for any date by hand.
- **Dashboard** — today's hours, this week's progress bar toward your target, and a
  suggested logout time once you're close to (or past) the weekly target.
- **Settings** — change daily/weekly targets, workdays, display name, and password.
- Modern UI with a **light/dark mode toggle** (remembers your choice).
- Single-user login (you set your username/password the first time you open the app).

## Project structure

```
app/
  __init__.py       Flask app factory
  models.py         User / TimeEntry / BreakSegment (SQLAlchemy)
  logic.py          All the hour/target/weekly-target math
  auth.py           First-run setup + login/logout
  main.py           Dashboard, punch actions, history, settings routes
  templates/        Jinja2 templates
  static/           CSS + JS (no build step, no frontend framework)
config.py           Reads SECRET_KEY / DATABASE_URL from the environment
wsgi.py             Entry point for PythonAnywhere / gunicorn
run.py              Entry point for local development
```

## Run it locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open `http://127.0.0.1:5000` — the first visit walks you through creating your account.

## Deploy for free on PythonAnywhere

1. **Create a PythonAnywhere account** at pythonanywhere.com (the free "Beginner" plan is
   enough for this app).

2. **Get the code onto PythonAnywhere.** Open a **Bash console** from the PythonAnywhere
   dashboard and clone your repo:

   ```bash
   git clone https://github.com/<your-username>/<your-repo>.git stm
   cd stm
   ```

   (No GitHub? Use the **Files** tab to upload the project as a zip and unzip it instead.)

3. **Create a virtualenv and install dependencies** (still in the Bash console):

   ```bash
   mkvirtualenv --python=python3.10 stm-venv
   pip install -r requirements.txt
   ```

4. **Create a Web app**: go to the **Web** tab → **Add a new web app** → choose
   **Manual configuration** → pick the same Python version as your virtualenv.

5. **Point it at your virtualenv**: on the Web tab, under **Virtualenv**, enter the path
   PythonAnywhere printed after `mkvirtualenv`, typically:
   `/home/<your-username>/.virtualenvs/stm-venv`

6. **Set the WSGI file**: click the `WSGI configuration file` link on the Web tab, delete
   its contents, and replace them with:

   ```python
   import sys
   path = '/home/<your-username>/stm'
   if path not in sys.path:
       sys.path.insert(0, path)

   from wsgi import application
   ```

7. **Set a real secret key** (recommended): on the Web tab under **Environment variables**
   add `SECRET_KEY` with a long random value. If your plan doesn't expose that section,
   instead add this near the top of the WSGI file, before the `from wsgi import application`
   line:

   ```python
   import os
   os.environ['SECRET_KEY'] = 'put-a-long-random-string-here'
   ```

8. **Set the working directory / static files** (optional but recommended): on the Web
   tab, set **Source code** to `/home/<your-username>/stm`, and add a static files mapping
   `/static/` → `/home/<your-username>/stm/app/static/`.

9. Click the big green **Reload** button, then open your app at
   `https://<your-username>.pythonanywhere.com`. The first visit will ask you to create
   your STM account (username + password) — that's your personal login for the app.

The app stores everything in a small SQLite database created automatically under
`instance/stm.db` the first time it runs, so there's nothing else to set up.

### Keeping it updated

When you pull new changes on PythonAnywhere:

```bash
cd ~/stm
git pull
workon stm-venv
pip install -r requirements.txt
```

Then hit **Reload** on the Web tab.
