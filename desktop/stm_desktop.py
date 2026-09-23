"""STM for Windows.

Starts STM on this PC, shows it in its own window, keeps it synced in the
background, and shuts everything down cleanly when the window closes --
sending anything still waiting on the way out.
"""

import ctypes
import logging
import logging.handlers
import os
import sys
import tempfile
import threading

if not getattr(sys, "frozen", False):
    # Run from source: make the repo importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_ID = "STM.TimeTracking"  # so the taskbar shows and pins it as STM, not Python
MUTEX_NAME = "Local\\STM-Windows-App"
WINDOW_TITLE = "STM"
log = logging.getLogger("stm")


def data_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "STM")


def _setup_logging(folder):
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(folder, "stm.log"), maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # The local server's request log would record the window's launch key in
    # the first address it opens. Nothing in it is worth keeping anyway.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _message(text):
    ctypes.windll.user32.MessageBoxW(None, text, WINDOW_TITLE, 0x40)


def _already_running():
    """A second copy would fight the first over the same files, so bring the
    open window forward instead."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        window = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
        if window:
            ctypes.windll.user32.ShowWindow(window, 9)  # restore if minimised
            ctypes.windll.user32.SetForegroundWindow(window)
        return True
    globals()["_mutex"] = handle  # held for as long as this copy runs
    return False


def _selftest():
    """Check a built copy is complete -- pages, templates, the window
    library -- without opening a window. Used by the build."""
    import webview  # noqa: F401  (the window library made it in)
    from desktop.core import create_desktop_app

    app = create_desktop_app(tempfile.mkdtemp(prefix="stm-selftest-"))
    client = app.test_client()
    page = client.get("/desktop/enter?key=" + app.config["DESKTOP_KEY"], follow_redirects=True)
    styles = client.get("/static/css/style.css")
    ok = (page.status_code == 200 and b"Welcome to STM for Windows" in page.data
          and styles.status_code == 200 and b"sync-pill" in styles.data)
    out = os.environ.get("STM_SELFTEST_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write("OK\n" if ok else "FAILED page=%s css=%s\n" % (page.status_code, styles.status_code))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return _selftest()

    folder = data_dir()
    os.makedirs(folder, exist_ok=True)
    _setup_logging(folder)
    if _already_running():
        return 0
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    from werkzeug.serving import make_server
    import webview
    from desktop.core import create_desktop_app

    app = create_desktop_app(folder)
    # Only this PC can reach it, on a port chosen fresh each launch.
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, name="stm-local", daemon=True).start()
    engine = app.extensions["stm_sync"]
    engine.start()
    log.info("STM started on port %s", server.server_port)

    url = "http://127.0.0.1:%d/desktop/enter?key=%s" % (server.server_port, app.config["DESKTOP_KEY"])
    webview.create_window(WINDOW_TITLE, url, width=1200, height=840, min_size=(420, 640),
                          background_color="#F4F5F9", text_select=True)
    try:
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=os.path.join(folder, "webview"))
    except Exception:
        log.exception("the window couldn't open")
        _message("STM couldn't open its window. It needs Microsoft Edge WebView2, which comes with "
                 "Windows 11 -- on Windows 10, install \"WebView2 Runtime\" from Microsoft, then try again.")
    finally:
        log.info("closing: sending anything still waiting")
        engine.stop(final_sync=True)
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
