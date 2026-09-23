# STM for Windows

STM running on your PC, in its own window, with its own copy of your hours — so it
works without internet — kept in step with your STM website whenever you're online.

## Using it

1. Unzip `STM-Windows.zip` somewhere permanent, e.g. `Documents\STM`.
2. Open `STM.exe`. The first time, Windows may say *"Windows protected your PC"* because
   the app isn't signed by a paid certificate: choose **More info → Run anyway**.
3. Enter your STM web address (the one you open in the browser, e.g.
   `https://yourname.pythonanywhere.com`), your username and password. The password is
   used once to connect and then forgotten; the app keeps a sign-in key instead,
   encrypted for your Windows account.
4. Right-click STM on the taskbar → **Pin to taskbar** to keep it one click away.

Everything works offline. The top bar shows the sync state:

| Pill | Meaning |
|---|---|
| ● Synced just now | Up to date with the website |
| ● 2 to send | Changes saved here, going up in a moment |
| ● Offline · 2 saved here | No connection; nothing is lost, it sends when you're back online |
| ● Sign in again | This PC was signed out from the website; keep working, then sign in |

If the same day was changed on the PC and on the web while they were apart, the newer
edit wins and a notice offers the other version back with one click.

Your targets, profile and password are changed on the website and flow into the app.
To stop a PC syncing — lost laptop, new machine — go to **Settings → Windows apps signed
in** on the website and sign it out.

The app keeps its data in `%LOCALAPPDATA%\STM` (the local copy, the encrypted sign-in
key, and a small log). **Settings → Sign out of this PC** removes your hours from it.

## How it works

`STM.exe` runs the same STM code as the website (`app/`), in "PC mode"
(`desktop/core.py`), on a private copy of the database, shown in a Microsoft Edge
WebView2 window (built into Windows 11; Windows 10 needs the free WebView2 Runtime).
The local server listens only on `127.0.0.1`, on a fresh port each launch, and
answers only the window, which carries a key made at launch.

Sync (`app/sync.py`, `app/api.py`, `desktop/engine.py`):

- Every change to a day, on either side, goes through one database hook. On the
  server it stamps the day with the user's next change number; on the PC it queues
  the day to send.
- The app sends its queued days and asks for everything changed since its last
  sync, in one request, every minute and ~1.5 s after each change.
- A day edited in both places is settled by the newer edit time, and the other
  version is returned so nothing is thrown away.
- Deleted days leave a marker so the delete reaches the other side too.
- The PC's database takes its write lock at the start of each transaction, so a
  sync and a click can never interleave.

## Building it

On Windows, from the repo root, with Python 3.12:

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r desktop\requirements-build.txt
.venv\Scripts\pyinstaller desktop\STM.spec --noconfirm
.venv\Scripts\python -m zipfile -c STM-Windows.zip dist\STM
```

`dist\STM\STM.exe --selftest` checks a build is complete without opening a window
(set `STM_SELFTEST_OUT` to a file path to get the result written there).

The server needs the sync API from this repo (`/api/v1/...`) deployed before a PC can
connect.
