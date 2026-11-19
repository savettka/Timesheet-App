"""Visits that come through a proxy putting STM on a domain of your own (a
Cloudflare Worker, say).

The proxy adds a shared secret and what it saw: the visitor's address, the
domain they asked for and its scheme. Only with the secret right does STM
believe them. Without it, or with STM_PROXY_SECRET unset, the headers count
for nothing, so nobody can claim another address by sending them straight
to the server.

    X-STM-Proxy      the shared secret (STM_PROXY_SECRET)
    X-STM-Client-IP  the visitor's address: sign-in limits count per address
    X-STM-Host       the domain asked for: links STM writes out use it
    X-STM-Proto      https or http

With STM_PUBLIC_URL set too, a page opened at the server's own address
sends the browser on to the same page there, for now (302) so it's undone by
unsetting it. Not sent on: the proxy's own visits, anything but GET and
HEAD, the GitHub webhook, and the leave calendar feed that calendar apps
already subscribe to.
"""
import hmac
import ipaddress
import re
from urllib.parse import quote

from werkzeug.utils import redirect

HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?\Z")
STAYS = ("/deploy/", "/calendar/leave.ics")


class FromProxy:
    """WSGI middleware: a visit with the right secret takes the proxy's word
    for who and where; the secret itself goes no further than here."""

    def __init__(self, wsgi_app, config):
        self.wsgi_app = wsgi_app
        self.config = config

    def __call__(self, environ, start_response):
        given = environ.pop("HTTP_X_STM_PROXY", "")
        secret = self.config.get("PROXY_SECRET") or ""
        if secret and given and hmac.compare_digest(given.encode(), secret.encode()):
            ip = environ.get("HTTP_X_STM_CLIENT_IP", "").strip()
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                ip = ""
            if ip:
                environ["HTTP_X_REAL_IP"] = ip
                environ["REMOTE_ADDR"] = ip
            host = environ.get("HTTP_X_STM_HOST", "").strip()
            if HOST.match(host):
                environ["HTTP_HOST"] = host
            proto = environ.get("HTTP_X_STM_PROTO", "")
            environ["wsgi.url_scheme"] = proto if proto in ("http", "https") else "https"
        elif not given:
            # Anything carrying the header, right or wrong, is the proxy: sent
            # on, a proxy with a stale secret would only come back here.
            to = self._public_page(environ)
            if to:
                return redirect(to, 302)(environ, start_response)
        return self.wsgi_app(environ, start_response)

    def _public_page(self, environ):
        public = (self.config.get("PUBLIC_URL") or "").rstrip("/")
        path = environ.get("SCRIPT_NAME", "") + environ.get("PATH_INFO", "")
        if (not public or environ.get("REQUEST_METHOD") not in ("GET", "HEAD")
                or path.startswith(STAYS)):
            return None
        to = public + quote(path.encode("latin-1"), safe="/:@!$&'()*+,;=-._~%")
        query = environ.get("QUERY_STRING", "")
        return to + "?" + query if query else to
