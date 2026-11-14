# STM — Simple Time Manager

A personal time-clock web app that replaces the manual Excel timesheet: tap **Login**,
**Break**, **Back** and **Logout** as your day goes, and STM works out **total hours without
break** and **weekly hours** for you — including telling you when you've hit your **48-hour weekly target** so you know
you can log out early (e.g. on Saturday).

## How the numbers are worked out

This mirrors the logic in the original spreadsheet:

- **Total hours for a day** = (logout time − login time) − actual break time taken.
  If logout is earlier in the day than login, STM assumes the shift ran past midnight.
- **Daily target** = 8 hours on workdays (Mon–Sat by default), 0 hours on your rest day
  (Sun by default). Both the days and the daily target are editable in **Settings**.
- **Weekly target** = 48 hours (Mon–Sat × 8h) by default, also editable in Settings.
- Once your running total for the week reaches the weekly target, STM tells you your
  target is complete — if you're still logged in, it shows a suggested logout time so
  you can leave early instead of working a full extra day.
- **Leave / holiday days**: any day can have its target overridden (e.g. 0 for a day
  off, half your usual target for a half day) from that day's Edit screen. The weekly
  target adjusts down (or up) to match, so a day of leave doesn't leave you owing extra
  hours later in the week.

## Features

- Big **Login / Break / Back / Logout** buttons. A missed or wrong tap is fixed afterwards
  on that day's Edit screen, which flags any day over 16 hours — nearly always an AM/PM slip.
- **Login again** after logging out carries on the same day, with the time away counted
  as a break. A shift left open overnight by mistake is spotted, and Home takes you to that
  day's Edit screen to put in when it really ended, rather than recording a 24-hour day.
- **Working past midnight** — a shift begun the day before and still open keeps STM on
  that day until the logout: Home, the Timesheet, Calendar and the rest stay on Friday at
  12:30 AM. Its lines carry on past 12 AM to the logout (a 3 PM to 12:30 AM day ends with
  12:00–12:30 AM), times typed across midnight work, the Timer writes after-midnight lines
  on that day, and a break taken after midnight counts as minutes. Logging in again within
  three hours of a logout around midnight carries that shift on, the time away a break.
- **Balances that make sense mid-day** — today only owes the hours it has reached so far,
  so the month's balance shows your real overtime at any moment.
- **History** — a full month view of every day, with an **Add/Edit** screen to enter or
  correct all 4 times (and multiple breaks) for any date by hand, or mark it as leave/a
  holiday with a custom target. That screen is where missed or wrong times are fixed.
- **Downloads** — any month from History as an **Excel** sheet (every punch, break,
  worked hours, target and over/short as real times that add up) or a **PDF** in soft
  pastels: a page of every day with the month's totals, then the month as a calendar
  of colour-coded days.
- **Calendar** — the same month as a plain grid, each day showing its hours and a small
  +/− for how far ahead or behind its target it was; tap a day to open it.
- **Timesheet** — what you worked on, through the day: a line per hour from when you
  logged in (a 12:47 login gives 12:47–2:00 PM, then 2–3 PM …) to the hour you're in now,
  which is marked. Breaks show as lines of their own, the hours carrying on as normal
  after them, and the last line ends at the logout -- once logged out, "End at logout" brings
  the last lines to it. Each line shows how long it is, and the day's hours sit at the top.
  Every line's times can be changed, and the line next to it follows (7–8 PM made 7–7:30 PM
  turns 8–9 PM into 7:30–9 PM), so there are no gaps or overlaps. Each line has a copy button
  that copies it as "Emails for a client (13:00 to 14:00 PM)" and turns it green -- for
  posting it on another timesheet -- until it's reset or changed; the top counts how many
  are copied. Lines can be added or deleted, and the day can be downloaded as a branded PNG
  with one button. Type and press Enter to save; a day with no login starts at 1 PM, and
  earlier days work the same way.
- **Timer** — type what you're working on and press Start: in the bar across the top of
  every page (on a phone, the pill above the tabs), on Home or on today's Timesheet. The
  bar also shows where the day is -- logged in, on a break, logged out -- and Start or
  Stop leaves you on the page you were on.
  Stop, Break or Logout writes the Timesheet line with the exact times; starting another
  task writes the running one first. The latest tasks are a tap from starting again.
- **A line's menu** — right-click a Timesheet line, a Board card or a Calendar block (or
  press its ⋯ button, or Shift+F10): Copy, Mark posted or not, Duplicate (into the free
  time straight after it, never over anything written), Split in two, Delete.
