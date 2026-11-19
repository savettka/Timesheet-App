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
"""
import hmac
import ipaddress
import re

HOST = re.compile(r"[A-Za-z0-9.-]{1,253}(:\d{1,5})?\Z")


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
        return self.wsgi_app(environ, start_response)
