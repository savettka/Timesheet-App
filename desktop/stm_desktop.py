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
import time
from ctypes import wintypes

if not getattr(sys, "frozen", False):
    # Run from source: make the repo importable.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

APP_ID = "STM.TimeTracking"  # so the taskbar shows and pins it as STM, not Python
WINDOW_TITLE = "STM"
log = logging.getLogger("stm")

# The window's own title bar, in the page's colours (the --bg, --text and
# --line values from style.css), so it doesn't sit white over a dark app.
TITLE_BAR = {
    "dark": {"caption": "#0e1016", "text": "#f2f4f8", "border": "#2d323f"},
    "light": {"caption": "#f4f5f9", "text": "#151823", "border": "#dde1ea"},
}


# ------------------------------------------------------------- title bar

def _colorref(hex_color):
    """#rrggbb as the 0x00bbggrr number Windows wants."""
    red, green, blue = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return red | (green << 8) | (blue << 16)


def _find_window():
    """This copy's STM window, looked up by process and title -- plain Win32,
    so it's safe from any thread."""
    user32 = ctypes.windll.user32
    pid, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def check(hwnd, _unused):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, title, 64)
            if title.value == WINDOW_TITLE:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(check, 0)
    return found[0] if found else None


def _paint_title_bar(hwnd, theme):
    dwm = ctypes.windll.dwmapi
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    dwm.DwmSetWindowAttribute.restype = ctypes.c_long

    def put(attribute, value):
        return dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))

    # Dark or light frame and buttons; 20 on current Windows, 19 on early Windows 10.
    dark = ctypes.c_int(1 if theme == "dark" else 0)
    if put(20, dark) != 0:
        put(19, dark)
    # Exact colours -- Windows 11 only; older versions keep the plain dark or light bar.
    colours = TITLE_BAR[theme]
    put(35, wintypes.DWORD(_colorref(colours["caption"])))
    put(36, wintypes.DWORD(_colorref(colours["text"])))
    put(34, wintypes.DWORD(_colorref(colours["border"])))
    # Repaint the frame now rather than at the next resize.
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
    ctypes.windll.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER
                                      | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def _windows_prefers_dark():
    """The PC's own light/dark setting -- the page follows it until someone
    picks a theme in the app."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class TitleBar:
    """The one thing the pages can ask of the window: to match its title bar
    to the theme they're showing. (Exposed to them as window.pywebview.api.)"""

    def __init__(self):
        self._theme = None
        self._lock = threading.Lock()

    def set_theme(self, theme):
        if theme not in TITLE_BAR:
            return False
        with self._lock:
            self._theme = theme
            self._paint()
        return True

    def _default(self, theme):
        """The PC's own setting, used only until the page says otherwise."""
        with self._lock:
            if self._theme is None:
                self._theme = theme
                self._paint()

    def _paint(self):
        hwnd = _find_window()
        if hwnd:
            _paint_title_bar(hwnd, self._theme)


def _first_paint(title_bar, theme):
    """Colour the title bar as soon as the window exists, before the page
    has loaded and said which theme it's showing."""
    for _attempt in range(50):
        if _find_window():
            title_bar._default(theme)
            return
        time.sleep(0.1)


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


def _already_running(folder):
    """A second copy using the same data folder would fight the first over
    its files, so bring the open window forward instead. The lock is named
    after the folder: copies with separate folders don't clash."""
    import hashlib

    kernel32 = ctypes.windll.kernel32
    tag = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:16]
    handle = kernel32.CreateMutexW(None, False, "Local\\STM-Windows-App-" + tag)
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
    if _already_running(folder):
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
    theme = "dark" if _windows_prefers_dark() else "light"
    title_bar = TitleBar()
    # Background in the same colour as the title bar, so a dark PC gets no
    # white flash while the first page loads.
    webview.create_window(WINDOW_TITLE, url, width=1200, height=840, min_size=(420, 640),
                          background_color=TITLE_BAR[theme]["caption"], text_select=True,
                          js_api=title_bar)
    try:
        webview.start(_first_paint, (title_bar, theme), gui="edgechromium", private_mode=False,
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