- **Arrow keys on times** — in any time box (Timesheet, Calendar, a day's Edit screen), Up
  and Down move the time 5 minutes, with Shift an hour; the next line follows, and the
  Timesheet saves once you stop.
- **Command palette** — Ctrl+K (or `/` when you're not in a box), or the search button
  at the top: type to go to any page or a day ("12 nov", "yesterday", "last friday"),
  Login, Break, Back or Logout as the day stands, start or stop the timer, start one of
  your last ten tasks again, or switch dark mode -- all from the keyboard.
- **Saves as you type** — the Timesheet saves a moment after typing stops (or when a box is
  left), and says when: "Saved · 2:41 PM". Enter saves there and then; anything still
  waiting is sent on the way out. A line with a problem waits, marked, while the rest save.
- **Timesheet without the arrows** — the Day tab shows the day's week as a strip (each day
  one tap away, with its hours) and a date box to go anywhere; the **All** tab lists every
  line written, newest first a day at a time, with search across all your history.
- **Boards** — **Posting**: To fill in, Written and Posted columns; drag a card across (or
  use its button) and the line turns green as posted. **Week**: a column a day, its hours
  against target over that day's lines as cards.
- **Week timesheet** — what you worked on down the side, the days across, each day's
  hours against the weekly target, "Not written yet" for hours with no words, **Copy
  week** (every line, day by day, ready to paste) and **Mark week posted**.
- **Many lines at once** — tick lines on the Timesheet's Day, Week and All tabs (Shift+click
  ticks a run of them) and a bar comes up: **Copy** them (then marked posted), **Mark
  posted**, **Mark not posted** or **Delete** (it asks first). On the Day tab anything typed
  is saved before the bar acts. Esc clears the ticks.
- **Copy last week and templates** — on the Week tab, **Copy last week** puts last week's
  lines on this week, day for day, at the same times; **Save as template** keeps this week's
  lines under a name (the same name replaces it) and **Use template** puts them on a week.
  Either fills only free time on days you've logged in -- clipped to that day's hours, up to
  now today, around breaks -- and never touches a line already there, or a deleted line's
  time, so doing it twice adds nothing.
- **Calendar day and week** — the Timesheet's lines as blocks on a time grid: click to
  write or change a line; drag a block's bottom edge to change its end, its top edge to
  change its start, or the block itself to move it, snapping to the zoom (1h, 30m, 15m or
  5m slots, remembered); the lines either side follow. With the keyboard, Up and Down move
  a block and Shift with them its end, saved once you stop (Esc puts it back). A red line
  marks now on today's column. The month grid is the third tab.
- **Reports** — this or last week or month: worked, due, overtime, breaks, days worked,
  usual hours, a bar per day against its target, where the time went, attendance, and
  the lines as a CSV.
- **Dashboard** — today's hours, this week's progress bar toward your target, a
  suggested logout time once you're close to (or past) the weekly target, and the last
  week's days with Timesheet lines still to fill in.
- **Last 12 months** — on Home and Reports, a square a day: darker for more of the day's
  target worked, striped for leave, outlined for today. Point at (or tab to) a square for its
  hours in words; click it to open the day. The arrow keys move a day or a week.
- **Goals** — switch them on in **Settings → Goals**: hours a day, hours a week, the
  Timesheet written (a share of the time worked), or logging out by a time. Home shows each
  with today's (or this week's) progress, the current streak and the best in the last year.
  Leave and rest days don't break a streak, and today can't until it's over; leave takes its
  hours off a week's goal.
- **Arrange Home** — the button at the foot of Home lists its cards: untick one to hide it,
  and put them in order with Up and Down (or drag). Home follows as you go and the layout is
  saved for next time; Reset brings back the usual one. The clock card can move but not hide.
  On a wide screen Month and Today sit beside the clock wherever they are in the list.
- **Pay** — put your monthly salary in **Settings → Pay**, with the month it starts from
  (a rise gets its own line, so earlier months keep their old salary). Each month's
  hourly rate is the salary over its working hours (its workdays in Settings × the daily
  target: ₹23,000 over 27 days of 8h is ₹106.48), and the month's overtime -- its balance
  on Calendar -- is paid at that rate; a month behind earns none. Home shows this
  month's overtime pay and the salary at month end, Calendar adds the month's overtime
  pay to its top line, and the **Pay** page leads with this month's salary so far, then
  lists every month with an overall total and a box for what the payslip really said --
  saved as you type, with the difference from STM's estimate beside it. An estimate, not
  a payslip: an employer's own rules can land a few hundred either way.
- **Weekly email** — 15 minutes after you log out on the week's last workday (Saturday), STM
  emails you the week: working days, working time, break time and productivity (time worked
  over the days worked at the daily target), the week against
  its target with overtime pay, each day's tasks with their times and what's not written yet,
  and what's still to do. There's no scheduler on a free PythonAnywhere account, so an open STM
  page sends it -- in the background, too -- or, with STM closed, the next page you open.
  Once a week; **Reports** has Preview and Send now, and **Settings → Weekly email** switches
  it off. It uses the same Gmail settings as the sign-in codes, and goes to your profile's
  email address.
- **Reminders** — while STM is open, a bar at the top of any page when you haven't logged in
  by a set time on a workday, are still logged in after so many hours, have taken more break
  than your usual allowance, or the last day worked has lines to fill in. Put one away with
  its x (it stays away that day); set each in **Settings → Reminders**, where browser
  notifications can be turned on too -- asked for only when you press the button.
- **Idle prompt** (off unless switched on in **Settings → Reminders**) — while the timer runs,
  coming back after the set minutes with no mouse or key activity on STM's pages asks what to
  do with the time away: keep it on the task, make it a break (the line splits around it), or
  drop it (the line ends where you went). It only sees STM's pages in this browser, so time
  spent working in other programs looks like time away too.
- **Change history** — every change to a day's login, logout, target, breaks and Timesheet
  line times is kept, with where it came from: punched, typed in by hand, the timer, copied in
  or a batch. A day's editor lists its changes; a day whose punch times were typed by hand is
  marked **By hand** on History, with a lilac dot on Calendar, and in the Excel and PDF month.
- **Settings** — change daily/weekly targets, workdays and your salary.
- **More room** — the button at the top left hides the side menu entirely (the logo in the
  top bar still goes home); press it again to bring the menu back. The browser remembers.
- **Profile** — tap your name or photo for a small window of your own settings: photo,
  name and email, password, dark mode, Pay and sign out. Settings opens it too.
- **Team members (admin only)** — the first account created owns the install. Only it
  can add people, reset their passwords, or remove them; everyone else never even sees
  that other accounts exist. Each person's hours, history and targets are their own.
- **Sign in with an emailed code** (optional) — see "Sign-in codes by email" below.
- **Easy to read and use**: 16px text, every colour pair checked for contrast in both
  light and dark mode, 44px+ touch targets, full keyboard use with visible focus, a
  bottom tab bar on phones, and a proper home-screen icon on iOS and Android.

## Sign-in codes by email (optional)

Off by default. With it configured, the login page also offers "Email me a sign-in
code": a 6-digit code is emailed and can be used instead of a password.

Set these environment variables (or add them to the WSGI file before the app is
imported), then reload the web app:

```
SMTP_USERNAME = your.address@gmail.com
SMTP_PASSWORD = your-16-character-app-password
MAIL_FROM     = your.address@gmail.com   # optional, defaults to SMTP_USERNAME
```

`SMTP_PASSWORD` must be a Google **App Password**, not your normal Gmail password:
turn on 2-Step Verification on the Google account, then create one under
Google Account → Security → App passwords. Gmail is the default host because
PythonAnywhere's free tier only permits outbound mail to Google's servers; on a paid
plan set `SMTP_HOST` / `SMTP_PORT` (and `SMTP_USE_SSL=1` for port 465) for any provider.

Each user needs their email address filled in under **Settings → Profile** before they
can receive a code.

How the codes are kept safe:

- generated with Python's `secrets` module, so they can't be predicted from earlier codes
- stored only as a hash, so a leaked database exposes no usable codes
- valid for 10 minutes, single-use, and replaced (not added to) when a new one is issued
- dead after 5 wrong guesses, which is what makes a 6-digit code impractical to guess
- one code per address per minute, and a cap per device per hour
- the same response whether or not the address has an account, so the form can't be used
  to discover who is registered

## Project structure

```
app/
  __init__.py       Flask app factory
  models.py         User / TimeEntry / BreakSegment / WorkBlock (SQLAlchemy)
  logic.py          All the hour/target/weekly-target math
  auth.py           First-run setup + login/logout
  main.py           Home, login/break/logout actions, timesheet, history, calendar, settings routes
  exports.py        The month as an Excel sheet or a PDF (XlsxWriter, ReportLab)
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

## Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

The tests run the real app on a database of their own in memory, with the clock held
still, so they never touch your hours. `tests/test_logic.py` pins down how the hours,
targets, balances and Timesheet lines are worked out today; `tests/test_routes.py` opens
every page and runs a day's Login, Break, Back and Logout.

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
